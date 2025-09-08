# Raspberry Pi Pico W — Option B (Split I2C buses)
# OLED on I2C1 (GP2/GP3, 400 kHz), MAX3010x on I2C0 (GP0/GP1, 100 kHz)
# Features: Wi-Fi, DHT11, SOUND/CO2/Alcohol, MAX HR/SpO2 (10 s calibration + latched),
# Firebase RTDB, OLED status page, serial print of HR/SpO2/BP, graceful Stop.

import network, time, sys, gc
try:
    import ujson as json
except ImportError:
    import json
from machine import Pin, I2C
try:
    import urequests as requests
except Exception:
    import requests
import dht

# Shorter socket timeouts help Thonny's Stop
try:
    import usocket as socket
    socket.setdefaulttimeout(3)
except Exception:
    pass

try:
    import ssd1306
except ImportError:
    raise RuntimeError("ssd1306.py missing. Upload the driver to the board.")

# ===================== USER CONFIG =====================
WIFI_SSID      = "health"
WIFI_PASSWORD  = "123456789"

API_KEY        = "AIzaSyAhLCi6JBT5ELkAFxTplKBBDdRdpATzQxI"
DATABASE_URL   = "https://smart-medicine-vending-machine-default-rtdb.asia-southeast1.firebasedatabase.app"
USER_EMAIL     = "spherenexgpt@gmail.com"
USER_PASSWORD  = "Spherenex@123"

FB_ROOT        = "/KS5160_Lung_Heart/1_Sensor_Data"
FB_ALERT_PATH  = "/KS5160_Lung_Heart/2_Notification/1_Alert"

# ===================== PINS =====================
# I2C1 → OLED (SSD1306 @ 0x3C)
I2C1_ID  = 1
SDA1_PIN = 2
SCL1_PIN = 3

# I2C0 → MAX3010x (0x57)
I2C0_ID  = 0
SDA0_PIN = 0
SCL0_PIN = 1

SOUND_PIN   = 12
CO2_PIN     = 13
ALCO_PIN    = 14
DHT_PIN     = 15

# OLED
OLED_W = 128
OLED_H = 64
OLED_I2C_ADDR = 0x3C

# MAX3010x
MAX3010X_I2C_ADDR = 0x57
FINGER_IR_THRESHOLD = 5000   # adjust for your module/skin

# Update periods (ms)
FB_CHECK_MS   = 200
ALERT_POLL_MS = 2000
OLED_MIN_MS   = 300

# Sound thresholds & codes
COUGH_BURST_COUNT = 1
TB_BURST_MIN      = 6
ASTHMATIC_MS      = 10000
BURST_GAP_MS      = 1200
DEBOUNCE_MS       = 80
SOUND_EVENT_MIN_GAP_MS = 2500
COUGH_CONFIRM_MS  = 6000

SOUND_NONE  = 0
SOUND_COUGH = 1
SOUND_TB    = 2
SOUND_ASTHMA= 3

# HR limits & calibration window
HR_MIN = 60
HR_MAX = 130
CAL_WINDOW_MS = 10000

# ===================== STATE/INIT =====================
sta = network.WLAN(network.STA_IF)
sta.active(True)

# ---- Split buses ----
i2c_oled = I2C(I2C1_ID, sda=Pin(SDA1_PIN), scl=Pin(SCL1_PIN), freq=400_000)   # OLED fast
i2c_max  = I2C(I2C0_ID, sda=Pin(SDA0_PIN), scl=Pin(SCL0_PIN), freq=100_000)   # MAX safer

print("I2C1 (OLED) scan:", [hex(x) for x in i2c_oled.scan()], "  (expect ['0x3c'])")
print("I2C0 (MAX)  scan:", [hex(x) for x in i2c_max.scan()],  "  (expect ['0x57'])")

oled = ssd1306.SSD1306_I2C(OLED_W, OLED_H, i2c_oled, addr=OLED_I2C_ADDR)

sound_pin = Pin(SOUND_PIN, Pin.IN, Pin.PULL_UP)
co2_pin   = Pin(CO2_PIN,   Pin.IN, Pin.PULL_UP)
alco_pin  = Pin(ALCO_PIN,  Pin.IN, Pin.PULL_UP)
_sensor_dht = dht.DHT11(Pin(DHT_PIN))

# ===== Minimal MAX3010x driver (SpO2 mode: Red + IR) =====
class MAX3010X:
    REG_INTR_STATUS_1   = 0x00
    REG_INTR_STATUS_2   = 0x01
    REG_INTR_ENABLE_1   = 0x02
    REG_INTR_ENABLE_2   = 0x03
    REG_FIFO_WR_PTR     = 0x04
    REG_OVF_COUNTER     = 0x05
    REG_FIFO_RD_PTR     = 0x06
    REG_FIFO_DATA       = 0x07
    REG_FIFO_CONFIG     = 0x08
    REG_MODE_CONFIG     = 0x09
    REG_SPO2_CONFIG     = 0x0A
    REG_LED1_PA         = 0x0C  # RED
    REG_LED2_PA         = 0x0D  # IR
    REG_LED3_PA         = 0x0E  # GREEN (unused on 30102)
    REG_PART_ID         = 0xFF

    def __init__(self, i2c, addr=0x57):
        self.i2c = i2c; self.addr = addr
        self._ir = 0; self._red = 0

    def _w(self, reg, val): self.i2c.writeto_mem(self.addr, reg, bytes([val & 0xFF]))
    def _r(self, reg, n=1): return self.i2c.readfrom_mem(self.addr, reg, n)

    def begin(self):
        try:
            # Soft reset & clear status
            self._w(self.REG_MODE_CONFIG, 0x40); time.sleep_ms(100)
            self._r(self.REG_INTR_STATUS_1, 1); self._r(self.REG_INTR_STATUS_2, 1)
            # Flush FIFO
            self._w(self.REG_FIFO_WR_PTR, 0); self._w(self.REG_OVF_COUNTER, 0); self._w(self.REG_FIFO_RD_PTR, 0)
            # FIFO: avg=4, rollover, almost_full=15
            self._w(self.REG_FIFO_CONFIG, (2<<5) | 0x0F)
            # SPO2: 4096 nA, 100 sps, 411 us
            self._w(self.REG_SPO2_CONFIG, (3<<5) | (1<<2) | 3)
            # Mode: SpO2 (RED+IR)
            self._w(self.REG_MODE_CONFIG, 0x03)
            # LED currents (moderate)
            self._w(self.REG_LED1_PA, 0x24); self._w(self.REG_LED2_PA, 0x24); self._w(self.REG_LED3_PA, 0x00)
            # Probe ID (don’t hard-fail)
            _ = self._r(self.REG_PART_ID, 1)[0]
        except OSError:
            return False
        return True

    def read_sample(self):
        d = self._r(self.REG_FIFO_DATA, 6)
        red = ((d[0]<<16)|(d[1]<<8)|d[2]) & 0x03FFFF
        ir  = ((d[3]<<16)|(d[4]<<8)|d[5]) & 0x03FFFF
        self._red, self._ir = red, ir
        return red, ir

    def getIR(self):  return self._ir
    def getRed(self): return self._red

    def shutdown(self):
        try:
            self._w(self.REG_LED1_PA, 0x00); self._w(self.REG_LED2_PA, 0x00); self._w(self.REG_LED3_PA, 0x00)
            self._w(self.REG_MODE_CONFIG, 0x80)  # SHDN
        except Exception:
            pass

# Instantiate MAX on I2C0
particle = MAX3010X(i2c_max, MAX3010X_I2C_ADDR)
max30105_ok = particle.begin()
print("MAX3010x:", "OK" if max30105_ok else "NOT FOUND")

# ==== Firebase Auth (Email/Password) ====
_id_token = None
_token_obtained_s = 0
IDENTITY_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=" + API_KEY

def fb_login():
    global _id_token, _token_obtained_s
    payload = {"email": USER_EMAIL, "password": USER_PASSWORD, "returnSecureToken": True}
    headers = {"Content-Type": "application/json"}
    r = requests.post(IDENTITY_URL, data=json.dumps(payload), headers=headers)
    if r.status_code != 200:
        msg = r.text; r.close()
        raise RuntimeError("Firebase login failed: {} {}".format(r.status_code, msg))
    j = r.json(); r.close()
    _id_token = j["idToken"]; _token_obtained_s = time.time()

def fb_auth_token():
    if (not _id_token) or (time.time() - _token_obtained_s) > 3300:
        fb_login()
    return _id_token

def fb_set(path, value):
    token = fb_auth_token()
    url = "{}/{}.json?auth={}".format(DATABASE_URL.rstrip('/'), path.strip('/'), token)
    r = requests.put(url, data=json.dumps(value)); r.close()

def fb_get(path):
    token = fb_auth_token()
    url = "{}/{}.json?auth={}".format(DATABASE_URL.rstrip('/'), path.strip('/'), token)
    r = requests.get(url)
    if r.status_code != 200:
        r.close(); return None
    v = r.json(); r.close(); return v

# ===================== RUNTIME STATE =====================
_last_oled_ms = 0
_alert_text = "-"

_sound_count = 0; _in_burst = False; _burst_last_ms = 0; _last_sound_ms = 0
_last_sound_event_ms = 0; _last_sound_event_code = SOUND_NONE
_last_sound_write_ms = 0; _last_sent_sound = 0
_pending_cough = False; _pending_cough_ms = 0

heartRate_live = 0; spo2_live = 0; systolic_live = 0; diastolic_live = 0
heartRate_latched = 0; spo2_latched = 0; systolic_latched = 0; diastolic_latched = 0; have_latched = False

last_ir = 0; hr_peak = False; last_beat_ms = 0; tempHR = 80

last_BP_ms = 0; target_sys = 118; target_dia = 78; cur_sys = 120; cur_dia = 80

class VitalsState: NO_FINGER=0; CALIBRATING=1; FROZEN=2
vitals_state = VitalsState.NO_FINGER; cal_start_ms = 0
sumHR=0; cntHR=0; sumSp=0; cntSp=0; sumSy=0; cntSy=0; sumDi=0; cntDi=0

_last_co2=-1; _last_alc=-1; _last_temp=-1000; _last_hum=-1000
_last_sys=-1; _last_dia=-1; _last_hr=-1; _last_sp=-1

_last_vitals_print_ms = 0

# ===================== UI =====================
def oled_splash():
    oled.fill(0); y=0
    oled.text("Lungs & Heart", 0, y); y+=12
    oled.text("Disease Analysis", 0, y); y+=12
    oled.text("Initializing...", 0, y); oled.show()

def oled_wifi_connecting(ssid, dots):
    oled.fill(0); y=0
    oled.text("WiFi:", 0, y); y+=12
    oled.text("SSID: "+ssid, 0, y); y+=12
    oled.text("Connecting"+"."*dots, 0, y); oled.show()

def oled_wifi_connected(ip):
    oled.fill(0); y=0
    oled.text("WiFi Connected!", 0, y); y+=12
    oled.text("IP:", 0, y); oled.text(str(ip), 24, y); oled.show()

def oled_stopped():
    try:
        oled.fill(0); oled.text("Stopped.", 0, 0); oled.show()
    except Exception:
        pass

def update_oled(temperature, humidity):
    global _last_oled_ms, vitals_state, cal_start_ms, have_latched, _alert_text
    now = time.ticks_ms()
    if time.ticks_diff(now, _last_oled_ms) < OLED_MIN_MS: return
    _last_oled_ms = now

    LEFT_X = 0; RIGHT_X = 68; y=0
    oled.fill(0)
    if vitals_state == VitalsState.CALIBRATING:
        secs = time.ticks_diff(now, cal_start_ms)//1000
        oled.text("CALIBRATION {}/10s".format(secs), LEFT_X, y)
    elif have_latched:
        oled.text("CALIBRATION OK", LEFT_X, y)
    else:
        oled.text("READY", LEFT_X, y)
    y+=12

    oled.text("HR:", LEFT_X, y)
    oled.text(str(heartRate_latched)+"bpm" if (have_latched and heartRate_latched>0) else "NA", LEFT_X+22, y)
    oled.text("SpO2:", RIGHT_X, y)
    oled.text(str(spo2_latched)+"%" if (have_latched and spo2_latched>0) else "NA", RIGHT_X+30, y)
    y+=12

    oled.text("BP:", LEFT_X, y)
    if have_latched and systolic_latched>0 and diastolic_latched>0:
        oled.text("{}/{} mmHg".format(systolic_latched, diastolic_latched), LEFT_X+20, y)
    else:
        oled.text("NA", LEFT_X+20, y)
    y+=12

    oled.text("T:", LEFT_X, y)
    oled.text("{:.1f} C".format(temperature) if temperature is not None else "NA", LEFT_X+16, y)
    oled.text("H:", RIGHT_X, y)
    oled.text("{:.1f} %".format(humidity) if humidity is not None else "NA", RIGHT_X+16, y)
    y+=12

    oled.text("Alert :", LEFT_X, y)
    msg = _alert_text if _alert_text else "-"
    if len(msg)>14: msg = msg[:14]
    oled.text(msg, LEFT_X+38, y)
    oled.show()

# ===================== SENSORS =====================
def read_dht_safely():
    try:
        _sensor_dht.measure()
        return float(_sensor_dht.temperature()), float(_sensor_dht.humidity())
    except Exception:
        return None, None

def simulate_bp():
    global last_BP_ms, target_sys, target_dia, cur_sys, cur_dia, heartRate_live, systolic_live, diastolic_live
    now = time.ticks_ms()
    if time.ticks_diff(now, last_BP_ms) > 10000:
        hrAdj = 0
        if heartRate_live > 0:
            hrAdj = max(-20, min(55, heartRate_live - 75))
        target_sys = 118 + (hrAdj // 6) + _rand_range(-3, 3)
        target_dia = 78  + _rand_range(-2, 2)
        last_BP_ms = now
    if cur_sys < target_sys: cur_sys += 1
    elif cur_sys > target_sys: cur_sys -= 1
    if cur_dia < target_dia: cur_dia += 1
    elif cur_dia > target_dia: cur_dia -= 1
    systolic_live = cur_sys; diastolic_live = cur_dia

_rng = 1234567
def _rand():
    global _rng
    _rng = (1103515245*_rng + 12345) & 0x7FFFFFFF
    return _rng
def _rand_range(a,b):
    return a + (_rand() % (b - a + 1))

def read_max3010x_vitals():
    """Read one sample from MAX (on I2C0), update live HR/SpO2, simulate BP."""
    global last_ir, hr_peak, last_beat_ms, tempHR, heartRate_live, spo2_live
    if not max30105_ok:
        heartRate_live = 0; spo2_live = 0; return
    try:
        red, ir = particle.read_sample()
    except OSError:
        return

    if ir < FINGER_IR_THRESHOLD:
        heartRate_live = 0; spo2_live = 0; last_ir = ir; return

    # Simple peak detection -> HR
    if ir > last_ir and not hr_peak:
        hr_peak = True
    elif ir < last_ir and hr_peak:
        now = time.ticks_ms()
        ibi = time.ticks_diff(now, last_beat_ms)
        if 300 < ibi < 2000:
            newHR = int(60000 / ibi)
            if HR_MIN <= newHR <= HR_MAX:
                tempHR = newHR
        last_beat_ms = now; hr_peak = False
    last_ir = ir

    heartRate_live = tempHR if (HR_MIN <= tempHR <= HR_MAX) else 0

    # Rough SpO2 (ratio-of-ratios)
    spo2_live = 0
    if ir > 0:
        ratio = red / ir
        est = 110 - int(25.0 * ratio)
        if est < 90: est = 90
        if est > 99: est = 99
        spo2_live = est

    simulate_bp()

# ===================== CALIBRATION FSM =====================
class VS: NO_FINGER=0; CALIBRATING=1; FROZEN=2

def update_calibration_state(finger_present):
    global vitals_state, cal_start_ms, have_latched
    global sumHR, cntHR, sumSp, cntSp, sumSy, cntSy, sumDi, cntDi
    global heartRate_latched, spo2_latched, systolic_latched, diastolic_latched

    now = time.ticks_ms()
    if vitals_state == VS.NO_FINGER:
        if finger_present:
            vitals_state = VS.CALIBRATING
            cal_start_ms = now
            sumHR=cntHR=sumSp=cntSp=sumSy=cntSy=sumDi=cntDi=0
            print("⏱️ Calibration started (10s)...")
    elif vitals_state == VS.CALIBRATING:
        if not finger_present:
            vitals_state = VS.NO_FINGER; print("⚠️ Finger removed — calibration aborted.")
        else:
            if heartRate_live>0: sumHR += heartRate_live; cntHR += 1
            if spo2_live>0:      sumSp += spo2_live;      cntSp += 1
            if systolic_live>0 and diastolic_live>0:
                sumSy += systolic_live; cntSy += 1
                sumDi += diastolic_live; cntDi += 1
            if time.ticks_diff(now, cal_start_ms) >= CAL_WINDOW_MS:
                heartRate_latched = (sumHR + (cntHR//2))//cntHR if cntHR>0 else 0
                if not (HR_MIN <= heartRate_latched <= HR_MAX): heartRate_latched = 0
                spo2_latched = (sumSp + (cntSp//2))//cntSp if cntSp>0 else 0
                if cntSy>0 and cntDi>0:
                    systolic_latched  = (sumSy + (cntSy//2))//cntSy
                    diastolic_latched = (sumDi + (cntDi//2))//cntDi
                else:
                    systolic_latched = diastolic_latched = 0
                have_latched = True; vitals_state = VS.FROZEN
                print("✅ Calibration complete — values latched.")
    elif vitals_state == VS.FROZEN:
        if not finger_present:
            vitals_state = VS.NO_FINGER; print("👆 Finger removed — holding last calibrated values.")

# ===================== SOUND CLASSIFICATION =====================
def classify_and_reset_burst():
    global _in_burst, _sound_count, _pending_cough, _pending_cough_ms
    global _last_sound_event_ms, _last_sound_event_code
    if not _in_burst: return
    if _sound_count >= TB_BURST_MIN:
        print("🔴 Detected: TB (>= {} highs)".format(TB_BURST_MIN))
        _last_sound_event_ms = time.ticks_ms(); _last_sound_event_code = SOUND_TB
        if _pending_cough: _pending_cough = False; print("↪️ TB overrides pending cough")
    elif _sound_count == COUGH_BURST_COUNT:
        if not _pending_cough:
            _pending_cough = True; _pending_cough_ms = time.ticks_ms()
            print("🟠 Cough candidate ({} highs) — checking for TB...".format(COUGH_BURST_COUNT))
    elif _sound_count == 1 or (_sound_count>COUGH_BURST_COUNT and _sound_count<TB_BURST_MIN):
        print("ℹ️ Sound burst: {} highs (no label)".format(_sound_count))
    _sound_count = 0; _in_burst = False

# ===================== WIFI =====================
def oled_wifi_connecting(ssid, dots):
    oled.fill(0); y=0
    oled.text("WiFi:", 0, y); y+=12
    oled.text("SSID: "+ssid, 0, y); y+=12
    oled.text("Connecting"+"."*dots, 0, y); oled.show()

def oled_wifi_connected(ip):
    oled.fill(0); y=0
    oled.text("WiFi Connected!", 0, y); y+=12
    oled.text("IP:", 0, y); oled.text(str(ip), 24, y); oled.show()

def wifi_connect():
    oled.fill(0); oled.text("Initializing...", 0, 0); oled.show()
    if not sta.isconnected():
        sta.connect(WIFI_SSID, WIFI_PASSWORD)
        tries = 0; dots = 0
        while not sta.isconnected() and tries < 80:
            oled_wifi_connecting(WIFI_SSID, dots); time.sleep(0.25)
            dots = (dots+1) % 4; tries += 1
    if sta.isconnected():
        ip = sta.ifconfig()[0]; print("WiFi OK, IP:", ip); oled_wifi_connected(ip); time.sleep(1.2); return True
    print("WiFi FAILED"); return False

# ===================== FIREBASE PUSH (change-only) =====================
def push_if_changed(co2Flag, alcoholFlag, soundCode, temp_i, hum_i, sys_i, dia_i, hr_i, sp_i):
    global _last_co2, _last_alc, _last_temp, _last_hum, _last_sys, _last_dia, _last_hr, _last_sp
    if co2Flag != _last_co2: fb_set(FB_ROOT + "/1_co2", str(co2Flag)); _last_co2 = co2Flag
    if alcoholFlag != _last_alc: fb_set(FB_ROOT + "/2_alcohol", str(alcoholFlag)); _last_alc = alcoholFlag
    if temp_i != _last_temp: fb_set(FB_ROOT + "/3_temp", str(temp_i)); _last_temp = temp_i
    if hum_i  != _last_hum:  fb_set(FB_ROOT + "/4_hum",  str(hum_i));  _last_hum  = hum_i
    if dia_i  != _last_dia:  fb_set(FB_ROOT + "/5_bp/1_diastolic", str(dia_i)); _last_dia = dia_i
    if sys_i  != _last_sys:  fb_set(FB_ROOT + "/5_bp/2_systolic",  str(sys_i)); _last_sys = sys_i
    if hr_i   != _last_hr:   fb_set(FB_ROOT + "/6_hr",   str(hr_i));  _last_hr   = hr_i
    if sp_i   != _last_sp:   fb_set(FB_ROOT + "/7_spo2", str(sp_i));  _last_sp   = sp_i
    if soundCode != SOUND_NONE: fb_set(FB_ROOT + "/8_sound", str(soundCode))

# ===================== SERIAL VITALS PRINT =====================
def print_vitals_serial(finger_present):
    global _last_vitals_print_ms
    now = time.ticks_ms()
    if time.ticks_diff(now, _last_vitals_print_ms) < 200:
        return
    _last_vitals_print_ms = now

    if finger_present and have_latched and \
       (heartRate_latched > 0) and (spo2_latched > 0) and \
       (systolic_latched > 0) and (diastolic_latched > 0):
        hr  = heartRate_latched
        sp  = spo2_latched
        sysv = systolic_latched
        diav = diastolic_latched
    else:
        hr = sp = sysv = diav = 0

    print("HR:{:>3d}  SpO2:{:>2d}  BP:{:>3d}/{:>3d}".format(hr, sp, sysv, diav))

# ===================== CLEANUP =====================
def cleanup():
    try:
        if max30105_ok:
            particle.shutdown()
    except Exception:
        pass
    try:
        oled_stopped()
    except Exception:
        pass
    try:
        sta.disconnect()
    except Exception:
        pass
    gc.collect()

# ===================== MAIN =====================
def main():
    global _alert_text, _in_burst, _sound_count, _burst_last_ms, _last_sound_ms
    global _pending_cough, _pending_cough_ms, _last_sound_event_ms, _last_sound_event_code
    global _last_sound_write_ms, _last_sent_sound

    if not wifi_connect(): return

    try:
        fb_login(); print("Firebase connected.")
    except Exception as e:
        print("Firebase NOT ready:", e)

    last_fb_ms = time.ticks_ms(); last_alert_ms = time.ticks_ms()
    print("🌡️ Pico W Environmental + Sound + Vitals Monitor Ready")

    try:
        while True:
            now = time.ticks_ms()
            # SOUND
            if sound_pin.value() == 1:
                if time.ticks_diff(now, _burst_last_ms) > DEBOUNCE_MS:
                    if not _in_burst: _in_burst = True; _sound_count = 0
                    _sound_count += 1; _burst_last_ms = now; _last_sound_ms = now
                    if _sound_count >= TB_BURST_MIN:
                        print("🔴 Detected: TB (>= {} highs)".format(TB_BURST_MIN))
                        _last_sound_event_ms = now; _last_sound_event_code = SOUND_TB
                        if _pending_cough: _pending_cough = False; print("↪️ TB overrides pending cough")
                        _sound_count = 0; _in_burst = False
            if _in_burst and time.ticks_diff(now, _burst_last_ms) > BURST_GAP_MS: classify_and_reset_burst()
            if _last_sound_ms and time.ticks_diff(now, _last_sound_ms) >= ASTHMATIC_MS:
                print("⚠️ Possible Asthmatic (no sound ≥ 10s)")
                _last_sound_event_ms = now; _last_sound_event_code = SOUND_ASTHMA
                if _pending_cough: _pending_cough = False; print("↪️ Asthma cancels pending cough")
                classify_and_reset_burst(); _last_sound_ms = now
            if _pending_cough and time.ticks_diff(now, _pending_cough_ms) >= COUGH_CONFIRM_MS:
                _last_sound_event_ms = time.ticks_ms(); _last_sound_event_code = SOUND_COUGH
                _pending_cough = False; print("🟠 Cough confirmed (no TB within window)")

            # CO2 & Alcohol (active-LOW)
            co2Flag = 1 if (co2_pin.value()==0) else 0
            alcFlag = 1 if (alco_pin.value()==0) else 0

            # DHT11
            t, h = read_dht_safely()
            if t is not None and h is not None:
                print("🌡️ Temp: {:.1f} °C | 💧 Humidity: {:.1f} %".format(t, h))
            else:
                print("❌ Failed to read from DHT11 sensor!")

            # Vitals
            read_max3010x_vitals()
            finger_present = max30105_ok and (particle.getIR() >= FINGER_IR_THRESHOLD)
            update_calibration_state(finger_present)

            # Serial vitals (latched)
            print_vitals_serial(finger_present)

            # OLED
            update_oled(t, h)

            # Firebase: read alert text
            if time.ticks_diff(now, last_alert_ms) >= ALERT_POLL_MS:
                last_alert_ms = now
                try:
                    v = fb_get(FB_ALERT_PATH)
                    if v is None: v = "-"
                    if not isinstance(v, str): v = str(v)
                    v = v.strip() or "-"
                    if v != _alert_text:
                        _alert_text = v
                        print("Firebase Alert:", _alert_text)
                except Exception as e:
                    print("Alert poll error:", e)

            # Firebase: change-only push
            if time.ticks_diff(now, last_fb_ms) >= FB_CHECK_MS:
                last_fb_ms = now
                sound_to_write = SOUND_NONE
                if _last_sound_event_code != SOUND_NONE:
                    allow_same = time.ticks_diff(now, _last_sound_write_ms) >= SOUND_EVENT_MIN_GAP_MS
                    if _last_sound_event_code != _last_sent_sound or allow_same:
                        sound_to_write = _last_sound_event_code
                temp_i = int(t+0.5) if t is not None else 0
                hum_i  = int(h+0.5) if h is not None else 0
                sys_i = systolic_latched if have_latched else 0
                dia_i = diastolic_latched if have_latched else 0
                hr_i  = heartRate_latched if have_latched else 0
                sp_i  = spo2_latched if have_latched else 0
                try:
                    push_if_changed(co2Flag, alcFlag, sound_to_write, temp_i, hum_i, sys_i, dia_i, hr_i, sp_i)
                    if sound_to_write != SOUND_NONE:
                        _last_sound_write_ms = now; _last_sent_sound = sound_to_write
                except Exception as e:
                    print("Firebase push error:", e)

            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\nKeyboardInterrupt — cleaning up…")
        cleanup()
        raise SystemExit

    except Exception as e:
        print("\nUnhandled error:", e)
        cleanup()
        raise

# ============== ENTRY ==============
if __name__ == "__main__":
    try:
        oled.fill(0); oled.text("Starting...", 0, 0); oled.show()
        print("Starting…")
        # Expect I2C scans to show 0x3c on I2C1 and 0x57 on I2C0
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
        cleanup()
        sys.exit(0)

