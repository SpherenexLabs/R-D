# main.py — Pico W Baby Cradle (MicroPython)
# - Mic AO -> ADC0 (GP26). SOUND classifier (Goertzel) -> SILENCE / DISCOMFORT / HUNGRY / PAIN.
# - Servo on GP15: 500 ms +25° (115°), 500 ms -25° (65°), repeat for RUN_WINDOW_MS, then back to 90°.
# - OLED 128x64 I2C (GP4 SDA / GP5 SCL): Emotion (from RTDB, small), Cradle status, DHT11 Temp/Humidity.
# - DHT11 on GP13.
# - Firebase RTDB (REST): writes Audio "1/0" (active/silent) + AudioLabel string; reads Emotion+Servo (~200 ms).
# - Serial monitor shows compact STATUS lines every 500 ms + ADC debug.

import network, time, ujson as json
import urequests as requests
from machine import Pin, ADC, PWM, I2C
import ssd1306, dht, math, gc

try:
    import usocket as socket
except ImportError:
    import socket

# ---------- Wi-Fi ----------
WIFI_SSID = "baby"
WIFI_PASS = "123456789"

# ---------- Firebase (UPDATED HOST/API) ----------
FB_API_KEY   = "AIzaSyAhLCi6JBT5ELkAFxTplKBBDdRdpATzQxI"
FB_DB_HOST   = "smart-medicine-vending-machine-default-rtdb.asia-southeast1.firebasedatabase.app"
FB_AUTH_HOST = "identitytoolkit.googleapis.com"
FB_EMAIL     = "spherenexgpt@gmail.com"
FB_PASSWORD  = "Spherenex@123"

# Run in open-rules mode. Flip to True when Identity sign-in is allowed on your network.
AUTH_REQUIRED = False

# ---------- RTDB endpoints ----------
PATH_AUDIO        = "/Baby_Cradlle/Audio.json"         # "1" / "0" (active / silent)
PATH_AUDIO_LABEL  = "/Baby_Cradlle/AudioLabel.json"    # "SILENCE" | "DISCOMFORT" | "HUNGRY" | "PAIN" (additive)
PATH_SERVO        = "/Baby_Cradlle/Servo.json"
PATH_EMOTION      = "/Baby_Cradlle/Emotion.json"
PATH_ROOT         = "/Baby_Cradlle.json"               # read Emotion+Servo together

# ---------- Pins (Pico W) ----------
PIN_ADC_MIC = 26      # GP26 / ADC0
PIN_SERVO   = 15
PIN_DHT     = 13
PIN_LED     = "LED"
PIN_I2C_SDA = 4
PIN_I2C_SCL = 5

# ---------- Timing / thresholds ----------
HOP_INTERVAL_MS = 500
RUN_WINDOW_MS   = 10000
FB_POLL_MS      = 200
DHT_INTERVAL_MS = 2000
STATUS_PRINT_MS = 500
ADC_PRINT_MS    = 250

# ---------- Globals (auth) ----------
id_token = ""
token_expiry_ms = 0

# ---------- ADC / Servo ----------
adc = ADC(Pin(PIN_ADC_MIC))
def read_adc_10bit(): return adc.read_u16() >> 6  # 0..1023

servo_pwm = PWM(Pin(PIN_SERVO)); servo_pwm.freq(50)
SERVO_MIN_US, SERVO_MAX_US = 500, 2500
def angle_to_duty_u16(angle):
    angle = 0 if angle < 0 else 180 if angle > 180 else angle
    pulse = SERVO_MIN_US + (SERVO_MAX_US - SERVO_MIN_US) * (angle / 180.0)
    return int((pulse / 20000.0) * 65535.0)
def servo_write(angle): servo_pwm.duty_u16(angle_to_duty_u16(angle))
SERVO_CENTER = 90
SERVO_LOW    = SERVO_CENTER - 25
SERVO_HIGH   = SERVO_CENTER + 25

# ---------- DHT / OLED / LED ----------
d = dht.DHT11(Pin(PIN_DHT)); last_temp_c = float("nan"); last_hum = float("nan"); last_dht_ms = 0
i2c = I2C(0, scl=Pin(PIN_I2C_SCL), sda=Pin(PIN_I2C_SDA), freq=400000)
oled = ssd1306.SSD1306_I2C(128, 64, i2c)
def center_text_y(y, text): oled.text(text, max(0, (128 - len(text)*8)//2), y)
led = Pin(PIN_LED, Pin.OUT)
def led_on():  led.value(1)
def led_off(): led.value(0)

# ---------- Motion / app state ----------
pattern_active = False
pattern_start_ms = 0
last_hop_ms = 0
current_target = SERVO_CENTER

# what we show / send
audio_active = False            # boolean -> PATH_AUDIO "1"/"0"
audio_label  = "SILENCE"        # string  -> PATH_AUDIO_LABEL
last_pushed_active = None
last_pushed_label  = None
last_emotion = ""               # read from RTDB to show on OLED

# ---------- ADC debug smoothing (unchanged) ----------
alpha_base, alpha_env = 0.001, 0.05
a_base, a_env, a_min_win, a_max_win = 0.0, 0.0, 1023, 0
last_adc_print_ms = 0
last_status_ms = 0

# =================================================================
#                 SOUND BLOCK (your code, integrated)
# =================================================================
ADC_SAMPLE_RATE = 8000      # Hz
SAMPLES         = 160       # per frame (~20 ms)
TARGET_FREQ_HUNGRY = 600    # Hz
TARGET_FREQ_PAIN   = 2000   # Hz
HUNGRY_THRESHOLD   = 0.1
PAIN_THRESHOLD     = 0.1
SILENCE_RMS_THRESHOLD = 0.3

# cadence for doing a classification frame inside the main loop
CLASSIFY_INTERVAL_MS = 200
last_classify_ms = 0
silence_printed = False
hungry_count = 0
HUNGRY_LIMIT = 5

def goertzel(samples, target_freq, sample_rate):
    n = len(samples)
    k = int(0.5 + ((n * target_freq) / sample_rate))
    omega = (2.0 * math.pi * k) / n
    coeff = 2.0 * math.cos(omega)
    s_prev = 0.0
    s_prev2 = 0.0
    for sample in samples:
        s = sample + coeff * s_prev - s_prev2
        s_prev2 = s_prev
        s_prev = s
    power = s_prev2*2 + s_prev*2 - coeff * s_prev * s_prev2   # (kept exactly as you provided)
    return power / n

def sample_audio():
    buf = []
    us = int(1_000_000 / ADC_SAMPLE_RATE)  # 125 us at 8 kHz
    for _ in range(SAMPLES):
        raw = adc.read_u16()
        normalized = (raw - 32768) / 32768.0
        buf.append(normalized)
        time.sleep_us(us)
    return buf

def compute_rms(samples):
    square_sum = 0.0
    for s in samples:
        square_sum += s*s
    return math.sqrt(square_sum / len(samples))

def classify_frame():
    """Return label string from one audio frame."""
    global silence_printed
    samples = sample_audio()
    rms = compute_rms(samples)
    # print("DEBUG RMS:", rms)   # keep quiet by default; uncomment to see
    if rms < SILENCE_RMS_THRESHOLD:
        silence_printed = True
        return "SILENCE"
    # energies
    e_h = goertzel(samples, TARGET_FREQ_HUNGRY, ADC_SAMPLE_RATE)
    e_p = goertzel(samples, TARGET_FREQ_PAIN, ADC_SAMPLE_RATE)
    # print("Hungry:", e_h, " Pain:", e_p)  # optional debug
    if e_p > PAIN_THRESHOLD and e_p > e_h * 1.5:
        return "PAIN"
    elif e_h > HUNGRY_THRESHOLD:
        return "HUNGRY"
    else:
        return "DISCOMFORT"
# =================================================================

# ---------- Wi-Fi + DNS ----------
def _force_public_dns(wlan):
    try:
        ip, nm, gw, dns = wlan.ifconfig()
        def _is_private(s): return s.startswith("10.") or s.startswith("192.168.") or s.startswith("172.")
        if dns == gw or _is_private(dns):
            for cand in ("8.8.8.8", "1.1.1.1"):
                try:
                    wlan.ifconfig((ip, nm, gw, cand))
                    print("DNS ->", cand)
                    break
                except Exception as e:
                    print("DNS set error:", e)
        else:
            print("DNS OK:", dns)
    except Exception as e:
        print("DNS check error:", e)

def _ensure_dns_resolves(host):
    try:
        socket.getaddrinfo(host, 443)
        return True
    except Exception:
        try:
            wlan = network.WLAN(network.STA_IF)
            _force_public_dns(wlan)
            socket.getaddrinfo(host, 443)
            return True
        except Exception as e:
            print("DNS resolve err for {}: {}".format(host, e))
            return False

def wifi_connect():
    wlan = network.WLAN(network.STA_IF); wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(WIFI_SSID, WIFI_PASS)
        print("WiFi: connecting", end="")
        t0 = time.ticks_ms()
        while not wlan.isconnected():
            print(".", end=""); time.sleep_ms(250)
            if time.ticks_diff(time.ticks_ms(), t0) > 20000:
                raise RuntimeError("WiFi connect timeout")
        print()
    print("WiFi OK:", wlan.ifconfig())
    _force_public_dns(wlan)
    return wlan

# ---------- Firebase Auth (kept; skipped unless AUTH_REQUIRED=True) ----------
def firebase_sign_in(max_retries=5):
    global id_token, token_expiry_ms
    if not AUTH_REQUIRED: return False
    endpoints = [
        "https://{}/v1/accounts:signInWithPassword?key={}".format(FB_AUTH_HOST, FB_API_KEY),
        "https://www.googleapis.com/identitytoolkit/v3/relyingparty/verifyPassword?key={}".format(FB_API_KEY),
    ]
    payload = {"email": FB_EMAIL, "password": FB_PASSWORD, "returnSecureToken": True}
    body = json.dumps(payload)
    headers = {"Content-Type": "application/json", "Connection": "close"}

    for ep in endpoints:
        for attempt in range(1, max_retries + 1):
            try:
                gc.collect()
                r = requests.post(ep, data=body, headers=headers)
                if r.status_code == 200:
                    data = r.json(); r.close()
                    id_token = data.get("idToken", "")
                    expires = int(data.get("expiresIn", "3600"))
                    token_expiry_ms = time.ticks_add(time.ticks_ms(), (expires - 60) * 1000)
                    print("Firebase sign-in OK (try {})".format(attempt))
                    return True
                else:
                    txt = r.text; r.close()
                    print("Auth HTTP {}: {}".format(r.status_code, txt))
            except Exception as e:
                print("Auth error (try {}): {}".format(attempt, e))
            time.sleep_ms(350 * attempt)
    return False

def ensure_auth():
    if not AUTH_REQUIRED: return False
    if (not id_token) or time.ticks_diff(time.ticks_ms(), token_expiry_ms) > 0:
        return firebase_sign_in()
    return True

# ---------- Firebase REST helpers ----------
def _db_url(path, use_auth=True):
    if use_auth and id_token:
        return "https://{}{}?auth={}".format(FB_DB_HOST, path, id_token)
    else:
        return "https://{}{}".format(FB_DB_HOST, path)

def db_put(path, body_str):
    headers = {"Content-Type": "application/json", "Connection": "close"}
    if not _ensure_dns_resolves(FB_DB_HOST):
        print("PUT skipped (DNS)"); return False
    if AUTH_REQUIRED and id_token:
        try:
            url = _db_url(path, use_auth=True)
            r = requests.put(url, data=body_str, headers=headers); code = r.status_code; r.close()
            if code == 200: return True
            if code in (401, 403) and ensure_auth():
                url = _db_url(path, use_auth=True)
                r = requests.put(url, data=body_str, headers=headers); ok = (r.status_code == 200); r.close()
                if ok: return True
        except Exception as e:
            print("PUT (auth) err:", e)
    try:
        url = _db_url(path, use_auth=False)
        r = requests.put(url, data=body_str, headers=headers); ok = (r.status_code == 200); r.close()
        return ok
    except Exception as e:
        print("PUT (open) err:", e); return False

def db_get(path):
    headers = {"Connection": "close"}
    if not _ensure_dns_resolves(FB_DB_HOST):
        print("GET skipped (DNS)"); return -1, None
    if AUTH_REQUIRED and id_token:
        try:
            url = _db_url(path, use_auth=True)
            r = requests.get(url, headers=headers); code = r.status_code; txt = r.text; r.close()
            if code == 200: return code, txt
            if code in (401, 403) and ensure_auth():
                url = _db_url(path, use_auth=True)
                r = requests.get(url, headers=headers); code = r.status_code; txt = r.text; r.close()
                if code == 200: return code, txt
        except Exception as e:
            print("GET (auth) err:", e)
    try:
        url = _db_url(path, use_auth=False)
        r = requests.get(url, headers=headers); code = r.status_code; txt = r.text; r.close()
        return code, txt
    except Exception as e:
        print("GET (open) err:", e); return -1, None

# ---------- OLED ----------
def draw_oled():
    # show server-provided Emotion if present; else our audio_label
    top = last_emotion if last_emotion else audio_label
    status = "Cradle: " + ("MOVING" if pattern_active else "STATIC")
    th = "T: --.-C  H: --.-%"
    if not (math.isnan(last_temp_c) or math.isnan(last_hum)):
        th = "T: {:.1f}C  H: {:.1f}%".format(last_temp_c, last_hum)
    oled.fill(0); oled.text("Baby Cradle", 0, 0); center_text_y(18, top[:14].upper()); oled.text(status, 0, 40); oled.text(th, 0, 56); oled.show()

# ---------- Servo pattern ----------
def start_pattern():
    global pattern_active, pattern_start_ms, last_hop_ms, current_target
    pattern_active = True; pattern_start_ms = time.ticks_ms(); last_hop_ms = pattern_start_ms
    current_target = SERVO_HIGH; servo_write(current_target); led_on()
    print("Servo START (500ms +25 / 500ms -25 for {}s)".format(RUN_WINDOW_MS//1000))

def stop_pattern():
    global pattern_active
    pattern_active = False; servo_write(SERVO_CENTER); led_off(); print("Servo STOP -> 90")

def update_pattern(now_ms):
    global last_hop_ms, current_target
    if not pattern_active: return
    if time.ticks_diff(now_ms, last_hop_ms) >= HOP_INTERVAL_MS:
        last_hop_ms = now_ms
        current_target = SERVO_LOW if current_target == SERVO_HIGH else SERVO_HIGH
        servo_write(current_target)
    if time.ticks_diff(now_ms, pattern_start_ms) >= RUN_WINDOW_MS:
        stop_pattern()

# ---------- DHT ----------
def maybe_read_dht(now_ms):
    global last_dht_ms, last_temp_c, last_hum
    if time.ticks_diff(now_ms, last_dht_ms) >= DHT_INTERVAL_MS:
        last_dht_ms = now_ms
        try:
            d.measure(); t, h = d.temperature(), d.humidity()
            if isinstance(t, (int, float)) and isinstance(h, (int, float)):
                last_temp_c, last_hum = float(t), float(h)
        except Exception:
            pass

# ---------- Serial STATUS ----------
def print_status(now_ms, raw10):
    global last_status_ms
    if time.ticks_diff(now_ms, last_status_ms) < STATUS_PRINT_MS: return
    last_status_ms = now_ms
    angle = current_target if pattern_active else SERVO_CENTER
    emo = last_emotion if last_emotion else audio_label
    t_str = "--.-C" if math.isnan(last_temp_c) else "{:.1f}C".format(last_temp_c)
    h_str = "--.-%" if math.isnan(last_hum) else "{:.1f}%".format(last_hum)
    secs = time.ticks_ms()/1000.0
    print('STATUS t={:.3f}s  A0={}  Audio={}  Cradle={}  Angle={}  Emotion="{}"  T={}  H={}'
          .format(secs, raw10, 1 if audio_active else 0, "MOVING" if pattern_active else "STATIC",
                  angle, emo, t_str, h_str))

# ---------- Main ----------
def main():
    global audio_active, audio_label, last_pushed_active, last_pushed_label, last_emotion
    global a_base, a_env, a_min_win, a_max_win, last_classify_ms, hungry_count, silence_printed

    wifi_connect()
    servo_write(SERVO_CENTER)

    # init baseline and DB fields
    a_base = float(read_adc_10bit())
    ensure_auth()
    db_put(PATH_AUDIO, "\"0\"")
    db_put(PATH_AUDIO_LABEL, "\"SILENCE\"")

    oled.fill(0); oled.text("Booting...", 0, 24); oled.show()

    last_fb_poll = 0
    last_adc_dbg = 0

    while True:
        now = time.ticks_ms()

        # --- ADC debug smoothing (same as before; independent from classifier)
        raw10 = read_adc_10bit()
        a_base += alpha_base * (raw10 - a_base)
        dev = abs(raw10 - a_base)
        a_env  += alpha_env  * (dev - a_env)
        if raw10 < a_min_win: a_min_win = raw10
        if raw10 > a_max_win: a_max_win = raw10

        if time.ticks_diff(now, last_adc_dbg) >= ADC_PRINT_MS:
            last_adc_dbg = now
            hh = (now//3600000)%24; mm = (now//60000)%60; ss = (now//1000)%60
            print("%02d:%02d:%02d -> A0:%4d  base:%7.1f  env:%6.1f  min:%4d  max:%4d" %
                  (hh, mm, ss, raw10, a_base, a_env, a_min_win, a_max_win))
            a_min_win, a_max_win = 1023, 0

        # --- SOUND CLASSIFICATION every CLASSIFY_INTERVAL_MS (~200 ms)
        if time.ticks_diff(now, last_classify_ms) >= CLASSIFY_INTERVAL_MS:
            last_classify_ms = now
            label = classify_frame()           # "SILENCE" | "DISCOMFORT" | "HUNGRY" | "PAIN"

            # small hysteresis: require several consecutive HUNGRY frames
            if label == "HUNGRY":
                hungry_count += 1
                if hungry_count < HUNGRY_LIMIT:
                    # keep label as DISCOMFORT until we’re sure
                    label = "DISCOMFORT"
                else:
                    hungry_count = 0
            else:
                hungry_count = 0

            audio_label = label
            audio_active = (label != "SILENCE")

            # start/stop motion based on activity (same behavior as your threshold version)
            if audio_active and not pattern_active:
                start_pattern()

        # --- Housekeeping
        update_pattern(now)
        maybe_read_dht(now)
        draw_oled()
        print_status(now, raw10)

        # --- Firebase I/O
        if time.ticks_diff(now, last_fb_poll) >= FB_POLL_MS:
            last_fb_poll = now

            # push Audio active flag if changed
            if audio_active != last_pushed_active:
                last_pushed_active = audio_active
                db_put(PATH_AUDIO, "\"1\"" if audio_active else "\"0\"")

            # push Audio label if changed
            if audio_label != last_pushed_label:
                last_pushed_label = audio_label
                db_put(PATH_AUDIO_LABEL, "\"{}\"".format(audio_label))

            # pull Emotion+Servo together
            code, body = db_get(PATH_ROOT)
            if code == 200 and body:
                try:
                    doc = json.loads(body)
                    em = doc.get("Emotion", None)
                    if isinstance(em, str) and em != last_emotion:
                        last_emotion = em
                        print('Emotion update -> "{}"'.format(last_emotion))
                        draw_oled()
                    svr = doc.get("Servo", None)
                    if isinstance(svr, str) and svr == "1":
                        start_pattern()
                        db_put(PATH_SERVO, "\"0\"")  # acknowledge
                except Exception:
                    pass
            elif code in (401, 403):
                # only matters when AUTH_REQUIRED=True
                id_token = ""

        time.sleep_ms(5)

# Run
if _name_ == "_main_":
    try:
        main()
    except KeyboardInterrupt:
        servo_write(SERVO_CENTER); led_off(); print("Stopped.")
