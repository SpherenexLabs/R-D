// ===== File: esp8266_ir2_servo_oled_priceflow_success.ino =====
// Board: NodeMCU 1.0 (ESP-12E)
//
// OLED behavior:
//   • Boot: show SSID and animated "Connecting..." until Wi-Fi connects
//   • Main screen: "Price: <val>" + "Payment Pending"
//   • When /Toll_Gate/Servo1 == 1: show "Payment Success" (3 s) and set /Toll_Gate/Price = 0
//
// Firebase paths used:
//   /Toll_Gate/IR1     (write 0/1)
//   /Toll_Gate/IR2     (write 0/1)
//   /Toll_Gate/Servo1  (stream 0/1 -> 0°/90°; triggers Payment Success & Price reset)
//   /Toll_Gate/Price   (stream/display; auto-reset to 0 on success)
//
// Libraries: ESP8266WiFi, Servo, Firebase_ESP_Client (mobizt), Adafruit_GFX, Adafruit_SSD1306

#include <ESP8266WiFi.h>
#include <Servo.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#include <Firebase_ESP_Client.h>
#include "addons/TokenHelper.h"
#include "addons/RTDBHelper.h"

// -------- Wi-Fi --------
#define WIFI_SSID       "toll"
#define WIFI_PASSWORD   "123456789"

// -------- Firebase (yours) --------
#define API_KEY         "AIzaSyB9ererNsNonAzH0zQo_GS79XPOyCoMxr4"
#define DATABASE_URL    "https://waterdtection-default-rtdb.firebaseio.com/"
#define USER_EMAIL      "spherenexgpt@gmail.com"
#define USER_PASSWORD   "Spherenex@123"

// -------- Pins (NodeMCU) --------
#define IR1_PIN   D5        // GPIO14
#define IR2_PIN   D6        // GPIO12
#define SERVO_PIN D8        // GPIO15  (signal only)
#define IR_ACTIVE_LOW 1

// -------- OLED --------
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET    -1
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// -------- Firebase paths --------
static const char* PATH_BASE   = "/Toll_Gate";
static const char* PATH_IR1    = "/Toll_Gate/IR1";
static const char* PATH_IR2    = "/Toll_Gate/IR2";
static const char* PATH_SERVO1 = "/Toll_Gate/Servo1";
static const char* PATH_PRICE  = "/Toll_Gate/Price";

// -------- Globals --------
FirebaseData   fbdo;
FirebaseData   streamBase;
FirebaseAuth   auth;
FirebaseConfig config;

Servo servo1;

int  g_ir1 = 1, g_ir2 = 1;
int  g_servoFlag = 0;     // 0 closed, 1 open
int  g_price = -1;        // unknown initially

const uint16_t SERVO_OPEN_DEG   = 90;
const uint16_t SERVO_CLOSED_DEG = 0;

// UI: show "Payment Success" until this time; otherwise show "Payment Pending"
unsigned long g_successUntilMs = 0;

// -------- Helpers --------
inline int irDetected(uint8_t pin) {
  int raw = digitalRead(pin);
  if (IR_ACTIVE_LOW) return (raw == LOW) ? 0 : 1;
  return (raw == HIGH) ? 0 : 1;
}

void driveServoFlag(int flag01) {
  g_servoFlag = flag01 ? 1 : 0;
  uint16_t angle = g_servoFlag ? SERVO_OPEN_DEG : SERVO_CLOSED_DEG;
  servo1.write(angle);
  Serial.printf("[servo] Servo1=%d -> %u deg\n", g_servoFlag, angle);
}

void setPriceInFirebase(int value) {
  if (!Firebase.RTDB.setInt(&fbdo, PATH_PRICE, value)) {
    Serial.printf("[price] set %d failed: %s\n", value, fbdo.errorReason().c_str());
  } else {
    Serial.printf("[price] set -> %d\n", value);
  }
}

// ------- OLED rendering -------
void oledDrawHeader(const char* line1) {
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.print(line1);
}

void oledRenderMain() {
  oledDrawHeader("Toll Gate");

  // Big "Price:"
  display.setTextSize(2);
  display.setCursor(0, 20);
  display.print("Price: ");
  if (g_price >= 0) display.print(g_price);
  else display.print("--");

  // Status line: Pending or Success (only these two)
  display.setTextSize(1);
  display.setCursor(0, 48);
  if (millis() < g_successUntilMs) {
    display.print("Payment Success");
  } else {
    display.print("WELCOME");
  }

  display.display();
}

void oledShowConnecting(const char* ssid) {
  int dots = 0;
  unsigned long tPrev = 0;

  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);

  while (WiFi.status() != WL_CONNECTED) {
    unsigned long now = millis();
    if (now - tPrev >= 400) {
      tPrev = now;
      display.clearDisplay();

      display.setTextSize(1);
      display.setCursor(0, 0);
      display.print("SSID: ");
      display.print(ssid);

      display.setCursor(0, 20);
      display.setTextSize(2);
      display.print("Connecting");
      for (int i = 0; i < dots; i++) display.print('.');
      display.display();

      dots = (dots + 1) % 7;
    }
    delay(10);
  }

  // Brief "Connected" splash
  display.clearDisplay();
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.print("SSID: ");
  display.print(ssid);

  display.setTextSize(2);
  display.setCursor(0, 20);
  display.print("Connected");
  display.setTextSize(1);
  display.setCursor(0, 48);
  display.print("IP: ");
  display.print(WiFi.localIP());
  display.display();
  delay(1000);
}

// -------- Firebase streaming --------
void streamCallback(FirebaseStream data) {
  // Streaming /Toll_Gate; dataPath is like "/Servo1" or "/Price"
  String path = data.dataPath();
  String type = data.dataType();

  if (path == "/Servo1") {
    int v = 0;
    if (type == "int" || type == "float" || type == "double") v = data.intData();
    else if (type == "string") v = (data.stringData() == "1") ? 1 : 0;

    int prev = g_servoFlag;
    driveServoFlag(v);

    // On transition to OPEN (1): show success for 3s and reset price to 0
    if (prev == 0 && v == 1) {
      g_successUntilMs = millis() + 3000UL;
      // Only reset if non-zero (optional); harmless to set anyway
      if (g_price != 0) setPriceInFirebase(0);
    }
    oledRenderMain();
  }
  else if (path == "/Price") {
    int p = g_price;
    if (type == "int" || type == "float" || type == "double") p = data.intData();
    else if (type == "string") p = data.stringData().toInt();
    g_price = p;
    Serial.printf("[price] /Toll_Gate/Price = %d\n", g_price);
    oledRenderMain();
  }
}

void streamTimeoutCallback(bool timeout) {
  if (timeout) Serial.println("[stream] timeout, resuming…");
}

// -------- Connect Wi-Fi (with OLED animation) --------
void connectWiFiWithOLED() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("WiFi connecting to %s\n", WIFI_SSID);
  oledShowConnecting(WIFI_SSID);
  Serial.printf("WiFi OK, IP=%s\n", WiFi.localIP().toString().c_str());
}

void setupFirebase() {
  config.api_key = API_KEY;
  config.database_url = DATABASE_URL;
  auth.user.email = USER_EMAIL;
  auth.user.password = USER_PASSWORD;
  config.token_status_callback = tokenStatusCallback;
  Firebase.reconnectWiFi(true);
  Firebase.begin(&config, &auth);

  // Stream base path so we catch Servo1 & Price
  if (!Firebase.RTDB.beginStream(&streamBase, PATH_BASE)) {
    Serial.printf("[stream] begin failed: %s\n", streamBase.errorReason().c_str());
  } else {
    Firebase.RTDB.setStreamCallback(&streamBase, streamCallback, streamTimeoutCallback);
    Serial.println("[stream] Listening -> /Toll_Gate");
  }

  // Prime initial values
  if (Firebase.RTDB.getInt(&fbdo, PATH_SERVO1)) driveServoFlag(fbdo.intData());
  else if (Firebase.RTDB.getString(&fbdo, PATH_SERVO1)) driveServoFlag(fbdo.stringData() == "1" ? 1 : 0);

  if (Firebase.RTDB.getInt(&fbdo, PATH_PRICE)) g_price = fbdo.intData();
  else if (Firebase.RTDB.getString(&fbdo, PATH_PRICE)) g_price = fbdo.stringData().toInt();
}

// -------- Setup / Loop --------
void setup() {
  Serial.begin(115200);
  delay(50);

  // I2C for OLED
  Wire.begin(D2, D1); // SDA, SCL
  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("[oled] SSD1306 allocation failed");
  } else {
    display.clearDisplay();
    display.display();
  }

  pinMode(IR1_PIN, INPUT_PULLUP);
  pinMode(IR2_PIN, INPUT_PULLUP);

  servo1.attach(SERVO_PIN, 500, 2500);
  servo1.write(SERVO_CLOSED_DEG);

  connectWiFiWithOLED();
  setupFirebase();

  // Seed & publish initial IR state (not shown on OLED)
  g_ir1 = irDetected(IR1_PIN);
  g_ir2 = irDetected(IR2_PIN);
  Firebase.RTDB.setInt(&fbdo, PATH_IR1, g_ir1);
  Firebase.RTDB.setInt(&fbdo, PATH_IR2, g_ir2);

  oledRenderMain();
}

void loop() {
  // Poll IRs ~20 Hz; keep writing to DB (OLED does not show IRs)
  static unsigned long lastPoll = 0;
  unsigned long now = millis();
  if (now - lastPoll >= 50) {
    lastPoll = now;

    int ir1 = irDetected(IR1_PIN);
    int ir2 = irDetected(IR2_PIN);

    if (ir1 != g_ir1) {
      g_ir1 = ir1;
      if (!Firebase.RTDB.setInt(&fbdo, PATH_IR1, g_ir1))
        Serial.printf("[IR1] write fail: %s\n", fbdo.errorReason().c_str());
    }

    if (ir2 != g_ir2) {
      g_ir2 = ir2;
      if (!Firebase.RTDB.setInt(&fbdo, PATH_IR2, g_ir2))
        Serial.printf("[IR2] write fail: %s\n", fbdo.errorReason().c_str());
    }
  }

  // When success window ends, redraw to show "Payment Pending" again
  static bool wasSuccess = false;
  bool successNow = (millis() < g_successUntilMs);
  if (successNow != wasSuccess) {
    wasSuccess = successNow;
    oledRenderMain();
  }
}
