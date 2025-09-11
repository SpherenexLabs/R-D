// ---------- File: esp8266_b_oled_fields_client_final.ino  (ESP8266_B)
// Role: Sensors + OLED + TCP client (NO Firebase).
// Sends ONLY the fields shown on the OLED every 5 s to ESP8266_A (192.168.4.1:5000).
// HR is constrained to [60..110]. If finger not detected or value invalid/out-of-range,
// it keeps sending the previous good values (defaults: HR=75, SpO2=97, BP=118/76).
// Serial Monitor: 115200 baud

// --- Fix Arduino auto-prototype issue (must be before any prototypes Arduino may inject) ---
struct Debounce;
static void deb_update(Debounce &d);

// ---------------------- Includes ----------------------
#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_MPU6050.h>
#include <DHT.h>

// Silence I2C_BUFFER_LENGTH redefinition warning from SparkFun MAX30105
#ifdef I2C_BUFFER_LENGTH
#undef I2C_BUFFER_LENGTH
#endif
#include <MAX30105.h>
#include "spo2_algorithm.h"

#include <math.h>

// ---------------------- Wi-Fi / TCP ----------------------
static const char* STA_SSID = "ESP_A_AP";     // <--- match ESP8266_A
static const char* STA_PASS = "12345678";     // <--- match ESP8266_A (>=8 chars)
static const char* HOST     = "192.168.4.1";
static const uint16_t PORT  = 5000;

WiFiClient tcp;
uint32_t t_net = 0;
const uint32_t NET_MS = 5000;

void connectWiFi() {
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(STA_SSID, STA_PASS);
  Serial.print("[NET] Connecting");
  while (WiFi.status() != WL_CONNECTED) { Serial.print("."); delay(400); }
  Serial.printf("\n[NET] WiFi OK, IP: %s\n", WiFi.localIP().toString().c_str());
}
bool connectTCP() {
  Serial.printf("[NET] TCP %s:%u\n", HOST, PORT);
  if (tcp.connect(HOST, PORT)) { tcp.setNoDelay(true); Serial.println("[NET] TCP connected"); return true; }
  Serial.println("[NET] TCP connect FAILED"); return false;
}

// ---------------------- OLED ----------------------
#define OLED_ADDR 0x3C
#define OLED_SDA  D2
#define OLED_SCL  D1
#define OLED_RST  -1
#define OLED_W    128
#define OLED_H    64
Adafruit_SSD1306 oled(OLED_W, OLED_H, &Wire, OLED_RST);

// ---------------------- DHT11 ----------------------
#define DHTPIN  D4
#define DHTTYPE DHT11
DHT dht(DHTPIN, DHTTYPE);

// ---------------------- MAX3010x ----------------------
MAX30105 mx;
bool mx_ok = false;
#define BUF_LEN 100
static uint32_t irBuf[BUF_LEN], redBuf[BUF_LEN];
static int bufPos = 0;
int32_t spo2_alg = 0, heart_alg = 0;
int8_t  spo2_valid = 0, hr_valid = 0;
float   hr_ema = NAN, spo2_ema = NAN;
float   last_hr = NAN, last_spo2 = NAN;
bool    finger_ok = false;

// Finger detect tuned for your logs (DC ~27k, AC 600–3000)
const uint32_t FINGER_DC_MIN = 15000;
const uint32_t FINGER_AC_MIN = 300;
const float    HR_EMA_A = 0.25f;
const float    SPO2_EMA_A = 0.20f;

// ---------------------- MPU6050 ----------------------
Adafruit_MPU6050 mpu;
bool mpu_ok = false;

const float G = 9.80665f;
const float IMPACT_G        = 2.5f;
const float TILT_DEG        = 60.0f;
const uint32_t IMPACT_WIN_MS= 700;
const uint32_t FALL_LATCH_MS= 5000;

enum FallState { IDLE, IMPACT_SEEN };
FallState fall_state = IDLE;
uint32_t  impact_t0 = 0;
bool      fall_latched = false;
uint32_t  fall_latch_until = 0;

// ---------------------- Buttons ----------------------
// NOTE: D3 (GPIO0) is a strap pin; don't hold it LOW during reset.
#define BTN_EMERGENCY   D5   // GPIO14
#define BTN_HEALTH      D6   // GPIO12
#define BTN_SAFE        D7   // GPIO13
#define BTN_FAMILY      D3   // GPIO0

struct Debounce {
  uint8_t pin; uint32_t lastMs; bool state; bool lastRaw;
};
Debounce db_emg{BTN_EMERGENCY,0,false,true};
Debounce db_hlp{BTN_HEALTH,   0,false,true};
Debounce db_safe{BTN_SAFE,    0,false,true};
Debounce db_fam{BTN_FAMILY,   0,false,true};

const uint16_t DB_MS = 25;

// ---------------------- Timers ----------------------
uint32_t t_disp = 0, t_algo = 0, t_log = 0;
const uint32_t DISP_MS = 500;
const uint32_t ALGO_MS = 1000;
const uint32_t LOG_MS  = 1000;

// ---------------------- OLED mirrored values (what we display & send) ----------------------
int   oled_hr   = 0;
int   oled_spo2 = 0;
float oled_temp = NAN;
float oled_hum  = NAN;
int   oled_bp_sys = 118, oled_bp_dia = 76;  // start with defaults
int   oled_fall = 0;
int   oled_btn_emg = 0, oled_btn_hlp = 0, oled_btn_safe = 0, oled_btn_fam = 0;

// ---- HR/SpO2 range & "previous good" logic ----
const int HR_MIN = 60;
const int HR_MAX = 110;
const uint32_t HOLD_MS = 3000;            // keep last good for brief dropouts

static uint32_t last_ok_ms = 0;
static float    last_ok_hr = NAN, last_ok_spo2 = NAN;

static int      hr_last_good_display = 75; // default until first valid
static bool     hr_have_good = false;

static int      spo2_last_good_display = 97; // default until first valid
static bool     spo2_have_good = false;

static int      bp_sys_last_good = 118;
static int      bp_dia_last_good = 76;
static bool     bp_have_good     = true;     // have defaults

// ---------------------- Helpers ----------------------
inline bool  validf(float x){ return !isnan(x) && x>0; }
inline float ema(float prev,float s,float a){ return validf(prev)?(a*s+(1-a)*prev):s; }

static void compute_dc_ac(const uint32_t *x,int n,uint32_t &dc,uint32_t &pp){
  uint64_t sum=0; uint32_t xmin=UINT32_MAX,xmax=0;
  for(int i=0;i<n;++i){ uint32_t v=x[i]; sum+=v; if(v<xmin)xmin=v; if(v>xmax)xmax=v; }
  dc=(uint32_t)(sum/(uint64_t)n); pp=(xmax>xmin)?(xmax-xmin):0;
}
static void read_mpu(float &gmag,float &pitch_deg,float &roll_deg){
  sensors_event_t a,g,temp; mpu.getEvent(&a,&g,&temp);
  float ax=a.acceleration.x, ay=a.acceleration.y, az=a.acceleration.z;
  float am=sqrtf(ax*ax+ay*ay+az*az); gmag=am/G;
  pitch_deg=atan2f(ax, sqrtf(ay*ay+az*az))*180.0f/PI;
  roll_deg =atan2f(ay, az)*180.0f/PI;
}
static void deb_update(Debounce &d){
  bool raw=(digitalRead(d.pin)==LOW); uint32_t now=millis();
  if(raw!=d.lastRaw){ d.lastMs=now; d.lastRaw=raw; }
  if((now-d.lastMs)>=DB_MS) d.state=raw;
}
static void estimate_bp(int hr,int spo2,int &sys,int &dia){
  float s=118.0f,d=76.0f;
  if(hr>0){ if(hr>100){ s+=6; d+=3; } else if(hr<55){ s-=5; d-=3; } }
  if(spo2>0){ if(spo2<93){ s+=6; d+=3; } else if(spo2>98){ s-=3; } }
  s=constrain(s,90.0f,150.0f); d=constrain(d,55.0f,100.0f);
  sys=(int)(s+0.5f); dia=(int)(d+0.5f);
}

// ---------------------- Setup ----------------------
void setup() {
  Serial.begin(115200); delay(150);

  Wire.begin(OLED_SDA, OLED_SCL);
  Wire.setClock(400000);

  // OLED
  if(!oled.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR)){ Serial.println(F("SSD1306 fail")); for(;;); }
  oled.clearDisplay(); oled.setTextSize(1); oled.setTextColor(SSD1306_WHITE);
  oled.setCursor(0,0); oled.println("Wearable init..."); oled.display();

  // Wi-Fi / TCP
  connectWiFi();
  connectTCP();

  // DHT
  dht.begin();

  // MAX3010x
  mx_ok = mx.begin(Wire, 400000);
  if(!mx_ok){ Serial.println("MAX3010x not found."); }
  else{
    // setup(ledBrightness, sampleAverage, ledMode, sampleRate, pulseWidth, adcRange)
    mx.setup(0 /*unused here*/, 8 /*avg*/, 2 /*red+IR*/, 100 /*Hz*/, 411 /*us*/, 16384 /*range*/);
    mx.setPulseAmplitudeIR(0x24);   // modest currents to avoid clipping
    mx.setPulseAmplitudeRed(0x1F);

    // prefill FIFO window
    int filled=0; uint32_t t0=millis();
    while(filled<BUF_LEN && (millis()-t0)<1500){
      mx.check();
      while(mx.available() && filled<BUF_LEN){
        redBuf[filled]=mx.getFIFORed();
        irBuf[filled] =mx.getFIFOIR();
        mx.nextSample(); filled++;
      }
      delay(2); yield();
    }
  }

  // MPU6050
  mpu_ok = mpu.begin();
  if(!mpu_ok){ Serial.println("MPU6050 not found."); }
  else{
    mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
    mpu.setGyroRange(MPU6050_RANGE_500_DEG);
    mpu.setFilterBandwidth(MPU6050_BAND_21_HZ);
  }

  // Buttons
  pinMode(BTN_EMERGENCY, INPUT_PULLUP);
  pinMode(BTN_HEALTH,    INPUT_PULLUP);
  pinMode(BTN_SAFE,      INPUT_PULLUP);
  pinMode(BTN_FAMILY,    INPUT_PULLUP);

  // CSV header (local)
  Serial.println("t_ms,HR,SpO2,TempC,Hum,IR_mean,IR_pp,Finger,Fall,g,Pitch,Roll,HR_valid,SpO2_valid,EMG,HELP,SAFE,FAM");
}

// ---------------------- Loop ----------------------
void loop() {
  const uint32_t now = millis();

  // Buttons
  deb_update(db_emg); deb_update(db_hlp); deb_update(db_safe); deb_update(db_fam);

  // MAX3010x
  static bool last_finger_state = false;
  if(mx_ok){
    mx.check();
    while(mx.available()){
      redBuf[bufPos]=mx.getFIFORed();
      irBuf[bufPos] =mx.getFIFOIR();
      bufPos=(bufPos+1)%BUF_LEN;
      mx.nextSample();
    }
    if(now - t_algo >= ALGO_MS){
      uint32_t irTmp[BUF_LEN], redTmp[BUF_LEN];
      for(int i=0;i<BUF_LEN;i++){ int idx=(bufPos+i)%BUF_LEN; irTmp[i]=irBuf[idx]; redTmp[i]=redBuf[idx]; }
      uint32_t dc=0, pp=0; compute_dc_ac(irTmp, BUF_LEN, dc, pp);

      // finger presence (with saturation guard)
      finger_ok = (dc >= FINGER_DC_MIN && dc <= 260000 && pp >= FINGER_AC_MIN);
      if (finger_ok != last_finger_state) {
        Serial.printf("[FINGER] %s (dc=%lu pp=%lu)\n", finger_ok ? "PRESENT" : "ABSENT",
                      (unsigned long)dc, (unsigned long)pp);
        last_finger_state = finger_ok;
      }

      maxim_heart_rate_and_oxygen_saturation(irTmp, BUF_LEN, redTmp,
                                             &spo2_alg, &spo2_valid,
                                             &heart_alg, &hr_valid);

      // Accept new data if finger present OR algo reports valid
      bool present = finger_ok || hr_valid || spo2_valid;

      if (present && hr_valid && heart_alg > 0) {
        hr_ema  = ema(hr_ema, (float)heart_alg, HR_EMA_A);
        last_hr = hr_ema;
        last_ok_hr = last_hr;
        last_ok_ms = now;
      }
      if (present && spo2_valid && spo2_alg > 0) {
        spo2_ema  = ema(spo2_ema, (float)spo2_alg, SPO2_EMA_A);
        last_spo2 = spo2_ema;
        last_ok_spo2 = last_spo2;
        last_ok_ms = now;
      }

      t_algo = now;
    }
  }

  // DHT11
  float humidity = dht.readHumidity();
  float temperature = dht.readTemperature();

  // MPU6050 + simple fall detect
  float gmag=NAN, pitch=NAN, roll=NAN;
  if(mpu_ok){
    read_mpu(gmag,pitch,roll);
    switch(fall_state){
      case IDLE: if(gmag>IMPACT_G){ fall_state=IMPACT_SEEN; impact_t0=now; } break;
      case IMPACT_SEEN:
        if(now - impact_t0 <= IMPACT_WIN_MS){
          if(fabsf(pitch)>=TILT_DEG || fabsf(roll)>=TILT_DEG){
            fall_latched=true; fall_latch_until=now+FALL_LATCH_MS; fall_state=IDLE;
          }
        } else { fall_state=IDLE; }
        break;
    }
    if(fall_latched && now>=fall_latch_until) fall_latched=false;
  }

  // ---- Compute OLED fields (HR constrained to 60..110; SpO2 & BP hold previous when no finger) ----
  // HR with short hold
  int hr_calc = validf(last_hr) ? (int)(last_hr + 0.5f)
            : (!isnan(last_ok_hr) && (now - last_ok_ms) < HOLD_MS ? (int)(last_ok_hr + 0.5f) : 0);

  int hr_final;
  if (finger_ok && hr_calc >= HR_MIN && hr_calc <= HR_MAX) {
    hr_final = hr_calc;
    hr_last_good_display = hr_final;
    hr_have_good = true;
  } else {
    hr_final = hr_have_good ? hr_last_good_display : 75;
  }
  oled_hr = constrain(hr_final, HR_MIN, HR_MAX);

  // SpO2 with short hold and previous-good fallback
  int s2_calc = validf(last_spo2) ? (int)(last_spo2 + 0.5f)
            : (!isnan(last_ok_spo2) && (now - last_ok_ms) < HOLD_MS ? (int)(last_ok_spo2 + 0.5f) : 0);

  int spo2_final;
  if (finger_ok && s2_calc > 0) {
    spo2_final = s2_calc;
    spo2_last_good_display = spo2_final;
    spo2_have_good = true;
  } else {
    spo2_final = spo2_have_good ? spo2_last_good_display : 97;
  }
  oled_spo2 = constrain(spo2_final, 85, 100);

  // Temp/Hum
  oled_temp = temperature;
  oled_hum  = humidity;

  // BP: update only when we accepted a new HR & SpO2; else keep previous/default
  if (finger_ok && oled_hr > 0 && oled_spo2 > 0) {
    estimate_bp(oled_hr, oled_spo2, oled_bp_sys, oled_bp_dia);
    bp_sys_last_good = oled_bp_sys;
    bp_dia_last_good = oled_bp_dia;
    bp_have_good = true;
  } else {
    if (bp_have_good) {
      oled_bp_sys = bp_sys_last_good;
      oled_bp_dia = bp_dia_last_good;
    } else {
      oled_bp_sys = 118; oled_bp_dia = 76;
    }
  }

  oled_fall = fall_latched ? 1 : 0;
  oled_btn_emg  = db_emg.state  ? 1 : 0;
  oled_btn_hlp  = db_hlp.state  ? 1 : 0;
  oled_btn_safe = db_safe.state ? 1 : 0;
  oled_btn_fam  = db_fam.state  ? 1 : 0;

  // OLED render
  if(now - t_disp >= DISP_MS){
    oled.clearDisplay(); oled.setTextSize(1); oled.setTextColor(SSD1306_WHITE);

    oled.setCursor(0,0);
    oled.print("HR:");   if(oled_hr)  { oled.print(oled_hr);  oled.print(" "); } else oled.print("-- ");
    oled.print("SpO2:"); if(oled_spo2){ oled.print(oled_spo2); oled.print("%"); } else oled.print("--");

    oled.setCursor(0,10);
    if(!isnan(oled_temp) && !isnan(oled_hum)){
      oled.print("T:"); oled.print(oled_temp,1); oled.print("C H:"); oled.print((int)oled_hum); oled.print("%");
    } else { oled.print("DHT: NA"); }

    oled.setCursor(0,20);
    if(oled_bp_sys>0){ oled.print("BP: "); oled.print(oled_bp_sys); oled.print("/"); oled.print(oled_bp_dia); }
    else              { oled.print("BP: --/--"); }

    oled.setCursor(0,30); oled.print("FALL: "); oled.print(oled_fall);

    oled.setCursor(0,40); oled.print("Emerg:"); oled.print(oled_btn_emg);  oled.print("  Help:");  oled.print(oled_btn_hlp);
    oled.setCursor(0,50); oled.print("Safe:");  oled.print(oled_btn_safe); oled.print("  Family:");oled.print(oled_btn_fam);

    oled.display();
    t_disp = now;
  }

  // Local CSV log (1 Hz) — useful for debugging
  if(now - t_log >= LOG_MS){
    uint32_t dc=0,pp=0;
    if(mx_ok){ uint32_t irTmp[BUF_LEN]; for(int i=0;i<BUF_LEN;i++){ int idx=(bufPos+i)%BUF_LEN; irTmp[i]=irBuf[idx]; } compute_dc_ac(irTmp,BUF_LEN,dc,pp); }
    Serial.print(now); Serial.print(",");
    Serial.print(oled_hr); Serial.print(",");
    Serial.print(oled_spo2); Serial.print(",");
    Serial.print(isnan(oled_temp)?NAN:oled_temp); Serial.print(",");
    Serial.print(isnan(oled_hum)?NAN:oled_hum); Serial.print(",");
    Serial.print(dc); Serial.print(",");
    Serial.print(pp); Serial.print(",");
    Serial.print(finger_ok?1:0); Serial.print(",");
    Serial.print(oled_fall); Serial.print(",");
    Serial.print(isnan(gmag)?NAN:gmag); Serial.print(",");
    Serial.print(isnan(pitch)?NAN:pitch); Serial.print(",");
    Serial.print(isnan(roll)?NAN:roll); Serial.print(",");
    Serial.print((int)hr_valid); Serial.print(",");
    Serial.print((int)spo2_valid); Serial.print(",");
    Serial.print(oled_btn_emg);  Serial.print(",");
    Serial.print(oled_btn_hlp);  Serial.print(",");
    Serial.print(oled_btn_safe); Serial.print(",");
    Serial.println(oled_btn_fam);
    t_log = now;
  }

  // ---- Network send (5 s) ----  [OLED fields only; values already with hold/fallbacks]
  if (WiFi.status() != WL_CONNECTED) { connectWiFi(); }
  if (!tcp.connected()) { tcp.stop(); delay(300); connectTCP(); }

  if (tcp.connected() && (now - t_net >= NET_MS)) {
    t_net = now;

    auto printFloatOrNull = [&](float f, const char* key){
      if (isnan(f)) tcp.printf("\"%s\":null,", key);
      else          tcp.printf("\"%s\":%.2f,", key, f);
    };

    tcp.print("{");
    tcp.printf("\"ts\":%lu,", (unsigned long)now);
    tcp.printf("\"hr\":%d,",   oled_hr);          // in [60..110], previous-good if needed
    tcp.printf("\"spo2\":%d,", oled_spo2);        // previous-good if needed
    printFloatOrNull(oled_temp, "temp");
    printFloatOrNull(oled_hum,  "hum");
    tcp.printf("\"bp_sys\":%d,\"bp_dia\":%d,", oled_bp_sys, oled_bp_dia); // always set from estimate or previous/default
    tcp.printf("\"fall\":%d,",  oled_fall);
    tcp.printf("\"btn\":{\"emg\":%d,\"hlp\":%d,\"safe\":%d,\"fam\":%d}", oled_btn_emg, oled_btn_hlp, oled_btn_safe, oled_btn_fam);
    tcp.print("}\n");

    Serial.println("[NET] TX JSON (OLED fields; holds & bounds)");
  }

  // Optional: read ACK
  while (tcp.connected() && tcp.available()) {
    String ack = tcp.readStringUntil('\n'); ack.trim();
    if (ack.length()) Serial.printf("[NET] RX: %s\n", ack.c_str());
  }

  yield();
}
