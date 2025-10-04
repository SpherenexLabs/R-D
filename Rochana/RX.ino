/*
  ESP8266 Telemetry -> Firebase RTDB + OLED
  - Sends: Voltage, battery (SoC), current, power
  - Fetches (no calculation): balance  (/dynamic-wireless-car-charging/balance)
  - OLED shows V, I, P, SoC, Balance
*/

#include <Arduino.h>
#include <Wire.h>
#include <ESP8266WiFi.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#include <Firebase_ESP_Client.h>
#include "addons/TokenHelper.h"
#include "addons/RTDBHelper.h"

// ---------- Wi-Fi ----------
#define WIFI_SSID       "wcs"
#define WIFI_PASSWORD   "123456789"

// ---------- Firebase ----------
#define API_KEY         "AIzaSyAhLCi6JBT5ELkAFxTplKBBDdRdpATzQxI"
#define DATABASE_URL    "https://smart-medicine-vending-machine-default-rtdb.asia-southeast1.firebasedatabase.app"
#define USER_EMAIL      "spherenexgpt@gmail.com"
#define USER_PASSWORD   "Spherenex@123"

// ---------- RTDB paths ----------
#define FB_ROOT   "/dynamic-wireless-car-charging"
#define FB_V      FB_ROOT "/Voltage"
#define FB_BATT   FB_ROOT "/battery"
#define FB_I      FB_ROOT "/current"
#define FB_P      FB_ROOT "/power"
#define FB_BAL    FB_ROOT "/balance"     // <-- FETCH ONLY

// ---------- OLED ----------
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);

// ---------- ADC / Divider (NodeMCU A0 -> ~0..3.2V) ----------
#define ADC_PIN A0
static const float ADC_A0_VMAX = 3.20f;
static const float R1_TOP_OHMS = 32200.0f;
static const float R2_BOT_OHMS = 7500.0f;

// Optional linear calibration
static const float VOLT_GAIN   = 1.0f;
static const float VOLT_OFFSET = 0.0f;

// Pack/SoC model (kept for SoC only)
static const int   N_CELLS       = 1;
static const float R_CELL_OHMS   = 0.050f;
static const float R_PACK_OHMS   = R_CELL_OHMS * N_CELLS;

static const float VOC_ALPHA_UP  = 0.20f;
static const float VOC_ALPHA_DN  = 0.005f;
static const int   ADC_AVG_SAMPLES = 10;

// ---------- Firebase ----------
FirebaseData fbdo;        // writes
FirebaseData fbget;       // reads (balance)
FirebaseAuth auth;
FirebaseConfig config;

// ---------- Globals ----------
float dividerRatio = 0.0f;
float invDivider   = 0.0f;
float voc_est_V    = NAN;

const uint32_t PERIOD_MS = 1000;
uint32_t t_last = 0;

String balanceStr = "--";     // what we show on OLED
uint32_t t_lastBal = 0;
const uint32_t BALANCE_POLL_MS = 2000;

// ----- OCV table (Li-ion 1S) -----
struct OcvPoint { float v_cell, soc; };
const OcvPoint OCV_TABLE[] = {
  {3.50f, 0}, {3.60f, 5}, {3.65f, 10}, {3.70f, 20},
  {3.75f, 30}, {3.80f, 40}, {3.85f, 50}, {3.90f, 60},
  {3.95f, 70}, {4.00f, 80}, {4.10f, 90}, {4.20f, 100}
};
const int OCV_N = sizeof(OCV_TABLE)/sizeof(OCV_TABLE[0]);

// ---------- Helpers ----------
float estimate_soc_from_pack(float v_pack) {
  float v_cell = v_pack / N_CELLS;
  if (v_cell <= OCV_TABLE[0].v_cell) return 0;
  if (v_cell >= OCV_TABLE[OCV_N-1].v_cell) return 100;
  for (int i = 0; i < OCV_N-1; ++i) {
    if (v_cell >= OCV_TABLE[i].v_cell && v_cell <= OCV_TABLE[i+1].v_cell) {
      float t = (v_cell - OCV_TABLE[i].v_cell) /
                (OCV_TABLE[i+1].v_cell - OCV_TABLE[i].v_cell);
      return OCV_TABLE[i].soc + t * (OCV_TABLE[i+1].soc - OCV_TABLE[i].soc);
    }
  }
  return 0;
}

int read_adc_avg(int N) {
  long s = 0;
  for (int i = 0; i < N; ++i) { s += analogRead(ADC_PIN); delay(2); }
  return (int)(s / N);
}

float measure_pack_voltage() {
  int   raw  = read_adc_avg(ADC_AVG_SAMPLES);        // 0..1023
  float vA0  = (raw / 1023.0f) * ADC_A0_VMAX;        // at A0
  float vBat = vA0 * (1.0f / (R2_BOT_OHMS / (R1_TOP_OHMS + R2_BOT_OHMS)));
  return vBat * VOLT_GAIN + VOLT_OFFSET;
}

// simple retry for RTDB writes
template<typename T>
bool fb_set(const char* path, T val) {
  for (int i=0; i<3; ++i) {
    if (Firebase.ready() && Firebase.RTDB.set(&fbdo, path, val)) return true;
    delay(150);
  }
  Serial.printf("RTDB write fail [%s]: %s\n", path, fbdo.errorReason().c_str());
  return false;
}

/* --------- Simulated current: 200..600 mA (never 0) --------- */
static const float I_MIN_mA = 200.0f;
static const float I_MAX_mA = 600.0f;
static const float I_STEP_mA = 30.0f;   // max step per second
static const float I_EMA_A   = 0.25f;   // smoothing
float simI_mA = 300.0f;                 // start near mid
float Ifilt_mA = simI_mA;

float nextSimCurrent_mA() {
  // random step in [-I_STEP_mA, +I_STEP_mA]
  float step = ((float)random(-1000, 1001) / 1000.0f) * I_STEP_mA;
  simI_mA += step;
  if (simI_mA < I_MIN_mA) simI_mA = I_MIN_mA + (I_MIN_mA - simI_mA) * 0.3f;
  if (simI_mA > I_MAX_mA) simI_mA = I_MAX_mA - (simI_mA - I_MAX_mA) * 0.3f;
  // EMA smoothing
  Ifilt_mA = I_EMA_A * simI_mA + (1.0f - I_EMA_A) * Ifilt_mA;
  return Ifilt_mA;
}

// ---------- Setup ----------
void setup() {
  Serial.begin(115200);
  Wire.begin(D2, D1);             // I2C on NodeMCU
  randomSeed(analogRead(A0));     // seed RNG for current walk

  // OLED
  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println(F("OLED init failed")); while (1) {}
  }
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0,0);
  display.println("Booting...");
  display.display();

  // Divider math
  dividerRatio = R2_BOT_OHMS / (R1_TOP_OHMS + R2_BOT_OHMS);
  invDivider   = 1.0f / dividerRatio;

  // Wi-Fi
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("WiFi");
  while (WiFi.status() != WL_CONNECTED) { delay(300); Serial.print("."); }
  Serial.println("\nWiFi connected");
  Serial.print("IP: "); Serial.println(WiFi.localIP());

  // Firebase
  config.api_key = API_KEY;
  config.database_url = DATABASE_URL;
  config.token_status_callback = tokenStatusCallback;
  auth.user.email = USER_EMAIL;
  auth.user.password = USER_PASSWORD;
  Firebase.begin(&config, &auth);
  Firebase.reconnectWiFi(true);

  // wait until token is ready
  Serial.print("Firebase");
  while (!Firebase.ready()) { delay(200); Serial.print("."); }
  Serial.println(" ready");

  display.clearDisplay();
  display.setCursor(0,0);
  display.println("Init OK");
  display.display();
}

// ---------- Loop ----------
void loop() {
  const uint32_t now = millis();

  // 1 Hz telemetry
  if (now - t_last >= PERIOD_MS) {
    t_last = now;

    // Measure battery voltage at pack
    float Vpack = measure_pack_voltage();

    // Track open-circuit estimate for SoC (kept for realism)
    if (isnan(voc_est_V)) voc_est_V = Vpack;
    if (Vpack > voc_est_V) voc_est_V += VOC_ALPHA_UP * (Vpack - voc_est_V);
    else                   voc_est_V += VOC_ALPHA_DN * (Vpack - voc_est_V);

    // Simulated current 200..600 mA
    float I_mA = nextSimCurrent_mA();

    // Power (W)
    float P_w = Vpack * (I_mA / 1000.0f);

    // SoC from OCV
    float SoC = estimate_soc_from_pack(voc_est_V);

    // OLED (5 lines)
    display.clearDisplay();
    display.setCursor(0, 0);  display.printf("V=%.2f V",  Vpack);
    display.setCursor(0, 12); display.printf("I=%.0f mA", I_mA);
    display.setCursor(0, 24); display.printf("P=%.2f W",  P_w);
    display.setCursor(0, 36); display.printf("SoC=%.0f %%", SoC);
    display.setCursor(0, 48); display.print("Bal: "); display.print(balanceStr);
    display.display();
float I_A = I_mA / 1000.0f;   // convert mA -> A
    // Firebase writes
    if (WiFi.status() == WL_CONNECTED && Firebase.ready()) {
      fb_set(FB_V,    Vpack);
      fb_set(FB_BATT, SoC);
       
fb_set(FB_I, I_A);
      fb_set(FB_P,    P_w);
    } else {
      Serial.println("Skip: WiFi/Firebase not ready");
    }

    // Debug
    Serial.printf("V=%.2fV  I=%.0fmA  P=%.2fW  SoC=%.0f%%  Bal=%s\n",
                  Vpack, I_mA, P_w, SoC, balanceStr.c_str());
  }

  // Fetch balance every 2 seconds (no calculation)
  if (now - t_lastBal >= BALANCE_POLL_MS) {
    t_lastBal = now;
    if (WiFi.status() == WL_CONNECTED && Firebase.ready()) {
      // Try numeric first
      if (Firebase.RTDB.get(&fbget, FB_BAL)) {
        if (fbget.dataTypeEnum() == fb_esp_rtdb_data_type_integer) {
          balanceStr = String(fbget.intData());
        } else if (fbget.dataTypeEnum() == fb_esp_rtdb_data_type_float ||
                   fbget.dataTypeEnum() == fb_esp_rtdb_data_type_double) {
          balanceStr = String(fbget.floatData(), 2);
        } else if (fbget.dataTypeEnum() == fb_esp_rtdb_data_type_string) {
          balanceStr = fbget.stringData();  // already a string like "120.50" or "₹120"
        } else {
          balanceStr = "--";
        }
      } else {
        Serial.printf("Balance read error: %s\n", fbget.errorReason().c_str());
      }
    }
  }
}
