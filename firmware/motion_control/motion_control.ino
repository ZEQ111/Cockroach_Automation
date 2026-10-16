/*
 * =====================================================================
 *  蟑螂自动化装配系统 —— 龙门 XYZ + 平台 运动控制固件 (V2)
 *  控制器: Arduino Mega2560
 *  驱动器:
 *    UMIP42 一体式步进 ×4 : X1(左丝杆) X2(右丝杆) Y Z  (共阴极, 脉冲)
 *    TB6600 外置步进   ×1 : B(平台/载物台, 42步进)      (共阴极, 脉冲)
 *  接线方式: 共阴极 (PUL-/DIR-/ENA- 接 GND, PUL+/DIR+ 接 Arduino 引脚)
 *  驱动模式: UMIP42 SW2=OFF (PU+DR), TB6600 用 PUL/DIR
 *  库依赖:   AccelStepper (Arduino IDE -> 库管理器 -> 搜索安装)
 * =====================================================================
 */

#include <AccelStepper.h>

// ==================== 引脚定义 (按实际接线修改这几行即可) ====================
#define STEP_X1 2
#define DIR_X1  3

#define STEP_X2 11   // 注: X2 与 Z 物理装反, 已互换引脚
#define DIR_X2  12

// 注: D6 损坏, 引脚顺延
#define STEP_Y  7
#define DIR_Y   8

#define STEP_Z  4    // 注: X2 与 Z 物理装反, 已互换引脚
#define DIR_Z   5

#define STEP_B  9
#define DIR_B   10

// 钻孔工具头：蓝线为 PWM 调速，白线为方向。
// D44 是 Mega2560 的 OC5C，可由 Timer5 输出独立的 25kHz PWM。
#define DRILL_PWM_PIN 44
#define DRILL_DIR_PIN 42
#define DRILL_RUN_DUTY 50   // 保留 PWM 跳变；100%会变成无边沿的持续高电平
#define DRILL_PWM_TOP 639       // 16MHz / (639 + 1) = 25kHzY-300
#define DRILL_CW_LEVEL LOW      // 商家资料：LOW=CW，HIGH=CCW

// 限位/原点开关输入 (NPN 常开, 低电平有效, 内部上拉)
// X1(左杆)、Y、Z 三处有开关, 每处两端各一个 (X2 无)
#define LIM_X1_MIN 28   // X1 左杆 原点端
#define LIM_X1_MAX 23   // X1 左杆 远端
#define LIM_Y_MIN  24   // Y 原点端
#define LIM_Y_MAX  25   // Y 远端
#define LIM_Z_MIN  26   // Z 原点端 (原 X2 的限位, 已随换装移到 Z)
#define LIM_Z_MAX  27  // Z 远端   (原 X2 的限位, 已随换装移到 Z)
// ==========================================================================

// 只记录软件调用了多少次脉冲输出，不等同于测量引脚电平或电机位移。
class LoggedBStepper : public AccelStepper {
public:
  unsigned long pulseCount = 0;
  unsigned long lastPulseUs = 0;
  LoggedBStepper() : AccelStepper(AccelStepper::DRIVER, STEP_B, DIR_B) {}
protected:
  void step(long position) override {
    AccelStepper::step(position);
    ++pulseCount;
    lastPulseUs = micros();
  }
};

// 5 个步进器 (DRIVER 模式: stepPin + dirPin)
AccelStepper axisX1(AccelStepper::DRIVER, STEP_X1, DIR_X1);
AccelStepper axisX2(AccelStepper::DRIVER, STEP_X2, DIR_X2);
AccelStepper axisY (AccelStepper::DRIVER, STEP_Y,  DIR_Y);
AccelStepper axisZ (AccelStepper::DRIVER, STEP_Z,  DIR_Z);
LoggedBStepper axisB;

// 默认速度/加速度 (单位: 步/秒, 步/秒^2)
#define DEFAULT_SPEED 500.0
#define DEFAULT_ACCEL 500.0

// ===== 回零参数 =====
#define HOME_SPEED   150.0   // 回零速度 (步/秒, 慢一点更准)
#define HOME_BACKOFF 20      // 触发后回退步数 (松开开关)
#define HOME_TIMEOUT 20000    // 最多走多少步就放弃, 防跑飞 (按实际行程调)

// 回零方向: 1 = 正方向找原点, -1 = 负方向找原点 (方向反了就改这里)
#define HOME_DIR_X1 -1   // X原点在后面, X正方向=前 -> 负方向回零
#define HOME_DIR_Y   1   // Y原点在左边, Y正方向=左 -> 正方向回零
#define HOME_DIR_Z  -1   // Z原点在最上, Z正方向=下 -> 负方向回零

// ===== 毫米/角度换算 =====
// steps/mm = 步/圈 ÷ 导程(mm/圈)
// X/Y 导程 12mm, Z 导程 2mm (UMIP42 都是 200 步/圈); B 旋转 400 步/圈 (TB6600)
#define STEPS_PER_MM_X  (200.0 / 12.0)    // ≈ 16.667 步/mm
#define STEPS_PER_MM_Y  (200.0 / 12.0)    // ≈ 16.667 步/mm
#define STEPS_PER_MM_Z  (200.0 / 2.0)     // = 100 步/mm
#define STEPS_PER_DEG_B (400.0 / 360.0)   // ≈ 1.111 步/度

String cmdBuf = "";
String lastLimitEvent = "NONE";
bool homingActive = false;
bool homingAborted = false;
bool homeFaultLatched = false;
bool discardLongCommand = false;
unsigned long bCommandSeq = 0;
const char *lastBCommand = "BOOT";
bool bPulseReporting = false;
unsigned long bReportedPulses = 0;
bool bAxisEnabled = false;  // 上电默认锁定，防止 B 轴未经命令输出脉冲
bool drillRunning = false;
uint8_t drillDuty = 0;
bool zContinuousDown = false;  // ZDOWN/ZD 后保持向 Z+（下）运动，直到 E/STOP/下限位
// 上电后各轴的软件坐标从0开始。首次成功碰到原点开关前的净步数，
// 等于“上电位置 -> 原点开关”的位移；取反即可推算上电时的绝对坐标。
bool powerOnTrackingIntact = true;
bool bootPosValidX = false, bootPosValidY = false, bootPosValidZ = false;
long homeNetX = 0, homeNetY = 0, homeNetZ = 0;
long bootPosX = 0, bootPosY = 0, bootPosZ = 0;

// Explicit declarations also support offline compilation without IDE preprocessing.
void handleCommand(String s);
void pollSerialCommands();
void reportB(const char *event);
void clearMotionTargets(const char *reason);
void initDrillSafe();
void setDrillDuty(uint8_t duty);
void printDrillStatus();
void setBAxisEnabled(bool enabled);
void stopAll();
void runHoming(char which);
bool homeService();
bool homeSingle(AccelStepper &axis, int homePin, int dir, const char *name);
bool homeX();
void homeAll();
void recordPowerOnHome(char axis, long netStepsToSwitch);
void printPowerOnPosition();

void setup() {
  // 钻头是风险最高的输出，进入 setup 后第一时间拉到停止电平。
  // 注意：复位/bootloader 运行前的悬空阶段仍需 D44 外接下拉电阻兜底。
  initDrillSafe();
  initDriverPinsSafe();

  Serial.begin(115200);       // 与 PC 上位机通信波特率

  // 限位开关输入 (NPN, 内部上拉: 平时HIGH, 触发LOW)
  pinMode(LIM_X1_MIN, INPUT_PULLUP);
  pinMode(LIM_X1_MAX, INPUT_PULLUP);
  pinMode(LIM_Y_MIN,  INPUT_PULLUP);
  pinMode(LIM_Y_MAX,  INPUT_PULLUP);
  pinMode(LIM_Z_MIN,  INPUT_PULLUP);
  pinMode(LIM_Z_MAX,  INPUT_PULLUP);

  initAxis(axisX1);
  initAxis(axisX2);
  initAxis(axisY);
  initAxis(axisZ);
  initAxis(axisB);

  Serial.println("READY");
  Serial.println("FW motion-bootpos-p2-20260930");
  reportB("BOOT");
  printHelp();
}

void initDriverPinsSafe() {
  const uint8_t stepPins[] = {STEP_X1, STEP_X2, STEP_Y, STEP_Z, STEP_B};
  const uint8_t dirPins[] = {DIR_X1, DIR_X2, DIR_Y, DIR_Z, DIR_B};
  for (uint8_t i = 0; i < 5; ++i) {
    digitalWrite(stepPins[i], LOW);
    digitalWrite(dirPins[i], LOW);
    pinMode(stepPins[i], OUTPUT);
    pinMode(dirPins[i], OUTPUT);
  }
}

void initDrillSafe() {
  // 先写低再切输出，避免 setup 初始化时产生启动脉冲。
  digitalWrite(DRILL_PWM_PIN, LOW);
  digitalWrite(DRILL_DIR_PIN, DRILL_CW_LEVEL);
  pinMode(DRILL_PWM_PIN, OUTPUT);
  pinMode(DRILL_DIR_PIN, OUTPUT);

#ifdef __AVR__
  // Timer5 Fast PWM, mode 14, TOP=ICR5, no prescaler. OC5C 先保持断开。
  TCCR5A = 0;
  TCCR5B = 0;
  TCNT5 = 0;
  ICR5 = DRILL_PWM_TOP;
  OCR5C = 0;
  TCCR5A = _BV(WGM51);
  TCCR5B = _BV(WGM53) | _BV(WGM52) | _BV(CS50);
#endif

  drillDuty = 0;
  drillRunning = false;
}

void setDrillDuty(uint8_t duty) {
  if (duty > 100) duty = 100;

#ifdef __AVR__
  // 0% 和 100% 直接输出确定电平，避免 Fast PWM 极值处的窄脉冲。
  if (duty == 0 || duty == 100) {
    TCCR5A &= ~(_BV(COM5C1) | _BV(COM5C0));
    digitalWrite(DRILL_PWM_PIN, duty == 0 ? LOW : HIGH);
  } else {
    uint16_t highCounts = ((uint32_t)(DRILL_PWM_TOP + 1) * duty + 50) / 100;
    OCR5C = highCounts - 1;
    TCCR5A = (TCCR5A & ~_BV(COM5C0)) | _BV(COM5C1);
  }
#else
  // 主机回归测试没有硬件定时器，用引脚电平表示启停状态。
  digitalWrite(DRILL_PWM_PIN, duty == 0 ? LOW : HIGH);
#endif

  drillDuty = duty;
  drillRunning = duty > 0;
}

void printDrillStatus() {
  Serial.print("DRILL ");
  Serial.print(drillRunning ? "ON" : "OFF");
  Serial.print(" DUTY="); Serial.print((int)drillDuty);
  Serial.print(" FREQ=");
  Serial.print(drillDuty > 0 && drillDuty < 100 ? "25000" : "DC");
  Serial.print(" DIR=");
  Serial.println(DRILL_CW_LEVEL == LOW ? "CW" : "CCW");
}

void setBAxisEnabled(bool enabled) {
  // 清除旧目标，解锁后也不会补跑锁定期间积压的位移。
  axisB.setCurrentPosition(axisB.currentPosition());
  axisB.setSpeed(0);
  digitalWrite(STEP_B, LOW);
  bAxisEnabled = enabled;
}

void initAxis(AccelStepper &s) {
  s.setMaxSpeed(DEFAULT_SPEED);
  s.setAcceleration(DEFAULT_ACCEL);
  // run() 会依据当前速度和目标刷新脉冲。启动时必须显式建立
  // “当前位置 == 目标位置、速度 == 0”的静止状态，不能预装非零速度。
  s.setCurrentPosition(0);
  s.moveTo(0);
  s.setSpeed(0);
}

void loop() {
  // 先处理停止指令和限位，再发本轮脉冲，避免已收到 E/STOP 后多走一步。
  pollSerialCommands();
  checkLimitStop();

  // 非阻塞刷新所有轴, 支持多轴同时运动
  axisX1.run();
  axisX2.run();
  axisY.run();
  axisZ.run();
  if (bAxisEnabled) axisB.run();
  else digitalWrite(STEP_B, LOW);
  if (axisB.pulseCount != bReportedPulses && !bPulseReporting) {
    reportB("PULSE_BEGIN");
    bPulseReporting = true;
  }
  bReportedPulses = axisB.pulseCount;
  if (bPulseReporting && axisB.distanceToGo() == 0 && axisB.speed() == 0) {
    reportB("PULSE_END");
    bPulseReporting = false;
  }

}

// 有限量读串口，HOME的每个运动循环也调用；超长整行丢弃，不执行截断命令。
void pollSerialCommands() {
  uint8_t budget = 64;
  while (Serial.available() && budget--) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (discardLongCommand) {
        discardLongCommand = false;
        cmdBuf = "";
        Serial.println("ERR command_too_long");
      } else if (cmdBuf.length() > 0) {
        String ready = cmdBuf;
        cmdBuf = ""; // 在派发前清空，HOME内部才能安全读取下一条命令。
        handleCommand(ready);
      }
    } else if (!discardLongCommand) {
      if (cmdBuf.length() < 96) cmdBuf += c;
      else { cmdBuf = ""; discardLongCommand = true; }
    }
  }
}

void handleCommand(String s) {
  s.trim();
  if (s.length() == 0) return;

  String up = s;
  up.toUpperCase();
  char c0 = toupper(s.charAt(0));
  if (up == "STOP" || up == "E") {
    setDrillDuty(0);
    if (homingActive) {
      homingAborted = true;
      homeFaultLatched = true;
      clearMotionTargets("HOME_STOP");
      Serial.println("STOP HOME");
    } else {
      // 安全停止不走减速距离：立即把所有目标清到当前位置。
      clearMotionTargets("STOP");
      reportB("STOP");
      Serial.println(up == "E" ? "STOP E" : "STOP");
    }
    return;
  }
  // 关钻头在回零或故障锁定期间仍然始终有效。
  if (up == "DRILL OFF") {
    setDrillDuty(0);
    printDrillStatus();
    return;
  }
  if (up == "B DISABLE") {
    setBAxisEnabled(false);
    Serial.println("B DISABLED");
    return;
  }
  if (up == "B STATUS") {
    Serial.print("B ");
    Serial.println(bAxisEnabled ? "ENABLED" : "DISABLED");
    return;
  }
  bool readOnly = up == "DIAG" || up == "POS" || up == "P" ||
                  up == "MPOS" || up == "LIM" || up == "?" ||
                  up == "DRILL STATUS" || up == "B STATUS" ||
                  up == "BOOTPOS" || up == "BP";
  bool homeCommand = up == "HOME" || up == "HX" || up == "HY" || up == "HZ";
  if (homingActive && !readOnly) {
    Serial.println("ERR BUSY HOME");
    return;
  }
  if (homeFaultLatched && !readOnly && !homeCommand) {
    Serial.println("ERR HOME_REQUIRED");
    return;
  }
  if (up == "BOOTPOS" || up == "BP") {
    printPowerOnPosition();
    return;
  }
  if (up == "DIAG") {
    Serial.print("HOMING="); Serial.print(homingActive);
    Serial.print(" HOME_FAULT="); Serial.println(homeFaultLatched);
    Serial.print("LAST_LIMIT "); Serial.println(lastLimitEvent);
    Serial.print("TARGET X="); Serial.print(axisX1.targetPosition());
    Serial.print(" X2="); Serial.print(axisX2.targetPosition());
    Serial.print(" Y="); Serial.print(axisY.targetPosition());
    Serial.print(" Z="); Serial.print(axisZ.targetPosition());
    Serial.print(" B="); Serial.println(axisB.targetPosition());
    Serial.print("SPEED_X="); Serial.print(axisX1.speed());
    Serial.print(" MAX_X="); Serial.println(axisX1.maxSpeed());
    Serial.print("Z_CONTINUOUS_DOWN="); Serial.println(zContinuousDown);
    Serial.print("STATE_B POS="); Serial.print(axisB.currentPosition());
    Serial.print(" TARGET="); Serial.print(axisB.targetPosition());
    Serial.print(" DIST="); Serial.print(axisB.distanceToGo());
    Serial.print(" SPEED="); Serial.print(axisB.speed());
    Serial.print(" MAX="); Serial.println(axisB.maxSpeed());
    reportB("DIAG");
    return;
  }
  if (up == "DRILL STATUS") {
    printDrillStatus();
    return;
  }
  if (up == "B ENABLE") {
    setBAxisEnabled(true);
    Serial.println("B ENABLED");
    return;
  }
  if (up == "DRILL ON") {
    digitalWrite(DRILL_DIR_PIN, DRILL_CW_LEVEL);
    setDrillDuty(DRILL_RUN_DUTY);
    printDrillStatus();
    return;
  }
  if (up.startsWith("DRILL PWM")) {
    String valueText = up.substring(9);
    valueText.trim();
    if (valueText.length() == 0) {
      Serial.println("ERR DRILL PWM needs 0..100");
      return;
    }
    int requestedDuty = valueText.toInt();
    if (requestedDuty < 0 || requestedDuty > 100) {
      Serial.println("ERR DRILL PWM range 0..100");
      return;
    }
    digitalWrite(DRILL_DIR_PIN, DRILL_CW_LEVEL);
    setDrillDuty((uint8_t)requestedDuty);
    printDrillStatus();
    return;
  }

  // ---- Z 轴持续下行：Z 正方向为下；E 或 STOP 立即停止，Zmax 限位兜底 ----
  if (up == "ZDOWN" || up == "ZD") {
    if (digitalRead(LIM_Z_MAX) == LOW) {
      zContinuousDown = false;
      axisZ.setCurrentPosition(axisZ.currentPosition());
      Serial.println("LIMIT_STOP Zmax_already_active");
      return;
    }
    zContinuousDown = true;
    axisZ.moveTo(2147483647L);
    Serial.println("MOVE Z DOWN CONTINUOUS; E=STOP");
    return;
  }

  // ZERO必须先于Z相对移动解析，避免被当作Z0吞掉。
  if (up == "ZERO" || up == "R") {
    zeroAll();
    reportB("ZERO");
    Serial.println("ZEROED");
    return;
  }

  // ---- X1 / X2 单独移动 (需加空格, 如 "X1 100", 避免与 "X100" 混淆) ----
  if (up.startsWith("X1") && (up.length() == 2 || up.charAt(2) == ' ')) {
    long steps = s.substring(2).toInt();
    axisX1.move(steps);
    Serial.print("MOVE X1 "); Serial.println(steps);
    return;
  }
  if (up.startsWith("X2") && (up.length() == 2 || up.charAt(2) == ' ')) {
    long steps = s.substring(2).toInt();
    axisX2.move(steps);
    Serial.print("MOVE X2 "); Serial.println(steps);
    return;
  }

  // ---- 轴相对移动: X(双轴联动)/Y/Z/B + 步数 (如 X1000, Y-500) ----
  if (c0 == 'X') {
    long steps = s.substring(1).toInt();
    axisX1.move(steps);
    axisX2.move(steps);
    Serial.print("MOVE X "); Serial.println(steps);
    return;
  }
  if (c0 == 'Y') {
    long steps = s.substring(1).toInt();
    axisY.move(steps);
    Serial.print("MOVE Y "); Serial.println(steps);
    return;
  }
  if (c0 == 'Z') {
    zContinuousDown = false;
    long steps = s.substring(1).toInt();
    axisZ.move(steps);
    Serial.print("MOVE Z "); Serial.println(steps);
    return;
  }
  if (c0 == 'B') {
    if (!bAxisEnabled) {
      Serial.println("ERR B_DISABLED: send B ENABLE first");
      return;
    }
    long steps = s.substring(1).toInt();
    reportB("BEFORE_REL");
    axisB.move(steps);
    ++bCommandSeq;
    lastBCommand = "REL";
    reportB("CMD_REL");
    Serial.print("MOVE B "); Serial.println(steps);
    return;
  }

  // ---- 设置速度: SPEED500 / V500 ----
  if (up.startsWith("SPEED") || c0 == 'V') {
    int idx = (c0 == 'V') ? 1 : 5;
    float v = s.substring(idx).toFloat();
    if (v <= 0) v = DEFAULT_SPEED;
    setAllSpeed(v);
    Serial.print("SPEED="); Serial.println(v);
    return;
  }

  // ---- 设置加速度: ACCEL2000 ----
  if (up.startsWith("ACCEL")) {
    float a = s.substring(5).toFloat();
    if (a <= 0) a = DEFAULT_ACCEL;
    setAllAccel(a);
    Serial.print("ACCEL="); Serial.println(a);
    return;
  }

  // ---- 查询位置: POS / P ----
  if (up == "POS" || c0 == 'P') {
    printPositions();
    return;
  }

  // ---- 查询位置(mm/度): MPOS ----
  if (up == "MPOS") {
    printPositionsMM();
    return;
  }

  // ---- 绝对坐标移动: G0 X.. Y.. Z.. B.. (可省略任意轴, 未指定的轴不动) ----
  if (up.startsWith("G0") || up.startsWith("G1")) {
    moveToAbsolute(up);
    return;
  }

  // ---- 绝对坐标移动(mm/度): M0 X.. Y.. Z.. B.. (X/Y/Z 单位mm, B 单位度) ----
  if (up.startsWith("M0")) {
    moveToAbsoluteMM(up);
    return;
  }

  // ---- X 双杆计数对齐: X2 计数器 := X1 (只对齐计数, 物理歪斜需机械/加开关) ----
  if (up == "ALIGN") {
    axisX2.setCurrentPosition(axisX1.currentPosition());
    Serial.print("ALIGN X1="); Serial.print(axisX1.currentPosition());
    Serial.print(" X2="); Serial.println(axisX2.currentPosition());
    return;
  }

  // ---- 回零(限位归零): HX / HY / HZ / HOME ----
  if (up == "HX") { runHoming('X'); return; }
  if (up == "HY") { runHoming('Y'); return; }
  if (up == "HZ") { runHoming('Z'); return; }
  if (up == "HOME") { runHoming('A'); return; }

  // ---- 读限位开关状态: LIM ----
  if (up == "LIM") {
    printLimitStates();
    return;
  }

  // ---- 帮助: ? ----
  if (c0 == '?') {
    printHelp();
    return;
  }

  Serial.print("ERR unknown cmd: ");
  Serial.println(s);
}

void setAllSpeed(float v) {
  axisX1.setMaxSpeed(v);
  axisX2.setMaxSpeed(v);
  axisY.setMaxSpeed(v);
  axisZ.setMaxSpeed(v);
  axisB.setMaxSpeed(v);
}

void setAllAccel(float a) {
  axisX1.setAcceleration(a);
  axisX2.setAcceleration(a);
  axisY.setAcceleration(a);
  axisZ.setAcceleration(a);
  axisB.setAcceleration(a);
}

void stopAll() {
  // setCurrentPosition() 同时把目标设为当前位置并把速度清零，属于立即停止。
  axisX1.setCurrentPosition(axisX1.currentPosition());
  axisX2.setCurrentPosition(axisX2.currentPosition());
  axisY.setCurrentPosition(axisY.currentPosition());
  axisZ.setCurrentPosition(axisZ.currentPosition());
  axisB.setCurrentPosition(axisB.currentPosition());
  axisX1.setSpeed(0);
  axisX2.setSpeed(0);
  axisY.setSpeed(0);
  axisZ.setSpeed(0);
  axisB.setSpeed(0);
  digitalWrite(STEP_X1, LOW);
  digitalWrite(STEP_X2, LOW);
  digitalWrite(STEP_Y, LOW);
  digitalWrite(STEP_Z, LOW);
  digitalWrite(STEP_B, LOW);
  zContinuousDown = false;
}

void zeroAll() {
  zContinuousDown = false;
  // ZERO会抹掉“自上电以来”的位移链；尚未记录的上电坐标此后不可再推算。
  powerOnTrackingIntact = false;
  axisX1.setCurrentPosition(0);
  axisX2.setCurrentPosition(0);
  axisY.setCurrentPosition(0);
  axisZ.setCurrentPosition(0);
  axisB.setCurrentPosition(0);
}

// 绝对移动: 只更新指定轴，未指定轴保持原有目标/运动状态。
// 注意: 传入的 s 已经是全大写 (调用处用 up)
void moveToAbsolute(String s) {
  long tx = axisX1.targetPosition();
  long ty = axisY.targetPosition();
  long tz = axisZ.targetPosition();
  long tb = axisB.targetPosition();
  bool hasX = false, hasY = false, hasZ = false, hasB = false;

  int p = (s.length() > 2) ? 2 : 0;   // 跳过 "G0"/"G1" 前缀
  int n = s.length();
  while (p < n) {
    while (p < n && s[p] == ' ') p++;
    if (p >= n) break;
    char ax = s[p];
    p++;
    if (ax < 'A' || ax > 'Z') continue;   // 非字母忽略
    bool neg = false;
    if (p < n && (s[p] == '-' || s[p] == '+')) { neg = (s[p] == '-'); p++; }
    long val = 0;
    bool hasVal = false;
    while (p < n && isDigit(s[p])) { val = val * 10 + (s[p] - '0'); hasVal = true; p++; }
    if (!hasVal) continue;
    if (neg) val = -val;
    switch (ax) {
      case 'X': tx = val; hasX = true; break;
      case 'Y': ty = val; hasY = true; break;
      case 'Z': tz = val; hasZ = true; break;
      case 'B': tb = val; hasB = true; break;
    }
  }

  if (!(hasX || hasY || hasZ || hasB)) { Serial.println("ERR G0: no axis"); return; }
  if (hasX) { axisX1.moveTo(tx); axisX2.moveTo(tx); }
  if (hasY) axisY.moveTo(ty);
  if (hasZ) {
    zContinuousDown = false;
    axisZ.moveTo(tz);
  }
  if (hasB) {
    reportB("BEFORE_G0");
    axisB.moveTo(tb);
    ++bCommandSeq;
    lastBCommand = "G0";
    reportB("CMD_G0");
  }

  Serial.print("G0 X"); Serial.print(tx);
  Serial.print(" Y"); Serial.print(ty);
  Serial.print(" Z"); Serial.print(tz);
  Serial.print(" B"); Serial.println(tb);
}

// 绝对移动(mm/度): 未指定轴保持原有目标/运动状态。
void moveToAbsoluteMM(String s) {
  long tx = axisX1.targetPosition();
  long ty = axisY.targetPosition();
  long tz = axisZ.targetPosition();
  long tb = axisB.targetPosition();
  bool hasX = false, hasY = false, hasZ = false, hasB = false;

  int start = 0;
  int n = s.length();
  while (start < n) {
    while (start < n && s[start] == ' ') start++;
    int end = start;
    while (end < n && s[end] != ' ') end++;
    if (start >= end) break;
    String tok = s.substring(start, end);
    start = end + 1;
    if (tok.length() < 2) continue;        // 跳过 "M0" 或孤立字母
    char ax = tok.charAt(0);
    float val = tok.substring(1).toFloat();
    switch (ax) {
      case 'X': tx = (long)round(val * STEPS_PER_MM_X); hasX = true; break;
      case 'Y': ty = (long)round(val * STEPS_PER_MM_Y); hasY = true; break;
      case 'Z': tz = (long)round(val * STEPS_PER_MM_Z); hasZ = true; break;
      case 'B': tb = (long)round(val * STEPS_PER_DEG_B); hasB = true; break;
    }
  }

  if (!(hasX || hasY || hasZ || hasB)) { Serial.println("ERR M0: no axis"); return; }
  if (hasX) { axisX1.moveTo(tx); axisX2.moveTo(tx); }
  if (hasY) axisY.moveTo(ty);
  if (hasZ) {
    zContinuousDown = false;
    axisZ.moveTo(tz);
  }
  if (hasB) {
    reportB("BEFORE_M0");
    axisB.moveTo(tb);
    ++bCommandSeq;
    lastBCommand = "M0";
    reportB("CMD_M0");
  }

  Serial.print("M0 X"); Serial.print(tx / STEPS_PER_MM_X, 2);
  Serial.print(" Y");  Serial.print(ty / STEPS_PER_MM_Y, 2);
  Serial.print(" Z");  Serial.print(tz / STEPS_PER_MM_Z, 2);
  Serial.print(" B");  Serial.println(tb / STEPS_PER_DEG_B, 2);
}

void reportB(const char *event) {
  Serial.print("B_EVENT "); Serial.print(event);
  Serial.print(" ms="); Serial.print(millis());
  Serial.print(" seq="); Serial.print(bCommandSeq);
  Serial.print(" cmd="); Serial.print(lastBCommand);
  Serial.print(" pos="); Serial.print(axisB.currentPosition());
  Serial.print(" target="); Serial.print(axisB.targetPosition());
  Serial.print(" speed="); Serial.print(axisB.speed());
  Serial.print(" sw_pulses="); Serial.print(axisB.pulseCount);
  Serial.print(" last_us="); Serial.println(axisB.lastPulseUs);
}

void clearMotionTargets(const char *reason) {
  reportB(reason); // 保存清理前的残留目标。
  stopAll();
  reportB("TARGETS_CLEARED");
}

bool homeService() {
  pollSerialCommands();
  return !homingAborted;
}

void runHoming(char which) {
  setDrillDuty(0);
  homingActive = true;
  homingAborted = false;
  homeFaultLatched = true;
  clearMotionTargets("HOME_ENTER");
  bool success = false;
  if (which == 'A') {
    homeAll();
    success = !homeFaultLatched;
  } else if (which == 'X') success = homeX();
  else if (which == 'Y') success = homeSingle(axisY, LIM_Y_MIN, HOME_DIR_Y, "Y");
  else if (which == 'Z') success = homeSingle(axisZ, LIM_Z_MIN, HOME_DIR_Z, "Z");
  clearMotionTargets("HOME_EXIT");
  homeFaultLatched = !success || homingAborted;
  homingActive = false;
  if (homingAborted) Serial.println("HOME FAIL aborted_by_STOP");
}

// 每段循环都处理串口；STOP立即清目标并退出，不进入后续轴。
bool homeSingle(AccelStepper &axis, int homePin, int dir, const char *name) {
  float previousMaxSpeed = axis.maxSpeed();
  auto finish = [&](bool success) -> bool {
    axis.setCurrentPosition(axis.currentPosition());
    axis.setMaxSpeed(previousMaxSpeed);
    return success;
  };
  axis.setMaxSpeed(HOME_SPEED);
  // 如果一开始就压在开关上, 先反方向退开 (最多 500 步)
  if (digitalRead(homePin) == LOW) {
    long s0 = axis.currentPosition();
    axis.setSpeed(-dir * HOME_SPEED);
    while (digitalRead(homePin) == LOW && abs(axis.currentPosition() - s0) < 500) {
      if (!homeService()) return finish(false);
      axis.runSpeed();
    }
    axis.setSpeed(0);
    if (digitalRead(homePin) == LOW) {
      Serial.print("HOME FAIL initial_switch_not_released "); Serial.println(name);
      return finish(false);
    }
  }
  // 往原点方向走, 直到触发开关 (或超时) —— 用实际步数判断, 不再数循环
  long s1 = axis.currentPosition();
  axis.setSpeed(dir * HOME_SPEED);
  while (digitalRead(homePin) == HIGH && abs(axis.currentPosition() - s1) < HOME_TIMEOUT) {
    if (!homeService()) return finish(false);
    axis.runSpeed();
  }
  axis.setSpeed(0);
  if (abs(axis.currentPosition() - s1) >= HOME_TIMEOUT) {
    Serial.print("HOME FAIL "); Serial.println(name);
    return finish(false);
  }
  recordPowerOnHome(name[0], axis.currentPosition());
  axis.setCurrentPosition(0);
  // 回退 HOME_BACKOFF 步, 松开开关
  long s2 = axis.currentPosition();
  axis.setSpeed(-dir * HOME_SPEED);
  while (abs(axis.currentPosition() - s2) < HOME_BACKOFF) {
    if (!homeService()) return finish(false);
    axis.runSpeed();
  }
  axis.setSpeed(0);
  if (digitalRead(homePin) == LOW) {
    Serial.print("HOME FAIL switch_not_released "); Serial.println(name);
    return finish(false);
  }
  if (!homeService()) return finish(false);
  Serial.print("HOMED "); Serial.println(name);
  return finish(true);
}

// 回零 X (双杆联动): X1+X2 一起走, 看 X1 的开关 (X2 无限位)
bool homeX() {
  float previousMaxX1 = axisX1.maxSpeed();
  float previousMaxX2 = axisX2.maxSpeed();
  auto finish = [&](bool success) -> bool {
    axisX1.setCurrentPosition(axisX1.currentPosition());
    axisX2.setCurrentPosition(axisX2.currentPosition());
    axisX1.setMaxSpeed(previousMaxX1);
    axisX2.setMaxSpeed(previousMaxX2);
    return success;
  };
  axisX1.setMaxSpeed(HOME_SPEED);
  axisX2.setMaxSpeed(HOME_SPEED);
  if (digitalRead(LIM_X1_MIN) == LOW) {
    long s0 = axisX1.currentPosition();
    axisX1.setSpeed(-HOME_DIR_X1 * HOME_SPEED);
    axisX2.setSpeed(-HOME_DIR_X1 * HOME_SPEED);
    while (digitalRead(LIM_X1_MIN) == LOW && abs(axisX1.currentPosition() - s0) < 500) {
      if (!homeService()) return finish(false);
      axisX1.runSpeed();
      axisX2.runSpeed();
    }
    axisX1.setSpeed(0);
    axisX2.setSpeed(0);
    if (digitalRead(LIM_X1_MIN) == LOW) {
      Serial.println("HOME FAIL initial_switch_not_released X");
      return finish(false);
    }
  }
  long s1 = axisX1.currentPosition();
  axisX1.setSpeed(HOME_DIR_X1 * HOME_SPEED);
  axisX2.setSpeed(HOME_DIR_X1 * HOME_SPEED);
  while (digitalRead(LIM_X1_MIN) == HIGH && abs(axisX1.currentPosition() - s1) < HOME_TIMEOUT) {
    if (!homeService()) return finish(false);
    axisX1.runSpeed();
    axisX2.runSpeed();
  }
  axisX1.setSpeed(0);
  axisX2.setSpeed(0);
  if (abs(axisX1.currentPosition() - s1) >= HOME_TIMEOUT) {
    Serial.println("HOME FAIL X");
    return finish(false);
  }
  recordPowerOnHome('X', axisX1.currentPosition());
  axisX1.setCurrentPosition(0);
  axisX2.setCurrentPosition(0);
  long s2 = axisX1.currentPosition();
  axisX1.setSpeed(-HOME_DIR_X1 * HOME_SPEED);
  axisX2.setSpeed(-HOME_DIR_X1 * HOME_SPEED);
  while (abs(axisX1.currentPosition() - s2) < HOME_BACKOFF) {
    if (!homeService()) return finish(false);
    axisX1.runSpeed();
    axisX2.runSpeed();
  }
  axisX1.setSpeed(0);
  axisX2.setSpeed(0);
  // 双杆计数对齐: X2 无独立限位, 回零后强制与 X1 计数一致, 避免绝对移动时累积歪斜
  axisX2.setCurrentPosition(axisX1.currentPosition());
  if (digitalRead(LIM_X1_MIN) == LOW) {
    Serial.println("HOME FAIL switch_not_released X");
    return finish(false);
  }
  if (!homeService()) return finish(false);
  Serial.println("HOMED X");
  return finish(true);
}

// 单轴限位检测: 朝已触发的开关方向走就立即停
bool checkAxisLimit(AccelStepper &axis, int minPin, int maxPin, int homeDir) {
  if (axis.distanceToGo() == 0) return false;
  bool movingHome = (axis.targetPosition() > axis.currentPosition()) == (homeDir > 0);
  int activePin = movingHome ? minPin : maxPin;
  if (digitalRead(activePin) == LOW) {
    lastLimitEvent = String("pin=") + activePin + " ms=" + millis()
      + " pos=" + axis.currentPosition() + " target=" + axis.targetPosition();
    axis.setCurrentPosition(axis.currentPosition());
    Serial.print("LIMIT_STOP "); Serial.println(lastLimitEvent);
    return true;
  }
  return false;
}

// 点动时碰到限位就停 (X2 跟随 X1)
void checkLimitStop() {
  if (checkAxisLimit(axisX1, LIM_X1_MIN, LIM_X1_MAX, HOME_DIR_X1)) {
    axisX2.setCurrentPosition(axisX2.currentPosition());
  }
  checkAxisLimit(axisY, LIM_Y_MIN, LIM_Y_MAX, HOME_DIR_Y);
  if (checkAxisLimit(axisZ, LIM_Z_MIN, LIM_Z_MAX, HOME_DIR_Z)) {
    zContinuousDown = false;
  }
}

void homeAll() {
  Serial.println("Homing: Z -> Y -> X");
  if (!homeSingle(axisZ, LIM_Z_MIN, HOME_DIR_Z, "Z")) return;
  if (!homeSingle(axisY, LIM_Y_MIN, HOME_DIR_Y, "Y")) return;
  if (!homeX()) return;
  homeFaultLatched = false;
  Serial.println("HOME DONE");
  printPowerOnPosition();
}

void recordPowerOnHome(char axis, long netStepsToSwitch) {
  if (!powerOnTrackingIntact) return;
  if (axis == 'X' && !bootPosValidX) {
    homeNetX = netStepsToSwitch;
    bootPosX = -netStepsToSwitch;
    bootPosValidX = true;
  } else if (axis == 'Y' && !bootPosValidY) {
    homeNetY = netStepsToSwitch;
    bootPosY = -netStepsToSwitch;
    bootPosValidY = true;
  } else if (axis == 'Z' && !bootPosValidZ) {
    homeNetZ = netStepsToSwitch;
    bootPosZ = -netStepsToSwitch;
    bootPosValidZ = true;
  }
}

void printPowerOnPosition() {
  Serial.print("BOOTPOS VALID_X="); Serial.print(bootPosValidX ? 1 : 0);
  Serial.print(" X="); Serial.print(bootPosX);
  Serial.print(" VALID_Y="); Serial.print(bootPosValidY ? 1 : 0);
  Serial.print(" Y="); Serial.print(bootPosY);
  Serial.print(" VALID_Z="); Serial.print(bootPosValidZ ? 1 : 0);
  Serial.print(" Z="); Serial.println(bootPosZ);
  Serial.print("HOME_NET_TO_SWITCH X="); Serial.print(homeNetX);
  Serial.print(" Y="); Serial.print(homeNetY);
  Serial.print(" Z="); Serial.print(homeNetZ);
  Serial.print(" TRACKING_INTACT="); Serial.println(powerOnTrackingIntact ? 1 : 0);
}

void printLimitStates() {
  Serial.print("LIM X1min="); Serial.print(digitalRead(LIM_X1_MIN) == LOW ? "HIT" : "---");
  Serial.print(" X1max=");    Serial.print(digitalRead(LIM_X1_MAX) == LOW ? "HIT" : "---");
  Serial.print(" Ymin=");     Serial.print(digitalRead(LIM_Y_MIN)  == LOW ? "HIT" : "---");
  Serial.print(" Ymax=");     Serial.print(digitalRead(LIM_Y_MAX)  == LOW ? "HIT" : "---");
  Serial.print(" Zmin=");     Serial.print(digitalRead(LIM_Z_MIN)  == LOW ? "HIT" : "---");
  Serial.print(" Zmax=");     Serial.println(digitalRead(LIM_Z_MAX) == LOW ? "HIT" : "---");
}

void printPositions() {
  Serial.print("POS X1="); Serial.print(axisX1.currentPosition());
  Serial.print(" X2=");    Serial.print(axisX2.currentPosition());
  Serial.print(" Y=");     Serial.print(axisY.currentPosition());
  Serial.print(" Z=");     Serial.print(axisZ.currentPosition());
  Serial.print(" B=");     Serial.println(axisB.currentPosition());
}

void printPositionsMM() {
  Serial.print("MPOS X="); Serial.print(axisX1.currentPosition() / STEPS_PER_MM_X, 2);
  Serial.print("mm Y=");   Serial.print(axisY.currentPosition() / STEPS_PER_MM_Y, 2);
  Serial.print("mm Z=");   Serial.print(axisZ.currentPosition() / STEPS_PER_MM_Z, 2);
  Serial.print("mm B=");   Serial.print(axisB.currentPosition() / STEPS_PER_DEG_B, 2);
  Serial.println("deg");
}

void printHelp() {
  Serial.println("--- 指令表 ---");
  Serial.println(" X1000 / Y-500 / Z200 / B100   相对移动 N 步 (X 为双轴联动)");
  Serial.println(" G0 X1000 Y2000 Z500 B0        绝对坐标移动-步 (仅更新指定轴)");
  Serial.println(" M0 X50 Y30 Z10 B45            绝对坐标移动-mm/度");
  Serial.println(" ALIGN                         X1/X2 计数对齐 (X2:=X1)");
  Serial.println(" X1 100 / X2 100               单独移动 X1/X2 (需加空格)");
  Serial.println(" B ENABLE / B DISABLE          解锁 / 锁定B轴脉冲(上电默认锁定)");
  Serial.println(" B STATUS                      查询B轴锁定状态");
  Serial.println(" SPEED500 (或 V500)       设置所有轴速度(步/秒)");
  Serial.println(" ACCEL2000                设置所有轴加速度");
  Serial.println(" POS (或 P) / MPOS        查询位置 (步 / mm度)");
  Serial.println(" BOOTPOS (或 BP)          查询上电位置估算及首次回零净步数");
  Serial.println(" ZERO (或 R)              位置计数器清零(不移动)");
  Serial.println(" HX / HY / HZ / HOME      限位回零(单轴/全部)");
  Serial.println(" LIM                      读限位开关状态");
  Serial.print(" DRILL ON / DRILL OFF     钻头以默认");
  Serial.print(DRILL_RUN_DUTY); Serial.println("%占空比启动 / 停止");
  Serial.println(" DRILL PWM 0..100         设置钻头PWM占空比");
  Serial.println(" DRILL STATUS             查询钻头启停、占空比与方向");
  Serial.println(" ZDOWN (或 ZD)            Z轴持续向下，直到E/STOP/下限位");
  Serial.println(" DIAG                     归零/B轴目标及软件脉冲记录(非实测位移)");
  Serial.println(" E (或 STOP)              立即停钻头和全部运动/HOME立即取消");
  Serial.println(" ?                        帮助");
  Serial.println("---------------");
}
