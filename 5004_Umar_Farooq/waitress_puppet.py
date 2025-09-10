from machine import Pin, PWM
from time import ticks_ms, sleep
import network
import urequests

# ==== DELAYS & SKIT TIMELINE ====
SKIT_DURATION_MS = 160_000   # Adjust as needed (ms)
action_windows = [
    (7000, 9000),   # Example: 7s to 9s
    # Add more as needed
]

# --- WiFi & Firebase setup ---
WIFI_SSID = "puppet"
WIFI_PASS = "123456789"
API_KEY = "AIzaSyCZI9YQKgjibw0XiIicEXRJlyibr_B1G6c"
USER_EMAIL = "spherenexgpt@gmail.com"
USER_PASSWORD = "Spherenex@123"
DB_PATH = "https://poppet-b8c23-default-rtdb.firebaseio.com/Puppet_ALL"

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print("Connecting to WiFi...")
        wlan.connect(WIFI_SSID, WIFI_PASS)
        while not wlan.isconnected():
            sleep(0.5)
    print("WiFi Connected:", wlan.ifconfig())

def get_id_token():
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={API_KEY}"
    payload = {
        "email": USER_EMAIL,
        "password": USER_PASSWORD,
        "returnSecureToken": True
    }
    res = urequests.post(url, json=payload)
    id_token = res.json()["idToken"]
    res.close()
    print("Firebase Authenticated")
    return id_token

def ensure_firebase_path(id_token):
    url = f"{DB_PATH}.json?auth={id_token}"
    res = urequests.get(url)
    data = res.json()
    res.close()
    if data is None or "start_stop" not in data:
        print("Creating Firebase start_stop path...")
        urequests.patch(url, json={"start_stop": 0}).close()
        print("Default start_stop = 0 set.")

def get_firebase_status(id_token):
    url = f"{DB_PATH}/start_stop.json?auth={id_token}"
    res = urequests.get(url)
    try:
        val = res.json()
    except:
        val = 0
    res.close()
    return int(val) if val is not None else 0

# --- Servo setups ---
head_servo = PWM(Pin(2)); head_servo.freq(50)
right_hand_fb_servo = PWM(Pin(4)); right_hand_fb_servo.freq(50)
left_hand_fb_servo = PWM(Pin(6)); left_hand_fb_servo.freq(50)
right_hand_ud_servo = PWM(Pin(7)); right_hand_ud_servo.freq(50)
left_hand_ud_servo = PWM(Pin(5)); left_hand_ud_servo.freq(50)
leg_servo = PWM(Pin(13)); leg_servo.freq(50)
gripper_servo = PWM(Pin(12)); gripper_servo.freq(50)  # Gripper
servo360 = PWM(Pin(8)); servo360.freq(50)             # 360-degree servo

def set_angle(pwm, angle):
    us = 500 + int((angle / 180) * 2000)
    duty = int(us * 65535 / 20000)
    pwm.duty_u16(duty)

def set_servo360_degree(degree):
    us = 500 + int((degree / 180) * 2000)
    duty = int(us * 65535 / 20000)
    servo360.duty_u16(duty)

def perform_action(action_ms, move_leg=True):
    head_state = right_hand_fb_state = left_hand_fb_state = right_hand_ud_state = left_hand_ud_state = leg_state = 0
    last_head_time = last_right_hand_fb_time = last_left_hand_fb_time = last_right_hand_ud_time = last_left_hand_ud_time = last_leg_time = ticks_ms()
    t_action = ticks_ms()
    while (ticks_ms() - t_action < action_ms):
        now = ticks_ms()
        if now - last_head_time > 200:
            angle = 180 if head_state % 2 == 0 else 70
            set_angle(head_servo, angle)
            head_state += 1
            last_head_time = now
        if now - last_right_hand_fb_time > 300:
            angle = 0 if right_hand_fb_state % 2 == 0 else 90
            set_angle(right_hand_fb_servo, angle)
            right_hand_fb_state += 1
            last_right_hand_fb_time = now
        if now - last_left_hand_fb_time > 300:
            angle = 90 if left_hand_fb_state % 2 == 0 else 0
            set_angle(left_hand_fb_servo, angle)
            left_hand_fb_state += 1
            last_left_hand_fb_time = now
        if now - last_right_hand_ud_time > 400:
            angle = 0 if right_hand_ud_state % 2 == 0 else 90
            set_angle(right_hand_ud_servo, angle)
            right_hand_ud_state += 1
            last_right_hand_ud_time = now
        if now - last_left_hand_ud_time > 400:
            angle = 90 if left_hand_ud_state % 2 == 0 else 0
            set_angle(left_hand_ud_servo, angle)
            left_hand_ud_state += 1
            last_left_hand_ud_time = now
        if move_leg and now - last_leg_time > 1000:
            angle = 0 if leg_state % 2 == 0 else 180
            set_angle(leg_servo, angle)
            leg_state += 1
            last_leg_time = now
        sleep(0.05)
    print("Action burst finished.")

def run_skit_1():
    print("SKIT 1: Moving Servo 360 (GPIO 8) to 120°")
    set_servo360_degree(120)
    sleep(2)  # Adjust duration for your servo speed
    set_servo360_degree(90)
    print("SKIT 1: Moving Gripper (GPIO 12) to 180°")
    set_angle(gripper_servo, 180)
    sleep(1)  # Adjust duration for your servo speed

    # === Play the skit ===
    t_start = ticks_ms()
    window = 0
    in_action = False

    while ticks_ms() - t_start < SKIT_DURATION_MS:
        elapsed = ticks_ms() - t_start
        if window < len(action_windows):
            start_ms, stop_ms = action_windows[window]
            if not in_action and elapsed >= start_ms:
                print(f"Starting action burst {window+1} at {elapsed/1000:.1f} sec")
                in_action = True
                perform_action(stop_ms - start_ms)
                print(f"Action burst {window+1} complete at {elapsed/1000:.1f} sec")
            if in_action and elapsed >= stop_ms:
                in_action = False
                window += 1
        sleep(0.05)

    # === After skit: Go to home positions ===
    print("After skit: Gripper (GPIO 12) to 90° (home)")
    set_angle(gripper_servo, 90)
    sleep(0.5)  # Adjust for your servo

    print("After skit: Servo 360 (GPIO 8) to 40° for 2s")
    set_servo360_degree(60)
    sleep(2)
    print("Servo 360 at home (0°)")
    set_servo360_degree(90)

def run_skit_2():
    print("SKIT 2: Gripper to 30°")
    set_angle(gripper_servo, 180)
    sleep(1)
    print("SKIT 2: DANCE with gripper at 30° (leg active)")
    perform_action(30000, move_leg=True)

    print("SKIT 2: Moving gripper from 30° to 90° (leg OFF during move)")
    for ang in range(90, 180, 5):
        set_angle(gripper_servo, ang)
        sleep(0.06)
    sleep(0.3)

    print("SKIT 2: DANCE with gripper at 90° (leg active)")
    perform_action(10000, move_leg=True)

    print("SKIT 2: Skit end, gripper to 60°")
    set_angle(gripper_servo, 90)
    sleep(0.5)
    print("SKIT 2: Complete (no servo 360 in this skit)")

# === MAIN ===
connect_wifi()
id_token = get_id_token()
ensure_firebase_path(id_token)

while True:
    print("Waiting for 'start_stop': 1 (Skit 1) or 2 (Skit 2) from Firebase...")
    while True:
        skit_cmd = get_firebase_status(id_token)
        if skit_cmd in (1,2):
            break
        sleep(0.2)
    print(f"Firebase start received! Command: {skit_cmd}")

    if skit_cmd == 1:
        run_skit_1()
    elif skit_cmd == 2:
        run_skit_2()
    else:
        print("Unknown skit command!")

    print("Skit finished. Waiting for Firebase to reset start_stop to 0...")
    while get_firebase_status(id_token) == skit_cmd:
        sleep(0.2)
    print("Ready for next Firebase command.")

