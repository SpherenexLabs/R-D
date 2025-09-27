/*
 * ESP32 AP+STA + Firebase RTDB logging for 5 known devices
 * - Persistent slots Device_1..Device_5 always exist.
 * - On AP join:   /Device_X/Connected = true
 * - On AP leave:  /Device_X/Connected = false
 * - Debounce AP events and retry Firebase writes to avoid TLS drop errors.
 * - No Firebase calls inside Wi-Fi event callback.
 */

#include <WiFi.h>

// ---------- Router Wi-Fi (for Internet/Firebase) ----------
#define WIFI_SSID_STA     "attendance"
#define WIFI_PASS_STA     "123456789"

// ---------- ESP32 SoftAP (local hotspot for presence) ----------
static const char* AP_SSID       = "BLE";
static const char* AP_PASS       = "123456789";
static const uint8_t AP_CHANNEL  = 6;
static const uint8_t AP_MAX_CONN = 7;   // >=5

// ---------- Firebase (Mobizt Firebase-ESP-Client) ----------
#include <Firebase_ESP_Client.h>
#include <addons/TokenHelper.h>   // provides tokenStatusCallback
#include <addons/RTDBHelper.h>

#define API_KEY       "AIzaSyC4ZJdYQSAaK0lLR8xsTRfiWNCi-EtV5k4"
#define DATABASE_URL  "https://home-automation-385a6-default-rtdb.firebaseio.com/"
#define USER_EMAIL    "spherenexgpt@gmail.com"
#define USER_PASSWORD "Spherenex@123"

FirebaseData fbdo;
FirebaseAuth auth;
FirebaseConfig config;

static const char* BASE_PATH = "/19_KS5253_BLE";

// ===== Whitelist: EXACTLY 5 devices =====
struct Device {
  const char* mac;      // AA:BB:CC:DD:EE:FF
  const char* name;     // Friendly name
  const char* slotKey;  // Device_1 .. Device_5
};

Device devices[5] = {
  {"80:54:9C:C5:5C:08", "Vibha",       "Device_1"},
  {"CC:F9:F0:F6:3F:7C", "Harshitha",    "Device_2"},
  {"7E:FC:0D:9B:F9:AD", "Eshanya",   "Device_3"},
  {"C8:58:95:A8:A6:C6", "Marnal",    "Device_4"},
  {"3C:B0:ED:6F:82:B8", "Yashwanth", "Device_5"}
};

// ---------- Helpers ----------
String macToStr(const uint8_t *mac) {
  char s[18];
  sprintf(s, "%02X:%02X:%02X:%02X:%02X:%02X",
          mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  return String(s);
}

int findDeviceIndexByMac(const String& mac) {
  for (int i = 0; i < 5; i++)
    if (mac.equalsIgnoreCase(devices[i].mac)) return i;
  return -1;
}

bool firebaseReady() {
  return WiFi.status() == WL_CONNECTED && Firebase.ready();
}

String slotPath(int idx) {
  return String(BASE_PATH) + "/" + devices[idx].slotKey;
}

// ---------- Deferred flags (set by event, handled in loop) ----------
volatile bool pendingConnect[5]    = {false,false,false,false,false};
volatile bool pendingDisconnect[5] = {false,false,false,false,false};

// Debounce and retry controls
unsigned long dueAtMs[5] = {0,0,0,0,0};
static const unsigned long AP_EVENT_DEBOUNCE_MS = 700;  // wait after AP event
static const int FIREBASE_RETRIES = 3;

void queueConnect(int idx)    { pendingConnect[idx] = true;  dueAtMs[idx] = millis() + AP_EVENT_DEBOUNCE_MS; }
void queueDisconnect(int idx) { pendingDisconnect[idx] = true; dueAtMs[idx] = millis() + AP_EVENT_DEBOUNCE_MS; }

// ---------- Robust Firebase setters with small retry/backoff ----------
bool fb_trySetBool(const String& path, bool v) {
  for (int attempt = 1; attempt <= FIREBASE_RETRIES; ++attempt) {
    if (!firebaseReady()) return false;
    if (Firebase.RTDB.setBool(&fbdo, path.c_str(), v)) return true;
    Serial.printf("[FB] setBool failed (attempt %d/%d): %s\n",
                  attempt, FIREBASE_RETRIES, fbdo.errorReason().c_str());
    delay(250 * attempt);  // simple backoff
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("[FB] Wi-Fi down, retrying STA connect...");
      WiFi.disconnect();
      delay(50);
      WiFi.begin(WIFI_SSID_STA, WIFI_PASS_STA);
    }
  }
  return false;
}

bool fb_trySetString(const String& path, const String& v) {
  for (int attempt = 1; attempt <= FIREBASE_RETRIES; ++attempt) {
    if (!firebaseReady()) return false;
    if (Firebase.RTDB.setString(&fbdo, path.c_str(), v.c_str())) return true;
    Serial.printf("[FB] setString failed (attempt %d/%d): %s\n",
                  attempt, FIREBASE_RETRIES, fbdo.errorReason().c_str());
    delay(250 * attempt);
  }
  return false;
}

// ---------- Firebase actions (called ONLY from loop) ----------
void fb_initSlots() {
  if (!firebaseReady()) return;
  for (int i = 0; i < 5; i++) {
    String base = slotPath(i);
    // Ensure stable structure: Name + Connected=false
    if (!fb_trySetString(base + "/fullName", devices[i].name)) {
      Serial.printf("[FB] init Name failed for %s\n", base.c_str());
    }
    if (!fb_trySetBool(base + "/Connected", false)) {
      Serial.printf("[FB] init Connected failed for %s\n", base.c_str());
    }
  }
}

void fb_setConnected(int idx, bool connecte/d) {
  if (!firebaseReady()) return;
  String base = slotPath(idx);

  // Keep Name present (idempotent)
  (void)fb_trySetString(base + "/fullName", devices[idx].name);

  // Optional tiny jitter to decorrelate writes under AP churn
  delay(50 + (esp_random() % 100));

  if (fb_trySetBool(base + "/Connected", connected)) {
    Serial.printf("[FB] %s/Connected <- %s\n", base.c_str(), connected ? "true" : "false");
  } else {
    Serial.printf("[FB] FINAL FAIL: %s/Connected <- %s\n",
                  base.c_str(), connected ? "true" : "false");
  }
}

// ---------- Wi-Fi event (no Firebase calls here) ----------
void onWiFiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  switch (event) {
    case ARDUINO_EVENT_WIFI_AP_STACONNECTED: {
      String mac = macToStr(info.wifi_ap_staconnected.mac);
      int idx = findDeviceIndexByMac(mac);
      if (idx >= 0) {
        Serial.printf("%s Connected (MAC=%s)\n", devices[idx].name, mac.c_str());
        queueConnect(idx);    // defer Firebase write
      } else {
        Serial.printf("Unknown device connected: %s (not logged)\n", mac.c_str());
      }
      break;
    }
    case ARDUINO_EVENT_WIFI_AP_STADISCONNECTED: {
      String mac = macToStr(info.wifi_ap_stadisconnected.mac);
      int idx = findDeviceIndexByMac(mac);
      if (idx >= 0) {
        Serial.printf("%s Disconnected (MAC=%s)\n", devices[idx].name, mac.c_str());
        queueDisconnect(idx); // defer Firebase write
      } else {
        Serial.printf("Unknown device disconnected: %s\n", mac.c_str());
      }
      break;
    }
    default: break;
  }
}

void connectSTA() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("Connecting STA to %s ...\n", WIFI_SSID_STA);
  WiFi.begin(WIFI_SSID_STA, WIFI_PASS_STA);
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) {
    delay(300);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("STA IP: "); Serial.println(WiFi.localIP());
  } else {
    Serial.println("STA connect failed (will retry).");
  }
}

void setupFirebase() {
  config.api_key = API_KEY;
  config.database_url = DATABASE_URL;
  auth.user.email = USER_EMAIL;
  auth.user.password = USER_PASSWORD;
  config.token_status_callback = tokenStatusCallback;  // from TokenHelper.h
  Firebase.begin(&config, &auth);
  Firebase.reconnectWiFi(true);
}

void setup() {
  Serial.begin(115200);
  delay(200);

  // AP + STA concurrently
  WiFi.mode(WIFI_AP_STA);
  WiFi.onEvent(onWiFiEvent);

  // Reduce micro-outages during AP events
  WiFi.setSleep(false);

  if (!WiFi.softAP(AP_SSID, AP_PASS, AP_CHANNEL, 0 /*visible*/, AP_MAX_CONN)) {
    Serial.println("SoftAP start failed!");
    while (true) delay(1000);
  }
  Serial.print("AP SSID: "); Serial.println(AP_SSID);
  Serial.print("AP IP  : "); Serial.println(WiFi.softAPIP());

  connectSTA();
  setupFirebase();

  // Create/refresh stable structure on boot (no deletes).
  if (firebaseReady()) {
    fb_initSlots();
  }
}

unsigned long lastStaRetryMs = 0;

void loop() {
  // Retry STA if disconnected
  if (WiFi.status() != WL_CONNECTED && millis() - lastStaRetryMs > 5000) {
    lastStaRetryMs = millis();
    connectSTA();
  }

  // Handle deferred Firebase updates once debounce has elapsed
  for (int i = 0; i < 5; i++) {
    bool time_ok = ((long)(millis() - dueAtMs[i]) >= 0);
    if (pendingConnect[i] && time_ok) {
      pendingConnect[i] = false;
      fb_setConnected(i, true);
    }
    if (pendingDisconnect[i] && time_ok) {
      pendingDisconnect[i] = false;
      fb_setConnected(i, false);
    }
  }
}
