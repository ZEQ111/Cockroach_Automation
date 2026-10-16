# -*- coding: utf-8 -*-
"""
转盘电机 第三步测试：转到 3 个工具位（0° / 120° / 240°）
运行前：
  1. 关闭达妙上位机，释放 COM11
  2. 失能（红灯）状态下，用手把转盘转到「工具1」位置（= 0°）
"""
import serial
import time
import math
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'code' / 'pc'))
from DM_CAN import *

SERIAL_PORT = 'COM11'
BAUD = 921600

KP = 20            # 位置刚度
KD = 1.0           # 速度阻尼
SPEED_RAD_S = 0.6  # 转盘角速度（弧度/秒），觉得慢就调大

TOOL_DEG = [0, 120, 240]               # 三个工具头角度（逆时针为正）
TOOL_RAD = [math.radians(d) for d in TOOL_DEG]


def ramp_move(ctrl, motor, start, end):
    """从 start 平滑转到 end，角速度恒定"""
    dist = abs(end - start)
    duration = dist / SPEED_RAD_S
    steps = max(int(duration / 0.01), 1)
    for i in range(steps + 1):
        q = start + (end - start) * i / steps
        ctrl.controlMIT(motor, KP, KD, q, 0, 0)
        time.sleep(0.01)


def main():
    ser = serial.Serial(SERIAL_PORT, BAUD, timeout=0.5)
    motor = Motor(DM_Motor_Type.DM4310, 0x01, 0x11)
    ctrl = MotorControl(ser)
    ctrl.addMotor(motor)

    # 失能状态下设零点（此时转盘应在「工具1」位置）
    ctrl.set_zero_position(motor)
    time.sleep(0.5)

    ctrl.enable(motor)
    time.sleep(1)

    # 依次：0° -> 120° -> 240° -> 0°
    current = 0.0
    for target in TOOL_RAD + [0.0]:
        print(f"转到 {math.degrees(target):.0f}° ...")
        ramp_move(ctrl, motor, current, target)
        time.sleep(1.2)   # 到位停 1.2 秒，方便你确认
        current = target

    ctrl.disable(motor)
    ser.close()
    print("完成")


if __name__ == '__main__':
    main()
