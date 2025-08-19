//YOUTUBE LINK FOR SOUND TESTING:https://youtu.be/dw6AvjAyFKs?feature=shared

#include <Arduino.h>
#include <Wire.h>
#include <ESP8266WiFi.h>
#include <arduinoFFT.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#include <Firebase_ESP_Client.h>
#include "addons/TokenHelper.h"    // optional
#include "addons/RTDBHelper.h"     // optional
#include <math.h>

// ---- Wi-Fi ----
#define WIFI_SSID       "rocket"
#define WIFI_PASSWORD   "123456789"

// ---- Firebase ----
#define API_KEY         "AIzaSyAhLCi6JBT5ELkAFxTplKBBDdRdpATzQxI"
#define DATABASE_URL    "https://smart-medicine-vending-machine-default-rtdb.asia-southeast1.firebasedatabase.app"
#define USER_EMAIL      "spherenexgpt@gmail.com"
#define USER_PASSWORD   "Spherenex@123"

#define FB_ROOT                 "/1_KS5169_Rocket_Detection"
#define FB_MOVEMENT_DETECTED    FB_ROOT "/Movement_Detected"
#define FB_SOUND_DETECTED       FB_ROOT "/Sound_Detected"

// ---- Hardware ----
#define MIC_PIN        A0          // ensure ≤1.0 V into A0 (use divider if needed)
#define RADAR_PIN      D5          // RCWL-0516 OUT (HIGH on motion)

// OLED (NodeMCU: SDA=D2/GPIO4, SCL=D1/GPIO5)
#define SCREEN_WIDTH   128
#define SCREEN_HEIGHT  64
#define OLED_RESET     -1
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// ---- FFT ----
#define SAMPLES        128         // power of 2
#define SAMPLING_FREQ  9600        // Hz  (bin ≈ 75 Hz)
#define SOUND_THRESH_HZ 1500.0      // >=1500 Hz => Sound_Detected = "1"
ArduinoFFT<double> FFT;
double vReal[SAMPLES];
double vImag[SAMPLES];
unsigned int sampPeriod_us = (unsigned int)round(1000000.0 / SAMPLING_FREQ);

// ---- Firebase objs ----
FirebaseData fbdo;
FirebaseAuth auth;
FirebaseConfig fbCfg;
// ---- State (for change detection) ----
int lastMotion = 0;
bool lastSound  = false;
// ---- Helpers ----
inline void removeDC(double* x, int n) {
  double sum = 0; for (int i = 0; i < n; i++) sum += x[i];
  double mean = sum / n; for (int i = 0; i < n; i++) x[i] -= mean;
}

void wifiConnect() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("WiFi connecting");
  while (WiFi.status() != WL_CONNECTED) { delay(250); Serial.print("."); }
  Serial.printf("\nWiFi OK  SSID=%s  IP=%s\n", WiFi.SSID().c_str(), WiFi.localIP().toString().c_str());
}

bool firebaseInit() {
  fbCfg.api_key       = API_KEY;
  fbCfg.database_url  = DATABASE_URL;
  auth.user.email     = USER_EMAIL;
  auth.user.password  = USER_PASSWORD;

  Firebase.reconnectWiFi(true);
  Firebase.begin(&fbCfg, &auth);
  // Initialize both keys to "0" on boot (optional)
  bool ok1 = Firebase.RTDB.setString(&fbdo, FB_MOVEMENT_DETECTED, "0");
  bool ok2 = Firebase.RTDB.setString(&fbdo, FB_SOUND_DETECTED,    "0");
  if (!ok1 || !ok2) {
    Serial.print("Firebase init write failed: "); Serial.println(fbdo.errorReason());
  }
  return ok1 && ok2;
}

void fbSetMovement(int motion) {
  // exact string "1" / "0"
  if (!Firebase.RTDB.setString(&fbdo, FB_MOVEMENT_DETECTED, motion ? "1" : "0")) {
    Serial.print("FB movement set error: "); Serial.println(fbdo.errorReason());
  }
}

void fbSetSound(bool sound) {
  // exact string "1" / "0"
  if (!Firebase.RTDB.setString(&fbdo, FB_SOUND_DETECTED, sound ? "1" : "0")) {
    Serial.print("FB sound set error: "); Serial.println(fbdo.errorReason());
  }
}

void drawOLED(int motion, bool sound, double peakHz) {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);

  display.setCursor(0, 0);   display.println("Rocket Monitor");
  display.setCursor(0, 12);  display.print("RADAR: ");
  display.println(motion ? "1" : "0");
  display.setCursor(0, 24);  display.print("SOUND: ");
  display.println(sound ? "1" : "0");
  display.setCursor(0, 36);  display.print("Peak: ");
  display.print(peakHz, 1);  display.println(" Hz");
  display.setCursor(0, 50);  display.print("SSID: ");
  display.println(WiFi.SSID());

  display.display();
}

// ---- Setup ----
void setup() {
  Serial.begin(115200);
  delay(50);

  Wire.begin(D2, D1); // SDA, SCL
  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("SSD1306 not found"); while (1) { delay(1); }
  }
  display.clearDisplay(); display.display();

  pinMode(RADAR_PIN, INPUT);
  wifiConnect();
  firebaseInit();
  drawOLED(false, false, 0.0);
}

// ---- Loop ----
void loop() {
  // 1) Motion from radar
  int motion = (digitalRead(RADAR_PIN) == HIGH);

  // 2) Acquire samples at fixed Fs
  for (int i = 0; i < SAMPLES; i++) {
    unsigned long t0 = micros();
    vReal[i] = analogRead(MIC_PIN);  // 0..1023
    vImag[i] = 0.0;
    while ((micros() - t0) < sampPeriod_us) { } // keep Fs constant
  }

  // 3) FFT → peak frequency
  removeDC(vReal, SAMPLES);
  FFT.windowing(vReal, SAMPLES, FFT_WIN_TYP_HAMMING, FFT_FORWARD);
  FFT.compute(vReal, vImag, SAMPLES, FFT_FORWARD);
  FFT.complexToMagnitude(vReal, vImag, SAMPLES);
  double peakHz = FFT.majorPeak(vReal, SAMPLES, SAMPLING_FREQ);

  // 4) Sound decision
  bool sound = (peakHz >= SOUND_THRESH_HZ);

  // 5) Update OLED + Serial
  drawOLED(motion, sound, peakHz);
  Serial.printf("RADAR=%s  SOUND=%s  Peak=%.1f Hz\n",
                motion ? "1" : "0",
                sound  ? "1" : "0",
                peakHz);

  // 6) Update Firebase only on change (exact strings "1"/"0")
  if (motion != lastMotion) { fbSetMovement(motion); lastMotion = motion; }
  if (sound  != lastSound)  { fbSetSound(sound);     lastSound  = sound;  }

  delay(35); // readability
}

