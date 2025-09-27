import time
import threading
import requests
import serial

# ========= USER CONFIG =========
SERIAL_PORT = "COM17"         # match your system
BAUD        = 115200          # also set this in Arduino: Serial.begin(115200)
SER_TIMEOUT = 0               # non-blocking (0 = return immediately)
NEWLINE     = b'\n'           # end-of-line marker from Arduino prints

DATABASE_URL = "https://self-balancing-7a9fe-default-rtdb.firebaseio.com"
PATH         = "/20_KS5265_Gesture/Flex_Value"   # exact node
USE_AUTH     = False

API_KEY   = "AIzaSyBzXzocbdytn4N8vLrT-V2JYZ8pgqWrbC0"
EMAIL     = "spherenexgpt@gmail.com"
PASSWORD  = "Spherenex@123"

# Throttle posting so we don't DDoS ourselves or the DB.
POST_HZ        = 20            # max posts per second (50 ms interval)
REQUEST_TIMEOUT = 2            # seconds
RETRY_SLEEP     = 0.2          # on transient network error

# ========= GLOBAL STATE (safe via GIL for simple types) =========
latest_value = None
have_new     = False
stop_flag    = False

def get_id_token(email, password, api_key):
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}"
    r = requests.post(url, json={
        "email": email, "password": password, "returnSecureToken": True
    }, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()["idToken"]

def serial_reader(ser):
    """Continuously read bytes, assemble lines, update latest_value ASAP."""
    global latest_value, have_new, stop_flag
    buf = bytearray()
    while not stop_flag:
        try:
            chunk = ser.read(1024)  # read whatever is available without blocking
            if chunk:
                buf.extend(chunk)
                while True:
                    nl = buf.find(NEWLINE)
                    if nl == -1:
                        break
                    # Extract a full line (strip \r\n)
                    line_bytes = buf[:nl]
                    # Remove the processed line + newline
                    del buf[:nl + 1]
                    # Decode and sanitize
                    line = line_bytes.decode(errors="replace").strip()
                    if not line:
                        continue
                    # Update the latest value immediately (skip queueing)
                    latest_value = line
                    have_new     = True
                    # Optional: print locally for quick human feedback
                    print(f"[SERIAL] {line}")
            else:
                # tiny sleep to reduce CPU spin when no data
                time.sleep(0.001)
        except Exception as e:
            print(f"[SER ERR] {e}")
            time.sleep(0.2)

def firebase_poster(session, token):
    """Post only the freshest observed value at a controlled rate."""
    global latest_value, have_new, stop_flag
    min_interval = 1.0 / max(1, POST_HZ)
    params = {"auth": token} if token else None
    url = f"{DATABASE_URL}{PATH}.json"

    last_post_t = 0.0
    last_sent   = None

    while not stop_flag:
        now = time.time()
        # Post at most POST_HZ, and only if there is something new to send
        if have_new and (now - last_post_t) >= min_interval:
            value_to_send = latest_value
            try:
                r = session.put(url, params=params, json=value_to_send, timeout=REQUEST_TIMEOUT)
                if r.status_code == 200:
                    last_post_t = now
                    last_sent   = value_to_send
                    have_new    = False  # consumed the latest snapshot
                else:
                    print(f"[FB ERR] {r.status_code}: {r.text}")
                    time.sleep(RETRY_SLEEP)
            except Exception as e:
                print(f"[FB EXC] {e}")
                time.sleep(RETRY_SLEEP)
        else:
            # sleep a bit to keep loop responsive but light
            time.sleep(0.005)

def main():
    global stop_flag
    token = None
    if USE_AUTH:
        token = get_id_token(EMAIL, PASSWORD, API_KEY)
        print("[AUTH] Got ID token")

    # Open serial in non-blocking mode
    ser = serial.Serial(SERIAL_PORT, BAUD, timeout=SER_TIMEOUT)
    print(f"[SERIAL] Listening on {SERIAL_PORT} @ {BAUD}")

    # Persistent HTTP session (keep-alive) for low latency
    session = requests.Session()
    session.headers.update({"Connection": "keep-alive"})

    # Launch threads
    t_reader = threading.Thread(target=serial_reader, args=(ser,), daemon=True)
    t_poster = threading.Thread(target=firebase_poster, args=(session, token), daemon=True)
    t_reader.start()
    t_poster.start()

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[EXIT] Stopping...")
    finally:
        stop_flag = True
        t_reader.join(timeout=1.0)
        t_poster.join(timeout=1.0)
        ser.close()
        session.close()
        print("[CLEAN] Done.")

if __name__ == "__main__":
    main()
