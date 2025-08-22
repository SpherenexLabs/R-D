# ---------- Wi-Fi + Firebase + Keypad + Touch (Pico W / MicroPython) ----------
import network, time, ujson
try:
    import urequests as requests
except ImportError:
    import requests

from machine import Pin

# ===== USER CONFIG =====
WIFI_SSID = "atm"
WIFI_PASS = "123456789"

API_KEY   = "AIzaSyB9ererNsNonAzH0zQo_GS79XPOyCoMxr4"
DB_URL    = "https://waterdtection-default-rtdb.firebaseio.com"
USER_EMAIL    = "spherenexgpt@gmail.com"
USER_PASSWORD = "Spherenex@123"

# RTDB paths
PIN_PATH = "/6_ATM/1_Authentication/3_PIN"                  # "1234" (string)
FP_PATH  = "/6_ATM/1_Authentication/2_Fingerprint"          # "1" pulse on 3s hold

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
HOLD_MS = 3000                 # 3 seconds
PULSE_MS = 1000                   # << set 0 for immediate reset to "0" after writing "1"
touch = Pin(TOUCH_PIN, Pin.IN, Pin.PULL_DOWN if TOUCH_ACTIVE_HIGH else Pin.PULL_UP)

def touch_active():
    v = touch.value()
    return (v == 1) if TOUCH_ACTIVE_HIGH else (v == 0)

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

    # [Init] ensure fingerprint path is 0 at boot
    rtdb_put_string(FP_PATH, "0", id_token=id_token)      # << init reset

    buf = ""
    print("Ready. Keypad: enter 4-digit PIN. '*'=backspace, '#'=clear.")
    touch_down = False
    touch_t0 = 0
    fired_this_hold = False

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

        # --- Touch sensor (3s hold -> pulse "1" then immediate "0") ---
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
                        rtdb_put_string(FP_PATH, "0", id_token=id_token)   # << immediate reset
                        print('Fingerprint pulse: 1→0')
                    else:
                        print("Fingerprint write FAILED")
                    fired_this_hold = True
        else:
            touch_down = False
            fired_this_hold = False

        time.sleep_ms(5)

# Auto-run
if __name__ == "__main__":
    main()

