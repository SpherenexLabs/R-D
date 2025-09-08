/*****************************************************
 * ESP8266 (NodeMCU) — Serial + OLED Vitals + Firebase
 * Splash -> WiFi SSID "Connecting..." -> Sensor pages
 * Reads alert text from /KS5160_Lung_Heart/2_Notification/1_Alert
 * and displays it on the last OLED line (Alert: ...).
 *
 * Sound thresholds are EASY TO TUNE:
 *   COUGH_BURST_COUNT : exactly this many highs -> cough
 *   TB_BURST_MIN      : >= this many highs      -> TB
 *****************************************************/

#include <ESP8266WiFi.h>
#include <FirebaseESP8266.h>
#include <addons/TokenHelper.h>
#include <addons/RTDBHelper.h>

#include <DHT.h>
#include <Wire.h>
#include <MAX30105.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// ---- Wi-Fi ----
#define WIFI_SSID       "health"
#define WIFI_PASSWORD   "123456789"

// ---- Firebase ----
#define API_KEY         "AIzaSyAhLCi6JBT5ELkAFxTplKBBDdRdpATzQxI"
#define DATABASE_URL    "https://smart-medicine-vending-machine-default-rtdb.asia-southeast1.firebasedatabase.app"
#define USER_EMAIL      "spherenexgpt@gmail.com"
#define USER_PASSWORD   "Spherenex@123"

// ---- Firebase objects ----
FirebaseData fbdo;
FirebaseAuth auth;
FirebaseConfig config;
bool firebaseReady = false;

// ---- Firebase paths ----
const char* FB_ROOT       = "/KS5160_Lung_Heart/1_Sensor_Data";
const char* FB_ALERT_PATH = "/KS5160_Lung_Heart/2_Notification/1_Alert";

// ---- Update ticks ----
const unsigned long FB_CHECK_MS   = 200;   // push-if-changed check
unsigned long lastFbCheckMs       = 0;

const unsigned long ALERT_POLL_MS = 2000;  // read alert text interval
unsigned long lastAlertPollMs     = 0;
String alertText = "";

// -------- Pins --------
#define SOUND_SENSOR    D5
#define CO2_SENSOR      D6
#define DHT_PIN         D7
#define ALCOHOL_SENSOR  D8

// -------- DHT11 --------
#define DHTTYPE DHT11
DHT dht(DHT_PIN, DHTTYPE);

// -------- OLED --------
#define SCREEN_WIDTH    128
#define SCREEN_HEIGHT   64
#define OLED_RESET_PIN  -1
#define OLED_I2C_ADDR   0x3C
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET_PIN);

// -------- Sound detection --------
volatile int soundCount = 0;
unsigned long lastSoundMs = 0;
unsigned long burstLastEventMs = 0;
bool inBurst = false;

const unsigned long ASTHMATIC_MS = 10000;
const unsigned long BURST_GAP_MS = 1200;
const unsigned long DEBOUNCE_MS  = 80;

// ==== EASY-TO-TUNE SOUND THRESHOLDS ====
const int COUGH_BURST_COUNT = 1;  // exactly this many highs -> cough
const int TB_BURST_MIN      = 6;  // >= this many highs -> TB  (change here)

// ---- Sound codes for Firebase 8_sound ----
#define SOUND_NONE    0
#define SOUND_COUGH   1
#define SOUND_TB      2
#define SOUND_ASTHMA  3

unsigned long lastSoundEventMs = 0;
int  lastSoundEventCode = SOUND_NONE;

const unsigned long SOUND_EVENT_MIN_GAP_MS = 2500;
unsigned long lastSoundWriteMs = 0;
int  lastSentSoundCode = 0;

bool pendingCough = false;
unsigned long pendingCoughMs = 0;
const unsigned long COUGH_CONFIRM_MS = 6000;

// -------- MAX30105 --------
#define FINGER_IR_THRESHOLD 20000UL
MAX30105 particleSensor;
bool max30105_ok = false;

// -------- HR constraints --------
#define HR_MIN 70
#define HR_MAX 120

// ----- Live vitals -----
int heartRate_live = 0;   // bpm
int spo2_live      = 0;   // %
int systolic_live  = 0;   // mmHg (sim)
int diastolic_live = 0;   // mmHg (sim)

// ----- Latched (frozen) vitals -----
int heartRate_latched = 0;
int spo2_latched      = 0;
int systolic_latched  = 0;
int diastolic_latched = 0;
bool haveLatched = false;

// ---- HR detection state ----
uint32_t lastIr = 0;
bool hrPeak = false;
unsigned long lastBeatMs = 0;
int tempHR = 80;

// ---- BP(sim) state ----
unsigned long lastBPms = 0;
int targetSys = 120, targetDia = 80;
int curSys    = 120, curDia    = 80;

// ---------- Calibration / freeze ----------
enum VitalsState { NO_FINGER, CALIBRATING, FROZEN };
VitalsState vitalsState = NO_FINGER;

const unsigned long CAL_WINDOW_MS = 10000;
unsigned long calStartMs = 0;

uint32_t sumHR = 0, cntHR = 0;
uint32_t sumSp = 0, cntSp = 0;
uint32_t sumSy = 0, cntSy = 0;
uint32_t sumDi = 0, cntDi = 0;

// ========== Change-only cache for Firebase ==========
int last_sent_co2      = -1;
int last_sent_alcohol  = -1;
int last_sent_temp     = -1000;
int last_sent_hum      = -1000;
int last_sent_sys      = -1;
int last_sent_dia      = -1;
int last_sent_hr       = -1;
int last_sent_spo2     = -1;

// ---- Prototypes ----
void simulateBP();
void readMAX30105Vitals();
void updateCalibrationState(bool fingerPresent);
void printVitalsLine();
void updateOLED(float temperature, float humidity);
void classifyAndResetBurst();

void initFirebase();
void pushIfChanged(int co2Flag,int alcoholFlag,int soundCode,int tempC_int,int hum_int,int sys,int dia,int hr,int spo2);
void readAlertFromFirebase();

// ---------- OLED helpers ----------
void oledSplash();
void oledWiFiConnecting(const char* ssid, int dots);
void oledWiFiConnected(IPAddress ip);

// ================== Setup ==================
void setup() {
  Serial.begin(115200);

  pinMode(SOUND_SENSOR, INPUT);
  pinMode(CO2_SENSOR, INPUT);
  pinMode(ALCOHOL_SENSOR, INPUT);
  dht.begin();

  Wire.begin(D2, D1); // I2C

  // OLED init & splash
  if (!display.begin(SSD1306_SWITCHCAPVCC, OLED_I2C_ADDR)) {
    Serial.println("SSD1306 init failed!");
  } else {
    oledSplash();
  }

  // MAX30105
  if (particleSensor.begin(Wire, I2C_SPEED_FAST)) {
    particleSensor.setup(60, 4, 2, 400, 411, 4096);
    particleSensor.setPulseAmplitudeRed(60);
    particleSensor.setPulseAmplitudeIR(60);
    particleSensor.setPulseAmplitudeGreen(0);
    max30105_ok = true;
    Serial.println("MAX30105 OK. Place finger for 10s calibration.");
  } else {
    max30105_ok = false;
    Serial.println("MAX30105 not found — vitals will show NA.");
  }

  // Wi-Fi connect with OLED “SSID + Connecting…”
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int tries = 0, dots = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 80) { // ~20 s
    oledWiFiConnecting(WIFI_SSID, dots);
    dots = (dots + 1) % 4;
    delay(250);
    Serial.print(".");
    tries++;
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("WiFi OK, IP: "); Serial.println(WiFi.localIP());
    oledWiFiConnected(WiFi.localIP());
    delay(1200);
  } else {
    Serial.println("WiFi FAILED");
  }

  initFirebase();

  Serial.println("🌡️ ESP8266 Environmental + Sound + Vitals Monitor Ready");
}

// ================== Loop ==================
void loop() {
  unsigned long now = millis();

  // ---------- SOUND ----------
  int s = digitalRead(SOUND_SENSOR);
  if (s == HIGH) {
    if (now - burstLastEventMs > DEBOUNCE_MS) {
      if (!inBurst) { inBurst = true; soundCount = 0; }
      soundCount++;
      burstLastEventMs = now;
      lastSoundMs = now;

      // TB immediate if >= TB_BURST_MIN highs
      if (soundCount >= TB_BURST_MIN) {
        Serial.print("🔴 Detected: TB (>= "); Serial.print(TB_BURST_MIN); Serial.println(" highs)");
        lastSoundEventMs   = now;
        lastSoundEventCode = SOUND_TB;
        if (pendingCough) { pendingCough = false; Serial.println("↪️ TB overrides pending cough"); }
        soundCount = 0; inBurst = false;
      }
    }
  }
  if (inBurst && (now - burstLastEventMs > BURST_GAP_MS)) classifyAndResetBurst();
  if (lastSoundMs != 0 && (now - lastSoundMs >= ASTHMATIC_MS)) {
    Serial.println("⚠️ Possible Asthmatic (no sound ≥ 10s)");
    lastSoundEventMs   = now;
    lastSoundEventCode = SOUND_ASTHMA;
    if (pendingCough) { pendingCough = false; Serial.println("↪️ Asthma cancels pending cough"); }
    classifyAndResetBurst();
    lastSoundMs = now;
  }
  // Cough confirmation timeout
  if (pendingCough && (millis() - pendingCoughMs >= COUGH_CONFIRM_MS)) {
    lastSoundEventMs   = millis();
    lastSoundEventCode = SOUND_COUGH;
    pendingCough = false;
    Serial.println("🟠 Cough confirmed (no TB within window)");
  }

  // ---------- CO₂ ----------
  int co2Flag = (digitalRead(CO2_SENSOR) == LOW) ? 1 : 0;
  Serial.println(co2Flag ? "⚠️ High CO₂ detected!" : "✅ CO₂ level normal.");

  // ---------- Alcohol ----------
  int alcoholFlag = (digitalRead(ALCOHOL_SENSOR) == LOW) ? 1 : 0;
  Serial.println(alcoholFlag ? "⚠️ Alcohol detected!" : "✅ No Alcohol.");

  // ---------- DHT11 ----------
  float temperature = dht.readTemperature();
  float humidity    = dht.readHumidity();
  if (isnan(temperature) || isnan(humidity)) {
    Serial.println("❌ Failed to read from DHT11 sensor!");
  } else {
    Serial.print("🌡️ Temp: "); Serial.print(temperature);
    Serial.print(" °C | 💧 Humidity: "); Serial.print(humidity);
    Serial.println(" %");
  }

  // ---------- Vitals ----------
  readMAX30105Vitals();
  bool fingerPresent = max30105_ok && (particleSensor.getIR() >= FINGER_IR_THRESHOLD);
  updateCalibrationState(fingerPresent);
  printVitalsLine();

  // ---------- Firebase: read alert text ----------
  if (firebaseReady && (millis() - lastAlertPollMs >= ALERT_POLL_MS)) {
    lastAlertPollMs = millis();
    if (Firebase.getString(fbdo, FB_ALERT_PATH)) {
      String v = fbdo.stringData(); v.trim();
      if (v.length() == 0) v = "-";
      if (v != alertText) { alertText = v; Serial.print("Firebase Alert: "); Serial.println(alertText); }
    }
  }

  // ---------- OLED ----------
  updateOLED(temperature, humidity);

  // ---------- Firebase push (change-only) ----------
  if (firebaseReady && (millis() - lastFbCheckMs >= FB_CHECK_MS)) {
    lastFbCheckMs = millis();

    int soundEventToWrite = SOUND_NONE;
    if (lastSoundEventCode != SOUND_NONE) {
      bool allowSameAgain = (millis() - lastSoundWriteMs >= SOUND_EVENT_MIN_GAP_MS);
      if (lastSoundEventCode != lastSentSoundCode || allowSameAgain) soundEventToWrite = lastSoundEventCode;
    }

    int temp_i = isnan(temperature) ? 0 : (int)(temperature + 0.5f);
    int hum_i  = isnan(humidity)    ? 0 : (int)(humidity + 0.5f);

    int sys = (haveLatched ? systolic_latched  : 0);
    int dia = (haveLatched ? diastolic_latched : 0);
    int hr  = (haveLatched ? heartRate_latched : 0);
    int sp  = (haveLatched ? spo2_latched      : 0);

    pushIfChanged(co2Flag, alcoholFlag, soundEventToWrite, temp_i, hum_i, sys, dia, hr, sp);

    if (soundEventToWrite != SOUND_NONE) {
      lastSoundWriteMs   = millis();
      lastSentSoundCode  = soundEventToWrite;
    }
  }

  Serial.println("---------------------------");
  delay(10);
}

// ================== Sound classification at burst end ==================
void classifyAndResetBurst() {
  if (!inBurst) return;

  if (soundCount >= TB_BURST_MIN) {
    Serial.print("🔴 Detected: TB (>= "); Serial.print(TB_BURST_MIN); Serial.println(" highs)");
    lastSoundEventMs   = millis();
    lastSoundEventCode = SOUND_TB;
    if (pendingCough) { pendingCough = false; Serial.println("↪️ TB overrides pending cough"); }
  }
  else if (soundCount == COUGH_BURST_COUNT) {
    // start cough confirmation window
    if (!pendingCough) {
      pendingCough   = true;
      pendingCoughMs = millis();
      Serial.print("🟠 Cough candidate ("); Serial.print(COUGH_BURST_COUNT); Serial.println(" highs) — checking for TB...");
    }
  }
  else if (soundCount == 1 || (soundCount > COUGH_BURST_COUNT && soundCount < TB_BURST_MIN)) {
    Serial.print("ℹ️ Sound burst: "); Serial.print(soundCount); Serial.println(" highs (no label)");
  }

  soundCount = 0;
  inBurst = false;
}

// ================== MAX30105 live vitals ==================
void readMAX30105Vitals() {
  if (!max30105_ok) { heartRate_live = 0; spo2_live = 0; systolic_live = 0; diastolic_live = 0; return; }

  particleSensor.check();
  long ir  = particleSensor.getIR();
  long red = particleSensor.getRed();

  if (ir < FINGER_IR_THRESHOLD) {
    heartRate_live = 0; spo2_live = 0; systolic_live = 0; diastolic_live = 0;
    lastIr = ir; return;
  }

  if (ir > lastIr && !hrPeak) {
    hrPeak = true;
  } else if (ir < lastIr && hrPeak) {
    unsigned long now = millis();
    unsigned long ibi = now - lastBeatMs;
    if (ibi > 300 && ibi < 2000) {
      int newHR = (int)(60000UL / ibi);
      if (newHR >= 50 && newHR <= 180) tempHR = newHR;
    }
    lastBeatMs = now; hrPeak = false;
  }
  lastIr = ir;

  heartRate_live = tempHR;
  if (heartRate_live < HR_MIN || heartRate_live > HR_MAX) heartRate_live = 0;

  float ratio = (float)red / (float)ir;
  int est = 110 - int(25.0f * ratio);
  est = constrain(est, 90, 99);
  spo2_live = est;

  simulateBP();
}
void simulateBP() {
  unsigned long now = millis();
  if (now - lastBPms > 10000) {
    int hrAdj = 0; if (heartRate_live > 0) hrAdj = constrain(heartRate_live - 70, -20, 40);
    targetSys = 118 + (hrAdj / 8) + random(-4, 5);
    targetDia = 78  + random(-3, 4);
    lastBPms  = now;
  }
  if (curSys < targetSys) curSys++;
  if (curSys > targetSys) curSys--;
  if (curDia < targetDia) curDia++;
  if (curDia > targetDia) curDia--;
  systolic_live  = curSys;
  diastolic_live = curDia;
}

// ========== Calibration / freeze ==========
void updateCalibrationState(bool fingerPresent) {
  unsigned long now = millis();

  switch (vitalsState) {
    case NO_FINGER:
      if (fingerPresent) {
        vitalsState = CALIBRATING;
        calStartMs = now;
        sumHR = sumSp = sumSy = sumDi = 0;
        cntHR = cntSp = cntSy = cntDi = 0;
        Serial.println("⏱️ Calibration started (10s)...");
      }
      break;

    case CALIBRATING:
      if (!fingerPresent) {
        vitalsState = NO_FINGER;
        Serial.println("⚠️ Finger removed — calibration aborted, showing previous values.");
        break;
      }
      if (heartRate_live > 0) { sumHR += heartRate_live; cntHR++; }
      if (spo2_live     > 0) { sumSp += spo2_live;       cntSp++; }
      if (systolic_live > 0 && diastolic_live > 0) { sumSy += systolic_live; cntSy++; sumDi += diastolic_live; cntDi++; }

      if (now - calStartMs >= CAL_WINDOW_MS) {
        heartRate_latched = (cntHR > 0) ? (int)((sumHR + cntHR/2) / cntHR) : 0;
        heartRate_latched = constrain(heartRate_latched, HR_MIN, HR_MAX);
        spo2_latched      = (cntSp > 0) ? (int)((sumSp + cntSp/2) / cntSp) : 0;
        if (cntSy > 0 && cntDi > 0) {
          systolic_latched  = (int)((sumSy + cntSy/2) / cntSy);
          diastolic_latched = (int)((sumDi + cntDi/2) / cntDi);
        } else { systolic_latched = diastolic_latched = 0; }
        haveLatched = true;
        vitalsState = FROZEN;
        Serial.println("✅ Calibration complete — values latched.");
      }
      break;

    case FROZEN:
      if (!fingerPresent) {
        vitalsState = NO_FINGER;
        Serial.println("👆 Finger removed — holding last calibrated values.");
      }
      break;
  }
}

// ================== Serial print (frozen values) ==================
void printVitalsLine() {
  Serial.print("HR=");
  Serial.print(haveLatched && heartRate_latched > 0 ? String(heartRate_latched) : "NA");
  Serial.print(" bpm | SpO2=");
  Serial.print(haveLatched && spo2_latched > 0 ? String(spo2_latched) : "NA");
  Serial.print(" % | BP=");
  if (haveLatched && systolic_latched > 0 && diastolic_latched > 0) {
    Serial.print(systolic_latched); Serial.print("/"); Serial.print(diastolic_latched);
  } else Serial.print("NA");
  Serial.print(" mmHg");

  if (vitalsState == CALIBRATING) {
    float secs = (millis() - calStartMs) / 1000.0f;
    Serial.print("  |  Calibration: "); Serial.print(secs,1); Serial.print("/10 s");
  } else if (vitalsState == FROZEN) {
    Serial.print("  |  Calibration Complete");
  }
  if (pendingCough) {
    int left = (int)((COUGH_CONFIRM_MS - (millis() - pendingCoughMs)) / 1000);
    if (left < 0) left = 0;
    Serial.print("  |  Pending cough… "); Serial.print(left); Serial.print("s");
  }
  Serial.println();
}

// ================== OLED: sensor page ==================
void updateOLED(float temperature, float humidity) {
  static unsigned long lastOLED = 0;
  if (millis() - lastOLED < 300) return;   // ~3 fps
  lastOLED = millis();

  const int LEFT_X  = 0;
  const int RIGHT_X = 68;
  int y = 0;

  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);

  // Status
  display.setCursor(LEFT_X, y);
  if (vitalsState == CALIBRATING) {
    int secs = (int)((millis() - calStartMs) / 1000);
    display.print("CALIBRATION "); display.print(secs); display.print("/10s");
  } else if (haveLatched) {
    display.print("CALIBRATION COMPLETE");
  } else {
    display.print("READY");
  }
  y += 12;

  // HR | SpO2
  display.setCursor(LEFT_X, y);
  display.print("HR: ");
  if (haveLatched && heartRate_latched > 0) { display.print(heartRate_latched); display.print(" bpm"); }
  else display.print("NA");
  display.setCursor(RIGHT_X, y);
  display.print("SpO2: ");
  if (haveLatched && spo2_latched > 0) { display.print(spo2_latched); display.print(" %"); }
  else display.print("NA");
  y += 12;

  // BP
  display.setCursor(LEFT_X, y);
  display.print("BP: ");
  if (haveLatched && systolic_latched > 0 && diastolic_latched > 0) {
    display.print(systolic_latched); display.print("/"); display.print(diastolic_latched); display.print(" mmHg");
  } else display.print("NA");
  y += 12;

  // Temp | Hum
  display.setCursor(LEFT_X, y);
  display.print("T: ");
  if (!isnan(temperature)) { display.print(temperature, 1); display.print(" C"); }
  else display.print("NA");
  display.setCursor(RIGHT_X, y);
  display.print("H: ");
  if (!isnan(humidity)) { display.print(humidity, 1); display.print(" %"); }
  else display.print("NA");
  y += 12;

  // Alert line
  display.setCursor(LEFT_X, y);
  display.print("Alert: ");
  if (alertText.length() > 14) display.print(alertText.substring(0, 14));
  else display.print(alertText.length() ? alertText : "-");

  display.display();
}

// ================== Firebase init & change-only writes ==================
void initFirebase() {
  if (WiFi.status() != WL_CONNECTED) { firebaseReady = false; return; }
  config.api_key = API_KEY;
  config.database_url = DATABASE_URL;
  config.token_status_callback = tokenStatusCallback;
  auth.user.email = USER_EMAIL;
  auth.user.password = USER_PASSWORD;
  Firebase.reconnectWiFi(true);
  Firebase.begin(&config, &auth);
  int waitMs = 0; while (!Firebase.ready() && waitMs < 8000) { delay(200); waitMs += 200; }
  firebaseReady = Firebase.ready();
  Serial.println(firebaseReady ? "Firebase connected." : "Firebase NOT ready.");
}

void pushIfChanged(int co2Flag,int alcoholFlag,int soundCode,int tempC_int,int hum_int,int sys,int dia,int hr,int spo2) {
  if (!firebaseReady) return;
  if (co2Flag != last_sent_co2)   { Firebase.setString(fbdo, String(FB_ROOT) + "/1_co2", String(co2Flag)); last_sent_co2 = co2Flag; }
  if (alcoholFlag != last_sent_alcohol) { Firebase.setString(fbdo, String(FB_ROOT) + "/2_alcohol", String(alcoholFlag)); last_sent_alcohol = alcoholFlag; }
  if (tempC_int != last_sent_temp){ Firebase.setString(fbdo, String(FB_ROOT) + "/3_temp", String(tempC_int)); last_sent_temp = tempC_int; }
  if (hum_int  != last_sent_hum)  { Firebase.setString(fbdo, String(FB_ROOT) + "/4_hum", String(hum_int));  last_sent_hum = hum_int;  }
  if (dia != last_sent_dia)       { Firebase.setString(fbdo, String(FB_ROOT) + "/5_bp/1_diastolic", String(dia)); last_sent_dia = dia; }
  if (sys != last_sent_sys)       { Firebase.setString(fbdo, String(FB_ROOT) + "/5_bp/2_systolic",  String(sys)); last_sent_sys = sys; }
  if (hr  != last_sent_hr)        { Firebase.setString(fbdo, String(FB_ROOT) + "/6_hr",   String(hr));  last_sent_hr = hr; }
  if (spo2!= last_sent_spo2)      { Firebase.setString(fbdo, String(FB_ROOT) + "/7_spo2", String(spo2)); last_sent_spo2 = spo2; }
  if (soundCode != SOUND_NONE)    { Firebase.setString(fbdo, String(FB_ROOT) + "/8_sound", String(soundCode)); }
}

// ================== OLED helper screens ==================
void oledSplash() {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 10);
  display.println("Lungs & Heart");
  display.println("Disease Analysis");
  display.println();
  display.println("Initializing...");
  display.display();
}
void oledWiFiConnecting(const char* ssid, int dots) {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 10);
  display.println("WiFi:");
  display.print("SSID: "); display.println(ssid);
  display.println();
  display.print("Connecting");
  for (int i = 0; i < dots; i++) display.print(".");
  display.display();
}
void oledWiFiConnected(IPAddress ip) {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 10);
  display.println("WiFi Connected!");
  display.print("IP: ");
  display.println(ip);
  display.display();
}
