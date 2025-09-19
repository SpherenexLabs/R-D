/*
 * ESP32 AP+STA + Firebase RTDB logging for 5 known devices
 * - SAFE: no Firebase calls inside Wi-Fi event callback.
 * - Uses RTDB.deleteNode() instead of setString("", ...) to clear.
 */

#include <WiFi.h>

// ---------- Router Wi-Fi (for Internet/Firebase) ----------
#define WIFI_SSID_STA     "attendance"
#define WIFI_PASS_STA     "123456789"

// ---------- ESP32 SoftAP (local hotspot for presence) ----------
const char* AP_SSID       = "BLE";
const char* AP_PASS       = "123456789";
const uint8_t AP_CHANNEL  = 6;
const uint8_t AP_MAX_CONN = 7;   // you asked for >= 5

// ---------- Firebase (Mobizt Firebase-ESP-Client) ----------
#include <Firebase_ESP_Client.h>
#include <addons/TokenHelper.h>
#include <addons/RTDBHelper.h>

#define API_KEY       "AIzaSyBzXzocbdytn4N8vLrT-V2JYZ8pgqWrbC0"
#define DATABASE_URL  "https://self-balancing-7a9fe-default-rtdb.firebaseio.com"
#define USER_EMAIL    "spherenexgpt@gmail.com"
#define USER_PASSWORD "Spherenex@123"

FirebaseData fbdo;
FirebaseAuth auth;
FirebaseConfig config;

const char* BASE_PATH = "/19_KS5253_BLE";

// ===== Whitelist: EXACTLY 5 devices =====
struct Device {
  const char* mac;      // AA:BB:CC:DD:EE:FF
  const char* name;     // Friendly name
  const char* slotKey;  // Device_1 .. Device_5
};

Device devices[5] = {
  {"D0:97:FE:1D:B9:09", "UFK",       "Device_1"},
  {"D4:63:DE:8B:AD:2E", "Sushma",    "Device_2"},
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

// ---------- Defer Firebase work: flags set by event, handled in loop() ----------
volatile bool pendingConnect[5]    = {false,false,false,false,false};
volatile bool pendingDisconnect[5] = {false,false,false,false,false};

void queueConnect(int idx)    { pendingConnect[idx] = true; }
void queueDisconnect(int idx) { pendingDisconnect[idx] = true; }

// ---------- Firebase actions (called ONLY from loop) ----------
void fb_setName(int idx) {
  if (!firebaseReady()) return;
  String path = String(BASE_PATH) + "/" + devices[idx].slotKey;
  String value = devices[idx].name;
  if (Firebase.RTDB.setString(&fbdo, path.c_str(), value.c_str())) {
    Serial.printf("[FB] %s <- \"%s\"\n", path.c_str(), value.c_str());
  } else {
    Serial.printf("[FB] setString failed: %s\n", fbdo.errorReason().c_str());
  }
}

void fb_clearSlot(int idx) {
  if (!firebaseReady()) return;
  String path = String(BASE_PATH) + "/" + devices[idx].slotKey;
  if (Firebase.RTDB.deleteNode(&fbdo, path.c_str())) {
    Serial.printf("[FB] deleteNode %s OK\n", path.c_str());
  } else {
    Serial.printf("[FB] deleteNode failed: %s\n", fbdo.errorReason().c_str());
  }
}

// ---------- Wi-Fi event (do NOT touch Firebase here) ----------
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
        queueDisconnect(idx); // defer Firebase delete
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
  config.token_status_callback = tokenStatusCallback;
  Firebase.begin(&config, &auth);
  Firebase.reconnectWiFi(true);
}

void setup() {
  Serial.begin(115200);
  delay(200);

  // AP + STA concurrently
  WiFi.mode(WIFI_AP_STA);
  WiFi.onEvent(onWiFiEvent);

  if (!WiFi.softAP(AP_SSID, AP_PASS, AP_CHANNEL, 0 /*visible*/, AP_MAX_CONN)) {
    Serial.println("SoftAP start failed!");
    while (true) delay(1000);
  }
  Serial.print("AP SSID: "); Serial.println(AP_SSID);
  Serial.print("AP IP  : "); Serial.println(WiFi.softAPIP());

  connectSTA();
  setupFirebase();

  // Optional: clean slate on boot (delete 5 nodes)
  if (firebaseReady()) {
    for (int i = 0; i < 5; i++) fb_clearSlot(i);
  }
}

unsigned long lastStaRetryMs = 0;

void loop() {
  // Retry STA if disconnected
  if (WiFi.status() != WL_CONNECTED && millis() - lastStaRetryMs > 5000) {
    lastStaRetryMs = millis();
    connectSTA();
  }

  // Handle deferred Firebase writes/deletes
  for (int i = 0; i < 5; i++) {
    if (pendingConnect[i]) {
      pendingConnect[i] = false;
      fb_setName(i);
    }
    if (pendingDisconnect[i]) {
      pendingDisconnect[i] = false;
      fb_clearSlot(i);
    }
  }

  // (Nothing else needed)
}
