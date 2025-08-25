# File: pico_l298_metal_ultra_autostart.py
# Board: Raspberry Pi Pico / Pico W
# Func: L298N serial drive (F/B/L/R/S) + Metal sensor + Ultrasonic stop@15cm
#       Behavior: If METAL detected OR distance <= 15 cm -> AUTO-STOP.
#                 When clear -> AUTO-START last motion (or default 'F').

from machine import Pin, time_pulse_us
from time import ticks_ms, ticks_diff, sleep_ms, sleep_us
import sys

# ---------------- Motor pins ----------------
IN1 = Pin(12, Pin.OUT)  # Left motor: IN1
IN2 = Pin(13, Pin.OUT)  # Left motor: IN2
IN3 = Pin(14, Pin.OUT)  # Right motor: IN3
IN4 = Pin(15, Pin.OUT)  # Right motor: IN4

# ---------------- Metal sensor ----------------
SENSOR_PIN = 2
ACTIVE_LOW = True  # True for NPN-NO (active LOW). False for PNP-NO (active HIGH via level shift)

if ACTIVE_LOW:
    metal_pin = Pin(SENSOR_PIN, Pin.IN, Pin.PULL_UP); METAL_ACTIVE_LEVEL = 0
else:
    metal_pin = Pin(SENSOR_PIN, Pin.IN, Pin.PULL_DOWN); METAL_ACTIVE_LEVEL = 1

# ---------------- Ultrasonic (HC-SR04 style) ----------------
TRIG_PIN = 4
ECHO_PIN = 5
ULTRA_STOP_CM   = 15.0   # stop threshold
ULTRA_TIMEOUT_US = 30000  # echo timeout (~5 m max)

trig = Pin(TRIG_PIN, Pin.OUT, value=0)
echo = Pin(ECHO_PIN, Pin.IN)

# ---------------- Status LED ----------------
LED = Pin("LED", Pin.OUT)

# ---------------- Debounce for metal ----------------
SAMPLE_MS = 5
METAL_STABLE_MS = 25
_m_last_raw = metal_pin.value()
_m_stable = _m_last_raw
_m_last_change = ticks_ms()

# ---------------- Ultrasonic sampling ----------------
ULTRA_SAMPLE_MS = 100
_ultra_last_ms = 0
_ultra_last_cm = None

# ---------------- Auto-stop/auto-start state ----------------
metal_stop_active = (_m_stable == METAL_ACTIVE_LEVEL)
ultra_stop_active = False
auto_stop_active = metal_stop_active or ultra_stop_active

last_motion_cmd = None        # remembers last non-stop motion: 'F','B','L','R'
AUTO_START_DEFAULT = 'F'      # if no previous command, go forward by default
LED.value(1 if auto_stop_active else 0)

# ---------------- Optional non-blocking serial input ----------------
try:
    import uselect
    poll = uselect.poll()
    poll.register(sys.stdin, uselect.POLLIN)
    HAVE_USELECT = True
except Exception:
    HAVE_USELECT = False

def read_cmd_nonblocking():
    if not HAVE_USELECT:
        return None
    if poll.poll(0):
        ch = sys.stdin.read(1)
        if ch:
            ch = ch.strip()
            if ch:
                return ch.upper()
    return None

def read_line_blocking():
    try:
        line = sys.stdin.readline()
        if not line:
            return None
        line = line.strip().upper()
        return line if line else None
    except Exception:
        return None

# ---------------- Motor helpers ----------------
def left_motor(dir_):
    # dir_: +1 forward, -1 backward, 0 stop
    if dir_ > 0:   IN1.value(1); IN2.value(0)
    elif dir_ < 0: IN1.value(0); IN2.value(1)
    else:          IN1.value(0); IN2.value(0)

def right_motor(dir_):
    # dir_: +1 forward, -1 backward, 0 stop
    if dir_ > 0:   IN3.value(1); IN4.value(0)
    elif dir_ < 0: IN3.value(0); IN4.value(1)
    else:          IN3.value(0); IN4.value(0)

def stop():
    left_motor(0); right_motor(0)

def forward():
    left_motor(+1); right_motor(+1)

def backward():
    left_motor(-1); right_motor(-1)

def left_pivot():
    left_motor(+1); right_motor(-1)

def right_pivot():
    left_motor(-1); right_motor(+1)

def apply_motion(cmd):
    if cmd == 'F': forward()
    elif cmd == 'B': backward()
    elif cmd == 'L': left_pivot()
    elif cmd == 'R': right_pivot()
    else: stop()

def handle_cmd(c):
    global last_motion_cmd
    if c == 'S':
        stop()
        last_motion_cmd = None
        print("→ STOP")
        return
    if c in ('F','B','L','R'):
        last_motion_cmd = c
        if auto_stop_active:
            print("! QUEUED {} (blocked)".format(c))
        else:
            apply_motion(c)
            print("→", {"F":"FORWARD","B":"BACKWARD","L":"LEFT","R":"RIGHT"}[c])
    else:
        print("Unknown cmd:", c, "| Use F,B,L,R,S")

# ---------------- Sensors update ----------------
def update_metal_state():
    global _m_last_raw, _m_last_change, _m_stable, metal_stop_active
    v = metal_pin.value()
    if v != _m_last_raw:
        _m_last_raw = v
        _m_last_change = ticks_ms()
    if ticks_diff(ticks_ms(), _m_last_change) >= METAL_STABLE_MS and v != _m_stable:
        _m_stable = v
        detected = (_m_stable == METAL_ACTIVE_LEVEL)
        if detected and not metal_stop_active:
            metal_stop_active = True
            print("[{:>8} ms] METAL DETECTED".format(ticks_ms()))
        elif (not detected) and metal_stop_active:
            metal_stop_active = False
            print("[{:>8} ms] METAL CLEARED".format(ticks_ms()))

def measure_distance_cm():
    # Trigger 10us pulse
    trig.value(0); sleep_us(2)
    trig.value(1); sleep_us(10)
    trig.value(0)
    # Measure echo high time
    dur = time_pulse_us(echo, 1, ULTRA_TIMEOUT_US)
    if dur < 0:
        return None  # timeout or error
    # Sound speed ~343 m/s => 0.0343 cm/us; distance is half the round-trip
    return (dur * 0.0343) / 2.0

def update_ultra_state():
    global _ultra_last_ms, _ultra_last_cm, ultra_stop_active
    now = ticks_ms()
    if ticks_diff(now, _ultra_last_ms) < ULTRA_SAMPLE_MS:
        return
    _ultra_last_ms = now
    d = measure_distance_cm()
    _ultra_last_cm = d
    if d is None:
        return
    if d <= ULTRA_STOP_CM and not ultra_stop_active:
        ultra_stop_active = True
        print("[{:>8} ms] ULTRA STOP @ {:.1f} cm".format(now, d))
    elif d > ULTRA_STOP_CM and ultra_stop_active:
        ultra_stop_active = False
        print("[{:>8} ms] ULTRA CLEAR @ {:.1f} cm".format(now, d))

def recompute_gate_and_act():
    global auto_stop_active, last_motion_cmd
    blocked = metal_stop_active or ultra_stop_active
    if blocked and not auto_stop_active:
        auto_stop_active = True
        LED.value(1)
        stop()
        print("[{:>8} ms] AUTO-STOP".format(ticks_ms()))
    elif (not blocked) and auto_stop_active:
        auto_stop_active = False
        LED.value(0)
        # Auto-start: resume last motion or default
        cmd = last_motion_cmd if last_motion_cmd in ('F','B','L','R') else AUTO_START_DEFAULT
        apply_motion(cmd)
        if last_motion_cmd not in ('F','B','L','R'):
            last_motion_cmd = cmd
        print("[{:>8} ms] AUTO-START {}".format(
            ticks_ms(), {"F":"FORWARD","B":"BACKWARD","L":"LEFT","R":"RIGHT"}[cmd]))

# ---------------- Main ----------------
print("\n===== L298N + Metal + Ultrasonic (Pico W) =====")
print("Motors: IN1=GP12, IN2=GP13, IN3=GP14, IN4=GP15")
print("Metal OUT -> GP{} (active {})".format(SENSOR_PIN, "LOW" if METAL_ACTIVE_LEVEL==0 else "HIGH"))
print("Ultrasonic: TRIG=GP{}, ECHO=GP{}, STOP<= {} cm".format(TRIG_PIN, ECHO_PIN, int(ULTRA_STOP_CM)))
print("Commands: F B L R S\n")

stop()

try:
    if HAVE_USELECT:
        while True:
            update_metal_state()
            update_ultra_state()
            recompute_gate_and_act()

            c = read_cmd_nonblocking()
            if c:
                handle_cmd(c)

            sleep_ms(SAMPLE_MS)
    else:
        while True:
            update_metal_state()
            update_ultra_state()
            recompute_gate_and_act()

            line = read_line_blocking()
            if not line:
                continue
            for c in line:
                handle_cmd(c)
            sleep_ms(SAMPLE_MS)

except KeyboardInterrupt:
    pass
finally:
    stop()
    LED.value(0)
    print("Stopped. Bye.")

