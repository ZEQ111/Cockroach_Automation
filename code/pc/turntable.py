import math
import threading
import time
import serial
from settings import *

class Turntable:
    """转盘电机控制（DM-J4310-2EC, USB-CAN, MIT 位置模式）

    注意：MIT 模式下电机不主动保持位置，必须持续发 controlMIT 帧才能
    "锁住"位置。这里开一个后台线程以 100Hz 持续下发当前目标角度，
    这样使能后电机会锁死（手转不动），转到某工具后也一直停在原位。
    """
    TOOL_RAD = [0.0, math.radians(120), math.radians(240)]   # 工具1/2/3 的绝对角度

    def __init__(self, port, baud):
        from DM_CAN import Motor, MotorControl, DM_Motor_Type
        self.ser = serial.Serial(port, baud, timeout=0.5)
        self.motor = Motor(DM_Motor_Type.DM4310, 0x01, 0x11)
        self.ctrl = MotorControl(self.ser)
        self.ctrl.addMotor(self.motor)
        self.angle = 0.0      # 当前绝对角度(rad)，连续值
        self.tool = 1         # 当前工具号 1/2/3
        self.enabled = False
        self.zeroed = False   # 是否已设过零点(TZ)
        self._target = 0.0    # 目标角度，后台线程持续下发这个值
        self._stop = threading.Event()
        self._thread = None
        self.hold_fault = None

    # ---- 后台保持线程：持续发 controlMIT 锁住位置 ----
    def _hold_loop(self):
        try:
            while not self._stop.is_set():
                self.ctrl.controlMIT(self.motor, TURNTABLE_KP, TURNTABLE_KD, self._target, 0, 0)
                stamp = self.motor.feedback_time
                if stamp is None or time.monotonic() - stamp > 1.0:
                    raise RuntimeError('转盘反馈超时')
                self._stop.wait(0.01)   # 100Hz
        except Exception as exc:
            self.hold_fault = str(exc)
            self.enabled = False
            self._stop.set()
            print('转盘保持故障，禁止继续下降:', exc)

    def _start_hold(self):
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._hold_loop, daemon=True)
            self._thread.start()

    def _stop_hold(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def read_actual_angle(self, timeout=2.0):
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError('保持线程运行时不能并发查询串口')
        self.ser.reset_input_buffer()
        self.ctrl.data_save = b''
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            self.ctrl.refresh_motor_status(self.motor)
            time.sleep(0.05)
            self.ctrl.recv()
            stamp = self.motor.feedback_time
            if stamp is not None and stamp >= started:
                angle = self.motor.getPosition()
                velocity = self.motor.getVelocity()
                if not math.isfinite(angle) or not -12.5 < angle < 12.5:
                    raise RuntimeError('转盘角度反馈无效')
                if not math.isfinite(velocity) or abs(velocity) > 0.1:
                    raise RuntimeError('转盘尚未静止')
                return angle
        raise RuntimeError('没有收到新鲜转盘反馈，拒绝按默认零角度使能')

    def enable(self):
        if self.enabled:
            return
        self._stop_hold()
        self.hold_fault = None
        angle = self.read_actual_angle()
        self.angle = self._target = angle
        try:
            # 先清除可能残留的运动设定，再使能并锁住实测角度。
            self.ctrl.controlMIT(self.motor, 0, 0, angle, 0, 0)
            self.ctrl.enable(self.motor)
            self.ctrl.controlMIT(self.motor, TURNTABLE_KP, TURNTABLE_KD, angle, 0, 0)
            self.enabled = True
            self._start_hold()
            time.sleep(0.3)
            if not self.enabled or self.hold_fault:
                raise RuntimeError(self.hold_fault or '转盘保持失败')
        except Exception:
            self.disable()
            raise
        print(f'转盘已使能，保持实测角度 {math.degrees(angle):.2f}°；工具编号需独立标定')

    def disable(self):
        self._stop_hold()
        self.ctrl.disable(self.motor)
        self.enabled = False
        print("转盘已失能（红灯）")

    def set_zero(self):
        if self.enabled:                # 设零点应在失能态，先停
            self.disable()
        self.ctrl.set_zero_position(self.motor)
        self.angle = 0.0
        self.tool = 1
        self._target = 0.0
        self.zeroed = True
        print("已设零点：当前位置 = 工具1 = 0°（请再 TE 使能）")

    def goto_tool(self, n):
        if not self.zeroed:
            print('工具零点尚未建立，禁止自动换头；当前角度保持不受影响')
            return
        if not self.enabled:
            print("转盘未使能，请先输入 TE")
            return
        n = int(n)
        if n not in (1, 2, 3):
            print("工具号只能是 1 / 2 / 3")
            return
        target0 = self.TOOL_RAD[n - 1]
        # 选离当前角度最近的目标表示，保证每次转 <= 180°（工具间总是 ±120°）
        k = round((self.angle - target0) / (2 * math.pi))
        target = target0 + k * 2 * math.pi
        delta = target - self.angle
        print(f"转盘 -> 工具{n}（转 {math.degrees(delta):+.0f}°）")
        self._ramp_target(self.angle, target)
        self.angle = target
        self.tool = n
        time.sleep(0.3)

    def next_tool(self):
        self.goto_tool(self.tool % 3 + 1)          # 1 -> 2 -> 3 -> 1

    def prev_tool(self):
        self.goto_tool((self.tool - 2) % 3 + 1)    # 1 -> 3 -> 2 -> 1

    def _ramp_target(self, start, end):
        """平滑更新目标角度（后台线程负责实际发送），角速度恒定"""
        delta = end - start
        duration = abs(delta) / TURNTABLE_SPEED
        steps = max(int(duration / 0.01), 1)
        for i in range(steps + 1):
            self._target = start + delta * i / steps
            time.sleep(0.01)
        self._target = end

    def close(self):
        try:
            self.disable()
            self.ser.close()
        except Exception:
            pass

def handle_turntable(tt, line):
    """处理转盘指令，返回 True 表示已识别"""
    up = line.upper()
    if up == 'TE':
        tt.enable()
    elif up == 'TD':
        tt.disable()
    elif up == 'TZ':
        tt.set_zero()
    elif up == 'TN':
        tt.next_tool()
    elif up == 'TP':
        tt.prev_tool()
    elif up in ('T1', 'T2', 'T3'):
        tt.goto_tool(int(up[1]))
    else:
        return False
    return True
