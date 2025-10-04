#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <Servo.h>
#include <Firebase_ESP_Client.h>
#include "addons/TokenHelper.h"
#include "addons/RTDBHelper.h"

// -------- Wi-Fi --------
#define WIFI_SSID       "wcs"
#define WIFI_PASSWORD   "123456789"

// -------- Firebase --------
#define API_KEY         "AIzaSyAhLCi6JBT5ELkAFxTplKBBDdRdpATzQxI"
#define DATABASE_URL    "https://smart-medicine-vending-machine-default-rtdb.asia-southeast1.firebasedatabase.app"
#define USER_EMAIL      "spherenexgpt@gmail.com"
#define USER_PASSWORD   "Spherenex@123"

// RTDB paths
#define FB_ROOT   "/dynamic-wireless-car-charging"
#define FB_ENTRY  FB_ROOT "/Entry"               // optional flag we set 1/0
#define FB_PAY    FB_ROOT "/paymentCompleted"    // watched key

// -------- Pins (as requested) --------
#define IR_PIN          D6          // HIGH -> vehicle present
#define SERVO_ENTRY_PIN D4          // entry barrier
#define SERVO_EXIT_PIN  D5          // exit barrier

// -------- Servo motion --------
#define SERVO_CLOSED_DEG  0
#define SERVO_OPEN_DEG    90
#define OPEN_TIME_MS      700
#define HOLD_TIME_MS      1200
#define CLOSE_TIME_MS     700

// -------- Debounce --------
#define DEBOUNCE_MS         25
#define RETRIGGER_GUARD_MS 1200

// Firebase objects
FirebaseData fbdo;
FirebaseData fbStream;
FirebaseAuth auth;
FirebaseConfig config;

// Servos
Servo servoEntry, servoExit;

// ---- helpers ----
void sweepOpenHoldClose(Servo &s) {
  // 0 -> 90 -> hold -> 0, with small steps for smoothness
  for (int d = SERVO_CLOSED_DEG; d <= SERVO_OPEN_DEG; ++d) { s.write(d); delay(OPEN_TIME_MS/90); yield(); }
  delay(HOLD_TIME_MS);
  for (int d = SERVO_OPEN_DEG; d >= SERVO_CLOSED_DEG; --d) { s.write(d); delay(CLOSE_TIME_MS/90); yield(); }
}

template<typename T>
bool fb_set(const char* path, T v) {
  for (int i=0;i<3;i++) {
    if (Firebase.ready() && Firebase.RTDB.set(&fbdo, path, v)) return true;
    delay(120);
  }
  Serial.printf("RTDB write fail [%s]: %s\n", path, fbdo.errorReason().c_str());
  return false;
}

// ---- stream: paymentCompleted -> exit servo ----
void streamCallback(FirebaseStream data) {
  if (data.dataPath() != String("/")) return; // root of watched key
  if (data.dataTypeEnum() == fb_esp_rtdb_data_type_string) {
    if (data.stringData() == "1") {
      Serial.println("paymentCompleted=1 -> EXIT OPEN");
      sweepOpenHoldClose(servoExit);
      fb_set(FB_PAY, "0");  // reset
    }
  } else if (data.dataTypeEnum() == fb_esp_rtdb_data_type_integer) {
    if (data.intData() == 1) {
      Serial.println("paymentCompleted=1 -> EXIT OPEN");
      sweepOpenHoldClose(servoExit);
      fb_set(FB_PAY, 0);   // reset
    }
  }
}
void streamTimeoutCallback(bool timeout) {
  if (timeout) Serial.println("Stream timeout, resuming...");
}

void setup() {
  Serial.begin(115200);

  // IO
  pinMode(IR_PIN, INPUT);                 // IR goes HIGH on detection (your request)
  servoEntry.attach(SERVO_ENTRY_PIN);
  servoExit.attach(SERVO_EXIT_PIN);
  servoEntry.write(SERVO_CLOSED_DEG);
  servoExit.write(SERVO_CLOSED_DEG);

  // Wi-Fi
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("WiFi");
  while (WiFi.status()!=WL_CONNECTED) { delay(300); Serial.print("."); }
  Serial.println(" OK  IP=" + WiFi.localIP().toString());

  // Firebase
  config.api_key = API_KEY;
  config.database_url = DATABASE_URL;
  config.token_status_callback = tokenStatusCallback;
  auth.user.email = USER_EMAIL;
  auth.user.password = USER_PASSWORD;

  fbdo.setBSSLBufferSize(4096, 1024);
  fbStream.setBSSLBufferSize(4096, 1024);

  Firebase.begin(&config, &auth);
  Firebase.reconnectWiFi(true);
  Serial.print("Firebase");
  while (!Firebase.ready()) { delay(200); Serial.print("."); }
  Serial.println(" ready");

  // init flags (optional)
  fb_set(FB_ENTRY, "0");
  fb_set(FB_PAY,   "0");

  // start streaming paymentCompleted
  if (!Firebase.RTDB.beginStream(&fbStream, FB_PAY)) {
    Serial.printf("beginStream failed: %s\n", fbStream.errorReason().c_str());
  } else {
    Firebase.RTDB.setStreamCallback(&fbStream, streamCallback, streamTimeoutCallback);
    Serial.println("Streaming paymentCompleted...");
  }
}

void loop() {
  // Entry logic: IR HIGH -> open entry servo and set Entry=1 briefly
  static bool lastStable=false, lastRaw=false;
  static uint32_t lastChange=0, lastTrig=0;
  uint32_t now = millis();

  bool raw = (digitalRead(IR_PIN) == HIGH);   // HIGH means present
  if (raw != lastRaw) { lastRaw = raw; lastChange = now; }
  if (now - lastChange >= DEBOUNCE_MS) {
    if (raw != lastStable) {
      lastStable = raw;
      if (lastStable && (now - lastTrig > RETRIGGER_GUARD_MS)) {
        Serial.println("ENTRY IR HIGH -> ENTRY OPEN");
        fb_set(FB_ENTRY, "1");
        sweepOpenHoldClose(servoEntry);
        fb_set(FB_ENTRY, "0");
        lastTrig = now;
      }
    }
  }

  // Fallback poll (if stream drops) every 2 s
  static uint32_t tPoll=0;
  if (now - tPoll > 2000) {
    tPoll = now;
    if (!fbStream.httpConnected()) {
      if (Firebase.RTDB.getString(&fbdo, FB_PAY)) {
        if (fbdo.stringData() == "1") {
          Serial.println("POLL: paymentCompleted=1 -> EXIT OPEN");
          sweepOpenHoldClose(servoExit);
          fb_set(FB_PAY, "0");
        }
      }
    }
  }

  delay(3);
}
