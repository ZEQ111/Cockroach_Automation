# -*- coding: utf-8 -*-
"""
转盘电机 第二步测试：小幅度转动，验证「运动 + 方向」
运行前：
  1. 关闭达妙上位机，释放 COM11
  2. 电机失能（红灯）状态下，用手把转盘转到「工具1」位置（当作 0°）
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

KP = 20    # 位置刚度（越大越"硬"，太大会抖，先别乱改）
KD = 1.0   # 速度阻尼


def ramp_move(ctrl, motor, start, end, duration):
    """从 start 平滑插值到 end，时长 duration 秒，避免突然猛转"""
    steps = int(duration / 0.01)  # 每 10ms 发一帧
    for i in range(steps + 1):
        q = start + (end - start) * i / steps
        ctrl.controlMIT(motor, KP, KD, q, 0, 0)
        time.sleep(0.01)


def main():
    ser = serial.Serial(SERIAL_PORT, BAUD, timeout=0.5)
    motor = Motor(DM_Motor_Type.DM4310, 0x01, 0x11)
    ctrl = MotorControl(ser)
    ctrl.addMotor(motor)

    # ① 失能状态下，把当前（工具1）位置设为零点
    print("把当前位置设为零点 ...")
    ctrl.set_zero_position(motor)
    time.sleep(0.5)

    # ② 使能
    print("使能 ...")
    ctrl.enable(motor)
    time.sleep(1)

    # ③ 正方向平滑转 0.3 rad（约 17°）—— 请盯着转盘看往哪边转！
    print("正方向转 0.3 rad（约 17°）...")
    ramp_move(ctrl, motor, 0.0, 0.3, 1.0)
    time.sleep(1.5)

    # ④ 转回 0
    print("转回 0 ...")
    ramp_move(ctrl, motor, 0.3, 0.0, 1.0)
    time.sleep(1.0)

    # ⑤ 失能
    ctrl.disable(motor)
    ser.close()
    print("完成")


if __name__ == '__main__':
    main()
