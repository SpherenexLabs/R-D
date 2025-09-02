// ===== File: esp8266_l298_ultra_serial.ino =====
// NodeMCU ESP8266 + L298N + Ultrasonic + Buzzer
// Commands via Serial Monitor: F,B,L,R,S
// Stops + beeps if obstacle ≤ 15 cm

#define TRIG_PIN    D4   // Trigger
#define ECHO_PIN    D3   // Echo
#define BUZZER_PIN  D2   // Buzzer

// L298N pins
#define IN1 D5   // Left motor IN1
#define IN2 D6   // Left motor IN2
#define IN3 D7   // Right motor IN3
#define IN4 D8   // Right motor IN4

const int DISTANCE_THRESHOLD_CM = 15;
char currentCmd = 'F';  // Default forward

// ---- Motor control ----
void stopMotor() {
  digitalWrite(IN1, LOW); digitalWrite(IN2, LOW);
  digitalWrite(IN3, LOW); digitalWrite(IN4, LOW);
  Serial.println("Action: STOP");
}
void forwardMotor() {
  digitalWrite(IN1, LOW);  digitalWrite(IN2, HIGH);
  digitalWrite(IN3, LOW);  digitalWrite(IN4, HIGH);
  Serial.println("Action: FORWARD");
}
void backwardMotor() {
  digitalWrite(IN1, HIGH); digitalWrite(IN2, LOW);
  digitalWrite(IN3, HIGH); digitalWrite(IN4, LOW);
  Serial.println("Action: BACKWARD");
}
void leftTurn() {
  digitalWrite(IN1, LOW); digitalWrite(IN2, HIGH);
  digitalWrite(IN3, HIGH);  digitalWrite(IN4, LOW);
  Serial.println("Action: LEFT");
}
void rightTurn() {
  digitalWrite(IN1, HIGH);  digitalWrite(IN2, LOW);
  digitalWrite(IN3, LOW); digitalWrite(IN4, HIGH);
  Serial.println("Action: RIGHT");
}

// ---- Ultrasonic ----
float getDistanceCm() {
  digitalWrite(TRIG_PIN, LOW); delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH); delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  long duration = pulseIn(ECHO_PIN, HIGH, 30000UL);
  if (duration == 0) return 9999;  // timeout
  return (duration * 0.0343f) / 2.0f;
}
void beep() {
  digitalWrite(BUZZER_PIN, HIGH);
  delay(200);
  digitalWrite(BUZZER_PIN, LOW);
  delay(200);
}

// ---- Apply command ----
void applyCommand(char c) {
  switch (c) {
    case 'F': forwardMotor(); break;
    case 'B': backwardMotor(); break;
    case 'L': leftTurn(); break;
    case 'R': rightTurn(); break;
    default:  stopMotor(); break;
  }
}

void setup() {
  Serial.begin(9600);

  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(BUZZER_PIN, OUTPUT);

  pinMode(IN1, OUTPUT); pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT); pinMode(IN4, OUTPUT);

  stopMotor();
  Serial.println("Ready. Send F,B,L,R,S over Serial.");
}

void loop() {
  // Read serial command
  if (Serial.available() > 0) {
    char c = Serial.read();
    if (c >= 'a' && c <= 'z') c -= 32;  // to uppercase
    if (c == 'F' || c == 'B' || c == 'L' || c == 'R' || c == 'S') {
      currentCmd = c;
      Serial.print("Cmd: "); Serial.println(currentCmd);
    }
  }

  // Distance check
  float d = getDistanceCm();
  Serial.print("Distance: "); Serial.print(d, 1); Serial.println(" cm");

  if (d <= DISTANCE_THRESHOLD_CM) {
    stopMotor();
    beep();
    Serial.println("Obstacle detected! STOP");
  } else {
    applyCommand(currentCmd);
  }

  delay(100);
}
