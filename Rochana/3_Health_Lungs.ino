/*****************************************************
 * ESP8266 (NodeMCU) — Serial + OLED Vitals + Firebase
 * Log to Firebase ONLY when a value CHANGES.
 *
 * Pins:
 *   D5 : Sound (digital)         D6 : CO₂ (digital)
 *   D7 : DHT11 (Temp/Hum)        D8 : Alcohol (digital)
 *   D2 : I2C SDA -> MAX30105 + SSD1306
 *   D1 : I2C SCL -> MAX30105 + SSD1306
 *
 * OLED: SSD1306 128x64 @ 0x3C
 *
 * Firebase paths (string values):
 *   /KS5160_Lung_Heart/1_Sensor_Data/
 *     1_co2, 2_alcohol, 3_temp, 4_hum,
 *     5_bp/1_diastolic, 5_bp/2_systolic,
 *     6_hr, 7_spo2, 8_sound
 *
 * 8_sound codes: 0=none, 1=cough, 2=TB, 3=asthma
 *
 * Sound rules:
 *   - Cough: exactly 2 highs in one burst (PENDING; confirm after window)
 *   - TB:    >=5 highs in one burst (immediate)
 *   - Asthma: no sound ≥10 s (immediate)
 *   - While cough pending, if TB occurs -> log TB (2) instead of cough.
 *
 * Vitals:
 *   - 10s calibration when finger detected, then values are FROZEN until finger removed.
 *   - HR clamped to 70..120 bpm.
 *
 * Firebase writes:
 *   - Temp/Hum/CO2/Alcohol/BP/HR/SpO2 written ONLY if changed from last sent value.
 *   - 8_sound written ONLY on event (1/2/3) and de-duplicated (min gap).
 *****************************************************/

#include <ESP8266WiFi.h>
#include <FirebaseESP8266.h>    // by Mobizt
#include <addons/TokenHelper.h> // optional
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

// ---- Firebase path root ----
const char* FB_ROOT = "/KS5160_Lung_Heart/1_Sensor_Data";

// ---- Update tick for checking changes (no forced writes) ----
const unsigned long FB_CHECK_MS = 200;  // check ~5 Hz
unsigned long lastFbCheckMs = 0;

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

// ---- Sound codes for Firebase 8_sound ----
#define SOUND_NONE    0
#define SOUND_COUGH   1
#define SOUND_TB      2
#define SOUND_ASTHMA  3

// Latest sound event (nonzero only when an event occurs)
unsigned long lastSoundEventMs = 0;
int  lastSoundEventCode = SOUND_NONE;

// De-duplicate sound writes (allow same code again after this much time)
const unsigned long SOUND_EVENT_MIN_GAP_MS = 2500;
unsigned long lastSoundWriteMs = 0;
int  lastSentSoundCode = 0;  // last code actually written to Firebase

// Pending cough confirmation
bool pendingCough = false;
unsigned long pendingCoughMs = 0;
const unsigned long COUGH_CONFIRM_MS = 4000; // wait up to 4s for TB after cough candidate

// -------- MAX30105 --------
#define FINGER_IR_THRESHOLD 20000UL  // tune 12k..30k per sensor/placement
MAX30105 particleSensor;
bool max30105_ok = false;

// -------- HR constraints --------
#define HR_MIN 70
#define HR_MAX 120

// ----- Live vitals (computed when finger present) -----
int heartRate_live = 0;   // bpm
int spo2_live      = 0;   // %
int systolic_live  = 0;   // mmHg (sim)
int diastolic_live = 0;   // mmHg (sim)

// ----- Frozen (latched) vitals shown on Serial & OLED -----
int heartRate_latched = 0;
int spo2_latched      = 0;
int systolic_latched  = 0;
int diastolic_latched = 0;
bool haveLatched = false;

// ---- Simple HR detector state ----
uint32_t lastIr = 0;
bool hrPeak = false;
unsigned long lastBeatMs = 0;
int tempHR = 80;

// ---- BP(sim) state ----
unsigned long lastBPms = 0;
int targetSys = 120, targetDia = 80;
int curSys    = 120, curDia    = 80;

// ---------- 10s Calibration/freeze state machine ----------
enum VitalsState { NO_FINGER, CALIBRATING, FROZEN };
VitalsState vitalsState = NO_FINGER;

const unsigned long CAL_WINDOW_MS = 10000;
unsigned long calStartMs = 0;

// running averages during calibration
uint32_t sumHR = 0, cntHR = 0;
uint32_t sumSp = 0, cntSp = 0;
uint32_t sumSy = 0, cntSy = 0;
uint32_t sumDi = 0, cntDi = 0;

// ========== Last-sent cache for change-only Firebase writes ==========
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

void connectWiFi();
void initFirebase();
void pushIfChanged(
  int co2Flag, int alcoholFlag, int soundCode,
  int tempC_int, int hum_int,
  int sys, int dia, int hr, int spo2
);

// Helpers for sound events
void registerTB(unsigned long now);
void registerAsthma(unsigned long now);
void registerCoughCandidate(unsigned long now);
void processPendingCough(); // confirm or timeout

// ================== Setup ==================
void setup() {
  Serial.begin(115200);

  pinMode(SOUND_SENSOR, INPUT);
  pinMode(CO2_SENSOR, INPUT);
  pinMode(ALCOHOL_SENSOR, INPUT);
  dht.begin();

  Wire.begin(D2, D1); // I2C

  // OLED
  if (!display.begin(SSD1306_SWITCHCAPVCC, OLED_I2C_ADDR)) {
    Serial.println("SSD1306 init failed!");
  } else {
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);
    display.setCursor(0, 0);
    display.println("Vitals Monitor");
    display.println("Init sensors...");
    display.display();
  }

  // MAX30105
  if (particleSensor.begin(Wire, I2C_SPEED_FAST)) {
    // brightness=60, avg=4, mode=2(Red+IR), sps=400, pw=411us, range=4096
    particleSensor.setup(60, 4, 2, 400, 411, 4096);
    particleSensor.setPulseAmplitudeRed(60);
    particleSensor.setPulseAmplitudeIR(60);
    particleSensor.setPulseAmplitudeGreen(0);
    max30105_ok = true;
    Serial.println("MAX30105 initialized. Place finger for Calibration (10s).");
  } else {
    max30105_ok = false;
    Serial.println("MAX30105 not found — vitals will show NA.");
  }

  connectWiFi();
  initFirebase();

  Serial.println("🌡️ ESP8266 Environmental + Sound + Vitals Monitor Ready");
}

// ================== Loop ==================
void loop() {
  unsigned long now = millis();

  // ---------- SOUND ----------
  int s = digitalRead(SOUND_SENSOR);
  if (s == HIGH) {                               // If your module is active-LOW, change to (s == LOW)
    if (now - burstLastEventMs > DEBOUNCE_MS) {
      if (!inBurst) { inBurst = true; soundCount = 0; }
      soundCount++;
      burstLastEventMs = now;
      lastSoundMs = now;

      // TB: immediate classification at >=5 highs
      if (soundCount >= 5) {
        registerTB(now);
        soundCount = 0; inBurst = false;
      }
    }
  }
  // End of burst -> classify
  if (inBurst && (now - burstLastEventMs > BURST_GAP_MS)) classifyAndResetBurst();

  // Asthma: no sound >= 10 s
  if (lastSoundMs != 0 && (now - lastSoundMs >= ASTHMATIC_MS)) {
    registerAsthma(now);
    classifyAndResetBurst(); // end any partial burst
    lastSoundMs = now;       // throttle asthma message
  }

  // Handle cough confirmation timeout
  processPendingCough();

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

  // ---------- MAX30105 live vitals ----------
  readMAX30105Vitals();

  // ---------- Calibration/freeze state machine ----------
  bool fingerPresent = max30105_ok && (particleSensor.getIR() >= FINGER_IR_THRESHOLD);
  updateCalibrationState(fingerPresent);

  // ---------- Serial summary (frozen values) ----------
  printVitalsLine();

  // ---------- OLED (frozen values; status banner) ----------
  updateOLED(temperature, humidity);

  // ---------- Firebase push (ONLY IF CHANGED) ----------
  if (firebaseReady && (millis() - lastFbCheckMs >= FB_CHECK_MS)) {
    lastFbCheckMs = millis();

    // Only nonzero sound events are candidates to write.
    // We also de-duplicate same events using SOUND_EVENT_MIN_GAP_MS.
    int soundEventToWrite = SOUND_NONE;
    if (lastSoundEventCode != SOUND_NONE) {
      bool allowSameAgain = (millis() - lastSoundWriteMs >= SOUND_EVENT_MIN_GAP_MS);
      if (lastSoundEventCode != lastSentSoundCode || allowSameAgain) {
        soundEventToWrite = lastSoundEventCode; // write this event
      }
    }

    // ints (like your DB)
    int temp_i = isnan(temperature) ? 0 : (int)(temperature + 0.5f);
    int hum_i  = isnan(humidity)    ? 0 : (int)(humidity + 0.5f);

    int sys = (haveLatched ? systolic_latched  : 0);
    int dia = (haveLatched ? diastolic_latched : 0);
    int hr  = (haveLatched ? heartRate_latched : 0);
    int sp  = (haveLatched ? spo2_latched      : 0);

    pushIfChanged(co2Flag, alcoholFlag, soundEventToWrite, temp_i, hum_i, sys, dia, hr, sp);

    // after pushing an event, clear local desire to write duplicates too quickly
    if (soundEventToWrite != SOUND_NONE) {
      lastSoundWriteMs   = millis();
      lastSentSoundCode  = soundEventToWrite;
      // keep lastSoundEventCode as is; next event can overwrite it
    }
  }

  Serial.println("---------------------------");
  delay(10); // keep loop responsive for pulse detection
}

// ================== Sound helpers ==================
void classifyAndResetBurst() {
  if (!inBurst) return;

  if (soundCount >= 5) {
    // TB at burst end too (>=5 highs)
    registerTB(millis());
  }
  else if (soundCount == 2) {
    // Start cough confirmation window; don't log yet
    registerCoughCandidate(millis());
  }
  else if (soundCount == 1 || (soundCount >= 3 && soundCount <= 4)) {
    Serial.print("ℹ️ Sound burst: "); Serial.print(soundCount); Serial.println(" highs (no label)");
    // generic burst -> no logging
  }

  soundCount = 0;
  inBurst = false;
}

void registerTB(unsigned long now) {
  Serial.println("🔴 Detected: TB (>=5 highs)");
  lastSoundEventMs   = now;
  lastSoundEventCode = SOUND_TB;
  // override any pending cough
  if (pendingCough) {
    pendingCough = false;
    Serial.println("↪️ TB overrides pending cough");
  }
}

void registerAsthma(unsigned long now) {
  Serial.println("⚠️ Possible Asthmatic (no sound ≥ 10s)");
  lastSoundEventMs   = now;
  lastSoundEventCode = SOUND_ASTHMA;
  // cancel pending cough
  if (pendingCough) {
    pendingCough = false;
    Serial.println("↪️ Asthma cancels pending cough");
  }
}

void registerCoughCandidate(unsigned long now) {
  // if a TB event just happened, ignore cough candidate
  if ((millis() - lastSoundEventMs) <= 200 && lastSoundEventCode == SOUND_TB) return;

  pendingCough   = true;
  pendingCoughMs = now;
  Serial.println("🟠 Cough candidate (2 highs) — checking for TB (4s window)...");
}

void processPendingCough() {
  if (!pendingCough) return;
  if (millis() - pendingCoughMs >= COUGH_CONFIRM_MS) {
    // Confirm cough now
    lastSoundEventMs   = millis();
    lastSoundEventCode = SOUND_COUGH;
    pendingCough = false;
    Serial.println("🟠 Cough confirmed (no TB within window)");
  }
}

// ================== MAX30105 live vitals ==================
void readMAX30105Vitals() {
  if (!max30105_ok) { heartRate_live = 0; spo2_live = 0; systolic_live = 0; diastolic_live = 0; return; }

  // Pull latest sample
  particleSensor.check();
  long ir  = particleSensor.getIR();
  long red = particleSensor.getRed();

  // Finger gate
  if (ir < FINGER_IR_THRESHOLD) {
    heartRate_live = 0; spo2_live = 0; systolic_live = 0; diastolic_live = 0;
    lastIr = ir;
    return;
  }

  // ---- HR (naive peak detector) ----
  if (ir > lastIr && !hrPeak) {
    hrPeak = true;
  } else if (ir < lastIr && hrPeak) {
    unsigned long now = millis();
    unsigned long ibi = now - lastBeatMs;          // inter-beat interval
    if (ibi > 300 && ibi < 2000) {                 // 30..200 bpm plausible
      int newHR = (int)(60000UL / ibi);
      if (newHR >= 50 && newHR <= 180) tempHR = newHR;
    }
    lastBeatMs = now;
    hrPeak = false;
  }
  lastIr = ir;

  heartRate_live = tempHR;

  // Reject out-of-range live HR from averaging
  if (heartRate_live < HR_MIN || heartRate_live > HR_MAX) {
    heartRate_live = 0;
  }

  // ---- SpO2 (simple Red/IR ratio; demo only) ----
  float ratio = (float)red / (float)ir;
  int est = 110 - int(25.0f * ratio);              // ~90..99 typical
  est = constrain(est, 90, 99);
  spo2_live = est;

  // ---- BP(sim) ----
  simulateBP();
}

void simulateBP() {
  unsigned long now = millis();

  if (now - lastBPms > 10000) {
    int hrAdj = 0;
    if (heartRate_live > 0) hrAdj = constrain(heartRate_live - 70, -20, 40);
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

// ========== 10s Calibration/Freeze logic ==========
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
        } else {
          systolic_latched = diastolic_latched = 0;
        }
        haveLatched = true;
        vitalsState = FROZEN;
        Serial.println("✅ Calibration complete — values latched.");
      }
      break;

    case FROZEN:
      if (!fingerPresent) {
        vitalsState = NO_FINGER;  // keep showing frozen values
        Serial.println("👆 Finger removed — holding last calibrated values.");
      }
      break;
  }
}

// ================== Serial line (frozen values) ==================
void printVitalsLine() {
  Serial.print("HR=");
  Serial.print(haveLatched && heartRate_latched > 0 ? String(heartRate_latched) : "NA");
  Serial.print(" bpm | SpO2=");
  Serial.print(haveLatched && spo2_latched > 0 ? String(spo2_latched) : "NA");
  Serial.print(" % | BP=");
  if (haveLatched && systolic_latched > 0 && diastolic_latched > 0) {
    Serial.print(systolic_latched); Serial.print("/"); Serial.print(diastolic_latched);
  } else {
    Serial.print("NA");
  }
  Serial.print(" mmHg");

  if (vitalsState == CALIBRATING) {
    float secs = (millis() - calStartMs) / 1000.0f;
    Serial.print("  |  Calibration: ");
    Serial.print(secs, 1);
    Serial.print("/");
    Serial.print(CAL_WINDOW_MS / 1000);
    Serial.print(" s");
  } else if (vitalsState == FROZEN) {
    Serial.print("  |  Calibration Complete");
  }

  if (pendingCough) {
    int left = (int)((COUGH_CONFIRM_MS - (millis() - pendingCoughMs)) / 1000);
    if (left < 0) left = 0;
    Serial.print("  |  Pending cough… waiting "); Serial.print(left); Serial.print("s");
  }
  Serial.println();
}

// ================== OLED rendering (compact 2-column layout) ==================
void updateOLED(float temperature, float humidity) {
  static unsigned long lastOLED = 0;
  if (millis() - lastOLED < 300) return;   // ~3 fps
  lastOLED = millis();

  const int LEFT_X  = 0;
  const int RIGHT_X = 68;   // second column start
  int y = 0;

  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);

  // ---- Line 1: Status ----
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

  // ---- Line 2: HR | SpO2 ----
  display.setCursor(LEFT_X, y);
  display.print("HR: ");
  if (haveLatched && heartRate_latched > 0) { display.print(heartRate_latched); display.print(" bpm"); }
  else display.print("NA");

  display.setCursor(RIGHT_X, y);
  display.print("SpO2: ");
  if (haveLatched && spo2_latched > 0) { display.print(spo2_latched); display.print(" %"); }
  else display.print("NA");
  y += 12;

  // ---- Line 3: BP ----
  display.setCursor(LEFT_X, y);
  display.print("BP: ");
  if (haveLatched && systolic_latched > 0 && diastolic_latched > 0) {
    display.print(systolic_latched); display.print("/"); display.print(diastolic_latched); display.print(" mmHg");
  } else display.print("NA");
  y += 12;

  // ---- Line 4: Temp | Hum ----
  display.setCursor(LEFT_X, y);
  display.print("Temp: ");
  if (!isnan(temperature)) { display.print(temperature, 1); display.print(" C"); }
  else display.print("NA");

  display.setCursor(RIGHT_X, y);
  display.print("Hum: ");
  if (!isnan(humidity)) { display.print(humidity, 1); display.print(" %"); }
  else display.print("NA");

  display.display();
}

// ================== Wi-Fi & Firebase ==================
void connectWiFi() {
  Serial.printf("Connecting to WiFi: %s\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 40) {
    delay(250);
    Serial.print(".");
    tries++;
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("WiFi OK, IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("WiFi FAILED");
  }
}

void initFirebase() {
  if (WiFi.status() != WL_CONNECTED) { firebaseReady = false; return; }

  config.api_key = API_KEY;
  config.database_url = DATABASE_URL;
  config.token_status_callback = tokenStatusCallback; // optional

  auth.user.email = USER_EMAIL;
  auth.user.password = USER_PASSWORD;

  Firebase.reconnectWiFi(true);
  Firebase.begin(&config, &auth);

  int waitMs = 0;
  while (!Firebase.ready() && waitMs < 8000) { delay(200); waitMs += 200; }

  firebaseReady = Firebase.ready();
  Serial.println(firebaseReady ? "Firebase connected." : "Firebase NOT ready.");
}

// ========= Change-only Firebase writes =========
void pushIfChanged(
  int co2Flag, int alcoholFlag, int soundCode,
  int tempC_int, int hum_int,
  int sys, int dia, int hr, int spo2
) {
  if (!firebaseReady) return;

  // CO2
  if (co2Flag != last_sent_co2) {
    Firebase.setString(fbdo, String(FB_ROOT) + "/1_co2", String(co2Flag));
    last_sent_co2 = co2Flag;
  }

  // Alcohol
  if (alcoholFlag != last_sent_alcohol) {
    Firebase.setString(fbdo, String(FB_ROOT) + "/2_alcohol", String(alcoholFlag));
    last_sent_alcohol = alcoholFlag;
  }

  // Temperature
  if (tempC_int != last_sent_temp) {
    Firebase.setString(fbdo, String(FB_ROOT) + "/3_temp", String(tempC_int));
    last_sent_temp = tempC_int;
  }

  // Humidity
  if (hum_int != last_sent_hum) {
    Firebase.setString(fbdo, String(FB_ROOT) + "/4_hum", String(hum_int));
    last_sent_hum = hum_int;
  }

  // BP (Diastolic / Systolic)
  if (dia != last_sent_dia) {
    Firebase.setString(fbdo, String(FB_ROOT) + "/5_bp/1_diastolic", String(dia));
    last_sent_dia = dia;
  }
  if (sys != last_sent_sys) {
    Firebase.setString(fbdo, String(FB_ROOT) + "/5_bp/2_systolic", String(sys));
    last_sent_sys = sys;
  }

  // HR
  if (hr != last_sent_hr) {
    Firebase.setString(fbdo, String(FB_ROOT) + "/6_hr", String(hr));
    last_sent_hr = hr;
  }

  // SpO2
  if (spo2 != last_sent_spo2) {
    Firebase.setString(fbdo, String(FB_ROOT) + "/7_spo2", String(spo2));
    last_sent_spo2 = spo2;
  }

  // 8_sound — only send on events; no "0" writes
  if (soundCode != SOUND_NONE) {
    Firebase.setString(fbdo, String(FB_ROOT) + "/8_sound", String(soundCode));
    // lastSentSoundCode & lastSoundWriteMs updated by caller after success
  }
}
