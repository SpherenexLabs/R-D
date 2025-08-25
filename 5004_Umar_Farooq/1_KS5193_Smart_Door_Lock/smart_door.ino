#include <Servo.h>

#define METAL_PIN   2
#define PIR_PIN     4
#define SERVO_PIN   9
#define BUZZER_PIN 10

Servo servo_1;

void setup() {
  Serial.begin(9600);

  pinMode(METAL_PIN, INPUT);   // use INPUT_PULLUP if your module is open-collector
  pinMode(PIR_PIN, INPUT);     // use INPUT_PULLUP if needed
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);

  servo_1.attach(SERVO_PIN);
  servo_1.write(0);
}

void loop() {
  bool metal_detected  = (digitalRead(METAL_PIN) == LOW);
  bool motion_detected = (digitalRead(PIR_PIN)   == HIGH);

  // Motion gate: move only when motion is present and no metal is detected
  if (motion_detected && !metal_detected) {
    Serial.println("Motion Detected (no metal)");
    servo_1.write(90);
    delay(5000);
    servo_1.write(0);
  }
  // Metal alarm: beep if metal present
  else if (metal_detected) {
    Serial.println("Metal Detected");
    digitalWrite(BUZZER_PIN, HIGH);
    delay(500);
    digitalWrite(BUZZER_PIN, LOW);
    delay(500);
  }
  // Idle: ensure buzzer is off
  else {
    digitalWrite(BUZZER_PIN, LOW);
  }
  delay(500);
}
