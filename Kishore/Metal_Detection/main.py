# main.py — Pico W/H + L293D + HC-SR04 + DHT11 + Firebase RTDB
# Uses ONLY these RTDB keys under /Land_Mine_Detection:
#   READ : direction   <-- lower-case key
#   WRITE: Ultrasonic, Metal, Rerouting, Temperature, Humidity
# - 10 Hz Metal+Ultrasonic PATCH, 5 Hz Direction poll, 2 s DHT
# - Per-motor invert (Motor-B inverted) + dead-time for reliable reverse
# - All RTDB writes are STRINGS

import time, ujson as json
import network, machine
from machine import Pin, PWM
import urequests as requests
import dht

# ===== USER CONFIG =====
WIFI_SSID = "landmine"
WIFI_PASSWORD = "123456789"

FIREBASE_API_KEY = "AIzaSyAXHnvNZkb00PXbG5JidbD4PbRgf7l6Lgg"
FIREBASE_DB_URL  = "https://v2v-communication-d46c6-default-rtdb.firebaseio.com"
AUTH_EMAIL       = "spherenexgpt@gmail.com"
AUTH_PASSWORD    = "Spherenex@123"

BASE_PATH = "/Land_Mine_Detection"

# ===== THRESHOLDS & RATES =====
ULTRA_THRESHOLD_CM = 20.0
TEMP_RELAY_THRESHOLD_C = 35.0
SENSOR_PUSH_MS = 100
DIR_POLL_MS    = 200
DHT_PERIOD_MS  = 2000

# Print ultrasonic distance to terminal
PRINT_DISTANCE = True  # set False to silence distance logs

# Reroute sequence on obstacle
SEQ = [
    ("back", 2.0),
    ("left", 1.7),
    ("front", 2.0),
    ("right", 1.7),
    ("front", 4.0),
    ("right", 1.7),
    ("front", 2.0),
    ("left", 1.7),
]

# ===== Pins =====
ENA_PIN = 4; IN1_PIN = 2; IN2_PIN = 3
ENB_PIN = 7; IN3_PIN = 5; IN4_PIN = 6
TRIG_PIN = 8; ECHO_PIN = 9
DHT_PIN  = 15
RELAY_PIN = 16; RELAY_ACTIVE_HIGH = False
METAL_PIN = 14; METAL_ACTIVE_LOW = True

# ===== Motor config (Motor-B inverted to fix your wiring) =====
MOTOR_A_INVERT = False
MOTOR_B_INVERT = True      # <-- keep True so Motor-2 works in F/B and Right spin
DEADTIME_MS    = 8         # brief coast before changing direction
SPEED_DUTY     = 52000     # 0..65535 PWM

# ===== Wi-Fi =====
def wifi_connect():
    wlan = network.WLAN(network.STA_IF)
    if not wlan.active():
        wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        t0 = time.ticks_ms()
        while not wlan.isconnected():
            if time.ticks_diff(time.ticks_ms(), t0) > 15000:
                raise RuntimeError("WiFi connect timeout")
            time.sleep(0.1)
    return wlan

# ===== Firebase (REST) =====
_JSON_HDR = {"Content-Type": "application/json"}

class Firebase:
    def __init__(self, api_key, db_url, email, password):
        self.api_key = api_key; self.db_url = db_url.rstrip("/")
        self.email = email; self.password = password
        self.id_token = None; self.refresh_token = None
        self.token_birth = 0; self.token_ttl = 0

    def sign_in(self):
        url = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=" + self.api_key
        payload = {"email": self.email, "password": self.password, "returnSecureToken": True}
        r = requests.post(url, data=json.dumps(payload), headers=_JSON_HDR)
        if r.status_code != 200:
            raise RuntimeError("Auth failed: {} {}".format(r.status_code, r.text))
        data = r.json(); r.close()
        self.id_token = data["idToken"]; self.refresh_token = data["refreshToken"]
        self.token_ttl = int(data.get("expiresIn", "3600")); self.token_birth = time.time()

    def ensure_token(self):  # refresh 5 min before expiry
        if (not self.id_token) or (time.time() - self.token_birth > (self.token_ttl - 300)):
            self.sign_in()

    def _url(self, path):
        p = path.lstrip("/")
        return "{}{}.json?auth={}".format(self.db_url, "/" + p if p else "", self.id_token)

    def patch(self, path, dict_value):
        self.ensure_token()
        r = requests.patch(self._url(path), data=json.dumps(dict_value), headers=_JSON_HDR)
        sc = r.status_code; txt = r.text; r.close()
        if sc >= 300: raise RuntimeError("PATCH {} -> {} {}".format(path, sc, txt))

    def get(self, path):
        self.ensure_token()
        r = requests.get(self._url(path)); sc = r.status_code
        if sc != 200:
            txt = r.text; r.close()
            raise RuntimeError("GET {} -> {} {}".format(path, sc, txt))
        data = r.json(); r.close(); return data

# ===== Sensors =====
TRIG = Pin(TRIG_PIN, Pin.OUT); ECHO = Pin(ECHO_PIN, Pin.IN)
dht_sensor = dht.DHT11(Pin(DHT_PIN))
RELAY = Pin(RELAY_PIN, Pin.OUT)
metal_in = Pin(METAL_PIN, Pin.IN, Pin.PULL_UP)

def relay_set(on):
    if RELAY_ACTIVE_HIGH:
        RELAY.value(1 if on else 0)
    else:
        RELAY.value(0 if on else 1)

def measure_distance_cm():
    TRIG.value(0); time.sleep_us(2); TRIG.value(1); time.sleep_us(10); TRIG.value(0)
    try:
        us = machine.time_pulse_us(ECHO, 1, 25000)
        return None if us < 0 else us / 58.0
    except:
        return None

def read_dht():
    try:
        dht_sensor.measure()
        return float(dht_sensor.temperature()), float(dht_sensor.humidity())
    except:
        return None, None

def metal_detected():
    v = metal_in.value()
    return (v == 0) if METAL_ACTIVE_LOW else (v == 1)

# ===== Motors (invert + dead-time) =====
in1 = Pin(IN1_PIN, Pin.OUT); in2 = Pin(IN2_PIN, Pin.OUT)
in3 = Pin(IN3_PIN, Pin.OUT); in4 = Pin(IN4_PIN, Pin.OUT)
ena = PWM(Pin(ENA_PIN)); enb = PWM(Pin(ENB_PIN))
ena.freq(1000); enb.freq(1000)

def _drive_pair(in_a, in_b, pwm, forward, invert, duty):
    if invert:
        forward = not forward
    a, b = ((1, 0) if forward else (0, 1))
    # dead-time before changing direction
    pwm.duty_u16(0); in_a(0); in_b(0); time.sleep_ms(DEADTIME_MS)
    in_a(a); in_b(b); pwm.duty_u16(duty)

def m_stop():
    ena.duty_u16(0); enb.duty_u16(0)
    in1(0); in2(0); in3(0); in4(0)

def set_motor_a(forward, duty): _drive_pair(in1, in2, ena, forward, MOTOR_A_INVERT, duty)
def set_motor_b(forward, duty): _drive_pair(in3, in4, enb, forward, MOTOR_B_INVERT, duty)

def m_front(duty): set_motor_a(True, duty);  set_motor_b(True, duty)
def m_back(duty):  set_motor_a(False, duty); set_motor_b(False, duty)
def m_left(duty):  set_motor_a(False, duty); set_motor_b(True, duty)   # spin left
def m_right(duty): set_motor_a(True, duty);  set_motor_b(False, duty)  # spin right

def move_for(action, secs, fb, duty, push_every_ms=200):
    fn = m_stop
    if   action == "front": fn = lambda d: m_front(d)
    elif action == "back":  fn = lambda d: m_back(d)
    elif action == "left":  fn = lambda d: m_left(d)
    elif action == "right": fn = lambda d: m_right(d)
    fn(duty)
    t0 = time.ticks_ms(); last = t0
    while time.ticks_diff(time.ticks_ms(), t0) < int(secs * 1000):
        now = time.ticks_ms()
        if time.ticks_diff(now, last) >= push_every_ms:
            try:
                ultra_cm = measure_distance_cm()
                # ---- distance print during reroute ----
                if PRINT_DISTANCE:
                    if ultra_cm is None:
                        print("DIST: -- (no echo)")
                    else:
                        print("DIST: {:.1f} cm".format(ultra_cm))
                ul = "1" if (ultra_cm is not None and ultra_cm <= ULTRA_THRESHOLD_CM) else "0"
                m  = "1" if metal_detected() else "0"
                fb.patch(BASE_PATH, {"Ultrasonic": ul, "Metal": m})
            except:
                pass
            last = now
        time.sleep(0.01)
    m_stop()

# ===== Safe PATCH =====
_last_warn = 0
def safe_patch(fb, payload):
    global _last_warn
    try:
        fb.patch(BASE_PATH, payload); return True
    except Exception as e:
        now = time.ticks_ms()
        if time.ticks_diff(now, _last_warn) > 1000:
            print("PATCH fail:", e); _last_warn = now
        time.sleep(0.05)
        try:
            fb.patch(BASE_PATH, payload); return True
        except Exception as e2:
            if time.ticks_diff(time.ticks_ms(), _last_warn) > 1000:
                print("PATCH retry fail:", e2); _last_warn = time.ticks_ms()
            return False

# ===== MAIN =====
def main():
    print("WiFi…"); wifi_connect(); print("WiFi OK")
    fb = Firebase(FIREBASE_API_KEY, FIREBASE_DB_URL, AUTH_EMAIL, AUTH_PASSWORD)
    print("Auth…"); fb.sign_in(); print("Firebase OK")

    # Seed ONLY existing movement/sensor keys (no extra nodes)
    safe_patch(fb, {"Metal":"0","Ultrasonic":"0","Rerouting":"0"})

    last_sensor = last_dir = last_dht = 0
    last_ultra_str = last_metal_str = None
    direction = "S"; rerouting = False
    duty = SPEED_DUTY

    m_stop(); relay_set(False)

    while True:
        now = time.ticks_ms()

        # --- 10 Hz Metal + Ultrasonic (batched) ---
        if time.ticks_diff(now, last_sensor) >= SENSOR_PUSH_MS:
            ultra_cm = measure_distance_cm()
            # ---- distance print in normal loop ----
            if PRINT_DISTANCE:
                if ultra_cm is None:
                    print("DIST: -- (no echo)")
                else:
                    print("DIST: {:.1f} cm".format(ultra_cm))
            ultra_str = "1" if (ultra_cm is not None and ultra_cm <= ULTRA_THRESHOLD_CM) else "0"
            metal_str = "1" if metal_detected() else "0"
            if (ultra_str != last_ultra_str) or (metal_str != last_metal_str) or (time.ticks_diff(now, last_sensor) >= 1000):
                safe_patch(fb, {"Ultrasonic": ultra_str, "Metal": metal_str})
                last_ultra_str = ultra_str; last_metal_str = metal_str
            last_sensor = now

            # Obstacle → run fixed reroute sequence
            if (ultra_str == "1") and not rerouting:
                rerouting = True; safe_patch(fb, {"Rerouting":"1"})
                for action, secs in SEQ:
                    move_for(action, secs, fb, duty, push_every_ms=200)
                safe_patch(fb, {"Rerouting":"0"}); rerouting = False; m_stop(); continue

        # --- 2 s DHT + relay ---
        if time.ticks_diff(now, last_dht) >= DHT_PERIOD_MS:
            t, h = read_dht()
            if (t is not None) and (h is not None):
                safe_patch(fb, {"Temperature": "{:.1f}".format(t), "Humidity": "{:.1f}".format(h)})
                relay_set(t > TEMP_RELAY_THRESHOLD_C)
            last_dht = now

        # --- 5 Hz Direction polling (when not rerouting) ---
        if (not rerouting) and (time.ticks_diff(now, last_dir) >= DIR_POLL_MS):
            try:
                v = fb.get("{}/direction".format(BASE_PATH))  # lower-case key
                direction = v[0].upper() if isinstance(v, str) and v else "S"
            except Exception as e:
                print("GET direction fail:", e); direction = "S"
            last_dir = now

            # Standard mapping from Firebase: F, B, L, R, S
            if   direction == "F": m_front(duty)
            elif direction == "B": m_back(duty)
            elif direction == "L": m_left(duty)
            elif direction == "R": m_right(duty)
            else: m_stop()

        time.sleep(0.01)

# Entry
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        try: m_stop()
        except: pass
        raise

