# File: pico_l298_serial.py
# Board: Raspberry Pi Pico / Pico W
# MicroPython: drive L298N from USB serial commands (Thonny Shell)
#
# Commands:
#   F = forward, B = backward, L = left, R = right, S = stop
#
# Pin map (your mapping):
#   GP12 -> IN1 (Motor A, left)
#   GP13 -> IN2 (Motor A, left)
#   GP14 -> IN3 (Motor B, right)
#   GP15 -> IN4 (Motor B, right)
#
# Notes:
# - Leave ENA/ENB jumpers ON on L298N (always enabled).
# - Type single letters in Thonny Shell and press Enter (or paste multiple).
# - For pivot turns we drive motors in opposite directions.
#
from machine import Pin
from time import sleep_ms
import sys

# Optional non-blocking input support (works in MicroPython)
try:
    import uselect  # available on MicroPython
    HAVE_USELECT = True
except ImportError:
    HAVE_USELECT = False

# ---------------- Pins ----------------
IN1 = Pin(12, Pin.OUT)  # Left motor A: IN1
IN2 = Pin(13, Pin.OUT)  # Left motor A: IN2
IN3 = Pin(14, Pin.OUT)  # Right motor B: IN3
IN4 = Pin(15, Pin.OUT)  # Right motor B: IN4

# ---------------- Motor helpers ----------------
def left_motor(dir_: int):
    """dir_: +1 forward, -1 backward, 0 stop"""
    if dir_ > 0:
        IN1.value(1); IN2.value(0)
    elif dir_ < 0:
        IN1.value(0); IN2.value(1)
    else:
        IN1.value(0); IN2.value(0)

def right_motor(dir_: int):
    """dir_: +1 forward, -1 backward, 0 stop"""
    if dir_ > 0:
        IN3.value(1); IN4.value(0)
    elif dir_ < 0:
        IN3.value(0); IN4.value(1)
    else:
        IN3.value(0); IN4.value(0)

def stop():
    left_motor(0); right_motor(0)

def forward():
    left_motor(+1); right_motor(+1)

def backward():
    left_motor(-1); right_motor(-1)

def left_pivot():
    # pivot left: left motor backward, right motor forward
    left_motor(+1); right_motor(-1)

def right_pivot():
    # pivot right: left motor forward, right motor backward
    left_motor(-1); right_motor(+1)

# ---------------- Input handling ----------------
if HAVE_USELECT:
    poll = uselect.poll()
    poll.register(sys.stdin, uselect.POLLIN)

def read_cmd_nonblocking():
    """Return next uppercase command char if available; else None."""
    if not HAVE_USELECT:
        return None
    if poll.poll(0):  # data ready
        ch = sys.stdin.read(1)
        if ch:
            ch = ch.strip()
            if ch:
                return ch.upper()
    return None

def read_line_blocking():
    """Fallback if uselect not available: read a line (needs Enter)."""
    try:
        line = sys.stdin.readline()
        if not line:
            return None
        line = line.strip().upper()
        return line if line else None
    except Exception:
        return None

def handle_cmd(c):
    if c == 'F':
        forward();  print("→ FORWARD")
    elif c == 'B':
        backward(); print("→ BACKWARD")
    elif c == 'L':
        left_pivot(); print("→ LEFT (pivot)")
    elif c == 'R':
        right_pivot(); print("→ RIGHT (pivot)")
    elif c == 'S':
        stop();     print("→ STOP")
    else:
        # Ignore but show help once
        print("Unknown cmd:", c, "| Use F,B,L,R,S")

def print_help():
    print("\n===== L298N Robot Control (Pico W) =====")
    print("Pins: IN1=GP12, IN2=GP13, IN3=GP14, IN4=GP15")
    print("Type commands then Enter:")
    print("  F=forward  B=backward  L=left  R=right  S=stop")
    print("=========================================\n")

# ---------------- Main loop ----------------
print_help()
stop()  # start safe

try:
    if HAVE_USELECT:
        # Non-blocking: you can paste "FFLRS" etc.; loop keeps motors running
        while True:
            c = read_cmd_nonblocking()
            if c:
                handle_cmd(c)
            sleep_ms(10)
    else:
        # Blocking line input: one command per line (press Enter)
        while True:
            line = read_line_blocking()
            if not line:
                continue
            for c in line:  # allow multiple letters per line
                handle_cmd(c)
except KeyboardInterrupt:
    pass
finally:
    stop()
    print("Stopped. Bye.")

