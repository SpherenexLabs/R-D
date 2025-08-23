# ===== main.py (fast polling + IR1 + Status LED rules) =====
# Pico W + L298N + SSD1306 128x32 OLED + Firebase (API-key auth)
# Motion rule (unchanged):
#   allow = gate_ok() AND (Servo==1 OR IR2==1)
#   -> STOP only when IR2==0 AND Servo==0
# OLED shows Voltage and /Toll_Gate/Price
#
# Status LED rules (as requested):
#   - LED ON  when IR1 == 0, stays ON until IR2 == 0
#   - LED OFF whenever IR_GO_PIN is triggered (and stays OFF while triggered)

import network, time, ujson as json, random, _thread
import urequests as requests
from machine import Pin, I2C
from time import ticks_ms, ticks_diff

# ---------- USER CONFIG ----------
WIFI_SSID = "toll"
WIFI_PASS = "123456789"

API_KEY       = "AIzaSyB9ererNsNonAzH0zQo_GS79XPOyCoMxr4"
USER_EMAIL    = "spherenexgpt@gmail.com"
USER_PASSWORD = "Spherenex@123"
DB_URL        = "https://waterdtection-default-rtdb.firebaseio.com"

# DB paths
PATH_ROOT  = "/Toll_Gate"                 # bulk-read root
PATH_VOLT  = "/Toll_Gate/Voltage"
PATH_HEART = "Toll_Gate/logger_alive"

# Pins (GPIO numbers)
IN1_PIN, IN2_PIN, IN3_PIN, IN4_PIN = 2, 3, 4, 5
IR_RIGHT_PIN, IR_LEFT_PIN = 6, 7
IR_GO_PIN = 8

# OLED (I2C0)
OLED_SDA_PIN, OLED_SCL_PIN = 0, 1
OLED_WIDTH, OLED_HEIGHT = 128, 32

# Status LED (onboard "LED" or set to a GPIO number like 15)
STATUS_LED_PIN  = 9    # "LED" = Pico W onboard LED; or an int for external GPIO
LED_ACTIVE_HIGH = 1        # set 0 if your LED is wired active-low

# Polarities
SENSOR_ACTIVE_LOW = 1
GATE_ACTIVE_LOW   = 1

# Cadences (tune for your setup)
CONTROL_HZ        = 200                 # control loop target Hz
CONTROL_DT_MS     = max(1, int(1000 // CONTROL_HZ))
SENSOR_DEBOUNCE_N = 2
STRICT_STOP_ON_LOST_LINE = True
LOG_PERIOD_MS     = 500                 # voltage log
REMOTE_POLL_MS    = 50                  # fast remote reads (20 Hz)
DISPLAY_MIN_MS    = 100                 # min gap between OLED refreshes
HTTP_TIMEOUT_S    = 6                   # shorter timeout

DEBUG = True
STATUS_EVERY_MS = 1000

# ---------- Hardware ----------
IN1 = Pin(IN1_PIN, Pin.OUT, value=0)
IN2 = Pin(IN2_PIN, Pin.OUT, value=0)
IN3 = Pin(IN3_PIN, Pin.OUT, value=0)
IN4 = Pin(IN4_PIN, Pin.OUT, value=0)

IR_RIGHT = Pin(IR_RIGHT_PIN, Pin.IN, Pin.PULL_UP)
IR_LEFT  = Pin(IR_LEFT_PIN,  Pin.IN, Pin.PULL_UP)
IR_GO    = Pin(IR_GO_PIN,    Pin.IN, Pin.PULL_UP)

# Status LED init
try:
    STATUS_LED = Pin(STATUS_LED_PIN, Pin.OUT)
except Exception as e:
    STATUS_LED = None
    print("Status LED init failed (continuing without LED):", e)

def led_on():
    if STATUS_LED is None: return
    STATUS_LED.value(1 if LED_ACTIVE_HIGH else 0)

def led_off():
    if STATUS_LED is None: return
    STATUS_LED.value(0 if LED_ACTIVE_HIGH else 1)

# ensure LED is OFF at boot
led_off()

# ---------- OLED ----------
try:
    import ssd1306
    i2c = I2C(0, sda=Pin(OLED_SDA_PIN), scl=Pin(OLED_SCL_PIN), freq=400000)
    oled = ssd1306.SSD1306_I2C(OLED_WIDTH, OLED_HEIGHT, i2c)
except Exception as e:
    oled = None
    print("OLED init failed (continuing without display):", e)

_last_oled_v = None
_last_oled_p = None
_last_oled_t = 0
def oled_draw(voltage, price):
    global _last_oled_v, _last_oled_p, _last_oled_t
    if oled is None: return
    now = ticks_ms()
    if (voltage == _last_oled_v and price == _last_oled_p
            and ticks_diff(now, _last_oled_t) < DISPLAY_MIN_MS):
        return
    _last_oled_v, _last_oled_p, _last_oled_t = voltage, price, now
    oled.fill(0)
    try:  vtxt = "V: %.2f V" % float(voltage)
    except: vtxt = "V: %s" % str(voltage)
    ptxt = "Price: %s" % (str(price) if price not in (None, "", "null") else "--")
    oled.text(vtxt, 0, 0)
    oled.text(ptxt, 0, 16)
    oled.show()

# ---------- Motor & sensor helpers ----------
def motors_stop():      IN1.value(0); IN2.value(0); IN3.value(0); IN4.value(0)
def motors_forward():   IN1.value(0); IN2.value(1); IN3.value(0); IN4.value(1)
def motors_turn_left(): IN1.value(0); IN2.value(1); IN3.value(0); IN4.value(0)
def motors_turn_right():IN1.value(0); IN2.value(0); IN3.value(0); IN4.value(1)

def _is_on(raw):  return (raw == 0) if SENSOR_ACTIVE_LOW else (raw == 1)
def gate_ok():
    v = IR_GO.value()
    return (v == 0) if GATE_ACTIVE_LOW else (v == 1)

def gate_triggered():
    # "Triggered" = sensor active; LED must be OFF while this is true.
    v = IR_GO.value()
    return (v == 0) if GATE_ACTIVE_LOW else (v == 1)

_left_on=_left_off=_right_on=_right_off=0
def debounced_states():
    global _left_on,_left_off,_right_on,_right_off
    rawL, rawR = IR_LEFT.value(), IR_RIGHT.value()
    if _is_on(rawL): _left_on=min(SENSOR_DEBOUNCE_N,_left_on+1); _left_off=0
    else:            _left_off=min(SENSOR_DEBOUNCE_N,_left_off+1); _left_on=0
    if _is_on(rawR): _right_on=min(SENSOR_DEBOUNCE_N,_right_on+1); _right_off=0
    else:            _right_off=min(SENSOR_DEBOUNCE_N,_right_off+1); _right_on=0
    return (_left_on>=SENSOR_DEBOUNCE_N, _right_on>=SENSOR_DEBOUNCE_N, rawL, rawR)

# ---------- Wi-Fi ----------
def wifi_connect_blocking():
    wlan = network.WLAN(network.STA_IF); wlan.active(True)
    try:
        wlan.config(pm=0xa11140)   # perf mode on some firmwares (ignored if unsupported)
    except:
        pass
    if not wlan.isconnected():
        wlan.connect(WIFI_SSID, WIFI_PASS); t0 = ticks_ms()
        while not wlan.isconnected():
            if ticks_diff(ticks_ms(), t0) > 20000: raise RuntimeError("Wi-Fi connect timeout")
            time.sleep_ms(100)
    if DEBUG: print("Wi-Fi:", wlan.ifconfig()[0])

# ---------- Firebase ----------
ID_TOKEN=None; TOKEN_EXPIRY=0
def firebase_sign_in():
    global ID_TOKEN, TOKEN_EXPIRY
    url = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=" + API_KEY
    payload = {"email": USER_EMAIL, "password": USER_PASSWORD, "returnSecureToken": True}
    backoff=1
    while True:
        try:
            r = requests.post(url, data=json.dumps(payload),
                              headers={"Content-Type":"application/json"},
                              timeout=HTTP_TIMEOUT_S)
            data = r.json(); r.close()
            if "idToken" not in data: raise RuntimeError("Auth error: " + str(data))
            ID_TOKEN = data["idToken"]; exp=int(data.get("expiresIn","3600"))
            TOKEN_EXPIRY = time.time() + exp - 60
            if DEBUG: print("Auth OK (~%ds)" % exp)
            return
        except Exception as e:
            if DEBUG: print("Auth fail:", e, "retry", backoff, "s")
            time.sleep(backoff); backoff=min(8, backoff*2)

def _auth_url(path): return DB_URL + "/" + path + ".json?auth=" + ID_TOKEN

def fb_put_path(path, value):
    if ID_TOKEN is None:
        if DEBUG: print("PUT skipped: no token"); return False
    try:
        r = requests.put(_auth_url(path), data=json.dumps(value),
                         headers={"Content-Type":"application/json"},
                         timeout=HTTP_TIMEOUT_S)
        ok = 200 <= r.status_code < 300
        if DEBUG and not ok: print("PUT HTTP", r.status_code, "on", path)
        r.close(); return ok
    except Exception as e:
        if DEBUG: print("PUT err on", path, ":", e); return False

def fb_get_root():
    """Bulk GET of /Toll_Gate subtree (Price, IR1, IR2, Servo1/Servo, etc.)."""
    if ID_TOKEN is None:
        if DEBUG: print("GET skipped: no token"); return None
    try:
        r = requests.get(_auth_url(PATH_ROOT.strip("/")), timeout=HTTP_TIMEOUT_S)
        data = r.json(); r.close(); return data
    except Exception as e:
        if DEBUG: print("GET err on ROOT:", e); return None

# ---------- Shared vars ----------
shared_lock = _thread.allocate_lock()
shared_voltage = 0.0
shared_price   = ""

def set_shared(voltage=None, price_sentinel=False, price=None):
    global shared_voltage, shared_price
    shared_lock.acquire()
    if voltage is not None: shared_voltage = voltage
    if price_sentinel:      shared_price   = price
    shared_lock.release()

def get_shared():
    shared_lock.acquire(); v=shared_voltage; p=shared_price; shared_lock.release(); return v,p

moving_lock = _thread.allocate_lock(); moving_flag=False
def set_moving(v):  global moving_flag; moving_lock.acquire(); moving_flag=bool(v); moving_lock.release()
def get_moving():   moving_lock.acquire(); m=moving_flag; moving_lock.release(); return m

# Remote states
remote_lock = _thread.allocate_lock()
remote_ir1  = 0      # default neutral
remote_ir2  = 1      # default allow
remote_servo= 0      # default closed

def set_remote(ir2=None, servo=None, ir1=None):
    global remote_ir1, remote_ir2, remote_servo
    remote_lock.acquire()
    if ir1   is not None: remote_ir1   = ir1
    if ir2   is not None: remote_ir2   = ir2
    if servo is not None: remote_servo = servo
    remote_lock.release()

def get_remote():
    remote_lock.acquire(); i=remote_ir2; s=remote_servo; remote_lock.release(); return i,s

def get_remote_ir1():
    remote_lock.acquire(); i=remote_ir1; remote_lock.release(); return i

def get_remote_all():
    remote_lock.acquire(); i1=remote_ir1; i2=remote_ir2; s=remote_servo; remote_lock.release(); return i1,i2,s

def _norm01(x):
    if x is None: return None
    if isinstance(x, bool): return 1 if x else 0
    if isinstance(x, int):  return 1 if x != 0 else 0
    if isinstance(x, str):  return 1 if x.strip().strip('"').strip("'") == "1" else 0
    return None

# ---------- Core1: auth + logger + OLED + fast remote polling ----------
def core1_task():
    print("[CORE1] start")
    wifi_connect_blocking()
    firebase_sign_in()
    fb_put_path(PATH_HEART, int(time.time()))

    next_log    = ticks_ms()
    next_remote = ticks_ms()

    # Caches to avoid extra work
    last_ir1 = None
    last_ir2 = None
    last_servo = None
    last_price = None

    while True:
        if (ID_TOKEN is None) or (time.time() > TOKEN_EXPIRY):
            firebase_sign_in()

        now = ticks_ms()

        # A) FAST: bulk GET remote state (20 Hz default)
        if ticks_diff(now, next_remote) >= 0:
            root = fb_get_root()
            if isinstance(root, dict):
                ir1    = _norm01(root.get("IR1"))
                ir2    = _norm01(root.get("IR2"))
                servo1 = _norm01(root.get("Servo1"))
                if servo1 is None:
                    servo1 = _norm01(root.get("Servo"))
                if ir2   is None: ir2   = 1
                if ir1   is None: ir1   = 0
                if servo1 is None: servo1 = 0

                if ir1 != last_ir1 or ir2 != last_ir2 or servo1 != last_servo:
                    set_remote(ir2=ir2, servo=servo1, ir1=ir1)
                    last_ir1, last_ir2, last_servo = ir1, ir2, servo1

                pr = root.get("Price", "")
                if isinstance(pr, (int, float)):
                    price_cache = pr
                elif isinstance(pr, str):
                    s = pr.strip()
                    if s:
                        try: price_cache = float(s.replace("₹","").replace("$",""))
                        except: price_cache = s
                    else:
                        price_cache = ""
                else:
                    price_cache = "" if pr is None else pr

                if price_cache != last_price:
                    set_shared(price_sentinel=True, price=price_cache)
                    last_price = price_cache

            next_remote = now + REMOTE_POLL_MS

        # B) Voltage logging (independent cadence)
        if ticks_diff(now, next_log) >= 0:
            val = round(random.uniform(4.0, 5.0), 2) if get_moving() else 0
            fb_put_path(PATH_VOLT, val)
            set_shared(voltage=val)
            next_log = now + LOG_PERIOD_MS

        # C) OLED (only when values changed / throttled inside oled_draw)
        v, p = get_shared()
        oled_draw(v, p)

        time.sleep_ms(0)

# ---------- LED update (runs every control tick) ----------
# ---------- LED update (runs every control tick) ----------
_last_led = None
def update_status_led():
    global _last_led
    v, _ = get_shared()  # shared_voltage is updated when we log to PATH_VOLT
    # Treat tiny values as zero (floating point safety). Adjust epsilon if needed.
    is_on = 1 if (v is not None and abs(float(v)) > 1e-6) else 0

    if is_on != _last_led:
        if is_on: led_on()
        else:     led_off()
        _last_led = is_on


# ---------- Core0: fast control ----------
def main():
    print("=== Pico W: line follower + OLED + fast remote poll + Status LED ===")
    wifi_connect_blocking()
    try: _thread.start_new_thread(core1_task, ())
    except Exception as e: print("WARN: cannot start core1 thread:", e)

    last_status = 0
    while True:
        t0 = ticks_ms()
        left_on, right_on, rawL, rawR = debounced_states()

        g_ok = gate_ok()
        ir2, servo = get_remote()
        allow_remote = (servo == 1) or (ir2 == 1)
        allow = g_ok and allow_remote

        if allow:
            if left_on and right_on:        motors_forward(); set_moving(True)
            elif left_on and not right_on:  motors_turn_left(); set_moving(True)
            elif not left_on and right_on:  motors_turn_right(); set_moving(True)
            else:
                if STRICT_STOP_ON_LOST_LINE: motors_stop(); set_moving(False)
        else:
            motors_stop(); set_moving(False)

        # Update LED per requested rule
        update_status_led()

        if DEBUG and ticks_diff(t0, last_status) >= STATUS_EVERY_MS:
            last_status = t0
            ir1_now = get_remote_ir1()
            print("CTRL gate=%d IR1=%d IR2=%d Servo=%d allow=%d | L=%s R=%s | moving=%d | LED=%d" %
                  (1 if g_ok else 0, ir1_now, ir2, servo, 1 if allow else 0,
                   "ON" if left_on else "OFF",
                   "ON" if right_on else "OFF",
                   1 if get_moving() else 0,
                   1 if _last_led else 0))

        # keep target Hz
        elapsed = ticks_diff(ticks_ms(), t0)
        rem = CONTROL_DT_MS - elapsed
        if rem > 0: time.sleep_ms(rem)

# ---- Entry ----
try:
    main()
except Exception as e:
    motors_stop()
    led_off()
    print("Fatal:", e)


