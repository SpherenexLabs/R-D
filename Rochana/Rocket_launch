#include <Arduino.h>
#include <Wire.h>
#include <ESP8266WiFi.h>
#include <arduinoFFT.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#include <Firebase_ESP_Client.h>
#include "addons/TokenHelper.h"   // optional (debug)
#include "addons/RTDBHelper.h"    // optional (debug)

// ---------- User config ----------
#define WIFI_SSID       "rocket"
#define WIFI_PASSWORD   "123456789"

#define API_KEY         "AIzaSyAhLCi6JBT5ELkAFxTplKBBDdRdpATzQxI"
#define DATABASE_URL    "https://smart-medicine-vending-machine-default-rtdb.asia-southeast1.firebasedatabase.app"
#define USER_EMAIL      "spherenexgpt@gmail.com"
#define USER_PASSWORD   "Spherenex@123"

// Firebase path root (events are pushed here)
#define FB_ROOT                 "/1_KS5169_Rocket_Detection"
#define FB_LAST_BOOT            FB_ROOT "/last_boot"

// ---------- Hardware ----------
#define MIC_PIN        A0              // analog mic (≤1.0 V max into A0!)
#define RADAR_PIN      D5              // RCWL-0516 OUT (HIGH when motion)

// OLED (NodeMCU: SDA=D2/GPIO4, SCL=D1/GPIO5)
#define SCREEN_WIDTH   128
#define SCREEN_HEIGHT  64
#define OLED_RESET     -1
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// ---------- DSP / Detection ----------
#define SAMPLES              128       // power-of-2
#define SAMPLING_FREQ        9600      // Hz  (bin ≈ 75 Hz)
#define BAND_MIN_HZ          800
#define BAND_MAX_HZ          1600
#define WINDOW_MS            2000      // 2 s window
#define HITS_THRESHOLD       3         // >=3 in-band peaks within WINDOW_MS -> detection

// ---------- Globals ----------
ArduinoFFT<double> FFT;


double vReal[SAMPLES];
double vImag[SAMPLES];
unsigned int sampPeriod_us = (unsigned int)round(1000000.0 / SAMPLING_FREQ);

FirebaseData fbdo;
FirebaseAuth auth;
FirebaseConfig fbCfg;

unsigned long windowStart = 0;
int hitsInWindow = 0;
bool detectedLatched = false;

unsigned long lastRadarHighMs = 0;

// ---------- Helpers ----------
void oledHeader(const char* line) {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.println("Rocket Band Monitor");
  if (line) {
    display.setCursor(0, 10);
    display.println(line);
  }
}

void wifiConnect() {
  oledHeader("WiFi connecting...");
  display.display();

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(250);
    Serial.print('.');
  }
  Serial.println();
  Serial.print("SSID: "); Serial.print(WiFi.SSID());
  Serial.print("  IP: "); Serial.println(WiFi.localIP());

  oledHeader("WiFi connected");
  display.setCursor(0, 22); display.print("SSID: "); display.println(WiFi.SSID());
  display.setCursor(0, 32); display.print("IP: ");   display.println(WiFi.localIP());
  display.display();
  delay(900);
}

bool firebaseInit() {
  fbCfg.api_key = API_KEY;
  fbCfg.database_url = DATABASE_URL;
  auth.user.email = USER_EMAIL;
  auth.user.password = USER_PASSWORD;

  Firebase.reconnectWiFi(true);
  Firebase.begin(&fbCfg, &auth);

  String s = String("boot ok | SSID=") + WiFi.SSID() + " | IP=" + WiFi.localIP().toString();
  bool ok = Firebase.RTDB.setString(&fbdo, FB_LAST_BOOT, s);
  if (!ok) {
    Serial.print("FB boot write failed: "); Serial.println(fbdo.errorReason());
  }
  return ok;
}

// DC offset removal improves peak accuracy
inline void removeDC(double* x, int n) {
  double sum = 0; for (int i = 0; i < n; i++) sum += x[i];
  double mean = sum / n; for (int i = 0; i < n; i++) x[i] -= mean;
}

void pushEvent(double peakHz, bool radarHigh, int hits) {
  FirebaseJson j;
  j.set("ts_ms", (int64_t)millis());
  j.set("ssid", WiFi.SSID());
  j.set("ip", WiFi.localIP().toString());
  j.set("radar", radarHigh ? 1 : 0);
  j.set("peak_hz", peakHz);
  j.set("hits", hits);
  j.set("band_min_hz", BAND_MIN_HZ);
  j.set("band_max_hz", BAND_MAX_HZ);
  if (!Firebase.RTDB.pushJSON(&fbdo, FB_ROOT, &j)) {
    Serial.print("FB pushJSON error: "); Serial.println(fbdo.errorReason());
  }
}

// ---------- Setup ----------
void setup() {
  Serial.begin(115200);
  delay(50);

  Wire.begin(D2, D1); // SDA, SCL
  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("SSD1306 not found");
    while (1) { delay(1); }
  }

  pinMode(RADAR_PIN, INPUT);

  oledHeader("Initializing...");
  display.display();

  wifiConnect();
  firebaseInit();

  windowStart = millis();

  oledHeader("Ready");
  display.setCursor(0, 22); display.print("SSID: "); display.println(WiFi.SSID());
  display.setCursor(0, 32); display.print("IP: ");   display.println(WiFi.localIP());
  display.display();
  delay(700);
}

// ---------- Loop ----------
void loop() {
  // 1) Radar state
  bool radarHigh = (digitalRead(RADAR_PIN) == HIGH);
  if (radarHigh) lastRadarHighMs = millis();

  // 2) Acquire fixed-rate samples
  for (int i = 0; i < SAMPLES; i++) {
    unsigned long t0 = micros();
    vReal[i] = analogRead(MIC_PIN);  // 0..1023
    vImag[i] = 0.0;
    while ((micros() - t0) < sampPeriod_us) { } // wait to keep Fs constant
  }

  // 3) FFT → peak frequency
  removeDC(vReal, SAMPLES);
  FFT.windowing(vReal, SAMPLES, FFT_WIN_TYP_HAMMING, FFT_FORWARD);
  FFT.compute(vReal, vImag, SAMPLES, FFT_FORWARD);
  FFT.complexToMagnitude(vReal, vImag, SAMPLES);
  double peakHz = FFT.majorPeak(vReal, SAMPLES, SAMPLING_FREQ);

  // 4) Band hit counting
  bool inBand = (peakHz >= BAND_MIN_HZ && peakHz <= BAND_MAX_HZ);
  if (inBand) hitsInWindow++;

  // 5) OLED + Serial (minimal as requested)
  oledHeader(nullptr);
  display.setCursor(0, 12); display.print("RADAR: ");
  display.println(radarHigh ? "HIGH" : "LOW");
  display.setCursor(0, 22); display.print("Peak: ");
  display.print(peakHz, 1); display.println(" Hz");
  display.setCursor(0, 32); display.print("Hits: ");
  display.print(hitsInWindow); display.print('/'); display.println(HITS_THRESHOLD);

  Serial.print("RADAR="); Serial.print(radarHigh ? "HIGH" : "LOW");
  Serial.print("  Peak="); Serial.print(peakHz, 1); Serial.print(" Hz");
  Serial.print("  Hits="); Serial.print(hitsInWindow); Serial.print('/'); Serial.println(HITS_THRESHOLD);

  // 6) Window decision
  unsigned long now = millis();
  if (now - windowStart >= WINDOW_MS) {
    if (hitsInWindow >= HITS_THRESHOLD && !detectedLatched) {
      detectedLatched = true;

      // OLED banner
      display.setCursor(0, 44);
      display.println(">>> ROCKET DETECTION <<<");
      display.display();

      // One JSON event to Firebase
      pushEvent(peakHz, radarHigh, hitsInWindow);

      Serial.println("=== ROCKET DETECTION ===");
    } else {
      // unlatch when quiet in the next window
      detectedLatched = false;
    }

    // reset window
    windowStart = now;
    hitsInWindow = 0;
  } else {
    // keep current OLED frame
    if (detectedLatched) {
      display.setCursor(0, 44);
      display.println("ROCKET DETECTION ");
    } else if (inBand) {
      display.setCursor(0, 44);
      display.println("IN BAND...");
    } else {
      display.setCursor(0, 44);
      display.println("Listening...");
    }
    display.display();
  }

  delay(35); // readability
}

