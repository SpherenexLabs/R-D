from machine import Pin, I2C
import network
import urequests
from ssd1306 import SSD1306_I2C
from time import sleep, localtime
from pca9548a import PCA9548A
import ntptime
import random  # For generating random temperature in range

# WiFi credentials
ssid = "face"
password = "123456789"

# Firebase URL
FIREBASE_URL = "https://regal-welder-453313-d6-default-rtdb.firebaseio.com/4_KS5108_Face_Detection.json"

# I2C and multiplexer
i2c = I2C(0, scl=Pin(1), sda=Pin(0), freq=400000)
mux = PCA9548A(i2c)

# Connect to WiFi
wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect(ssid, password)

print("Connecting to WiFi", end="")
while not wlan.isconnected():
    print(".", end="")
    sleep(1)
print("\n✅ Connected:", wlan.ifconfig())

# Sync RTC via NTP with retries
MAX_RETRIES = 5
for attempt in range(MAX_RETRIES):
    try:
        ntptime.host = 'time.google.com'
        ntptime.settime()
        print("🕒 RTC synced to UTC time")
        break
    except Exception as e:
        print(f"⚠️ NTP sync attempt {attempt+1} failed: {e}")
        sleep(5)
else:
    print("⚠️ NTP sync failed after several attempts")

# Get time in 12-hour format with IST (+5:30)
def get_rtc_time():
    t = localtime()
    hour = t[3]
    minute = t[4]
    second = t[5]

    # Apply IST offset (+5 hours 30 minutes)
    hour += 5
    minute += 30

    if minute >= 60:
        minute -= 60
        hour += 1

    if hour >= 24:
        hour -= 24

    suffix = "AM"
    if hour >= 12:
        suffix = "PM"
        if hour > 12:
            hour -= 12
    elif hour == 0:
        hour = 12

    return "{:02d}:{:02d}:{:02d} {}".format(hour, minute, second, suffix)

# Get data from Firebase (except weather)
def get_firebase_data():
    try:
        r = urequests.get(FIREBASE_URL)
        data = r.json()
        r.close()

        if not data:
            print("⚠️ No data found in Firebase")
            return {
                "face": "N/A",
                "percentage": "N/A",
                "stage": "N/A",
                "doctor": "N/A"
            }

        return {
            "face": data.get("face", "N/A"),
            "percentage": data.get("percentage", "N/A"),
            "stage": data.get("stage", "N/A"),
            "doctor": data.get("doctor", "N/A")
        }

    except Exception as e:
        print("Firebase read error:", e)
        return {
            "face": "N/A",
            "percentage": "N/A",
            "stage": "N/A",
            "doctor": "N/A"
        }

# Push data to Firebase
def push_to_firebase(data):
    headers = {'Content-Type': 'application/json'}
    try:
        r = urequests.put(FIREBASE_URL, json=data, headers=headers)
        print("✅ Updated Firebase:", r.text)
        r.close()
    except Exception as e:
        print("Firebase write error:", e)

# Large text simulation for title
def large_text(oled, text, x, y):
    for i, char in enumerate(text):
        xpos = x + i * 12
        oled.text(char, xpos, y)
        oled.text(char, xpos + 1, y)

# Generate fake weather between 25°C and 29°C
def get_fixed_weather():
    temp = random.randint(25, 29)
    return "{}C".format(temp)

# === MAIN LOOP ===
while True:
    time_now = get_rtc_time()
    fb_data = get_firebase_data()
    weather = get_fixed_weather()

    display_data = {
        "face": fb_data["face"],
        "percentage": fb_data["percentage"],
        "stage": fb_data["stage"],
        "doctor": fb_data["doctor"],
        "time": time_now,
        "weather": weather  # Fixed 25 to 29 °C
    }

    # Push updated data (with fixed weather and current time) to Firebase
    push_to_firebase(display_data)

    screens = [
        ("FACE", display_data["face"]),
        ("PERCENT", display_data["percentage"]),
        ("STAGE", display_data["stage"]),
        ("DOCTOR", display_data["doctor"]),
        ("TIME", display_data["time"]),
        ("WEATHER", display_data["weather"])
    ]

    for i, (title, value) in enumerate(screens):
        try:
            mux.channel(i)
            sleep(0.05)
            oled = SSD1306_I2C(128, 32, i2c)
            oled.fill(0)
            large_text(oled, title, 0, 0)
            oled.text(value, 0, 20)
            oled.show()
            sleep(3)
        except Exception as e:
            print(f"OLED error on channel {i}:", e)
            

