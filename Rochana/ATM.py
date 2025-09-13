# File: pico_atm.py   (Pico W / Pico 2 W, MicroPython — no-index lookup + retries + tri-state verify + balance check + amount mirror)

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
WELCOME_PATH           = "/6_ATM/1_Welcome_Screen"
SELECT_PATH            = "/6_ATM/2_Menu/1_Authentication_Selection"
VERIFY_BASE            = "/6_ATM/2_Menu/2_Authentication_Verification"   # tri-state: 0 idle, 1 success, 2 fail
FINGER_PATH            = VERIFY_BASE + "/2_Finger"
PIN_PATH               = VERIFY_BASE + "/3_PIN"
PATTERN_PATH           = VERIFY_BASE + "/4_Pattern"
VERIFY_STATUS_PATH     = "/6_ATM/2_Menu/3_Authentication_Verified"
ACCOUNT_TYPE_PATH      = "/6_ATM/2_Menu/4_Account_Type"
BANK_SELECTION_PATH    = "/6_ATM/2_Menu/5_Bank_Selection"
WITHDRAWAL_AMOUNT_PATH = "/6_ATM/2_Menu/6_Withdrawal_Amount"             # <— mirror target (global)
TX_COMPLETED_PATH      = "/6_ATM/2_Menu/7_Transaction_Completed"
INSUFFICIENT_FLAG_PATH = "/6_ATM/2_Menu/Insufficient_Balance"

AC_NO_PATH     = "/6_ATM/2_Menu/AC_NO"
ATM_USERS_BASE = "/6_ATM/ATMusers"

ACCOUNT_NUMBER_FIELD = "accountNumber"  # stored inside each ATMusers child

# ---------- Keypad wiring (3x4) ----------
ROWS, COLS = 4, 3
KEYS = [['1','2','3'],
        ['4','5','6'],
        ['7','8','9'],
        ['*','0','#']]
ROW_PINS = [6, 7, 8, 9]
COL_PINS = [10, 11, 12]
rows = [Pin(p, Pin.IN, Pin.PULL_UP) for p in ROW_PINS]
cols = [Pin(p, Pin.OUT, value=1) for p in COL_PINS]
DEBOUNCE_MS = 25

# ---------- Touch sensor ----------
TOUCH_PIN = 14
TOUCH_ACTIVE_HIGH = True
touch = Pin(TOUCH_PIN, Pin.IN, Pin.PULL_DOWN if TOUCH_ACTIVE_HIGH else Pin.PULL_UP)

def touch_active():
    v = touch.value()
    return (v == 1) if TOUCH_ACTIVE_HIGH else (v == 0)

# ---------- Servo ----------
SERVO_PIN = 15
SERVO_MIN_US = 500
SERVO_MAX_US = 2500
servo_pwm = PWM(Pin(SERVO_PIN))
servo_pwm.freq(50)

def _us_to_duty(us): return int(us * 65535 // 20000)

def servo_angle(deg):
    if deg < 0: deg = 0
    if deg > 180: deg = 180
    pulse = SERVO_MIN_US + (SERVO_MAX_US - SERVO_MAX_US) * 0 // 180  # placeholder to keep static analyzers happy
    pulse = SERVO_MIN_US + (SERVO_MAX_US - SERVO_MIN_US) * deg // 180
    servo_pwm.duty_u16(_us_to_duty(pulse))

# ---------- Buzzer ----------
BUZZER_PIN = 16
BUZZER_ACTIVE_HIGH = True
buzzer = Pin(BUZZER_PIN, Pin.OUT, value=0 if BUZZER_ACTIVE_HIGH else 1)

def buzzer_on():  buzzer.value(1 if BUZZER_ACTIVE_HIGH else 0)
def buzzer_off(): buzzer.value(0 if BUZZER_ACTIVE_HIGH else 1)

def beep_for_ms(total_ms=1200, toggle_ms=150):
    t_end = time.ticks_add(time.ticks_ms(), total_ms)
    state = False
    buzzer_off()
    while time.ticks_diff(t_end, time.ticks_ms()) > 0:
        state = not state
        buzzer.value(1 if (BUZZER_ACTIVE_HIGH and state) else 0 if BUZZER_ACTIVE_HIGH else (0 if state else 1))
        time.sleep_ms(toggle_ms)
    buzzer_off()

# ---------- Wi-Fi & Firebase ----------
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
    url = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=" + API_KEY
    payload = {"email": USER_EMAIL, "password": USER_PASSWORD, "returnSecureToken": True}
    try:
        r = requests.post(url, data=ujson.dumps(payload), headers={"Content-Type": "application/json"})
        if r.status_code != 200:
            print("Auth error:", r.status_code, r.text); r.close(); return None
        data = r.json(); r.close()
        return data.get("idToken")
    except Exception as e:
        print("Auth exception:", e); return None

def rtdb_url(path, id_token=None, query=None):
    if not path.startswith('/'): path = '/' + path
    url = DB_URL + path + '.json'
    qs = []
    if id_token: qs.append('auth=' + id_token)
    if query:    qs.extend(query)
    if qs:       url += '?' + '&'.join(qs)
    return url

def rtdb_put(path, value, id_token=None):
    try:
        r = requests.put(rtdb_url(path, id_token=id_token), data=ujson.dumps(value), headers={'Content-Type': 'application/json'})
        ok = 200 <= r.status_code < 300
        if not ok: print('RTDB write failed:', r.status_code, r.text)
        r.close(); return ok
    except Exception as e:
        print('RTDB write exception:', e); return False

def rtdb_put_string(path, value, id_token=None): return rtdb_put(path, value, id_token=id_token)

def rtdb_get(path, id_token=None, query=None):
    try:
        r = requests.get(rtdb_url(path, id_token=id_token, query=query))
        if not (200 <= r.status_code < 300):
            print('RTDB get failed:', r.status_code, r.text); r.close(); return None
        val = r.json(); r.close(); return val
    except Exception as e:
        print('RTDB get exception:', e); return None

# ---------- Keypad ----------
def get_key_once():
    key = None
    for ci, _ in enumerate(cols):
        for j, cp in enumerate(cols): cp.value(0 if j == ci else 1)
        time.sleep_us(200)
        for ri, rpin in enumerate(rows):
            if rpin.value() == 0:
                t0 = time.ticks_ms()
                while rpin.value() == 0 and time.ticks_diff(time.ticks_ms(), t0) < DEBOUNCE_MS: time.sleep_ms(1)
                while rpin.value() == 0: time.sleep_ms(1)
                time.sleep_ms(DEBOUNCE_MS)
                key = KEYS[ri][ci]; break
        if key is not None: break
    for cp in cols: cp.value(1)
    return key

def collect_n_digits(n, prompt, mask=True):
    print(prompt); print('Enter {} digits: (*=backspace, #=confirm)'.format(n))
    buf = ''
    while True:
        k = get_key_once()
        if not k: time.sleep_ms(5); continue
        if k.isdigit():
            if len(buf) < n: buf += k; print(('*'*len(buf)) if mask else buf)
        elif k == '*':
            if buf: buf = buf[:-1]; print(('*'*len(buf)) if mask else (buf if buf else ''))
        elif k == '#':
            if len(buf) == n: return buf

def collect_4_digits(prompt): return collect_n_digits(4, prompt, mask=True)

def collect_number_until_hash(prompt, max_len=24, min_len=1, mask=False):
    print(prompt); print('Type digits, *=backspace, #=confirm')
    buf = ''
    while True:
        k = get_key_once()
        if not k: time.sleep_ms(5); continue
        if k.isdigit():
            if len(buf) < max_len: buf += k; print(('*'*len(buf)) if mask else buf)
        elif k == '*':
            if buf: buf = buf[:-1]; print(('*'*len(buf)) if mask else (buf if buf else ''))
        elif k == '#':
            if len(buf) >= min_len: return buf

def wait_touch_hold_ms(hold_ms=2000):
    print('Touch and hold sensor for {:.1f} s...'.format(hold_ms/1000))
    while not touch_active(): time.sleep_ms(10)
    t0 = time.ticks_ms()
    while touch_active():
        if time.ticks_diff(time.ticks_ms(), t0) >= hold_ms:
            print('Touch hold verified.'); return True
        time.sleep_ms(10)
    print('Released too early; aborted.'); return False

# ---------- Account lookup WITHOUT index ----------
def find_account_node_by_ac(id_token, ac_no):
    keys_map = rtdb_get(ATM_USERS_BASE, id_token=id_token, query=['shallow=true'])
    if not isinstance(keys_map, dict) or len(keys_map) == 0: return None, None
    ac_no_str = str(ac_no)
    for k in keys_map.keys():
        node = rtdb_get(ATM_USERS_BASE + '/' + k, id_token=id_token)
        if isinstance(node, dict):
            try:
                if str(node.get(ACCOUNT_NUMBER_FIELD, "")) == ac_no_str: return k, node
            except Exception:
                pass
    return None, None

def get_field(paths_dict, default=''):
    for _, val in paths_dict.items():
        if val is not None: return val
    return default

# ---------- Tri-state verification helpers ----------
# ---------- Tri-state verification helpers ----------
def set_verify_state(state, id_token, node_key=None):
    """
    state: '0' = checking, '1' = success, '2' = fail
    2_Authentication_Verification → transient state (0/1/2 then reset)
    3_Authentication_Verified     → final result (0/1)
    """
    # Write live verification state
    rtdb_put_string(VERIFY_BASE, state, id_token=id_token)

    # Map to final pass/fail
    final_flag = '1' if state == '1' else '0'
    rtdb_put_string(VERIFY_STATUS_PATH, final_flag, id_token=id_token)

    if node_key is not None:
        rtdb_put_string(ATM_USERS_BASE + '/' + node_key + '/lastVerify', state, id_token=id_token)
        rtdb_put_string(ATM_USERS_BASE + '/' + node_key + '/verifiedFlag', final_flag, id_token=id_token)


def show_fail_then_idle(id_token, node_key=None, hold_ms=800):
    # Set to fail (2), keep for a while, then reset to checking (0)
    set_verify_state('2', id_token, node_key)
    time.sleep_ms(hold_ms)
    set_verify_state('0', id_token, node_key)

def show_fail_then_idle(id_token, node_key=None, hold_ms=800):
    set_verify_state('2', id_token, node_key)
    time.sleep_ms(hold_ms)
    set_verify_state('0', id_token, node_key)

# ---------- Balance helpers ----------
def read_balance_of_node(id_token, node_key):
    node = rtdb_get(ATM_USERS_BASE + '/' + node_key, id_token=id_token)
    bal = 0
    if isinstance(node, dict):
        val = node.get('balance', 0)
        try: bal = int(val)
        except Exception:
            try: bal = int(float(val))
            except Exception: bal = 0
    return bal

def set_insufficient_flag(val_str, id_token, node_key):
    rtdb_put_string(INSUFFICIENT_FLAG_PATH, val_str, id_token=id_token)
    rtdb_put_string(ATM_USERS_BASE + '/' + node_key + '/Insufficient_Balance', val_str, id_token=id_token)

# ---------- Menus ----------
def account_type_menu(account_code, id_token, node_key=None):
    """
    account_code: '1' = Savings, '2' = Current
    """
    rtdb_put_string(ATM_BASE + '/4_Account_Type', str(account_code), id_token=id_token)
    if node_key is not None:
        rtdb_put_string(ATM_USERS_BASE + '/' + node_key + '/accountType', str(account_code), id_token=id_token)
    print("Account type set to:", "Savings" if account_code == '1' else "Current")



# ---------- Amount flow WITH balance check + full global mirror ----------
def per_account_withdrawal_amount_flow(id_token, node_key):
    amount_path = ATM_USERS_BASE + '/' + node_key + '/Withdrawal_Amount'
    done_path   = ATM_USERS_BASE + '/' + node_key + '/Transaction_Completed'

    # Initialize both per-user and global mirrors
    rtdb_put_string(amount_path, '', id_token=id_token)
    rtdb_put_string(done_path,   '0', id_token=id_token)
    rtdb_put_string(WITHDRAWAL_AMOUNT_PATH, '', id_token=id_token)   # <— global mirror init
    set_insufficient_flag('0', id_token, node_key)

    print('\nEnter amount (digits).  *=backspace, #=confirm')
    buf = ''
    while True:
        k = get_key_once()
        if not k:
            time.sleep_ms(5); continue
        if k.isdigit():
            if len(buf) < 6:
                if len(buf) == 0 and k == '0':  # block leading zero
                    continue
                buf += k
                print(buf)
                rtdb_put_string(amount_path, buf, id_token=id_token)
                rtdb_put_string(WITHDRAWAL_AMOUNT_PATH, buf, id_token=id_token)  # <— mirror while typing
        elif k == '*':
            if buf:
                buf = buf[:-1]
                print(buf if buf else '')
                rtdb_put_string(amount_path, buf, id_token=id_token)
                rtdb_put_string(WITHDRAWAL_AMOUNT_PATH, buf, id_token=id_token)  # <— mirror on backspace
        elif k == '#':
            if len(buf) > 0:
                # final write of amount
                rtdb_put_string(amount_path, buf, id_token=id_token)
                rtdb_put_string(WITHDRAWAL_AMOUNT_PATH, buf, id_token=id_token)  # <— mirror on confirm

                # --- balance check ---
                try: amt = int(buf)
                except Exception: amt = 0

                bal = read_balance_of_node(id_token, node_key)
                print('Requested:', amt, 'Available balance:', bal)

                if amt <= 0:
                    print('Invalid amount.'); beep_for_ms(800, 120)
                    set_insufficient_flag('1', id_token, node_key)
                    return buf

                if amt <= bal:
                    # sufficient
                    set_insufficient_flag('0', id_token, node_key)
                    new_bal = bal - amt
                    rtdb_put_string(ATM_USERS_BASE + '/' + node_key + '/balance', str(new_bal), id_token=id_token)
                    print('Balance OK. New balance:', new_bal)
                    dispense_action(id_token, node_key)
                    return buf
                else:
                    # insufficient
                    print('Insufficient balance.')
                    set_insufficient_flag('1', id_token, node_key)
                    beep_for_ms(1200, 150)
                    # Do NOT dispense; leave Transaction_Completed = 0
                    return buf

def dispense_action(id_token=None, node_key=None):
    print('Dispensing...')
    servo_angle(180)
    beep_for_ms(5000, 200)
    servo_angle(0)
    if id_token is not None:
        if node_key:
            rtdb_put_string(ATM_USERS_BASE + '/' + node_key + '/Transaction_Completed', '1', id_token=id_token)
        rtdb_put_string(TX_COMPLETED_PATH, '1', id_token=id_token)
    print('Transaction completed for node', node_key)

# ---------- Verification (read from node) ----------
def local_verify_pin(id_token, node_key):
    pin_entered = collect_4_digits('PIN selected.')
    rtdb_put_string(PIN_PATH, pin_entered, id_token=id_token)
    node = rtdb_get(ATM_USERS_BASE + '/' + node_key, id_token=id_token)
    stored = get_field({
        'top':      node.get('PIN') if isinstance(node, dict) else None,
        'auth':     node.get('auth', {}).get('PIN') if isinstance(node, dict) and isinstance(node.get('auth'), dict) else None,
        'authData': node.get('authData', {}).get('pin') if isinstance(node, dict) and isinstance(node.get('authData'), dict) else None
    }, default='')
    stored = str(stored)
    if pin_entered == stored: print('PIN verification OK.'); return True
    print('PIN verification FAILED.'); beep_for_ms(1200, 150); return False

def local_verify_pattern(id_token, node_key):
    patt_entered = collect_4_digits('Pattern selected.')
    rtdb_put_string(PATTERN_PATH, patt_entered, id_token=id_token)
    node = rtdb_get(ATM_USERS_BASE + '/' + node_key, id_token=id_token)
    stored = get_field({
        'top':      node.get('Pattern') if isinstance(node, dict) else None,
        'auth':     node.get('auth', {}).get('Pattern') if isinstance(node, dict) else None,
        'authData': node.get('authData', {}).get('pattern') if isinstance(node, dict) and isinstance(node.get('authData'), dict) else None
    }, default='')
    stored = str(stored)
    if patt_entered == stored: print('Pattern verification OK.'); return True
    print('Pattern verification FAILED.'); beep_for_ms(1200, 150); return False

# ---------- 3-attempt wrappers ----------
def verify_touch_with_retries(id_token, node_key, max_attempts=3):
    attempts = 0
    set_verify_state('0', id_token, node_key)
    while attempts < max_attempts:
        attempts += 1
        print('Touch attempt {}/{}...'.format(attempts, max_attempts))
        ok = wait_touch_hold_ms(2000)
        if ok:
            set_verify_state('1', id_token, node_key)
            rtdb_put_string(FINGER_PATH, '1', id_token=id_token)
            return True, attempts
        else:
            rtdb_put_string(FINGER_PATH, '0', id_token=id_token)
            show_fail_then_idle(id_token, node_key)
            if attempts < max_attempts: beep_for_ms(600, 120)
    print('Touch verification failed after {} attempts.'.format(max_attempts))
    return False, attempts

def verify_pin_with_retries(id_token, node_key, max_attempts=3):
    attempts = 0
    set_verify_state('0', id_token, node_key)
    while attempts < max_attempts:
        attempts += 1
        print('PIN attempt {}/{}...'.format(attempts, max_attempts))
        ok = local_verify_pin(id_token, node_key)
        if ok:
            set_verify_state('1', id_token, node_key)
            return True, attempts
        else:
            show_fail_then_idle(id_token, node_key)
            if attempts < max_attempts: beep_for_ms(600, 120)
    print('PIN failed after {} attempts.'.format(max_attempts))
    return False, attempts

def verify_pattern_with_retries(id_token, node_key, max_attempts=3):
    attempts = 0
    set_verify_state('0', id_token, node_key)
    while attempts < max_attempts:
        attempts += 1
        print('Pattern attempt {}/{}...'.format(attempts, max_attempts))
        ok = local_verify_pattern(id_token, node_key)
        if ok:
            set_verify_state('1', id_token, node_key)
            return True, attempts
        else:
            show_fail_then_idle(id_token, node_key)
            if attempts < max_attempts: beep_for_ms(600, 120)
    print('Pattern failed after {} attempts.'.format(max_attempts))
    return False, attempts

def print_menu():
    print('\nSelect Authentication method via keypad:')
    print('  1 = Facial   (external)')
    print('  2 = Finger   (touch-hold 2s)')
    print('  3 = PIN      (4 digits, local verify)')
    print('  4 = Pattern  (4 digits, local verify)')
    print('Press one key (1–4).')

# ---------- Main ----------
def main():
    print('\n=== PICO ATM (funds check + mirrored amount) ===')
    wifi_connect()
    id_token = firebase_login()
    if id_token: print('Firebase auth OK')
    else:        print('No auth token; public rules required.')

    servo_angle(0); buzzer_off()

    if rtdb_put_string(WELCOME_PATH, '1', id_token=id_token if id_token else None):
        print('Welcome flag = 1')
    else:
         print('Welcome flag write failed (no auth?)')
    # Variable-length AC entry; '#' confirms
    ac_no = collect_number_until_hash('\nEnter Account Number:', max_len=24, min_len=1, mask=False)
    print('AC_NO entered:', ac_no)
    rtdb_put_string(AC_NO_PATH, ac_no, id_token=id_token)

    # Find the child under ATMusers where accountNumber == ac_no (no index)
    node_key, node_val = find_account_node_by_ac(id_token, ac_no)
    if not node_key:
        print('Account NOT found under ATMusers. Aborting.')
        show_fail_then_idle(id_token)
        beep_for_ms(1000, 100)
        return

    print('Account found. Node key =', node_key)

    # Menu + verification
    print_menu()
    last_written, welcome_is_one = None, True

    while True:
        k = get_key_once()
        if k in ('1','2','3','4') and k != last_written:
            if rtdb_put_string(SELECT_PATH, k, id_token=id_token):
                print('Selected:', k); last_written = k
                if welcome_is_one and rtdb_put_string(WELCOME_PATH, '0', id_token=id_token):
                    print('Welcome flag = 0'); welcome_is_one = False

                verified, attempts_used = False, 0
                if k == '1':
                    print('Facial selected (external verifier).')
                    set_verify_state('0', id_token, node_key)
                elif k == '2':
                    verified, attempts_used = verify_touch_with_retries(id_token, node_key, 3)
                elif k == '3':
                    verified, attempts_used = verify_pin_with_retries(id_token, node_key, 3)
                elif k == '4':
                    verified, attempts_used = verify_pattern_with_retries(id_token, node_key, 3)

                if verified:
                    account_type_menu(id_token, node_key)
                    per_account_withdrawal_amount_flow(id_token, node_key)
                else:
                    print('Not verified; ending attempt.')
                    rtdb_put_string(ATM_USERS_BASE + '/' + node_key + '/Last_Attempt', 'failed', id_token=id_token)
                    rtdb_put_string(ATM_USERS_BASE + '/' + node_key + '/Failed_Attempts', str(attempts_used), id_token=id_token)

                print_menu()
        time.sleep_ms(5)

# Auto-run
if __name__ == '__main__':
    main()

