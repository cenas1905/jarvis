/*
  JARVIS robot kol - guvenli tek-motor seri test yazilimi
  Kart: klasik ESP32 Dev Module, Serial Monitor: 115200, satir sonu: Newline.
  Gerekli kutuphane: ESP32Servo (madhephaestus).

  USB/ESP32 5 V pininden motor beslemeyin. Servo ve ULN2003 harici, yeterli
  akim verebilen regule 5 V kaynaktan beslensin. Tum GND'ler ortak olsun.

  Acilista servo attach/homing YOK. Yalnizca step cikislari LOW yapilir.
  Ilk jog, fiziksel aci bilinmedigi icin secilen servoyu 1500 us yakinina
  SICRATABILIR. Kolu elle destekleyin. release sonrasi kol dusebilir.
*/

#include <Arduino.h>
#include <ESP32Servo.h>

constexpr uint8_t SERVO_COUNT = 5;
const uint8_t SERVO_PINS[SERVO_COUNT] = {13, 14, 25, 26, 27};
const char *SERVO_NAMES[SERVO_COUNT] = {
  "taban", "ust_kol", "kiskac_egimi", "bilek", "kiskac"
};
const uint8_t STEP_PINS[4] = {18, 19, 21, 22};

constexpr int MIN_PULSE_US = 1400;
constexpr int CENTER_PULSE_US = 1500;
constexpr int MAX_PULSE_US = 1600;
constexpr int US_PER_JOG_UNIT = 6;  // jog 10 = 60 us; aci mekanige gore degisir.
constexpr int MAX_JOG_UNITS = 10;
constexpr int SERVO_US_PER_TICK = 2;
constexpr uint32_t SERVO_TICK_MS = 20;
constexpr int MAX_HALF_STEPS = 32;
constexpr uint32_t STEP_TICK_US = 4000;

// 28BYJ-48 icin 8 durumlu yarim adim dizisi, IN1-IN4 sirasinda.
const uint8_t HALF_STEP[8][4] = {
  {1, 0, 0, 0}, {1, 1, 0, 0}, {0, 1, 0, 0}, {0, 1, 1, 0},
  {0, 0, 1, 0}, {0, 0, 1, 1}, {0, 0, 0, 1}, {1, 0, 0, 1}
};

Servo servos[SERVO_COUNT];
int servoPulseUs[SERVO_COUNT] = {1500, 1500, 1500, 1500, 1500};
int selectedServo = -1;  // 0-4; secmek PWM baslatmaz.

bool servoMoving = false;
int servoTargetUs = CENTER_PULSE_US;
uint32_t lastServoTickMs = 0;

bool stepMoving = false;
bool stepCoilsEnergized = false;
int stepRemaining = 0;
int stepDirection = 1;
uint8_t stepPhase = 0;
uint32_t lastStepTickUs = 0;

char lineBuffer[64];
uint8_t lineLength = 0;
bool discardUntilNewline = false;

void stepCoilsOff() {
  for (uint8_t i = 0; i < 4; ++i) digitalWrite(STEP_PINS[i], LOW);
  stepCoilsEnergized = false;
}

void stopMotion() {
  servoMoving = false;  // Servo PWM acik kalir, son konumunu tutar.
  stepMoving = false;
  stepRemaining = 0;
  stepCoilsOff();
}

void releaseAll() {
  stopMotion();
  for (uint8_t i = 0; i < SERVO_COUNT; ++i) {
    if (servos[i].attached()) servos[i].detach();
    servoPulseUs[i] = CENTER_PULSE_US;  // Yeniden takilinca gercek aci bilinmez.
  }
  selectedServo = -1;
}

void printHelp() {
  Serial.println("Komutlar: servo 1..5 | jog -10..10 | step -32..32 | stop | release | ! | help | status");
  Serial.println("Ornek: servo 1 [Enter], jog 10 [Enter]; step -8 [Enter]");
  Serial.println("jog 10 = 60 us; sinir 1400..1600 us. Tek seferde yalniz bir motor hareket eder.");
  Serial.println("UYARI: ilk servo jog sicrayabilir; release sonrasi kol dusebilir.");
}

void printStatus() {
  Serial.print("STATUS selected=");
  if (selectedServo < 0) Serial.print("none");
  else Serial.print(selectedServo + 1);
  Serial.print(" moving=");
  Serial.print(servoMoving ? "servo" : (stepMoving ? "step" : "none"));
  Serial.print(" step_remaining=");
  Serial.print(stepRemaining);
  for (uint8_t i = 0; i < SERVO_COUNT; ++i) {
    Serial.print(" s");
    Serial.print(i + 1);
    Serial.print("=");
    if (servos[i].attached()) Serial.print(servoPulseUs[i]);
    else Serial.print("off");
  }
  Serial.println();
}

bool parseInteger(const String &value, int &out) {
  String trimmed = value;
  trimmed.trim();
  if (trimmed.length() == 0) return false;
  char *end = nullptr;
  long n = strtol(trimmed.c_str(), &end, 10);
  if (end == trimmed.c_str() || *end != '\0' || n < -1000 || n > 1000) return false;
  out = static_cast<int>(n);
  return true;
}

void processCommand(const char *rawLine) {
  String cmd(rawLine);
  cmd.trim();
  cmd.toLowerCase();
  if (cmd.length() == 0) return;

  if (cmd == "help") { printHelp(); return; }
  if (cmd == "status") { printStatus(); return; }
  if (cmd == "stop") { stopMotion(); Serial.println("OK stop"); return; }
  if (cmd == "release" || cmd == "!") {
    releaseAll(); Serial.println("OK release"); return;
  }

  if (cmd.startsWith("servo ")) {
    int index;
    if (!parseInteger(cmd.substring(6), index) || index < 1 || index > 5) {
      Serial.println("ERR servo 1..5 olmali"); return;
    }
    if (servoMoving || stepMoving) { Serial.println("ERR busy; once stop"); return; }
    selectedServo = index - 1;
    Serial.print("OK servo ");
    Serial.print(index);
    Serial.print(" ");
    Serial.print(SERVO_NAMES[selectedServo]);
    Serial.println(" selected; PWM henuz acilmadi");
    return;
  }

  if (cmd.startsWith("jog ")) {
    int units;
    if (!parseInteger(cmd.substring(4), units) || units == 0 ||
        units < -MAX_JOG_UNITS || units > MAX_JOG_UNITS) {
      Serial.println("ERR jog -10..-1 veya 1..10 olmali"); return;
    }
    if (selectedServo < 0) { Serial.println("ERR once servo 1..5 sec"); return; }
    if (servoMoving || stepMoving) { Serial.println("ERR busy; once stop"); return; }

    Servo &motor = servos[selectedServo];
    if (!motor.attached()) {
      // Ilk PWM burada baslar. Kutuphane attach aninda merkeze yakin darbe
      // verebilir; mekanik konum bilinmedigi icin kolu desteklemek sarttir.
      motor.setPeriodHertz(50);
      motor.attach(SERVO_PINS[selectedServo], MIN_PULSE_US, MAX_PULSE_US);
      if (!motor.attached()) { Serial.println("ERR servo attach basarisiz"); return; }
      servoPulseUs[selectedServo] = CENTER_PULSE_US;
      motor.writeMicroseconds(CENTER_PULSE_US);
    }
    servoTargetUs = constrain(servoPulseUs[selectedServo] +
                              units * US_PER_JOG_UNIT,
                              MIN_PULSE_US, MAX_PULSE_US);
    if (servoTargetUs == servoPulseUs[selectedServo]) {
      Serial.println("OK servo sinirda; hareket yok"); return;
    }
    servoMoving = true;
    lastServoTickMs = millis();
    Serial.print("OK jog started target_us=");
    Serial.println(servoTargetUs);
    return;
  }

  if (cmd.startsWith("step ")) {
    int count;
    if (!parseInteger(cmd.substring(5), count) || count == 0 ||
        count < -MAX_HALF_STEPS || count > MAX_HALF_STEPS) {
      Serial.println("ERR step -32..-1 veya 1..32 olmali"); return;
    }
    if (servoMoving || stepMoving) { Serial.println("ERR busy; once stop"); return; }
    stepRemaining = abs(count);
    stepDirection = count > 0 ? 1 : -1;
    stepMoving = true;
    lastStepTickUs = micros() - STEP_TICK_US;  // Ilk adim hemen.
    Serial.print("OK step started count=");
    Serial.println(count);
    return;
  }

  Serial.println("ERR bilinmeyen komut; help yaz");
}

void readSerial() {
  while (Serial.available() > 0) {
    char c = static_cast<char>(Serial.read());
    if (c == '!') {
      releaseAll();
      lineLength = 0;
      discardUntilNewline = true;
      Serial.println("OK emergency release");
      continue;
    }
    if (c == '\n' || c == '\r') {
      if (!discardUntilNewline && lineLength > 0) {
        lineBuffer[lineLength] = '\0';
        processCommand(lineBuffer);
      }
      lineLength = 0;
      discardUntilNewline = false;
      continue;
    }
    if (discardUntilNewline) continue;
    if (lineLength < sizeof(lineBuffer) - 1) lineBuffer[lineLength++] = c;
    else {
      lineLength = 0;
      discardUntilNewline = true;
      Serial.println("ERR komut cok uzun");
    }
  }
}

void tickServo() {
  if (!servoMoving || millis() - lastServoTickMs < SERVO_TICK_MS) return;
  lastServoTickMs = millis();
  int &pulse = servoPulseUs[selectedServo];
  if (pulse < servoTargetUs) pulse = min(pulse + SERVO_US_PER_TICK, servoTargetUs);
  else pulse = max(pulse - SERVO_US_PER_TICK, servoTargetUs);
  servos[selectedServo].writeMicroseconds(pulse);
  if (pulse == servoTargetUs) {
    servoMoving = false;
    Serial.print("DONE servo ");
    Serial.print(selectedServo + 1);
    Serial.print(" us=");
    Serial.println(pulse);
  }
}

void tickStep() {
  if (!stepMoving || static_cast<uint32_t>(micros() - lastStepTickUs) < STEP_TICK_US) return;
  lastStepTickUs = micros();
  for (uint8_t i = 0; i < 4; ++i) digitalWrite(STEP_PINS[i], HALF_STEP[stepPhase][i]);
  stepCoilsEnergized = true;
  stepPhase = (stepPhase + stepDirection + 8) % 8;
  --stepRemaining;
  if (stepRemaining == 0) {
    stepMoving = false;
    // Son bobin darbesinin en az bir tam adim suresi kalmasi icin hemen
    // kapatma yerine asagidaki tick'te kapatilir.
  }
}

void setup() {
  Serial.begin(115200);
  for (uint8_t i = 0; i < 4; ++i) {
    pinMode(STEP_PINS[i], OUTPUT);
    digitalWrite(STEP_PINS[i], LOW);
  }
  // Burada servo attach, write veya otomatik homing YOK.
  Serial.println("READY JARVIS robot arm test; help yaz");
}

void loop() {
  readSerial();  // stop ve !, bekleme/delay olmadan islenir.
  tickServo();
  tickStep();
  if (!stepMoving && stepCoilsEnergized &&
      static_cast<uint32_t>(micros() - lastStepTickUs) >= STEP_TICK_US) {
    stepCoilsOff();
    Serial.println("DONE step; coils=off");
  }
}
