const int flexPins[5] = {A0, A1, A2, A3, A4};  // 5 flex sensors
int adcValues[5];

void setup() {
  Serial.begin(9600);
  analogReference(INTERNAL);   // Use 1.1V ADC reference for better resolution
  for (int i = 0; i < 5; i++) {
    pinMode(flexPins[i], INPUT);
  }
}

void loop() {
  // Read all sensors
  for (int i = 0; i < 5; i++) {
    adcValues[i] = analogRead(flexPins[i]);
  }

  int out = -1;  // default (undefined)

  // ---- Conditions ----
  if (adcValues[0] > 100 &&
      adcValues[1] <= 100 &&
      adcValues[2] <= 100 &&
      adcValues[3] <= 100 &&
      adcValues[4] <= 100) {
    out = 1;  // Only S0 > 100
  }
  else if (adcValues[0] > 100 &&
           adcValues[1] > 100 &&
           adcValues[2] > 100 &&
           adcValues[3] > 100 &&
           adcValues[4] > 100) {
    out = 0;  // All > 100
  }
  else if (adcValues[0] > 100 &&
           adcValues[1] > 100 &&
           adcValues[2] <= 100 &&
           adcValues[3] <= 100 &&
           adcValues[4] <= 100) {
    out = 2;  // Only S0 & S1 > 100
  }
  else if (adcValues[0] > 100 &&
           adcValues[1] > 100 &&
           adcValues[2] > 100 &&
           adcValues[3] <= 100 &&
           adcValues[4] <= 100) {
    out = 3;  // Only S0, S1, S2 > 100
  }
  else if (adcValues[0] > 100 &&
           adcValues[1] > 100 &&
           adcValues[2] > 100 &&
           adcValues[3] > 100 &&
           adcValues[4] <= 100) {
    out = 4;  // Only S0, S1, S2, S3 > 100
  }
  else if (adcValues[0] <= 100 &&
           adcValues[1] <= 100 &&
           adcValues[2] <= 100 &&
           adcValues[3] <= 100 &&
           adcValues[4] <= 100) {
    out = 5;  // All ≤ 100
  }

  // Print ADCs + Result
  for (int i = 0; i < 5; i++) {
    Serial.print("S"); Serial.print(i);
    Serial.print("="); Serial.print(adcValues[i]);
    Serial.print("  ");
  }
  Serial.print("-> Output: ");
  Serial.println(out);

  delay(300);
}
