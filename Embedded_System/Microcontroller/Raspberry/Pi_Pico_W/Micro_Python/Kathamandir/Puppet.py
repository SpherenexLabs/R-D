
import network
import urequests
from machine import Pin, PWM
from time import sleep
# ---------- WiFi Credentials ----------
WIFI_SSID = "puppet"
WIFI_PASS = "123456789"
# ---------- Firebase Credentials ----------
API_KEY = "AIzaSyCZI9YQKgjibw0XiIicEXRJlyibr_B1G6c"
USER_EMAIL = "spherenexgpt@gmail.com"
USER_PASSWORD = "Spherenex@123"
# ---------- Firebase DB Path ----------
DB_PATH = "https://poppet-b8c23-default-rtdb.firebaseio.com/1_Puppet_1"
# ---------- Servo Mapping ----------
servo_map = {
    "1_Head": 2,
    "2_Leg": 13,
    "3_Right": {
        "R_FB": 4,
        "R_UD": 7
    },
    "4_Left": {
        "L_FB": 6,
        "L_UD": 5
    },
    "5_Arm": {
        "base_joint": 8,
        "elbow_joint": 9,
        "wrist_joint": 10,
        "gripper": 12
    }
}
servos = {}          # maps servo name to PWM object
last_angles = {}     # maps servo name to last known angle
id_token = ""
last_state = {}
# ---------- WiFi Connect ----------
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print("Connecting to WiFi...")
        wlan.connect(WIFI_SSID, WIFI_PASS)
        while not wlan.isconnected():
            sleep(0.5)
    print("WiFi Connected:", wlan.ifconfig())
# ---------- Firebase Auth ----------
def get_id_token():
    global id_token
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={API_KEY}"
    payload = {
        "email": USER_EMAIL,
        "password": USER_PASSWORD,
        "returnSecureToken": True
    }
    res = urequests.post(url, json=payload)
    id_token = res.json()["idToken"]
    print("Firebase Authenticated")
    res.close()
# ---------- Initialize Servos ----------
def setup_servos():
    for group, val in servo_map.items():
        if isinstance(val, dict):
            for subkey, gpio in val.items():
                pwm = PWM(Pin(gpio))
                pwm.freq(50)
                servos[subkey] = pwm
                last_angles[subkey] = 90
                set_angle(pwm, 90)
        else:
            pwm = PWM(Pin(val))
            pwm.freq(50)
            servos[group] = pwm
            last_angles[group] = 90
            set_angle(pwm, 90)
# ---------- Set Servo Angle ----------
def set_angle(pwm, angle):
    us = 500 + int((angle / 180) * 2000)  # 0° = 0.5ms, 180° = 2.5ms
    duty = int(us * 65535 / 20000)
    pwm.duty_u16(duty)
# ---------- Detect Firebase Changes ----------
def compare_nodes(current, previous, path=""):
    changes = []
    for key in current:
        full_path = f"{path}/{key}" if path else key
        if key not in previous:
            changes.append((full_path, current[key]))
        elif isinstance(current[key], dict) and isinstance(previous[key], dict):
            changes += compare_nodes(current[key], previous[key], full_path)
        elif current[key] != previous[key]:
            changes.append((full_path, current[key]))
    return changes
# ---------- Smooth Servo Movement ----------
def handle_servo(key, value):
    if key not in servos or not isinstance(value, str):
        return
    parts = value.split(",")
    try:
        target_angle = int(parts[0])
        delay_ms = int(parts[1]) if len(parts) == 2 else 0
        pwm = servos[key]
        current = last_angles.get(key, 90)
        step = 1 if target_angle > current else -1
        total_steps = abs(target_angle - current)
        delay_per_step = delay_ms / total_steps / 1000 if total_steps > 0 else 0
        print(f"[ACTION] Smooth move {key} from {current}° to {target_angle}°, delay: {delay_ms}ms")
        for angle in range(current, target_angle + step, step):
            set_angle(pwm, angle)
            sleep(delay_per_step)
        last_angles[key] = target_angle
    except Exception as e:
        print(f"[ERROR] {key}: {value} →", e)
# ---------- Main Loop ----------
def main_loop():
    global last_state
    while True:
        try:
            url = f"{DB_PATH}.json?auth={id_token}"
            res = urequests.get(url)
            data = res.json()
            res.close()
            changes = compare_nodes(data, last_state)
            for path, value in changes:
                print(f"[UPDATE] {path} → {value}")
                keys = path.split("/")
                if len(keys) == 1:
                    handle_servo(keys[0], value)
                elif len(keys) == 2:
                    handle_servo(keys[1], value)
            last_state = data
            sleep(0.5)
        except Exception as e:
            print("[ERROR]", e)
            sleep(3)
# ---------- Run ----------
connect_wifi()
get_id_token()
setup_servos()
main_loop()
