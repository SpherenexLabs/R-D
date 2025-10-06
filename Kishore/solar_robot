# main.py — Pico W + L298N + 2-Relay + 4x Ultrasonic + Firebase RTDB
# Safety override:
#   - If any ultrasonic <= OBSTACLE_ON_CM:
#       Ultrasonic=1, Direction="S", brake()
#   - When all clear:
#       Ultrasonic=0, restore last non-stop Direction (F/B/L/R) and move
#
# DB paths:
#   /Solar_pannel_cleaning/Direction   -> "F","B","L","R","S"
#   /Solar_pannel_cleaning/Detect      -> truthy -> active (1/"1"/"HIGH"/"true")
#   /Solar_pannel_cleaning/Percentage  -> 0..100 (relay split thresholds)
#   /Solar_pannel_cleaning/Ultrasonic  -> 0|1 (written by this script)

import time, ujson as json
from machine import Pin, PWM, time_pulse_us
import network
import urequests as requests

# ========= USER CONFIG =========
WIFI_SSID      = "solar"
WIFI_PASSWORD  = "123456789"

# Firebase project
FIREBASE_API_KEY = "AIzaSyBi4imuMT5imCT-8IBULdyFqj-ZZtl68Do"
FIREBASE_DB_URL  = "https://regal-welder-453313-d6-default-rtdb.firebaseio.com"

# Email/Password user (must exist in Firebase Auth → Users)
AUTH_EMAIL       = "spherenexgpt@gmail.com"
AUTH_PASSWORD    = "Spherenex@123"
AUTH_ENABLED     = True   # set False if DB rules are public

# ========= DB PATHS =========
BASE_PATH        = "/Solar_pannel_cleaning"
PATH_DIRECTION   = BASE_PATH + "/Direction"
PATH_DETECT      = BASE_PATH + "/Detect"
PATH_PERCENTAGE  = BASE_PATH + "/Percentage"
PATH_USONIC_FLAG = BASE_PATH + "/Ultrasonic"   # scalar: 0|1

# Timings
POLL_MS                = 500
HTTP_TIMEOUT           = 10
US_CHECK_INTERVAL_MS   = 250     # how often to sample ultrasonics
US_TIMEOUT_US          = 30000   # echo timeout (≈ 5 m max)

# Obstacle threshold (trigger at or below this distance)
OBSTACLE_ON_CM = 20

# Defaults if missing
DEF_DIRECTION  = "S"
DEF_DETECT     = "NONE"
DEF_PERCENTAGE = 0
DEF_US_FLAG    = 0

# ========= L298N wiring (Pico W pins) =========
ENA_PIN = 6   # ENA -> GP6 (remove ENA jumper on L298N)
ENB_PIN = 7   # ENB -> GP7 (remove ENB jumper on L298N)
IN1_PIN = 2   # IN1 -> GP2
IN2_PIN = 3   # IN2 -> GP3
IN3_PIN = 4   # IN3 -> GP4
IN4_PIN = 5   # IN4 -> GP5

PWM_FREQ       = 1000
DEFAULT_SPEED  = 70
MOTOR_A_INVERT = False
MOTOR_B_INVERT = False

# ========= 2-Channel Relay wiring =========
RELAY1_PIN       = 8   # Relay-1 IN -> GP8
RELAY2_PIN       = 9   # Relay-2 IN -> GP9
RELAY_ACTIVE_LOW = True  # True for low-level trigger boards (LOW=ON)

# ========= Ultrasonic sensors =========
#   US1: TRIG GP11, ECHO GP12
#   US2: TRIG GP13, ECHO GP14
#   US3: TRIG GP15, ECHO GP16
#   US4: TRIG GP17, ECHO GP18
US_CFG = [
    {"name":"US1", "trig":11, "echo":12},
    {"name":"US2", "trig":13, "echo":14},
    {"name":"US3", "trig":15, "echo":16},
    {"name":"US4", "trig":17, "echo":18},
]

# ========= Setup motors =========
ena = PWM(Pin(ENA_PIN)); ena.freq(PWM_FREQ)
enb = PWM(Pin(ENB_PIN)); enb.freq(PWM_FREQ)
in1 = Pin(IN1_PIN, Pin.OUT, value=0)
in2 = Pin(IN2_PIN, Pin.OUT, value=0)
in3 = Pin(IN3_PIN, Pin.OUT, value=0)
in4 = Pin(IN4_PIN, Pin.OUT, value=0)

# ========= Setup relays =========
def _relay_off_value():
    return 1 if RELAY_ACTIVE_LOW else 0
def _relay_on_value():
    return 0 if RELAY_ACTIVE_LOW else 1

relay1 = Pin(RELAY1_PIN, Pin.OUT, value=_relay_off_value())
relay2 = Pin(RELAY2_PIN, Pin.OUT, value=_relay_off_value())

def relay_set(ch, on: bool):
    v = _relay_on_value() if on else _relay_off_value()
    if ch == 1:
        relay1.value(v)
    else:
        relay2.value(v)

def relay_all_off():
    relay_set(1, False)
    relay_set(2, False)

# ========= Setup ultrasonics =========
class USonic:
    def __init__(self, trig_pin, echo_pin):
        self.trig = Pin(trig_pin, Pin.OUT, value=0)
        self.echo = Pin(echo_pin, Pin.IN)  # level-shift ECHO if sensor is 5V type
    def read_cm(self):
        # trigger 10us pulse
        self.trig.value(0); time.sleep_us(2)
        self.trig.value(1); time.sleep_us(10)
        self.trig.value(0)
        # measure high pulse
        try:
            dur = time_pulse_us(self.echo, 1, US_TIMEOUT_US)
            if dur < 0:
                return None
        except Exception:
            return None
        # distance(cm) = (duration_us * 0.0343) / 2
        return dur * 0.01715

usonics = [USonic(c["trig"], c["echo"]) for c in US_CFG]

# ========= Motor helpers =========
def _duty_from_percent(p):
    p = max(0, min(100, int(p))); return int(65535 * p // 100)

def set_speed(percent):
    d = _duty_from_percent(percent)
    ena.duty_u16(d); enb.duty_u16(d)

def _motor_a(forward: bool):
    fwd = (forward ^ MOTOR_A_INVERT)
    in1.value(1 if fwd else 0); in2.value(0 if fwd else 1)

def _motor_b(forward: bool):
    fwd = (forward ^ MOTOR_B_INVERT)
    in3.value(1 if fwd else 0); in4.value(0 if fwd else 1)

def brake():
    in1.value(1); in2.value(1); in3.value(1); in4.value(1)
    ena.duty_u16(0); enb.duty_u16(0)

def coast():
    in1.value(0); in2.value(0); in3.value(0); in4.value(0)
    ena.duty_u16(0); enb.duty_u16(0)

def forward():
    set_speed(DEFAULT_SPEED); _motor_a(True);  _motor_b(True)

def backward():
    set_speed(DEFAULT_SPEED); _motor_a(False); _motor_b(False)

def left():
    set_speed(DEFAULT_SPEED); _motor_a(False); _motor_b(True)

def right():
    set_speed(DEFAULT_SPEED); _motor_a(True);  _motor_b(False)

# ========= Wi-Fi =========
def wifi_connect(timeout_s=20):
    wlan = network.WLAN(network.STA_IF)
    if not wlan.active(): wlan.active(True)
    if not wlan.isconnected():
        print("[WiFi] Connecting to", WIFI_SSID)
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        t0 = time.ticks_ms()
        while not wlan.isconnected() and time.ticks_diff(time.ticks_ms(), t0) < timeout_s*1000:
            time.sleep_ms(200)
    if wlan.isconnected():
        print("[WiFi] Connected. IP:", wlan.ifconfig()[0]); return True
    print("[WiFi] Failed."); return False

# ========= Firebase Auth =========
class FirebaseAuth:
    def __init__(self, api_key, email, password):
        self.api_key = api_key
        self.email = email
        self.password = password
        self.id_token = None
        self.refresh_token = None
        self.expiry_ms = 0

    def _now(self): return time.ticks_ms()
    def _expiring(self):
        return self.id_token is None or time.ticks_diff(self.expiry_ms, self._now()) < 60000

    def sign_in(self):
        url = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={}".format(self.api_key)
        payload = {"email": self.email, "password": self.password, "returnSecureToken": True}
        try:
            r = requests.post(url, data=json.dumps(payload),
                              headers={"Content-Type":"application/json"},
                              timeout=HTTP_TIMEOUT)
            sc, txt = r.status_code, r.text
            if sc != 200:
                print("[AUTH] Sign-in failed:", sc, "-", txt); r.close(); return False
            data = r.json(); r.close()
            self.id_token = data.get("idToken")
            self.refresh_token = data.get("refreshToken")
            expires_in = int(data.get("expiresIn", "3600"))
            self.expiry_ms = time.ticks_add(self._now(), (expires_in - 60) * 1000)
            print("[AUTH] Signed in. Token set; expires in ~{}s".format(expires_in))
            return True
        except Exception as e:
            print("[AUTH] Sign-in error:", e); return False

    def refresh(self):
        if not self.refresh_token:
            return self.sign_in()
        url = "https://securetoken.googleapis.com/v1/token?key={}".format(self.api_key)
        payload = "grant_type=refresh_token&refresh_token={}".format(self.refresh_token)
        try:
            r = requests.post(url, data=payload,
                              headers={"Content-Type":"application/x-www-form-urlencoded"},
                              timeout=HTTP_TIMEOUT)
            sc, txt = r.status_code, r.text
            if sc != 200:
                print("[AUTH] Refresh failed:", sc, "-", txt); r.close(); return self.sign_in()
            data = r.json(); r.close()
            self.id_token = data.get("id_token")
            self.refresh_token = data.get("refresh_token", self.refresh_token)
            expires_in = int(data.get("expires_in", "3600"))
            self.expiry_ms = time.ticks_add(self._now(), (expires_in - 60) * 1000)
            print("[AUTH] Token refreshed; expires in ~{}s".format(expires_in))
            return True
        except Exception as e:
            print("[AUTH] Refresh error:", e); return self.sign_in()

    def token(self):
        if not AUTH_ENABLED:
            return None
        if self._expiring():
            self.refresh()
        return self.id_token

auth = FirebaseAuth(FIREBASE_API_KEY, AUTH_EMAIL, AUTH_PASSWORD)

def _base_url(path, use_auth=True):
    url = "{}{}.json".format(FIREBASE_DB_URL.rstrip("/"), path)
    if AUTH_ENABLED and use_auth:
        tok = auth.token()
        if tok:
            sep = "&" if "?" in url else "?"
            url = "{}{}auth={}".format(url, sep, tok)
    return url

# ========= Firebase helpers =========
def fb_get(path, retry=True):
    try:
        r = requests.get(_base_url(path, use_auth=True), timeout=HTTP_TIMEOUT)
        sc, txt = r.status_code, r.text
        if sc in (401, 403):
            r.close()
            if AUTH_ENABLED and retry:
                print("[FB][GET] 401/403; refreshing token and retrying.")
                auth.refresh(); return fb_get(path, retry=False)
            print("[FB][GET] Auth denied:", sc, txt); return None
        if sc != 200:
            print("[FB][GET]", sc, "-", txt); r.close(); return None
        try: data = r.json()
        except: data = txt
        r.close(); return data
    except Exception as e:
        print("[FB][ERR GET]", e); return None

def fb_put(path, value, retry=True):
    try:
        r = requests.put(_base_url(path, use_auth=True),
                         data=json.dumps(value),
                         headers={"Content-Type":"application/json"},
                         timeout=HTTP_TIMEOUT)
        sc, txt = r.status_code, r.text
        if sc in (401, 403):
            r.close()
            if AUTH_ENABLED and retry:
                print("[FB][PUT] 401/403; refreshing token and retrying.")
                auth.refresh(); return fb_put(path, value, retry=False)
            print("[FB][PUT] Auth denied:", sc, txt); return False
        r.close()
        if sc != 200:
            print("[FB][PUT]", sc, "-", txt); return False
        return True
    except Exception as e:
        print("[FB][ERR PUT]", e); return False

def ensure_schema():
    parent = fb_get(BASE_PATH)
    if parent is None or not isinstance(parent, dict):
        fb_put(PATH_DIRECTION,  DEF_DIRECTION)
        fb_put(PATH_DETECT,     DEF_DETECT)
        fb_put(PATH_PERCENTAGE, DEF_PERCENTAGE)
        fb_put(PATH_USONIC_FLAG, DEF_US_FLAG)
        print("[FB] Initialized schema under", BASE_PATH); return

    # Coerce Ultrasonic to scalar if missing or wrong type
    if parent.get("Ultrasonic") is None or isinstance(parent.get("Ultrasonic"), dict):
        fb_put(PATH_USONIC_FLAG, DEF_US_FLAG)

    # Migrate any legacy nested structure for Direction
    if isinstance(parent.get("Direction"), dict):
        d = parent["Direction"]
        cmd = d.get("Cmd", DEF_DIRECTION)
        det = parent.get("Detect", d.get("Detect", DEF_DETECT))
        pct = parent.get("Percentage", d.get("Percentage", DEF_PERCENTAGE))
        fb_put(PATH_DIRECTION,  cmd)
        fb_put(PATH_DETECT,     det)
        fb_put(PATH_PERCENTAGE, pct)
        print("[FB] Migrated nested Direction -> siblings.")

# ========= Direction / Relay logic =========
def execute_direction(val):
    if not val: return
    c = str(val).strip().upper()
    if not c: return
    print("[ACT] Direction =", c)
    if   c == "F": forward()
    elif c == "B": backward()
    elif c == "L": left()
    elif c == "R": right()
    elif c == "S": brake()
    else: print("[ACT] Unknown; expected F/B/L/R/S")

def _detect_is_active(det_val) -> bool:
    if det_val is None: return False
    s = str(det_val).strip().lower()
    return s in ("1", "high", "true")

def _pct_as_int(pct_val) -> int:
    try:
        return max(0, min(100, int(float(pct_val))))
    except:
        return 0

def update_relays(det_val, pct_val):
    if not _detect_is_active(det_val):
        relay_all_off()
        print("[RELAY] Detect=0 -> both OFF (0)")
        return
    p = _pct_as_int(pct_val)
    if p <= 25:
        relay_all_off()
        print(f"[RELAY] Detect=1, {p}% -> none (0)")
    elif 26 <= p <= 50:
        relay_set(1, True); relay_set(2, False)
        print(f"[RELAY] Detect=1, {p}% -> CH1 ON (1)")
    else:
        relay_set(1, False); relay_set(2, True)
        print(f"[RELAY] Detect=1, {p}% -> CH2 ON (2)")

# ========= Ultrasonic safety =========
def any_obstacle_cm(threshold_cm=OBSTACLE_ON_CM):
    """Return True if ANY ultrasonic reading is <= threshold."""
    for us in usonics:
        cm = us.read_cm()
        if cm is not None and cm <= threshold_cm:
            return True
    return False

# ========= Main =========
def main():
    set_speed(DEFAULT_SPEED); coast()
    relay_all_off()

    # Wi-Fi
    if not wifi_connect():
        while not wifi_connect():
            time.sleep(2)

    # Auth
    if AUTH_ENABLED:
        if not auth.sign_in():
            print("[AUTH] Could not sign in. If rules are public, set AUTH_ENABLED=False.")
        else:
            print("[AUTH] Ready.")

    ensure_schema()
    print("[SYS] Polling", BASE_PATH, "every", POLL_MS, "ms")

    # Load initial state
    parent = fb_get(BASE_PATH)
    last_dir = DEF_DIRECTION
    last_det = DEF_DETECT
    last_pct = DEF_PERCENTAGE
    last_us_flag = DEF_US_FLAG

    # Track last non-stop direction to restore after obstacle clears
    last_non_stop_dir = "F"

    if isinstance(parent, dict):
        last_dir = str(parent.get("Direction", DEF_DIRECTION)).upper()
        last_det = parent.get("Detect",    DEF_DETECT)
        last_pct = parent.get("Percentage",DEF_PERCENTAGE)
        last_us_flag = int(parent.get("Ultrasonic", DEF_US_FLAG) or 0)
        if last_dir in ("F","B","L","R"):
            last_non_stop_dir = last_dir
        print(f"[FB] Initial -> Direction:{last_dir}  Detect:{last_det}  Percentage:{last_pct}  Ultrasonic:{last_us_flag}")

        # Apply initial states (direction may be overridden below by obstacle)
        execute_direction(last_dir)
        update_relays(last_det, last_pct)
        fb_put(PATH_USONIC_FLAG, last_us_flag)

    last_us_check = 0

    while True:
        if not network.WLAN(network.STA_IF).isconnected():
            wifi_connect()

        # Read current commands
        parent = fb_get(BASE_PATH)
        if isinstance(parent, dict) and not isinstance(parent.get("Direction"), dict):
            dir_now = str(parent.get("Direction", "")).upper()
            det_now = parent.get("Detect", "")
            pct_now = parent.get("Percentage", None)

            # Update our remembered non-stop direction whenever user asks F/B/L/R
            if dir_now in ("F","B","L","R"):
                last_non_stop_dir = dir_now

            # Apply user direction only if it changed AND no active obstacle override
            if dir_now != "" and dir_now != last_dir and last_us_flag == 0:
                execute_direction(dir_now)
                last_dir = dir_now

            # Relays always follow Detect/Percentage
            if det_now != last_det or pct_now != last_pct:
                update_relays(det_now, pct_now)
                last_det = det_now
                last_pct = pct_now

        # Ultrasonic: sample periodically; enforce override
        now = time.ticks_ms()
        if time.ticks_diff(now, last_us_check) >= US_CHECK_INTERVAL_MS:
            obstacle = any_obstacle_cm(OBSTACLE_ON_CM)
            flag = 1 if obstacle else 0

            # Write Ultrasonic flag only on change
            if flag != last_us_flag:
                fb_put(PATH_USONIC_FLAG, flag)
                last_us_flag = flag
                print(f"[US] Obstacle flag -> {flag}")

                if flag == 1:
                    # Enter override: force stop + show "S" in DB
                    if last_dir != "S":
                        fb_put(PATH_DIRECTION, "S")
                        last_dir = "S"
                    brake()
                else:
                    # Exit override: restore last non-stop direction automatically
                    # If DB currently shows "S", push back the previous direction
                    dir_current = str(fb_get(PATH_DIRECTION)).strip('"').upper() if True else last_dir
                    if dir_current == "S":
                        fb_put(PATH_DIRECTION, last_non_stop_dir)
                        last_dir = last_non_stop_dir
                        execute_direction(last_non_stop_dir)
                    else:
                        # If user already changed it while stopped, honor that
                        execute_direction(dir_current)
                        last_dir = dir_current

            else:
                # No change in flag: maintain state. If still blocked, ensure motors are braked.
                if last_us_flag == 1:
                    brake()

            last_us_check = now

        time.sleep_ms(POLL_MS)

if __name__ == "__main__":
    main()

