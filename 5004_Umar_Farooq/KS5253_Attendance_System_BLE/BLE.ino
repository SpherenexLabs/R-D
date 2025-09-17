#include <WiFi.h>

// ===== Hotspot config =====
const char* AP_SSID     = "BLE";
const char* AP_PASS     = "123456789";
const uint8_t AP_CHANNEL = 6;   // 1,6,11 are good
const uint8_t AP_MAX_CONN = 7;  // allow up to 7 devices

// ===== List of known devices (MAC + name) =====
struct Device {
  const char* mac;
  const char* name;
};

Device myDevices[] = {
  {"D0:97:FE:1D:B9:09", "UFK"},
  {"D4:63:DE:8B:AD:2E", "Sushma"},
  {"7E:FC:0D:9B:F9:AD", "Eshanya"}, 
  {"C8:58:95:A8:A6:C6", "Marnal"},
  {"AA:BB:CC:DD:EE:02", "Marnal"},
  {"3C:B0:ED:6F:82:B8", "Yashwanth"},
  {"AA:BB:CC:DD:EE:05", "Phone_3"}
};
const int NUM_DEVICES = sizeof(myDevices) / sizeof(myDevices[0]);

// Utility: convert MAC bytes -> string
String macToStr(const uint8_t *mac) {
  char macStr[18];
  sprintf(macStr, "%02X:%02X:%02X:%02X:%02X:%02X",
          mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  return String(macStr);
}

// Utility: lookup name by MAC
String findDeviceName(const String& mac) {
  for (int i = 0; i < NUM_DEVICES; i++) {
    if (mac.equalsIgnoreCase(myDevices[i].mac)) {
      return String(myDevices[i].name);
    }
  }
  return ""; // not found
}

// Wi-Fi event handler
void onWiFiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  switch (event) {
    case ARDUINO_EVENT_WIFI_AP_STACONNECTED: {
      String mac = macToStr(info.wifi_ap_staconnected.mac);
      String name = findDeviceName(mac);
      if (name != "") {
        Serial.print(name); Serial.println(" Connected");
      } else {
        Serial.print("Unknown device connected: "); Serial.println(mac);
      }
      break;
    }

    case ARDUINO_EVENT_WIFI_AP_STADISCONNECTED: {
      String mac = macToStr(info.wifi_ap_stadisconnected.mac);
      String name = findDeviceName(mac);
      if (name != "") {
        Serial.print(name); Serial.println(" Disconnected");
      } else {
        Serial.print("Unknown device disconnected: "); Serial.println(mac);
      }
      break;
    }

    default: break;
  }
}

void setup() {
  Serial.begin(115200);
  delay(200);

  WiFi.mode(WIFI_AP);
  WiFi.onEvent(onWiFiEvent);

  // softAP(ssid, pass, channel, hidden, max_connections)
  if (!WiFi.softAP(AP_SSID, AP_PASS, AP_CHANNEL, 0 /*visible*/, AP_MAX_CONN)) {
    Serial.println("SoftAP start failed!");
    while (true) delay(1000);
  }

  Serial.println("Hotspot started");
  Serial.print("SSID: "); Serial.println(AP_SSID);
  Serial.print("PASS: "); Serial.println(AP_PASS);
  Serial.print("Channel: "); Serial.println(AP_CHANNEL);
  Serial.print("Max clients: "); Serial.println(AP_MAX_CONN);
  Serial.print("AP IP: "); Serial.println(WiFi.softAPIP());
}

void loop() {
  // nothing needed
}
