this code is working fine 

# File: pi_knee_ultra.py
# Raspberry Pi 4B + MPU6050 — Knee Angle & Impact + HC-SR04 Ultrasonic + Buzzer

import time, math
import board, busio, digitalio
import adafruit_mpu6050

# ───────── Wiring Summary ─────────
# MPU6050: VCC→3V3 (Pin1), GND→GND (Pin6), SDA→GPIO2/Pin3, SCL→GPIO3/Pin5, AD0→GND (0x68)
# Button:  one lead → GPIO17/Pin11, other → GND (internal pull-up used)
# HC-SR04: VCC→5V (Pin2/4), GND→GND (Pin6), TRIG→GPIO27/Pin13, ECHO→GPIO22/Pin15 (5V→3.3V divider)
# Divider: ECHO(5V) -- 1kΩ --+--> GPIO22 ; 2kΩ from node to GND
# Buzzer:  + → GPIO12/Pin32, - → GND

# ───────── Tunables ─────────
I2C_ADDRESS         = 0x68     # 0x69 if AD0 high
ACC_THRESHOLD_G     = 2.5      # impact threshold (g)
CALIBRATION_SAMPLES = 50
ANGLE_ALPHA         = 0.2      # 0..1 (higher = snappier)
PRINT_RATE_HZ       = 10

# Button
RESET_BUTTON_GPIO   = board.D25  # GPIO26 (Pin 11)

# Ultrasonic
ULTRA_TRIG_GPIO     = board.D27  # GPIO27 (Pin 13)
ULTRA_ECHO_GPIO     = board.D22  # GPIO22 (Pin 15)
ULTRA_TIMEOUT_S     = 0.06
ULTRA_SAMPLES       = 3
ULTRA_STOP_CM       = 15.0       # obstacle threshold (cm)
SND_CM_PER_S        = 34300.0

# Buzzer
BUZZER_GPIO         = board.D12  # GPIO12 (Pin 32)   # [BUZZER]

G0 = 9.80665  # m/s^2 per g

# ───────── Ultrasonic helpers ─────────
def _ultra_read_once_cm(trig, echo, timeout_s=ULTRA_TIMEOUT_S):
    trig.value = False
    time.sleep(0.000005)
    trig.value = True
    time.sleep(0.000010)
    trig.value = False

    t0 = time.monotonic_ns()
    # wait rising
    while not echo.value:
        if (time.monotonic_ns() - t0) > int(timeout_s * 1e9):
            return math.nan
    t_start = time.monotonic_ns()
    # wait falling
    while echo.value:
        if (time.monotonic_ns() - t_start) > int(timeout_s * 1e9):
            return math.nan
    t_end = time.monotonic_ns()

    pulse_s = (t_end - t_start) / 1e9
    return (pulse_s * SND_CM_PER_S) / 2.0

def ultra_read_cm(trig, echo, samples=ULTRA_SAMPLES, timeout_s=ULTRA_TIMEOUT_S):
    vals = []
    for _ in range(max(1, samples)):
        d = _ultra_read_once_cm(trig, echo, timeout_s)
        if not math.isnan(d):
            vals.append(d)
        time.sleep(0.010)
    if not vals:
        return math.nan
    vals.sort()
    return vals[len(vals)//2]

def main():
    # I2C + MPU
    i2c = busio.I2C(board.SCL, board.SDA)
    mpu = adafruit_mpu6050.MPU6050(i2c, address=I2C_ADDRESS)
    mpu.accelerometer_range = adafruit_mpu6050.Range.RANGE_2_G
    mpu.gyro_range = adafruit_mpu6050.GyroRange.RANGE_250_DPS
    mpu.filter_bandwidth = adafruit_mpu6050.Bandwidth.BAND_44_HZ

    # Button
    btn = digitalio.DigitalInOut(RESET_BUTTON_GPIO)
    btn.switch_to_input(pull=digitalio.Pull.UP)
    last_btn_state = True

    # Ultrasonic
    trig = digitalio.DigitalInOut(ULTRA_TRIG_GPIO)
    trig.direction = digitalio.Direction.OUTPUT
    trig.value = False
    echo = digitalio.DigitalInOut(ULTRA_ECHO_GPIO)
    echo.switch_to_input(pull=None)

    # Buzzer setup                                                    # [BUZZER]
    buzzer = digitalio.DigitalInOut(BUZZER_GPIO)                       # [BUZZER]
    buzzer.direction = digitalio.Direction.OUTPUT                      # [BUZZER]
    buzzer.value = False                                               # [BUZZER]

    print("MPU6050 Knee + Ultrasonic + Buzzer (Ctrl+C to exit)")
    print(f"Obstacle threshold: {ULTRA_STOP_CM:.1f} cm\n")

    rest_x_sum = 0.0
    calib_count = 0
    is_calibrated = False
    last_angle = 0.0
    last_dist_cm = math.nan

    period = 1.0 / max(1, PRINT_RATE_HZ)
    next_t = time.time()

    while True:
        # Button edge (recalibrate)
        cur_btn_state = bool(btn.value)
        if last_btn_state and (not cur_btn_state):
            time.sleep(0.05)
            if not btn.value:
                print("\nRESET! Re-calibrating… keep leg straight.\n")
                rest_x_sum = 0.0
                calib_count = 0
                is_calibrated = False
                last_angle = 0.0
        last_btn_state = cur_btn_state

        # Read MPU6050
        ax_ms2, ay_ms2, az_ms2 = mpu.acceleration
        ax_g, ay_g, az_g = ax_ms2/G0, ay_ms2/G0, az_ms2/G0
        a_mag_g = math.sqrt(ax_g*ax_g + ay_g*ay_g + az_g*az_g)
        impact = a_mag_g > ACC_THRESHOLD_G

        # Calibration or angle
        if not is_calibrated:
            rest_x_sum += ax_ms2
            calib_count += 1
            if (calib_count % 10) == 0:
                pct = int((calib_count * 100) / CALIBRATION_SAMPLES)
                print(f"Calibrating: {min(pct,100)}%")
            if calib_count >= CALIBRATION_SAMPLES:
                rest_x = rest_x_sum / CALIBRATION_SAMPLES
                is_calibrated = True
                print(f"Calibration complete. Rest X = {rest_x:.3f} m/s^2\n")
        else:
            accel_x_offset = ax_ms2 - rest_x
            angle = math.degrees(math.atan2(accel_x_offset, G0))
            if abs(angle) > 90.0:
                angle = last_angle
            else:
                angle = ANGLE_ALPHA*angle + (1.0-ANGLE_ALPHA)*last_angle
                last_angle = angle

        # Ultrasonic read
        d = ultra_read_cm(trig, echo)
        if not math.isnan(d):
            last_dist_cm = d
        obstacle = (not math.isnan(last_dist_cm)) and (last_dist_cm < ULTRA_STOP_CM)

        # Buzzer control                                               # [BUZZER]
        buzzer.value = obstacle                                        # [BUZZER]

        # Output
        now = time.time()
        if now >= next_t:
            next_t += period
            line = (f"Acc (g): {ax_g:+6.3f}, {ay_g:+6.3f}, {az_g:+6.3f} | "
                    f"|A|={a_mag_g:.2f} g")
            if impact:
                line += "  <IMPACT>"
            if math.isnan(last_dist_cm):
                line += " | Dist: --.- cm"
            else:
                line += f" | Dist: {last_dist_cm:5.1f} cm"
                if obstacle:
                    line += "  <OBSTACLE> BUZZER ON"
            print(line)

            if is_calibrated:
                sign = "+" if last_angle >= 0 else ""
                print(f"Knee Angle: {sign}{last_angle:.1f}°")

        time.sleep(0.002)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting.")
