from machine import Pin, I2C
from ssd1306 import SSD1306_I2C
import time
import urequests
import network

# WiFi Configuration
SSID = "traffic"
PASSWORD = "123456789"

# Firebase URL
FIREBASE_URL = "https://regal-welder-453313-d6-default-rtdb.firebaseio.com/5_KS5976_Traffic_Control.json"

# Connect to WiFi
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(SSID, PASSWORD)
    while not wlan.isconnected():
        time.sleep(0.5)
    print("Connected to WiFi:", wlan.ifconfig())

connect_wifi()

# I2C and PCA9548A Setup
i2c = I2C(0, scl=Pin(1), sda=Pin(0), freq=400000)
PCA_ADDR = 0x70
OLED_WIDTH = 128
OLED_HEIGHT = 64

def select_channel(ch):
    i2c.writeto(PCA_ADDR, bytes([1 << ch]))
    time.sleep(0.2)

# Initialize OLEDs
oleds = []
for ch in range(4):
    select_channel(ch)
    time.sleep(0.5)
    oled = SSD1306_I2C(OLED_WIDTH, OLED_HEIGHT, i2c)
    oleds.append(oled)

# Ultrasonic Sensor Pins
trig_pins = [Pin(2, Pin.OUT), Pin(4, Pin.OUT), Pin(6, Pin.OUT), Pin(8, Pin.OUT)]
echo_pins = [Pin(3, Pin.IN), Pin(5, Pin.IN), Pin(7, Pin.IN), Pin(9, Pin.IN)]

# Traffic Lights RGB LEDs (R, Y, G per lane)
led_red    = [Pin(10, Pin.OUT), Pin(13, Pin.OUT), Pin(16, Pin.OUT), Pin(19, Pin.OUT)]
led_yellow = [Pin(11, Pin.OUT), Pin(14, Pin.OUT), Pin(17, Pin.OUT), Pin(20, Pin.OUT)]
led_green  = [Pin(12, Pin.OUT), Pin(15, Pin.OUT), Pin(18, Pin.OUT), Pin(21, Pin.OUT)]

# Buzzer Pin for Priority Alert
buzzer = Pin(22, Pin.OUT)

# Default Traffic Light Cycle Durations
GREEN_DURATION = 30  # seconds
YELLOW_DURATION = 5  # seconds
RED_DURATION = 30    # seconds
PRIORITY_GREEN_DURATION = 45  # Extended green during priority

def read_distance(trig, echo):
    trig.low()
    time.sleep_us(2)
    trig.high()
    time.sleep_us(10)
    trig.low()
    
    start = time.ticks_us()
    while echo.value() == 0:
        if time.ticks_diff(time.ticks_us(), start) > 30000:
            return -1
    
    start = time.ticks_us()
    while echo.value() == 1:
        if time.ticks_diff(time.ticks_us(), start) > 30000:
            return -1
    
    end = time.ticks_us()
    pulse_time = end - start
    distance = (pulse_time * 0.0343) / 2
    return int(distance)

def update_oled(ch, distance, signal_color, countdown, priority_active):
    select_channel(ch)
    oled = oleds[ch]
    oled.fill(0)
    oled.text(f"Lane {ch+1}", 0, 0)
    oled.text(f"Dist: {distance} cm", 0, 20)
    oled.text(f"Signal: {signal_color}", 0, 30)
    oled.text(f"Countdown: {countdown}s", 0, 40)
    if priority_active and ch == priority_active:
        oled.text("PRIORITY ALERT!", 0, 50)
    oled.show()

def update_traffic_lights(active_lane, phase):
    for lane in range(4):
        if lane == active_lane:
            if phase == "GREEN":
                led_red[lane].low()
                led_yellow[lane].low()
                led_green[lane].high()
            elif phase == "YELLOW":
                led_red[lane].low()
                led_yellow[lane].high()
                led_green[lane].low()
            else:
                led_red[lane].high()
                led_yellow[lane].low()
                led_green[lane].low()
        else:
            led_red[lane].high()
            led_yellow[lane].low()
            led_green[lane].low()

def update_firebase(distances):
    data = {
        "Lane1": distances[0],
        "Lane2": distances[1],
        "Lane3": distances[2],
        "Lane4": distances[3],
        "timestamp": time.time()
    }
    
    try:
        response = urequests.put(FIREBASE_URL, json=data)
        print("Firebase response:", response.text)
        response.close()
    except Exception as e:
        print("Firebase update error:", e)

# Main Loop with Priority Mode
while True:
    priority_triggered = False
    priority_lane = -1
    
    # Step 1: Check if any lane needs priority mode
    for lane in range(4):
        distance = read_distance(trig_pins[lane], echo_pins[lane])
        if distance == -1:
            distance = 999
        if distance > 200:
            priority_triggered = True
            priority_lane = lane
            print(f"Priority mode activated for Lane {lane+1}")
            break  # Trigger priority mode immediately

    # Step 2: Priority Mode Logic
    if priority_triggered:
        buzzer.high()  # Turn buzzer ON during priority
        for remaining in range(PRIORITY_GREEN_DURATION, 0, -1):
            distances = []
            for ch in range(4):
                distance = read_distance(trig_pins[ch], echo_pins[ch])
                if distance == -1:
                    distance = 999
                distances.append(distance)
                signal_color = "GREEN" if ch == priority_lane else "RED"
                update_oled(ch, distance, signal_color, remaining, priority_active=priority_lane)
            
            update_traffic_lights(priority_lane, "GREEN")
            update_firebase(distances)
            time.sleep(1)
        buzzer.low()  # Turn buzzer OFF after priority mode

    # Step 3: Normal Timer-Based Cycle
    for active_lane in range(4):
        # GREEN Phase
        for remaining in range(GREEN_DURATION, 0, -1):
            distances = []
            for ch in range(4):
                distance = read_distance(trig_pins[ch], echo_pins[ch])
                if distance == -1:
                    distance = 999
                distances.append(distance)
                
                signal_color = "GREEN" if ch == active_lane else "RED"
                update_oled(ch, distance, signal_color, remaining, priority_active=None)
            
            update_traffic_lights(active_lane, "GREEN")
            update_firebase(distances)
            time.sleep(1)

        # YELLOW Phase
        for remaining in range(YELLOW_DURATION, 0, -1):
            distances = []
            for ch in range(4):
                distance = read_distance(trig_pins[ch], echo_pins[ch])
                if distance == -1:
                    distance = 999
                distances.append(distance)
                
                signal_color = "YELLOW" if ch == active_lane else "RED"
                update_oled(ch, distance, signal_color, remaining, priority_active=None)
            
            update_traffic_lights(active_lane, "YELLOW")
            update_firebase(distances)
            time.sleep(1)

