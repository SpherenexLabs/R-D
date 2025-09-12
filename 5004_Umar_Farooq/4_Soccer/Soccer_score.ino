// File: esp8266_soccer_dual_ultra_firebase.ino

#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <Firebase_ESP_Client.h>
#include "addons/TokenHelper.h"
#include "addons/RTDBHelper.h"

// ---------- WiFi ----------
#define WIFI_SSID      "soccer"
#define WIFI_PASSWORD  "123456789"

// ---------- Firebase ----------
#define API_KEY        "AIzaSyBzXzocbdytn4N8vLrT-V2JYZ8pgqWrbC0"
#define DATABASE_URL   "https://self-balancing-7a9fe-default-rtdb.firebaseio.com"
#define USER_EMAIL     "spherenexgpt@gmail.com"
#define USER_PASSWORD  "Spherenex@123"

// ---------- PATHS (UPDATED) ----------
#define PATH_BLUE "/9_KS5116_Soccer_Robot_1/BLUE_TEAM/score"   // (Change L33)
#define PATH_RED  "/9_KS5116_Soccer_Robot_1/RED_TEAM/score"    // (Change L34)

// ---------- Ultrasonic pins ----------
#define TRIG_RED   D5   // Sensor 1 (RED team) TRIG
#define ECHO_RED   D6   // Sensor 1 (RED team) ECHO  (level-shift to 3.3 V)
#define TRIG_BLUE  D7   // Sensor 2 (BLUE team) TRIG
#define ECHO_BLUE  D8   // Sensor 2 (BLUE team) ECHO (GPIO15 strap must be LOW at boot)

// ---------- Tunables ----------
const float    SOUND_CM_PER_US = 0.0343f;
const uint32_t PULSE_TIMEOUT_US = 30000UL;   // ~5 m
const uint8_t  N_MEDIAN = 5;
const uint16_t INTER_SENSOR_DELAY_MS = 60;   // anti-crosstalk
const float    THRESHOLD_CM = 30.0f;
const uint32_t PULSE_HOLD_MS = 5000UL;       // 3 s HIGH

// ---------- Firebase globals ----------
FirebaseData fbdo;
FirebaseAuth auth;
FirebaseConfig config;

// ---------- Internal state ----------
struct Latch { bool active=false; uint32_t until=0; };
Latch redLatch, blueLatch;
bool firebaseReady = false;

// ---------- Helpers ----------
static inline float usToCm(uint32_t us) {
  return (us == 0) ? NAN : (us * SOUND_CM_PER_US * 0.5f);
}
uint32_t measureEchoUS(uint8_t trigPin, uint8_t echoPin) {
  digitalWrite(trigPin, LOW); delayMicroseconds(3);
  digitalWrite(trigPin, HIGH); delayMicroseconds(10);
  digitalWrite(trigPin, LOW);
  uint32_t t = pulseIn(echoPin, HIGH, PULSE_TIMEOUT_US); yield();
  return t;
}
float distanceMedian(uint8_t trigPin, uint8_t echoPin) {
  uint32_t a[N_MEDIAN];
  for (uint8_t i=0;i<N_MEDIAN;++i){ a[i]=measureEchoUS(trigPin,echoPin); delay(5); }
  for (uint8_t i=1;i<N_MEDIAN;++i){ uint32_t k=a[i]; int8_t j=i-1; while(j>=0 && a[j]>k){a[j+1]=a[j];--j;} a[j+1]=k; }
  return usToCm(a[N_MEDIAN/2]);
}
bool setIntRTDB(const char* path, int value){
  if(!firebaseReady) return false;
  bool ok = Firebase.RTDB.setInt(&fbdo, path, value);
  if(!ok){ Serial.printf("[Firebase] setInt fail @ %s → %s\n", path, fbdo.errorReason().c_str()); }
  return ok;
}
void tryInitKeys(){
  // ensure keys exist as integers
  setIntRTDB(PATH_RED, 0);
  setIntRTDB(PATH_BLUE, 0);
}

// ---------- Setup / Loop ----------
void setup() {
  Serial.begin(9600);
  pinMode(TRIG_RED, OUTPUT);  digitalWrite(TRIG_RED, LOW);
  pinMode(ECHO_RED, INPUT);
  pinMode(TRIG_BLUE, OUTPUT); digitalWrite(TRIG_BLUE, LOW);
  pinMode(ECHO_BLUE, INPUT);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("[WiFi] Connecting");
  while (WiFi.status()!=WL_CONNECTED){ Serial.print('.'); delay(400); }
  Serial.printf(" OK  IP=%s\n", WiFi.localIP().toString().c_str());

  config.api_key = API_KEY;
  config.database_url = DATABASE_URL;
  auth.user.email = USER_EMAIL;
  auth.user.password = USER_PASSWORD;

  Firebase.begin(&config, &auth);
  Firebase.reconnectWiFi(true);

  Serial.print("[Firebase] Signing in");
  uint32_t t0=millis();
  while(!Firebase.ready()){ Serial.print('.'); delay(300); if(millis()-t0>15000) break; }
  firebaseReady = Firebase.ready();
  Serial.println(firebaseReady ? " OK" : " timeout");
  if(firebaseReady) tryInitKeys();

  Serial.println("[System] RED (D5/D6) & BLUE (D7/D8) → write 1 for 3 s when <15 cm, else 0");
}

void loop() {
  float dRed  = distanceMedian(TRIG_RED,  ECHO_RED);
  delay(INTER_SENSOR_DELAY_MS);
  float dBlue = distanceMedian(TRIG_BLUE, ECHO_BLUE);

  uint32_t now = millis();

  // RED
  if(!isnan(dRed) && dRed < THRESHOLD_CM && !redLatch.active){
    redLatch.active = true; redLatch.until = now + PULSE_HOLD_MS;
    Serial.println("[RED] <15 cm → 1 (3 s)"); setIntRTDB(PATH_RED, 1);
  }
  if(redLatch.active && (int32_t)(now - redLatch.until) >= 0){
    redLatch.active = false; Serial.println("[RED] hold end → 0"); setIntRTDB(PATH_RED, 0);
  }

  // BLUE
  if(!isnan(dBlue) && dBlue < THRESHOLD_CM && !blueLatch.active){
    blueLatch.active = true; blueLatch.until = now + PULSE_HOLD_MS;
    Serial.println("[BLUE] <15 cm → 1 (3 s)"); setIntRTDB(PATH_BLUE, 1);
  }
  if(blueLatch.active && (int32_t)(now - blueLatch.until) >= 0){
    blueLatch.active = false; Serial.println("[BLUE] hold end → 0"); setIntRTDB(PATH_BLUE, 0);
  }

  // Telemetry
  Serial.print("RED=");  if(isnan(dRed))  Serial.print("timeout"); else Serial.printf("%.1fcm", dRed);
  Serial.print(" | BLUE="); if(isnan(dBlue)) Serial.println("timeout"); else Serial.printf("%.1fcm\n", dBlue);

  delay(80);
}
