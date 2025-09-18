# main.py — Pico W Baby Cradle (MicroPython)
# Servo starts ONLY when Firebase /Baby_Cradlle/servo == "1". Mic is used only for logging/labels.
# Pins: MIC(AO)->ADC0 GP26, SERVO->GP15, DHT11->GP13, OLED I2C0 SDA=GP4 SCL=GP5, LED builtin.

import network, time, ujson as json
import urequests as requests
from machine import Pin, ADC, PWM, I2C
import ssd1306, dht, math, gc

try:
    import usocket as socket
except ImportError:
    import socket

# ---------- Wi-Fi ----------
WIFI_SSID = "baby1"
WIFI_PASS = "123456789"

# ---------- Firebase (HOST/API) ----------
FB_API_KEY   = "AIzaSyAhLCi6JBT5ELkAFxTplKBBDdRdpATzQxI"
FB_DB_HOST   = "smart-medicine-vending-machine-default-rtdb.asia-southeast1.firebasedatabase.app"
FB_AUTH_HOST = "identitytoolkit.googleapis.com"
FB_EMAIL     = "spherenexgpt@gmail.com"
FB_PASSWORD  = "Spherenex@123"

# Open-rules mode by default (set True if Identity sign-in works on your network)
AUTH_REQUIRED = False

# ---------- RTDB paths ----------
PATH_AUDIO        = "/Baby_Cradlle/Audio.json"         # "1"/"0"
PATH_AUDIO_LABEL  = "/Baby_Cradlle/AudioLabel.json"    # "SILENCE"/"DISCOMFORT"/"HUNGRY"/"PAIN"
PATH_SERVO        = "/Baby_Cradlle/servo.json"         # <-- lowercase 'servo'
PATH_EMOTION      = "/Baby_Cradlle/Emotion.json"
PATH_ROOT         = "/Baby_Cradlle.json"               # read Emotion + servo together

# ---------- Pins ----------
PIN_ADC_MIC = 26
PIN_SERVO   = 15
PIN_DHT     = 13
PIN_LED     = "LED"
PIN_I2C_SDA = 4
PIN_I2C_SCL = 5

# ---------- Timing ----------
HOP_INTERVAL_MS = 500
RUN_WINDOW_MS   = 10000
FB_POLL_MS      = 200
DHT_INTERVAL_MS = 2000
STATUS_PRINT_MS = 500
ADC_PRINT_MS    = 250

# ---------- Globals ----------
id_token = ""
token_expiry_ms = 0

# ---------- ADC / Servo ----------
adc = ADC(Pin(PIN_ADC_MIC))
def read_adc_10bit(): return adc.read_u16() >> 6

servo_pwm = PWM(Pin(PIN_SERVO)); servo_pwm.freq(50)
SERVO_MIN_US, SERVO_MAX_US = 500, 2500
def angle_to_duty_u16(angle):
    angle = 0 if angle < 0 else 180 if angle > 180 else angle
    pulse = SERVO_MIN_US + (SERVO_MAX_US - SERVO_MIN_US) * (angle / 180.0)
    return int((pulse / 20000.0) * 65535.0)
def servo_write(angle): servo_pwm.duty_u16(angle_to_duty_u16(angle))
SERVO_CENTER, SERVO_LOW, SERVO_HIGH = 90, 65, 115

# ---------- DHT / OLED / LED ----------
d = dht.DHT11(Pin(PIN_DHT)); last_temp_c = float("nan"); last_hum = float("nan"); last_dht_ms = 0
i2c = I2C(0, scl=Pin(PIN_I2C_SCL), sda=Pin(PIN_I2C_SDA), freq=400000)
oled = ssd1306.SSD1306_I2C(128, 64, i2c)
def center_text_y(y, text): oled.text(text, max(0, (128 - len(text)*8)//2), y)
led = Pin(PIN_LED, Pin.OUT)
def led_on(): led.value(1)
def led_off(): led.value(0)

# ---------- App state ----------
pattern_active = False
pattern_start_ms = 0
last_hop_ms = 0
current_target = SERVO_CENTER

audio_active = False
audio_label  = "SILENCE"
last_pushed_active = None
last_pushed_label  = None
last_emotion = ""

alpha_base, alpha_env = 0.001, 0.05
a_base, a_env, a_min_win, a_max_win = 0.0, 0.0, 1023, 0
last_adc_print_ms = 0
last_status_ms = 0

# ---------- SOUND (labels only; no servo control) ----------
ADC_SAMPLE_RATE = 8000
SAMPLES = 160
TARGET_FREQ_HUNGRY, TARGET_FREQ_PAIN = 600, 2000
HUNGRY_THRESHOLD, PAIN_THRESHOLD = 0.1, 0.1
SILENCE_RMS_THRESHOLD = 0.3
CLASSIFY_INTERVAL_MS = 200
last_classify_ms = 0
hungry_count, HUNGRY_LIMIT = 0, 5

def goertzel(samples, f, fs):
    n = len(samples); k = int(0.5 + (n*f)/fs)
    omega = (2.0*math.pi*k)/n; coeff = 2.0*math.cos(omega)
    s1=s2=0.0
    for x in samples:
        s = x + coeff*s1 - s2
        s2, s1 = s1, s
    return (s2*2 + s1*2 - coeff*s1*s2)/n

def sample_audio():
    buf=[]
    us = int(1_000_000/ADC_SAMPLE_RATE)
    for _ in range(SAMPLES):
        buf.append((adc.read_u16()-32768)/32768.0)
        time.sleep_us(us)
    return buf

def compute_rms(samples):
    s=0.0
    for x in samples: s += x*x
    return math.sqrt(s/len(samples))

def classify_frame():
    s = sample_audio()
    rms = compute_rms(s)
    if rms < SILENCE_RMS_THRESHOLD: return "SILENCE"
    eh = goertzel(s, TARGET_FREQ_HUNGRY, ADC_SAMPLE_RATE)
    ep = goertzel(s, TARGET_FREQ_PAIN,   ADC_SAMPLE_RATE)
    if ep > PAIN_THRESHOLD and ep > eh*1.5: return "PAIN"
    if eh > HUNGRY_THRESHOLD: return "HUNGRY"
    return "DISCOMFORT"

# ---------- Wi-Fi + DNS ----------
def _force_public_dns(wlan, prefer_public=False):
    """
    Keep DHCP-provided DNS by default. Only switch to a public DNS if explicitly
    requested (prefer_public=True) or as a last-resort within _ensure_dns_resolves().
    """
    try:
        ip, nm, gw, dns = wlan.ifconfig()
        if not prefer_public:
            print("DNS keep:", dns)
            return
        for cand in ("1.1.1.1", "8.8.8.8"):
            try:
                wlan.ifconfig((ip, nm, gw, cand))
                print("DNS ->", cand)
                return
            except Exception as e:
                print("DNS set error:", e)
        # If we couldn't change, keep the original
        wlan.ifconfig((ip, nm, gw, dns))
    except Exception as e:
        print("DNS check error:", e)

def _ensure_dns_resolves(host):
    # Try current DNS first
    try:
        socket.getaddrinfo(host, 443); return True
    except Exception:
        pass

    # Temporarily try public DNS candidates
    try:
        wlan = network.WLAN(network.STA_IF)
        ip, nm, gw, dns_orig = wlan.ifconfig()
    except Exception as e:
        print("DNS: no WLAN ifconfig:", e)
        return False

    for cand in ("1.1.1.1", "8.8.8.8"):
        try:
            wlan.ifconfig((ip, nm, gw, cand))
            time.sleep_ms(200)
            socket.getaddrinfo(host, 443)
            print("DNS via", cand)
            return True
        except Exception as e2:
            print("DNS via {} failed: {}".format(cand, e2))

    # Restore original DNS
    try:
        wlan.ifconfig((ip, nm, gw, dns_orig))
    except:
        pass
    return False

def wifi_connect():
    wlan = network.WLAN(network.STA_IF); wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(WIFI_SSID, WIFI_PASS)
        print("WiFi: connecting", end=""); t0 = time.ticks_ms()
        while not wlan.isconnected():
            print(".", end=""); time.sleep_ms(250)
            if time.ticks_diff(time.ticks_ms(), t0) > 20000: raise RuntimeError("WiFi connect timeout")
        print()
    print("WiFi OK:", wlan.ifconfig()); _force_public_dns(wlan, prefer_public=False); return wlan

# ---------- Auth (kept; skipped unless AUTH_REQUIRED=True) ----------
def firebase_sign_in(max_retries=5):
    global id_token, token_expiry_ms
    if not AUTH_REQUIRED: return False
    endpoints = [
        "https://{}/v1/accounts:signInWithPassword?key={}".format(FB_AUTH_HOST, FB_API_KEY),
        "https://www.googleapis.com/identitytoolkit/v3/relyingparty/verifyPassword?key={}".format(FB_API_KEY),
    ]
    payload = {"email": FB_EMAIL, "password": FB_PASSWORD, "returnSecureToken": True}
    body = json.dumps(payload); headers = {"Content-Type":"application/json","Connection":"close"}
    for ep in endpoints:
        for attempt in range(1, max_retries+1):
            try:
                gc.collect(); r = requests.post(ep, data=body, headers=headers)
                if r.status_code == 200:
                    data = r.json(); r.close()
                    id_token = data.get("idToken",""); expires = int(data.get("expiresIn","3600"))
                    token_expiry_ms = time.ticks_add(time.ticks_ms(), (expires-60)*1000)
                    print("Firebase sign-in OK (try {})".format(attempt)); return True
                else:
                    txt=r.text; r.close(); print("Auth HTTP {}: {}".format(r.status_code, txt))
            except Exception as e:
                print("Auth error (try {}): {}".format(attempt, e))
            time.sleep_ms(350*attempt)
    return False

def ensure_auth():
    if not AUTH_REQUIRED: return False
    if (not id_token) or time.ticks_diff(time.ticks_ms(), token_expiry_ms) > 0: return firebase_sign_in()
    return True

# ---------- REST helpers ----------
def _db_url(path, use_auth=True):
    return "https://{}{}{}".format(
        FB_DB_HOST, path, ("?auth="+id_token) if (use_auth and id_token) else ""
    )

def db_put(path, body_str):
    headers = {"Content-Type": "application/json", "Connection": "close"}
    if not _ensure_dns_resolves(FB_DB_HOST): print("PUT skipped (DNS)"); return False
    if AUTH_REQUIRED and id_token:
        try:
            url=_db_url(path,True); r=requests.put(url,data=body_str,headers=headers); code=r.status_code; r.close()
            if code==200: return True
            if code in (401,403) and ensure_auth():
                url=_db_url(path,True); r=requests.put(url,data=body_str,headers=headers); ok=(r.status_code==200); r.close()
                if ok: return True
        except Exception as e: print("PUT (auth) err:", e)
    try:
        url=_db_url(path,False); r=requests.put(url,data=body_str,headers=headers); ok=(r.status_code==200); r.close(); return ok
    except Exception as e: print("PUT (open) err:", e); return False

def db_get(path):
    headers = {"Connection": "close"}
    if not _ensure_dns_resolves(FB_DB_HOST): print("GET skipped (DNS)"); return -1, None
    if AUTH_REQUIRED and id_token:
        try:
            url=_db_url(path,True); r=requests.get(url,headers=headers); code=r.status_code; txt=r.text; r.close()
            if code==200: return code, txt
            if code in (401,403) and ensure_auth():
                url=_db_url(path,True); r=requests.get(url,headers=headers); code=r.status_code; txt=r.text; r.close()
                if code==200: return code, txt
        except Exception as e:
            print("GET (auth) err:", e)
    try:
        url=_db_url(path,False); r=requests.get(url,headers=headers); code=r.status_code; txt=r.text; r.close(); return code, txt
    except Exception as e: print("GET (open) err:", e); return -1, None

# ---------- OLED ----------
def draw_oled():
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
          .format(secs, raw10, 1 if audio_active else 0,
                  "MOVING" if pattern_active else "STATIC",
                  angle, emo, t_str, h_str))

# ---------- Main ----------
def main():
    global audio_active, audio_label, last_pushed_active, last_pushed_label, last_emotion
    global a_base, a_env, a_min_win, a_max_win, last_classify_ms, hungry_count

    wifi_connect()
    servo_write(SERVO_CENTER)

    # init baseline and DB fields
    a_base = float(read_adc_10bit())
    ensure_auth()
    db_put(PATH_AUDIO, "\"0\"")
    db_put(PATH_AUDIO_LABEL, "\"SILENCE\"")
    db_put(PATH_SERVO, "\"0\"")  # make sure it starts in idle

    oled.fill(0); oled.text("Booting...", 0, 24); oled.show()

    last_fb_poll = 0
    last_adc_dbg = 0

    while True:
        now = time.ticks_ms()

        # ADC smoothing (debug only)
        raw10 = read_adc_10bit()
        a_base += alpha_base * (raw10 - a_base)
        dev = abs(raw10 - a_base)
        a_env += alpha_env * (dev - a_env)
        if raw10 < a_min_win: a_min_win = raw10
        if raw10 > a_max_win: a_max_win = raw10

        if time.ticks_diff(now, last_adc_dbg) >= ADC_PRINT_MS:
            last_adc_dbg = now
            hh = (now//3600000)%24; mm = (now//60000)%60; ss = (now//1000)%60
            print("%02d:%02d:%02d -> A0:%4d  base:%7.1f  env:%6.1f  min:%4d  max:%4d" %
                  (hh, mm, ss, raw10, a_base, a_env, a_min_win, a_max_win))
            a_min_win, a_max_win = 1023, 0

        # SOUND classification (labels only; NO servo trigger here)
        if time.ticks_diff(now, last_classify_ms) >= CLASSIFY_INTERVAL_MS:
            last_classify_ms = now
            label = classify_frame()
            if label == "HUNGRY":
                hungry_count = min(hungry_count + 1, HUNGRY_LIMIT)
                if hungry_count < HUNGRY_LIMIT:
                    label = "DISCOMFORT"
                else:
                    hungry_count = 0
            else:
                hungry_count = 0
            audio_label = label
            audio_active = (label != "SILENCE")

        # Housekeeping
        update_pattern(now)
        maybe_read_dht(now)
        draw_oled()
        print_status(now, raw10)

        # Firebase I/O
        if time.ticks_diff(now, last_fb_poll) >= FB_POLL_MS:
            last_fb_poll = now

            # push Audio flag/label on change
            if audio_active != last_pushed_active:
                last_pushed_active = audio_active
                db_put(PATH_AUDIO, "\"1\"" if audio_active else "\"0\"")
            if audio_label != last_pushed_label:
                last_pushed_label = audio_label
                db_put(PATH_AUDIO_LABEL, "\"{}\"".format(audio_label))

            # pull Emotion + servo (lowercase key)
            code, body = db_get(PATH_ROOT)
            if code == 200 and body:
                try:
                    doc = json.loads(body)
                    em = doc.get("Emotion", None)
                    if isinstance(em, str) and em != last_emotion:
                        last_emotion = em
                        print('Emotion update -> "{}"'.format(last_emotion))
                        draw_oled()
                    svr = doc.get("servo", None)            # <-- lowercase
                    if isinstance(svr, str) and svr == "1":
                        start_pattern()
                        db_put(PATH_SERVO, "\"0\"")         # ack back to same lowercase path
                except Exception:
                    pass
            elif code in (401, 403):
                # only matters if AUTH_REQUIRED=True
                id_token = ""

        time.sleep_ms(5)

# Run
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        servo_write(SERVO_CENTER); led_off(); print("Stopped.")

