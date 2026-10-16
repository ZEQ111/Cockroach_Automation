import re
import sys
import time
from datetime import datetime
from settings import *

motion_fault = None
vision_reference_ready = False

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("缺少 pyserial, 请先运行: pip install pyserial")
    sys.exit(1)

def log_io(direction, message):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open('a', encoding='utf-8') as stream:
        stream.write(f'{datetime.now().isoformat(timespec="milliseconds")} {direction} {message!r}\n')

def note_response(message):
    global motion_fault
    log_io('RX', message)
    if 'LIMIT_STOP' in message or 'HOME FAIL' in message:
        motion_fault = message

def pick_port():
    all_ports = list(list_ports.comports())
    if not all_ports:
        print("没有检测到串口, 请确认 Arduino 已插上 USB")
        sys.exit(1)
    # 排除蓝牙端口和转盘(USB-CAN)端口，避免误选
    ports = []
    for p in all_ports:
        desc = (p.description or '').lower()
        if p.device.upper() == TURNTABLE_PORT.upper():
            continue                      # 转盘口
        if '蓝牙' in desc or 'bluetooth' in desc:
            continue                      # 蓝牙口
        ports.append(p)
    if not ports:
        ports = all_ports                 # 都过滤完了就全显示

    # 这台设备同时接有 FTDI/RS485 等串口。只有一个明显的 Arduino/CH340 时
    # 直接选它，避免用户误选后“能写入但永远没有 Arduino 回包”。
    likely_arduino = [
        p for p in ports
        if (p.vid, p.pid) in ARDUINO_USB_IDS
        or 'arduino' in (p.description or '').lower()
        or 'ch340' in (p.description or '').lower()
    ]
    if len(likely_arduino) == 1:
        selected = likely_arduino[0]
        print(f"自动识别 Arduino: {selected.device} - {selected.description}")
        return selected.device

    print("检测到串口(已排除蓝牙和转盘口):")
    for i, p in enumerate(ports):
        print(f"  [{i}] {p.device} - {p.description}")
    while True:
        sel = input(f"选择 Arduino 串口序号 [0-{len(ports)-1}]: ").strip()
        if sel.isdigit() and 0 <= int(sel) < len(ports):
            return ports[int(sel)].device
        print("输入无效")

def open_verified_arduino(port):
    """连接并验证 Arduino 固件；启动时先停机，绝不静默连接其他串口设备。"""
    try:
        ser = serial.Serial(port, BAUD, timeout=1)
    except serial.SerialException as exc:
        raise RuntimeError(
            f'无法打开 {port}；请先关闭 Arduino IDE 串口监视器或其他占用该端口的程序'
        ) from exc
    time.sleep(2)              # 打开串口会让 Mega2560 复位
    startup = ser.read(ser.in_waiting).decode('utf-8', 'replace')
    if startup:
        sys.stdout.write(startup)

    if 'READY' not in startup:
        send(ser, 'DIAG')
        probe = read_response(ser, timeout=0.8)
        if probe:
            print(probe, end='')
        if 'HOMING=' not in probe or 'TARGET X=' not in probe:
            ser.close()
            raise RuntimeError(
                f'{port} 没有返回 Arduino 运动固件握手；请关闭串口监视器并确认选择 CH340 端口'
            )

    if EXPECTED_FIRMWARE_ID not in startup:
        ser.close()
        raise RuntimeError(
            f'{port} 上不是本次 ZDOWN/PWM 固件（缺少 {EXPECTED_FIRMWARE_ID}）'
        )

    # Arduino 可能一直由 USB 供电，连接时强制清掉串口监视器留下的运动/PWM状态。
    send(ser, 'E')
    stopped = read_response(ser, markers=('STOP E',), timeout=1.0)
    if stopped:
        print(stopped, end='')
    if 'STOP E' not in stopped:
        ser.close()
        raise RuntimeError(f'{port} 未确认安全停止，拒绝进入控制界面')
    return ser

def send(ser, line):
    log_io('TX', line)
    ser.write((line + "\n").encode('utf-8'))
    ser.flush()

def normalize_motion_command(line):
    """把上位机的简写转成固件协议，并在发送前校验钻头占空比。"""
    up = line.strip().upper()
    tokens = up.split()
    duty_text = None
    if len(tokens) == 2 and tokens[0] == 'PWM':
        duty_text = tokens[1]
    elif len(tokens) == 3 and tokens[:2] == ['DRILL', 'PWM']:
        duty_text = tokens[2]
    elif (len(tokens) == 2 and tokens[0] == 'DRILL'
          and tokens[1] not in ('ON', 'OFF', 'STATUS')):
        duty_text = tokens[1]

    if duty_text is not None:
        try:
            duty = int(duty_text)
        except ValueError as exc:
            raise ValueError('钻头占空比必须是 0 到 100 的整数') from exc
        if not 0 <= duty <= 100:
            raise ValueError('钻头占空比必须在 0 到 100 之间')
        return f'DRILL PWM {duty}'
    return line.strip()

def read_response(ser, markers=None, timeout=2.0):
    """读串口; 给 markers 就等到出现任一标记, 否则读一小段时间"""
    out = ""
    t0 = time.time()
    while time.time() - t0 < timeout:
        n = ser.in_waiting
        if n:
            out += ser.read(n).decode('utf-8', 'replace')
            note_response(out)
            if markers and any(m in out for m in markers):
                break
            t0 = time.time()   # 有数据就刷新等待
        else:
            time.sleep(0.01)
    return out

POS_RE = re.compile(r"POS X1=(-?\d+) X2=(-?\d+) Y=(-?\d+) Z=(-?\d+) B=(-?\d+)")

def query_positions(ser, timeout=1.0):
    """查询实时步数坐标；返回 X/X2/Y/Z/B 字典，超时返回 None。"""
    send(ser, "POS")
    out = ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        n = ser.in_waiting
        if n:
            out += ser.read(n).decode('utf-8', 'replace')
            note_response(out)
            match = POS_RE.search(out)
            if match:
                x1, x2, y, z, b = map(int, match.groups())
                return {'X': x1, 'X2': x2, 'Y': y, 'Z': z, 'B': b}
        else:
            time.sleep(0.01)
    return None

def wait_positions(ser, targets, timeout=75.0):
    """等待一个或多个轴到达目标位置。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        pos = query_positions(ser)
        if motion_fault:
            print('运动故障:', motion_fault)
            return False
        if pos is not None:
            reached = all(pos[axis] == target for axis, target in targets.items())
            if 'X' in targets:
                reached = reached and pos['X2'] == targets['X']
            if reached:
                return True
        time.sleep(0.05)
    return False

def move_continuous(ser, targets):
    """使用一次 G0 连续移动到给定的绝对步数坐标。"""
    global motion_fault
    motion_fault = None
    cmd = "G0 " + " ".join(f"{axis}{target}" for axis, target in targets.items())
    send(ser, cmd)
    out = read_response(ser, timeout=0.3)
    if out:
        print(out, end="")
    if wait_positions(ser, targets):
        return True
    send(ser, "DIAG")
    print(read_response(ser, timeout=0.2), end='')
    send(ser, "STOP")
    print(read_response(ser, timeout=0.2), end='')
    print(f"\n未能到达 {targets}，已停止 VISION")
    return False

def goto_vision_position(ser):
    """HOME 后移动到固定视觉识别机位并停下；不采图、不识别、不移动目标。"""
    global vision_reference_ready
    vision_reference_ready = False
    print("VISION: 开始归零（Z -> Y -> X）")
    send(ser, "HOME")
    out = read_response(ser, markers=("HOME DONE", "HOME FAIL"), timeout=90)
    print(out, end="")
    if "HOME DONE" not in out or "HOME FAIL" in out:
        print("VISION: 归零失败，未执行后续移动")
        return False
    vision_reference_ready = True

    print("VISION: 连续移动 X/Y，然后连续下降 Z")
    xy_target = {axis: VISION_POS_STEPS[axis] for axis in ('X', 'Y')}
    if not move_continuous(ser, xy_target):
        vision_reference_ready = False
        return False
    if not move_continuous(ser, {'Z': VISION_POS_STEPS['Z']}):
        vision_reference_ready = False
        return False
    pos = query_positions(ser)
    if pos is None or any(
        pos[axis] != VISION_POS_STEPS[axis] for axis in ('X', 'Y', 'Z')
    ):
        vision_reference_ready = False
        print(f'VISION: 未能确认已到达视觉位：{pos}')
        return False
    print(f"VISION READY: {pos}；已停在视觉位，等待下一条命令")
    return True

def goto_vision_position_without_home(ser):
    """在本次运行已确认机械零点时，跳过 HOME 并安全返回视觉位。"""
    global vision_reference_ready
    if not vision_reference_ready:
        print('VIS_FAST: 本次程序运行尚未确认机械零点，请先执行一次 VIS 或 HOME')
        return False

    pos = query_positions(ser)
    if pos is None:
        print('VIS_FAST: 无法读取当前 POS，拒绝移动')
        return False
    if motion_fault:
        print(f'VIS_FAST: 当前存在运动故障，拒绝移动：{motion_fault}')
        return False
    if pos['X'] != pos['X2']:
        print(f'VIS_FAST: X1/X2 位置不一致，拒绝移动：{pos}')
        return False

    expected = VISION_POS_STEPS
    if all(pos[axis] == expected[axis] for axis in ('X', 'Y', 'Z')):
        print(f'VIS_FAST READY: {pos}；当前已在视觉位，直接开始识别')
        return True

    print('VIS_FAST: 跳过归零，先提升 Z，再移动 X/Y，最后下降到视觉高度')
    if pos['Z'] != 20 and not move_continuous(ser, {'Z': 20}):
        vision_reference_ready = False
        return False
    xy_target = {axis: expected[axis] for axis in ('X', 'Y')}
    if pos['X'] != expected['X'] or pos['Y'] != expected['Y']:
        if not move_continuous(ser, xy_target):
            vision_reference_ready = False
            return False
    if not move_continuous(ser, {'Z': expected['Z']}):
        vision_reference_ready = False
        return False

    final_pos = query_positions(ser)
    if final_pos is None or any(
        final_pos[axis] != expected[axis] for axis in ('X', 'Y', 'Z')
    ):
        vision_reference_ready = False
        print(f'VIS_FAST: 未能到达视觉位，拒绝识别：{final_pos}')
        return False
    print(f'VIS_FAST READY: {final_pos}；已跳过归零并到达视觉位')
    return True
