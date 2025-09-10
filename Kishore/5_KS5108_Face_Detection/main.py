from machine import Pin, I2C
import network
import urequests
from ssd1306 import SSD1306_I2C
from time import sleep, localtime
from pca9548a import PCA9548A
import ntptime 

# WiFi credentials
ssid = "face"
password = "123456789"

# Firebase URL (same for read and write, no auto-generated child IDs)
FIREBASE_URL = "https://regal-welder-453313-d6-default-rtdb.firebaseio.com/4_KS5108_Face_Detection.json"

# Weather API
WEATHER_API = "http://api.openweathermap.org/data/2.5/weather?q=London&appid=100495c4ea9e3964ab0b91a4c51bc773"

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

# Sync RTC via NTP
try:
    ntptime.settime()
    print("🕒 RTC synced")
except:
    print("⚠️ Failed to sync NTP time")

# Get time in 12-hour format
def get_rtc_time():
    t = localtime()
    hour = t[3]
    minute = t[4]
    second = t[5]
    suffix = "AM"
    if hour >= 12:
        suffix = "PM"
        if hour > 12:
            hour -= 12
    elif hour == 0:
        hour = 12
    return "{:02d}:{:02d}:{:02d} {}".format(hour, minute, second, suffix)

# Get only temperature
def get_temperature():
    try:
        r = urequests.get(WEATHER_API)
        data = r.json()
        r.close()
        temp = round(data["main"]["temp"] - 273.15, 1)
        return f"{temp}C"
    except Exception as e:
        print("Weather error:", e)
        return "N/A"

# Get data from Firebase
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

# Push data to Firebase (overwrite same node every time)
def push_to_firebase(data):
    headers = {'Content-Type': 'application/json'}
    try:
        r = urequests.put(FIREBASE_URL, json=data, headers=headers)
        print("✅ Uploaded to Firebase:", r.text)
        r.close()
    except Exception as e:
        print("Firebase write error:", e)

# Simulate large title font
def large_text(oled, text, x, y):
    for i, char in enumerate(text):
        xpos = x + i * 12
        oled.text(char, xpos, y)
        oled.text(char, xpos + 1, y)

# === MAIN LOOP ===
while True:
    weather = get_temperature()
    time_now = get_rtc_time()
    fb_data = get_firebase_data()

    display_data = {
        "face": fb_data["face"],
        "percentage": fb_data["percentage"],
        "stage": fb_data["stage"],
        "doctor": fb_data["doctor"],
        "time": time_now,
        "weather": weather
    }

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
