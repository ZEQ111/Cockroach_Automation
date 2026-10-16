# -*- coding: utf-8 -*-
"""
转盘电机（DM-J4310-2EC）第一步测试：验证「使能 / 失能」
目的：确认 Python -> USB-CAN -> 电机 这条链路能正常使能电机

运行前准备：
  1. 关闭达妙上位机，释放 COM11
  2. 确认 COM11 是你设备管理器里那个 USB-CAN 模块
  3. 依赖：pip install pyserial numpy
"""
import serial
import time
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'code' / 'pc'))
from DM_CAN import *

# 串口参数（COM 号改成你设备管理器里看到的）
SERIAL_PORT = 'COM11'
BAUD = 921600


def main():
    ser = serial.Serial(SERIAL_PORT, BAUD, timeout=0.5)

    # 电机对象：类型 DM4310 / SlaveID=0x01（电机 CAN ID）/ MasterID=0x11（主机 ID）
    motor = Motor(DM_Motor_Type.DM4310, 0x01, 0x11)
    ctrl = MotorControl(ser)
    ctrl.addMotor(motor)

    print("使能电机（电机指示灯应变绿）...")
    ctrl.enable(motor)
    # 如果灯没变绿，说明是旧版固件，把上一行换成下面这行再跑：
    # ctrl.enable_old(motor, Control_Type.MIT)
    time.sleep(3)

    print("失能电机（电机指示灯应变红）...")
    ctrl.disable(motor)
    time.sleep(1)

    ser.close()
    print("完成！请确认刚才灯是 红 -> 绿 -> 红 变了一遍")


if __name__ == '__main__':
    main()
