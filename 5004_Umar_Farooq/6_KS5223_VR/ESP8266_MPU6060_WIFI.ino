/*
 * ESP8266 (NodeMCU) + MPU6050
 * Integers-only + symmetric mapping:
 *   servo = 90 + 2*angle  (so 5° angle step -> 10° servo step)
 *
 * Serial columns: Roll_X(deg), Yaw_Z(deg), ServoX(deg), ServoZ(deg)
 * - Angles printed as integers (no decimals).
 * - Servo outputs snapped to nearest 10° (…, 70, 80, 90, 100, 110, …).
 * - Limits: Roll ±50°, Yaw ±30°.
 * - Button D3→GND zeros both angles and recenters to 90.
 * - Yaw has anti-drift (bias learn + freeze when still).
 * - Wi-Fi: connects to Pico W AP (SSID "VR", PASS "123456789") and UDP-sends "X:<deg>,Z:<deg>\n" to 192.168.4.1:4210
 *
 * I2C: SDA=D2 (GPIO4), SCL=D1 (GPIO5)
 */

#include <Wire.h>
#include <math.h>
#include "I2Cdev.h"
#include "MPU6050.h"

// --- Wi-Fi / UDP to Pico W AP ---
#include <ESP8266WiFi.h>
#include <WiFiUdp.h>
const char* WIFI_SSID = "VR";
const char* WIFI_PASS = "123456789";
const char* PICO_IP   = "192.168.4.1";   // Pico W AP default IP
const uint16_t UDP_PORT = 4210;
WiFiUDP udp;

void wifiInit() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("WiFi(STA) connecting to "); Serial.print(WIFI_SSID);
  while (WiFi.status() != WL_CONNECTED) { delay(300); Serial.print("."); }
  Serial.print("\nConnected. ESP IP: "); Serial.println(WiFi.localIP());
  udp.begin(0); // ephemeral local port
}

void sendDegrees(int roll_deg_i, int yaw_deg_i){
  char buf[32];
  int n = snprintf(buf, sizeof(buf), "X:%d,Z:%d\n", roll_deg_i, yaw_deg_i);
  udp.beginPacket(PICO_IP, UDP_PORT);
  udp.write((const uint8_t*)buf, n);
  udp.endPacket();
}

#define RESET_BUTTON_PIN D3

// ---------- MOUNT ORIENTATION ----------
/*
  0: default     1: +90° about Z     2: -90° about Z
  3: flip X      4: flip Y           5: flip Z
*/
#define ORIENT 1
// --------------------------------------

MPU6050 mpu;

// ===== IMU & filter params =====
const float GYRO_SENS     = 131.0f;   // LSB/(deg/s) @ ±250 dps
const float ALPHA_ROLL    = 0.96f;    // complementary blend for roll
const uint16_t LOOP_HZ    = 100;
const uint32_t LOOP_DT_US = 1000000UL / LOOP_HZ;

// ===== State =====
float roll_deg = 0.0f, yaw_deg = 0.0f;
float roll_zero = 0.0f, yaw_zero = 0.0f;

// Gyro biases (deg/s)
float gyro_bias_x = 0.0f, gyro_bias_y = 0.0f, gyro_bias_z = 0.0f;

// Limits
const int ROLL_LIMIT_DEG = 50;   // ±50°
const int YAW_LIMIT_DEG  = 30;   // ±30°

const float G_DPS_STILL = 1.5f;  // per-axis gyro rate for stillness
const float A_G_STILL   = 0.05f; // | |a| - 1g |

// Virtual “servo” outputs (integers, snapped to 10°)
int servoX_cmd = 90, servoZ_cmd = 90;

// Optional direction/trim (integers)
bool INVERT_X = false, INVERT_Z = false;
int  TRIM_X   = 0,     TRIM_Z   = 0;

// Timing
uint32_t t_prev_us = 0;

// ---------- helpers ----------
static inline float rad2deg(float r){ return r * (180.0f / PI); }
static inline int   clampi(int v, int lo, int hi){ return v < lo ? lo : (v > hi ? hi : v); }
static inline float clampf(float v, float lo, float hi){ return v < lo ? lo : (v > hi ? hi : v); }

// Axis remap sensor -> body
static inline void remap(float ax,float ay,float az,float gx,float gy,float gz,
                         float &bx,float &by,float &bz,float &wx,float &wy,float &wz){
#if   ORIENT==0
  bx=ax;  by=ay;  bz=az;  wx=gx;  wy=gy;  wz=gz;
#elif ORIENT==1  // +90° about Z
  bx=ay;  by=-ax; bz=az;  wx=gy;  wy=-gx; wz=gz;
#elif ORIENT==2  // -90° about Z
  bx=-ay; by=ax;  bz=az;  wx=-gy; wy=gx;  wz=gz;
#elif ORIENT==3  // flip X
  bx=ax;  by=-ay; bz=-az; wx=gx;  wy=-gy; wz=-gz;
#elif ORIENT==4  // flip Y
  bx=-ax; by=ay;  bz=-az; wx=-gx; wy=gy;  wz=-gz;
#elif ORIENT==5  // flip Z
  bx=-ax; by=-ay; bz=az;  wx=-gx; wy=-gy; wz=gz;
#endif
}

static inline bool stillXYZ(float wx_dps,float wy_dps,float wz_dps,
                            float bx,float by,float bz){
  float amag_g = sqrtf(bx*bx + by*by + bz*bz) / 16384.0f;
  return (fabsf(wx_dps) < G_DPS_STILL &&
          fabsf(wy_dps) < G_DPS_STILL &&
          fabsf(wz_dps) < G_DPS_STILL &&
          fabsf(amag_g - 1.0f) < A_G_STILL);
}

// --- linear, symmetric mapping to servo, snapped to 10° ---
// servo = 90 + 2*angle + trim, rounded to nearest 10°, clamped 0..180
static inline int mapAngleToServo_Linear2degPerDeg_Int10(int a_deg, int trim_deg){
  int s = 90 + 2 * a_deg + trim_deg;                       // linear 2° per 1°
  int s10 = (int)lroundf((float)s / 10.0f) * 10;           // snap to 10°
  return clampi(s10, 0, 180);
}

// Startup: estimate roll and gyro biases from still samples
void prime() {
  const int N = 500; int acc=0;
  double sR=0, sGx=0, sGy=0, sGz=0;
  for (int i=0;i<N;++i){
    int16_t ax,ay,az,gx,gy,gz; mpu.getMotion6(&ax,&ay,&az,&gx,&gy,&gz);
    float bx,by,bz, wx,wy,wz;  remap(ax,ay,az,gx,gy,gz, bx,by,bz, wx,wy,wz);
    float wx_dps=wx/GYRO_SENS, wy_dps=wy/GYRO_SENS, wz_dps=wz/GYRO_SENS;
    if (stillXYZ(wx_dps,wy_dps,wz_dps,bx,by,bz)) {
      float acc_roll = rad2deg(atan2f(by,bz));
      sR  += acc_roll; sGx += wx_dps; sGy += wy_dps; sGz += wz_dps; acc++;
    }
    delayMicroseconds(LOOP_DT_US);
  }
  if (acc>10) {
    roll_deg    = sR/acc;
    gyro_bias_x = sGx/acc; gyro_bias_y = sGy/acc; gyro_bias_z = sGz/acc;
  }
  yaw_deg = 0.0f;
  servoX_cmd = servoZ_cmd = 90;
}

void setup() {
  Serial.begin(115200); delay(100);
  pinMode(RESET_BUTTON_PIN, INPUT_PULLUP);
  Wire.begin(D2, D1); delay(10);

  mpu.initialize();
  if (!mpu.testConnection()) { Serial.println("MPU6050 not found."); while(1){ delay(1000); } }

  // Optional filter/range
  mpu.setFullScaleGyroRange (MPU6050_GYRO_FS_250);
  mpu.setFullScaleAccelRange(MPU6050_ACCEL_FS_2);
  mpu.setDLPFMode(3);
  mpu.setRate(9);

  prime(); t_prev_us = micros();

  // Wi-Fi → Pico AP
  wifiInit();

  Serial.println("\n=== IMU X/Z — integer angles, 2°/° mapping, 10° steps — UDP TX ===");
  Serial.println("Roll_X(deg), Yaw_Z(deg), ServoX(deg), ServoZ(deg)");
}

void loop() {
  // precise dt
  uint32_t t = micros(); if (t - t_prev_us < LOOP_DT_US) return;
  float dt = (t - t_prev_us) / 1e6f; t_prev_us = t;

  // read & remap
  int16_t ax,ay,az,gx,gy,gz; mpu.getMotion6(&ax,&ay,&az,&gx,&gy,&gz);
  float bx,by,bz, wx,wy,wz; remap(ax,ay,az,gx,gy,gz, bx,by,bz, wx,wy,wz);

  // raw gyro (deg/s)
  float wx_raw = wx / GYRO_SENS, wy_raw = wy / GYRO_SENS, wz_raw = wz / GYRO_SENS;

  // stillness (before bias removal)
  bool still = stillXYZ(wx_raw, wy_raw, wz_raw, bx, by, bz);

  // adaptive bias while still
  if (still) {
    const float beta = 0.003f;
    gyro_bias_x = (1.0f - beta) * gyro_bias_x + beta * wx_raw;
    gyro_bias_y = (1.0f - beta) * gyro_bias_y + beta * wy_raw;
    gyro_bias_z = (1.0f - beta) * gyro_bias_z + beta * wz_raw;
  }

  // bias-compensated gyro
  float wx_dps = wx_raw - gyro_bias_x;
  float wy_dps = wy_raw - gyro_bias_y;
  float wz_dps = wz_raw - gyro_bias_z;

  // accel tilt → roll
  float acc_roll_deg = rad2deg(atan2f(by, bz));

  // update roll (X)
  roll_deg = ALPHA_ROLL * (roll_deg + wx_dps * dt) + (1.0f - ALPHA_ROLL) * acc_roll_deg;

  // update yaw (Z): integrate only when not still
  if (!still) yaw_deg += wz_dps * dt;

  // zeroing
  if (digitalRead(RESET_BUTTON_PIN) == LOW) {
    delay(25);
    if (digitalRead(RESET_BUTTON_PIN) == LOW) {
      // refine z-bias quickly (assumes still for ~300ms)
      double sGz=0; int n=0; uint32_t t0=micros();
      while (micros()-t0 < 300000UL) {
        int16_t _ax,_ay,_az,_gx,_gy,_gz; mpu.getMotion6(&_ax,&_ay,&_az,&_gx,&_gy,&_gz);
        sGz += (double)_gz / GYRO_SENS; n++; delayMicroseconds(10000);
      }
      if (n>0) gyro_bias_z = sGz / n;

      roll_zero = roll_deg; yaw_zero = yaw_deg;
      servoX_cmd = servoZ_cmd = 90;
      Serial.println("[Zeroed+Centered]");
      while (digitalRead(RESET_BUTTON_PIN) == LOW) { delay(10); }
    }
  }

  // apply zero & limits (then round to whole degrees)
  float roll_out_f = clampf(roll_deg - roll_zero, -ROLL_LIMIT_DEG, ROLL_LIMIT_DEG);
  float yaw_out_f  = clampf(yaw_deg  - yaw_zero,  -YAW_LIMIT_DEG,  YAW_LIMIT_DEG);

  int roll_out_i = (int)lroundf(roll_out_f);   // integer degrees
  int yaw_out_i  = (int)lroundf(yaw_out_f);    // integer degrees

  // optional invert
  if (INVERT_X) roll_out_i = -roll_out_i;
  if (INVERT_Z) yaw_out_i  = -yaw_out_i;

  // linear symmetric mapping -> servo, then snap to 10°
  servoX_cmd = mapAngleToServo_Linear2degPerDeg_Int10(roll_out_i, TRIM_X);
  servoZ_cmd = mapAngleToServo_Linear2degPerDeg_Int10(yaw_out_i,  TRIM_Z);

  // print integers only
  Serial.print(roll_out_i);  Serial.print(", ");
  Serial.print(yaw_out_i);   Serial.print(", ");
  Serial.print(servoX_cmd);  Serial.print(", ");
  Serial.println(servoZ_cmd);

  // --- UDP send to Pico AP ---
  static uint32_t lastSendMs = 0;
  uint32_t nowMs = millis();
  if (nowMs - lastSendMs >= 50) {     // ~20 Hz
    sendDegrees(roll_out_i, yaw_out_i);
    lastSendMs = nowMs;
  }
}
