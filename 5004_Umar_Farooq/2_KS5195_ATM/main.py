# ============================================================================
#  Wiring Table — Raspberry Pi Pico W + 3x4 Keypad + Touch + Servo + Buzzer
# ----------------------------------------------------------------------------
# | Module     | Signal | Connect To (Module) | Pico W GPIO | Mode                | Notes                         |
# |------------|--------|---------------------|-------------|---------------------|-------------------------------|
# | Keypad 3x4 | R1     | Row 1               | GP6         | INPUT_PULLUP        | Top row                       |
# |            | R2     | Row 2               | GP7         | INPUT_PULLUP        |                               |
# |            | R3     | Row 3               | GP8         | INPUT_PULLUP        |                               |
# |            | R4     | Row 4               | GP9         | INPUT_PULLUP        |                               |
# |            | C1     | Column 1            | GP10        | OUTPUT (idle HIGH)  | Scanned LOW one at a time     |
# |            | C2     | Column 2            | GP11        | OUTPUT (idle HIGH)  |                               |
# |            | C3     | Column 3            | GP12        | OUTPUT (idle HIGH)  |                               |
# | Touch/TTP223| OUT   | Touch output        | GP14        | INPUT               | ACTIVE-HIGH by default        |
# |            | VCC    | +3.3V               | 3V3         | —                   |                               |
# |            | GND    | Ground              | GND         | —                   |                               |
# | Servo (SG90/MG995) | SIG | PWM signal     | GP15        | PWM @ 50 Hz         | 0–180°; external 5V power     |
# |            | V+     | +5V (servo supply)  | —           | —                   | DO NOT power from Pico 3V3    |
# |            | GND    | Ground              | GND         | —                   | Common ground with Pico       |
# | Buzzer (active) | SIG| Buzzer control     | GP16        | OUTPUT              | Active-HIGH → ON              |
# |            | V+     | +3.3V or +5V        | —           | —                   | per buzzer module             |
# |            | GND    | Ground              | GND         | —                   |                               |
# ----------------------------------------------------------------------------
# Touch logic level:
#   - If your TTP223 board is ACTIVE-HIGH (default), set TOUCH_ACTIVE_HIGH = True (and we use Pin.PULL_DOWN).
#   - If ACTIVE-LOW, set TOUCH_ACTIVE_HIGH = False (and we use Pin.PULL_UP).
# ============================================================================

# ---------- Wi-Fi + Firebase + Keypad + Touch + Actions (Pico W / MicroPython) ----------
import network, time, ujson
try:
    import urequests as requests
except ImportError:
    import requests

from machine import Pin, PWM

# ===== USER CONFIG =====
WIFI_SSID = "atm"
WIFI_PASS = "123456789"

API_KEY   = "AIzaSyB9ererNsNonAzH0zQo_GS79XPOyCoMxr4"
DB_URL    = "https://waterdtection-default-rtdb.firebaseio.com"
USER_EMAIL    = "spherenexgpt@gmail.com"
USER_PASSWORD = "Spherenex@123"

# RTDB paths
PIN_PATH     = "/6_ATM/1_Authentication/3_PIN"           # "1234" (string)
FP_PATH      = "/6_ATM/1_Authentication/2_Fingerprint"   # pulse "1" then back to "0"
SERVO_PATH   = "/6_ATM/2_Action/1_Servo"                 # "1"->180°, "0"->0°
BUZZER_PATH  = "/6_ATM/2_Action/2_Buzzer"                # "1"->ON, "0"->OFF

# Poll period for action paths (ms)
ACTION_POLL_MS = 500

# ===== Keypad (3x4) =====
ROWS, COLS = 4, 3
KEYS = [['1','2','3'],
        ['4','5','6'],
        ['7','8','9'],
        ['*','0','#']]
ROW_PINS = [6, 7, 8, 9]     # GP6..GP9
COL_PINS = [10, 11, 12]     # GP10..GP12
rows = [Pin(p, Pin.IN, Pin.PULL_UP) for p in ROW_PINS]
cols = [Pin(p, Pin.OUT, value=1) for p in COL_PINS]   # idle HIGH
DEBOUNCE_MS = 25

# ===== Touch sensor =====
TOUCH_PIN = 14                 # GP14 → TTP223 OUT
TOUCH_ACTIVE_HIGH = True       # set False if your board is active-LOW
HOLD_MS = 3000                 # must hold touch for 3 seconds to trigger
PULSE_MS = 1000                # keep "1" in Firebase this long, then write "0"
touch = Pin(TOUCH_PIN, Pin.IN, Pin.PULL_DOWN if TOUCH_ACTIVE_HIGH else Pin.PULL_UP)

def touch_active():
    v = touch.value()
    return (v == 1) if TOUCH_ACTIVE_HIGH else (v == 0)

# ===== Servo (PWM @ 50 Hz) =====
SERVO_PIN = 15
SERVO_MIN_US = 500    # adjust if needed
SERVO_MAX_US = 2500   # adjust if needed
servo_pwm = PWM(Pin(SERVO_PIN))
servo_pwm.freq(50)    # 50 Hz for RC servos

def _us_to_duty(us):
    # 20 ms period at 50 Hz
    return int(us * 65535 // 20000)

def servo_angle(deg):
    if deg < 0: deg = 0
    if deg > 180: deg = 180
    pulse = SERVO_MIN_US + (SERVO_MAX_US - SERVO_MIN_US) * deg // 180
    servo_pwm.duty_u16(_us_to_duty(pulse))

# ===== Buzzer (ACTIVE-HIGH digital) =====
BUZZER_PIN = 16
BUZZER_ACTIVE_HIGH = True
buzzer = Pin(BUZZER_PIN, Pin.OUT, value=0 if BUZZER_ACTIVE_HIGH else 1)

def buzzer_on():
    buzzer.value(1 if BUZZER_ACTIVE_HIGH else 0)

def buzzer_off():
    buzzer.value(0 if BUZZER_ACTIVE_HIGH else 1)

# ===== Wi-Fi + Firebase =====
def wifi_connect():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(WIFI_SSID, WIFI_PASS)
        t0 = time.ticks_ms()
        while not wlan.isconnected():
            if time.ticks_diff(time.ticks_ms(), t0) > 15000:
                raise RuntimeError("Wi-Fi connect timeout")
            time.sleep_ms(250)
    print("Wi-Fi OK:", wlan.ifconfig()[0])

def firebase_login():
    url = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=" + API_KEY
    payload = {"email": USER_EMAIL, "password": USER_PASSWORD, "returnSecureToken": True}
    r = requests.post(url, data=ujson.dumps(payload), headers={"Content-Type":"application/json"})
    if r.status_code != 200:
        print("Auth error:", r.status_code, r.text); r.close(); return None
    data = r.json(); r.close(); return data.get("idToken")

def rtdb_put_string(path, value, id_token=None):
    if not path.startswith("/"): path = "/" + path
    url = DB_URL + path + ".json" + (("?auth="+id_token) if id_token else "")
    r = requests.put(url, data=ujson.dumps(value), headers={"Content-Type":"application/json"})
    ok = (200 <= r.status_code < 300)
    if not ok: print("RTDB write failed:", r.status_code, r.text)
    r.close(); return ok

def rtdb_get(path, id_token=None):
    if not path.startswith("/"): path = "/" + path
    url = DB_URL + path + ".json" + (("?auth="+id_token) if id_token else "")
    r = requests.get(url)
    if not (200 <= r.status_code < 300):
        print("RTDB get failed:", r.status_code, r.text)
        r.close(); return None
    val = r.json()
    r.close()
    return val

# ===== Keypad scan: one char per physical press =====
def get_key_once():
    for ci, _ in enumerate(cols):
        for j, cp in enumerate(cols): cp.value(0 if j == ci else 1)
        time.sleep_us(200)
        for ri, rpin in enumerate(rows):
            if rpin.value() == 0:
                t0 = time.ticks_ms()
                while rpin.value() == 0 and time.ticks_diff(time.ticks_ms(), t0) < DEBOUNCE_MS:
                    time.sleep_ms(1)
                while rpin.value() == 0: time.sleep_ms(1)
                time.sleep_ms(DEBOUNCE_MS)
                for cp in cols: cp.value(1)
                return KEYS[ri][ci]
    for cp in cols: cp.value(1)
    return None

# ===== Main =====
def main():
    print("Connecting Wi-Fi..."); wifi_connect()
    print("Signing in to Firebase...")
    id_token = firebase_login()
    if not id_token:
        print("Continuing without auth (works only if DB rules are public).")

    # init outputs
    rtdb_put_string(FP_PATH, "0", id_token=id_token)  # fingerprint default 0
    servo_angle(0)                                    # default servo at 0°
    buzzer_off()                                      # default buzzer OFF

    buf = ""
    print("Ready. Keypad: enter 4-digit PIN. '*'=backspace, '#'=clear.")
    touch_down = False
    touch_t0 = 0
    fired_this_hold = False

    last_poll = time.ticks_ms()
    last_servo_cmd = None
    last_buzzer_cmd = None

    while True:
        # --- Keypad handling ---
        k = get_key_once()
        if k:
            if k.isdigit():
                if len(buf) < 4:
                    buf += k
                    print(buf)
                if len(buf) == 4:
                    print("PIN entered:", buf)
                    rtdb_put_string(PIN_PATH, buf, id_token=id_token)   # store as string
                    print("Firebase updated:", PIN_PATH)
                    buf = ""; print("Ready.")
            elif k == '*':  # backspace
                if buf: buf = buf[:-1]; print(buf if buf else "")
            elif k == '#':  # clear
                buf = ""; print("Cleared.")

        # --- Touch sensor (3s hold -> pulse "1" then back to "0" after PULSE_MS) ---
        if touch_active():
            if not touch_down:
                touch_down = True
                touch_t0 = time.ticks_ms()
                fired_this_hold = False
            else:
                if (not fired_this_hold) and time.ticks_diff(time.ticks_ms(), touch_t0) >= HOLD_MS:
                    if rtdb_put_string(FP_PATH, "1", id_token=id_token):
                        if PULSE_MS > 0:
                            time.sleep_ms(PULSE_MS)
                        rtdb_put_string(FP_PATH, "0", id_token=id_token)
                        print('Fingerprint pulse: 1→0')
                    else:
                        print("Fingerprint write FAILED")
                    fired_this_hold = True
        else:
            touch_down = False
            fired_this_hold = False

        # --- Poll Firebase actions (servo + buzzer) ---
        if time.ticks_diff(time.ticks_ms(), last_poll) >= ACTION_POLL_MS:
            last_poll = time.ticks_ms()

            # Servo command
            sv = rtdb_get(SERVO_PATH, id_token=id_token)
            if sv is not None:
                sv_str = str(sv).strip().strip('"')
                if sv_str in ("0","1"):
                    if sv_str != last_servo_cmd:
                        if sv_str == "1":
                            servo_angle(180)
                            print("Servo → 180° (from DB)")
                        else:
                            servo_angle(0)
                            print("Servo → 0° (from DB)")
                        last_servo_cmd = sv_str

            # Buzzer command
            bz = rtdb_get(BUZZER_PATH, id_token=id_token)
            if bz is not None:
                bz_str = str(bz).strip().strip('"')
                if bz_str in ("0","1"):
                    if bz_str != last_buzzer_cmd:
                        if bz_str == "1":
                            buzzer_on()
                            print("Buzzer → ON (from DB)")
                        else:
                            buzzer_off()
                            print("Buzzer → OFF (from DB)")
                        last_buzzer_cmd = bz_str

        time.sleep_ms(5)

# Auto-run
if __name__ == "__main__":
    main()

