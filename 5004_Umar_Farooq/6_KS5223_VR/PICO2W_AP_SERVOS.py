# main.py — Pico W AP "VR" + UDP receiver
# Prints X,Z (deg) and corresponding virtual servo angles.
import network, socket, time

SSID = "VR"
PASS = "123456789"
UDP_PORT = 4210

# Print every packet or only when values change:
PRINT_EVERY_PACKET = False

# ---------- Wi-Fi AP ----------
ap = network.WLAN(network.AP_IF)
ap.active(True)
ap.config(essid=SSID, password=PASS)  # WPA2 automatically
while not ap.active():
    time.sleep(0.05)

# ---------- UDP listener ----------
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("0.0.0.0", UDP_PORT))
s.settimeout(None)  # blocking

print("X_deg,Z_deg,ServoX_deg,ServoZ_deg")  # header

last_x = None
last_z = None

def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v

def snap10(v):
    # nearest 10°, clamped to a valid servo range
    sv = int(round(v / 10.0)) * 10
    return 0 if sv < 0 else 180 if sv > 180 else sv

def parse_xz(msg: str):
    # expects "X:<int>,Z:<int>"
    kv = {}
    for part in msg.split(","):
        k, v = part.split(":")
        kv[k.strip()] = int(v)
    return kv.get("X"), kv.get("Z")

while True:
    try:
        data, addr = s.recvfrom(32)
        msg = data.decode().strip()
        x, z = parse_xz(msg)
    except Exception:
        continue  # ignore malformed lines and keep listening

    # Match ESP limits (safety)
    x = clamp(x, -50, 50)   # Roll/X
    z = clamp(z, -30, 30)   # Yaw/Z

    # Virtual servo mapping: 90 + 2*angle, snapped to 10°
    sx = snap10(90 + 2*x)
    sz = snap10(90 + 2*z)

    if PRINT_EVERY_PACKET or (x != last_x or z != last_z):
        print(f"{x},{z},{sx},{sz}")
        last_x, last_z = x, z

