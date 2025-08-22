# ===== File: atm_welcome_menu.py =====
# Board: Raspberry Pi Pico W (MicroPython)
# Boot: set Welcome flag "1" -> show menu on Serial -> on selection:
#   1 Facial  : no action
#   2 Finger  : require 2 s touch hold, then set Verification/2_Finger = "1"
#   3 PIN     : collect 4 digits, set Verification/3_PIN = "<4digits>"
#   4 Pattern : collect 4 digits, set Verification/4_Pattern = "<4digits>"
# Also: after first valid selection write, set Welcome flag back to "0".
# After writing verification, poll /6_ATM/2_Menu/3_Authentication_Verified:
#   '1' -> print 'Verified' and prompt for Account Type menu;
#   '2' -> print 'Wrong verification';
#   '0' or '' -> do nothing; keep waiting.
# Then, after Account Type, show Bank Selection (1..4) and write to /6_ATM/2_Menu/5_Bank_Selection.
# After Bank Selection, prompt for Withdrawal Amount, update /6_ATM/2_Menu/6_Withdrawal_Amount
# as digits are entered, and on '#' confirm: run dispenser (servo+buzzer) and set
# /6_ATM/2_Menu/7_Transaction_Completed = "1".

import time, network, ujson
try:
    import urequests as requests
except ImportError:
    import requests

from machine import Pin, PWM

# ---------- USER CONFIG ----------
WIFI_SSID = "atm"
WIFI_PASS = "123456789"

API_KEY   = "AIzaSyB9ererNsNonAzH0zQo_GS79XPOyCoMxr4"
DB_URL    = "https://waterdtection-default-rtdb.firebaseio.com"

USER_EMAIL    = "spherenexgpt@gmail.com"
USER_PASSWORD = "Spherenex@123"

# ---------- RTDB paths ----------
WELCOME_PATH  = "/6_ATM/1_Welcome_Screen"
SELECT_PATH   = "/6_ATM/2_Menu/1_Authentication_Selection"
VERIFY_BASE   = "/6_ATM/2_Menu/2_Authentication_Verification"
FINGER_PATH   = VERIFY_BASE + "/2_Finger"
PIN_PATH      = VERIFY_BASE + "/3_PIN"
PATTERN_PATH  = VERIFY_BASE + "/4_Pattern"
VERIFY_STATUS_PATH = "/6_ATM/2_Menu/3_Authentication_Verified"
ACCOUNT_TYPE_PATH  = "/6_ATM/2_Menu/4_Account_Type"
BANK_SELECTION_PATH = "/6_ATM/2_Menu/5_Bank_Selection"
WITHDRAWAL_AMOUNT_PATH = "/6_ATM/2_Menu/6_Withdrawal_Amount"
TX_COMPLETED_PATH      = "/6_ATM/2_Menu/7_Transaction_Completed"

# ---------- Keypad wiring (3x4) ----------
ROWS, COLS = 4, 3
KEYS = [['1','2','3'],
        ['4','5','6'],
        ['7','8','9'],
        ['*','0','#']]
ROW_PINS = [6, 7, 8, 9]      # GP6..GP9 as inputs (pull-up)
COL_PINS = [10, 11, 12]      # GP10..GP12 as outputs (idle HIGH)
rows = [Pin(p, Pin.IN, Pin.PULL_UP) for p in ROW_PINS]
cols = [Pin(p, Pin.OUT, value=1) for p in COL_PINS]  # idle HIGH
DEBOUNCE_MS = 25

# ---------- Touch sensor (TTP223) ----------
TOUCH_PIN = 14
TOUCH_ACTIVE_HIGH = True                      # default TTP223 boards
touch = Pin(TOUCH_PIN, Pin.IN,
            Pin.PULL_DOWN if TOUCH_ACTIVE_HIGH else Pin.PULL_UP)

def touch_active():
    v = touch.value()
    return (v == 1) if TOUCH_ACTIVE_HIGH else (v == 0)

# ---------- Servo (PWM @ 50 Hz) ----------
SERVO_PIN = 15
SERVO_MIN_US = 500
SERVO_MAX_US = 2500
servo_pwm = PWM(Pin(SERVO_PIN))
servo_pwm.freq(50)

def _us_to_duty(us):
    return int(us * 65535 // 20000)

def servo_angle(deg):
    if deg < 0: deg = 0
    if deg > 180: deg = 180
    pulse = SERVO_MIN_US + (SERVO_MAX_US - SERVO_MIN_US) * deg // 180
    servo_pwm.duty_u16(_us_to_duty(pulse))

# ---------- Buzzer (active-high) ----------
BUZZER_PIN = 16
BUZZER_ACTIVE_HIGH = True
buzzer = Pin(BUZZER_PIN, Pin.OUT, value=0 if BUZZER_ACTIVE_HIGH else 1)

def buzzer_on():
    buzzer.value(1 if BUZZER_ACTIVE_HIGH else 0)

def buzzer_off():
    buzzer.value(0 if BUZZER_ACTIVE_HIGH else 1)

def buzzer_toggle():
    buzzer.value(1 - buzzer.value())

# ---------- Wi-Fi & Firebase helpers ----------
def wifi_connect():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(WIFI_SSID, WIFI_PASS)
        t0 = time.ticks_ms()
        while not wlan.isconnected():
            if time.ticks_diff(time.ticks_ms(), t0) > 15000:
                raise RuntimeError("Wi-Fi connect timeout")
            time.sleep_ms(200)
    print("Wi-Fi OK:", wlan.ifconfig()[0])

def firebase_login():
    # Email/password sign-in; returns idToken or None
    url = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=" + API_KEY
    payload = {"email": USER_EMAIL, "password": USER_PASSWORD, "returnSecureToken": True}
    try:
        r = requests.post(url, data=ujson.dumps(payload),
                          headers={"Content-Type": "application/json"})
        if r.status_code != 200:
            print("Auth error:", r.status_code, r.text)
            r.close()
            return None
        data = r.json()
        r.close()
        return data.get("idToken")
    except Exception as e:
        print("Auth exception:", e)
        return None

def rtdb_put_string(path, value, id_token=None):
    # Writes a JSON string value to RTDB
    if not path.startswith('/'):
        path = '/' + path
    url = DB_URL + path + '.json' + (('?auth=' + id_token) if id_token else '')
    try:
        r = requests.put(url, data=ujson.dumps(value),
                         headers={'Content-Type': 'application/json'})
        ok = 200 <= r.status_code < 300
        if not ok:
            print('RTDB write failed:', r.status_code, r.text)
        r.close()
        return ok
    except Exception as e:
        print('RTDB write exception:', e)
        return False

def rtdb_get(path, id_token=None):
    # Reads and returns JSON value (None on error)
    if not path.startswith('/'):
        path = '/' + path
    url = DB_URL + path + '.json' + (('?auth=' + id_token) if id_token else '')
    try:
        r = requests.get(url)
        if not (200 <= r.status_code < 300):
            print('RTDB get failed:', r.status_code, r.text)
            r.close()
            return None
        val = r.json()
        r.close()
        return val
    except Exception as e:
        print('RTDB get exception:', e)
        return None

# ---------- Keypad scan: one key per physical press ----------
def get_key_once():
    # Drive each column LOW in turn; read rows (active LOW)
    key = None
    for ci, _ in enumerate(cols):
        for j, cp in enumerate(cols):
            cp.value(0 if j == ci else 1)
        time.sleep_us(200)  # settle

        for ri, rpin in enumerate(rows):
            if rpin.value() == 0:  # pressed
                t0 = time.ticks_ms()
                while rpin.value() == 0 and time.ticks_diff(time.ticks_ms(), t0) < DEBOUNCE_MS:
                    time.sleep_ms(1)
                while rpin.value() == 0:
                    time.sleep_ms(1)
                time.sleep_ms(DEBOUNCE_MS)
                key = KEYS[ri][ci]
                break
        if key is not None:
            break

    for cp in cols:
        cp.value(1)  # idle HIGH
    return key

# ---------- Helpers for verification flows ----------
def collect_4_digits(prompt):
    '''Read 4 digits from keypad with * = backspace, # = clear.
       Returns a 4-char string.'''
    print(prompt)
    print('Enter 4 digits:  ( * = backspace,  # = clear )')
    buf = ''
    while True:
        k = get_key_once()
        if not k:
            time.sleep_ms(5)
            continue
        if k.isdigit():
            if len(buf) < 4:
                buf += k
                print('*' * len(buf))  # masked echo
            if len(buf) == 4:
                return buf
        elif k == '*':
            if buf:
                buf = buf[:-1]
                print('*' * len(buf))
        elif k == '#':
            buf = ''
            print('')
        # ignore others

def wait_touch_hold_ms(hold_ms=2000):
    '''Return True when touch is kept active for hold_ms continuously.'''
    print('Touch and hold sensor for {:.1f} s...'.format(hold_ms/1000))
    # Wait for touch start
    while not touch_active():
        time.sleep_ms(10)
    t0 = time.ticks_ms()
    # Require continuous active
    while touch_active():
        if time.ticks_diff(time.ticks_ms(), t0) >= hold_ms:
            print('Touch hold verified.')
            return True
        time.sleep_ms(10)
    print('Released too early; aborted.')
    return False

def account_type_menu(id_token):
    print('\nAccount Type: 1 = Savings_account, 2 = Current_account')
    while True:
        k = get_key_once()
        if k == '1':
            if rtdb_put_string(ACCOUNT_TYPE_PATH, '1', id_token=id_token):
                print('Account type set to 1 (Savings_account).')
            else:
                print('Failed to write account type.')
            return '1'
        elif k == '2':
            if rtdb_put_string(ACCOUNT_TYPE_PATH, '2', id_token=id_token):
                print('Account type set to 2 (Current_account).')
            else:
                print('Failed to write account type.')
            return '2'
        time.sleep_ms(5)

def bank_selection_menu(id_token):
    names = {'1':'BANK1', '2':'BANK2', '3':'BANK3', '4':'BANK4'}
    print('\nBank Selection: 1=BANK1, 2=BANK2, 3=BANK3, 4=BANK4')
    while True:
        k = get_key_once()
        if k in ('1','2','3','4'):
            if rtdb_put_string(BANK_SELECTION_PATH, k, id_token=id_token):
                print('Bank selected: {} ({})'.format(k, names[k]))
            else:
                print('Failed to write bank selection.')
            return k
        time.sleep_ms(5)

def withdrawal_amount_flow(id_token):
    # Reset paths
    rtdb_put_string(WITHDRAWAL_AMOUNT_PATH, '', id_token=id_token)
    rtdb_put_string(TX_COMPLETED_PATH, '0', id_token=id_token)
    print('\nEnter amount (digits).  * = backspace,  # = confirm')
    buf = ''
    while True:
        k = get_key_once()
        if not k:
            time.sleep_ms(5)
            continue
        if k.isdigit():
            # Limit length to, say, 6 digits (optional)
            if len(buf) < 6:
                buf += k
                print(buf)
                rtdb_put_string(WITHDRAWAL_AMOUNT_PATH, buf, id_token=id_token)
        elif k == '*':
            if buf:
                buf = buf[:-1]
                print(buf if buf else '')
                rtdb_put_string(WITHDRAWAL_AMOUNT_PATH, buf, id_token=id_token)
        elif k == '#':
            if len(buf) > 0:
                print('Amount confirmed: {}'.format(buf))
                rtdb_put_string(WITHDRAWAL_AMOUNT_PATH, buf, id_token=id_token)
                dispense_action(id_token)
                return buf
        # ignore others

def beep_for_ms(total_ms=5000, toggle_ms=200):
    t_end = time.ticks_add(time.ticks_ms(), total_ms)
    state = False
    buzzer_off()
    while time.ticks_diff(t_end, time.ticks_ms()) > 0:
        state = not state
        if BUZZER_ACTIVE_HIGH:
            buzzer.value(1 if state else 0)
        else:
            buzzer.value(0 if state else 1)
        time.sleep_ms(toggle_ms)
    buzzer_off()

def dispense_action(id_token=None):
    print('Dispensing... (servo 0->180, buzzer beeps 5s, back to 0)')
    # Move to 180
    servo_angle(180)
    # Beep for 5s
    beep_for_ms(5000, 200)
    # Back to 0
    servo_angle(0)
    # Mark transaction completion
    if id_token is not None:
        rtdb_put_string(TX_COMPLETED_PATH, '1', id_token=id_token)
    print('Transaction completed -> {} = "1"'.format(TX_COMPLETED_PATH))

def wait_and_handle_verification_status(id_token):
    print('Waiting for verification result at {} ...'.format(VERIFY_STATUS_PATH))
    last = None
    while True:
        v = rtdb_get(VERIFY_STATUS_PATH, id_token=id_token)
        if v is None:
            time.sleep_ms(300)
            continue
        s = str(v).strip().strip('\"')
        if s != last:
            last = s
            if s == '1':
                print('Authentication Verified.')
                account_type_menu(id_token)
                bank_selection_menu(id_token)
                return 'verified'
            elif s == '2':
                print('Wrong verification.')
                return 'wrong'
            elif s in ('0', ''):
                # do nothing
                pass
            else:
                print('Unhandled status:', s)
        time.sleep_ms(300)

# ---------- Main ----------
def main():
    print('\n=== PICO ATM: Welcome + Auth + Verification + Account + Bank + Amount ===')
    wifi_connect()
    id_token = firebase_login()
    if id_token:
        print('Firebase auth OK')
    else:
        print('No auth token. Proceeding unauthenticated (public rules required).')

    # Initialize actuators
    servo_angle(0)
    buzzer_off()

    # 1) Set welcome flag = '1'
    welcome_is_one = False
    if rtdb_put_string(WELCOME_PATH, '1', id_token=id_token):
        print('Set {} = "1"'.format(WELCOME_PATH))
        welcome_is_one = True
    else:
        print('Failed to set Welcome flag.')

    # 2) Show menu and accept 1..4
    def print_menu():
        print('\nSelect Authentication method via keypad:')
        print('  1 = Facial  (noop now)')
        print('  2 = Finger  (hold touch 2 s)')
        print('  3 = PIN     (enter 4 digits)')
        print('  4 = Pattern (enter 4 digits)')
        print('Press one key (1–4). Each press updates Firebase.\n')

    print_menu()
    last_written = None

    while True:
        k = get_key_once()
        if k in ('1', '2', '3', '4'):
            if k != last_written:
                ok = rtdb_put_string(SELECT_PATH, k, id_token=id_token)
                if ok:
                    print('Selected {} -> wrote "{}" to {}'.format(
                        {'1':'Facial','2':'Finger','3':'PIN','4':'Pattern'}[k], k, SELECT_PATH))
                    last_written = k

                    # After selection, set Welcome flag back to '0' once
                    if welcome_is_one:
                        if rtdb_put_string(WELCOME_PATH, '0', id_token=id_token):
                            print('Set {} = "0" (after selection)'.format(WELCOME_PATH))
                            welcome_is_one = False
                        else:
                            print('Failed to reset Welcome flag to 0.')

                    # --- Verification branch ---
                    if k == '1':
                        print('Facial selected: no action here; waiting for verification status...')
                    elif k == '2':
                        if wait_touch_hold_ms(2000):
                            rtdb_put_string(FINGER_PATH, '1', id_token=id_token)
                            print('Verification: wrote "1" to {}'.format(FINGER_PATH))
                    elif k == '3':
                        pin = collect_4_digits('PIN selected.')
                        if rtdb_put_string(PIN_PATH, pin, id_token=id_token):
                            print('Verification: wrote "{}" to {}'.format(pin, PIN_PATH))
                    elif k == '4':
                        patt = collect_4_digits('Pattern selected.')
                        if rtdb_put_string(PATTERN_PATH, patt, id_token=id_token):
                            print('Verification: wrote "{}" to {}'.format(patt, PATTERN_PATH))

                    # After writing verification (or for Facial), wait for status
                    result = wait_and_handle_verification_status(id_token)

                    # If verified, proceed to withdrawal amount flow
                    if result == 'verified':
                        withdrawal_amount_flow(id_token)

                    # Re-print menu for convenience (allow new session)
                    print_menu()
                else:
                    print('Write failed; will retry on next key.')
        time.sleep_ms(5)

# Auto-run
if __name__ == '__main__':
    main()

