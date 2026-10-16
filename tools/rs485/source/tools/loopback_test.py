# -*- coding: utf-8 -*-
"""
无硬件链路自检 —— 自动起一个模拟从站当"假变送器"，验证整套协议代码。

一条命令搞定：
    .venv\\Scripts\\python.exe tools\\loopback_test.py

测试内容：
    [1] CRC16 自查：对照手册里的 6 条预设帧（CRC 已知，错一位都能看出来）
    [2] 交叉验证：和 pymodbus / minimalmodbus 库的 CRC 实现逐字节比对
    [3] 实链路：走 ModbusLink(TCP) 读模拟从站的寄存器，验证组帧/收帧/解析

加 --external 参数则不改用内置模拟从站，而是连外部从站（如 pymodbus 模拟器
或 tools/mock_slave.py 常驻进程）：
    .venv\\Scripts\\python.exe tools\\loopback_test.py --external --port 5020
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

import modbus_link as mb

HOST = '127.0.0.1'
PORT = 5023

# 接线与调试指南里的预设帧（左：帧，右：说明），CRC 是手册标准值
KNOWN_FRAMES = {
    '01 03 00 00 00 01 84 0A': '从站1 读保持寄存器0 x1',
    '01 03 00 00 00 02 C4 0B': '从站1 读保持寄存器0 x2',
    '01 03 00 11 00 01 D4 0F': '从站1 读寄存器17',
    '01 04 00 00 00 01 31 CA': '从站1 读输入寄存器0',
    '6F 03 00 11 00 01 DC 81': '从站111 读寄存器17',
    '6F 03 00 13 00 01 7D 41': '从站111 读寄存器19',
}

ok_count = 0
fail_count = 0


def check(name, cond, detail=''):
    global ok_count, fail_count
    if cond:
        ok_count += 1
        print(f'  [OK] {name} {detail}')
    else:
        fail_count += 1
        print(f'  [FAIL] {name} {detail}')


print('=' * 60)
print('  [1] CRC16 对照已知帧')
print('=' * 60)
for frame_hex, desc in KNOWN_FRAMES.items():
    data = bytes.fromhex(frame_hex.replace(' ', ''))
    body, crc = data[:-2], data[-2:]
    calc = mb.crc16(body)
    check(desc, calc == crc, f'算出 {calc.hex(" ").upper()} / 手册 {crc.hex(" ").upper()}')

print()
print('=' * 60)
print('  [2] 与 pymodbus / minimalmodbus 交叉验证 CRC')
print('=' * 60)

import random
random.seed(42)
PAYLOADS = [bytes(random.randrange(256) for _ in range(random.randint(1, 30))) for _ in range(200)]

try:
    from pymodbus.framer.rtu import FramerRTU

    mismatch = 0
    for payload in PAYLOADS:
        mine = mb.crc16(payload)
        val = FramerRTU.compute_CRC(payload)
        # pymodbus 返回的数值已经是"总线顺序"（高位在前），直接按大端转字节
        if mine != val.to_bytes(2, 'big'):
            mismatch += 1
    check('pymodbus 200 组随机数据 CRC 全一致', mismatch == 0, f'不一致 {mismatch} 组')
except Exception as e:
    print(f'  [SKIP] pymodbus CRC 不可用: {e}')

try:
    import minimalmodbus as mm

    mismatch = 0
    for payload in PAYLOADS:
        if mb.crc16(payload) != mm._calculate_crc(payload):
            mismatch += 1
    check('minimalmodbus 200 组随机数据 CRC 全一致', mismatch == 0, f'不一致 {mismatch} 组')
except Exception as e:
    print(f'  [SKIP] minimalmodbus CRC 不可用: {e}')

print()
print('=' * 60)
print('  [3] 实链路：ModbusLink(TCP) 读模拟从站')
print('=' * 60)

external = '--external' in sys.argv
if '--port' in sys.argv:
    PORT = int(sys.argv[sys.argv.index('--port') + 1])

slave = None
if not external:
    from mock_slave import MockSlave
    slave = MockSlave(host=HOST, port=PORT, unit=1, signal='puncture', verbose=False).start()
    print(f'  (已自动启动内置模拟从站 {HOST}:{PORT})')
else:
    print(f'  (使用外部从站 {HOST}:{PORT})')

link = mb.ModbusLink(mode='tcp', host=HOST, tcp_port=PORT, unit=1, timeout=0.5,
                     on_tx=lambda d, tag: print(f'  TX -> {mb.hex_frame(d)}'),
                     on_rx=lambda d, tag, ms: print(f'  RX <- {mb.hex_frame(d)}  ({ms:.0f}ms)'))
try:
    link.open()
    print(f'  已连接 {link.describe()}')
    import time as _time
    vals = []
    for i in range(5):
        regs = link.read_registers(0, 1)
        vals.append(regs[0])
        print(f'  第 {i + 1} 次读寄存器0 = {regs[0]}')
        _time.sleep(0.05)
    check('连读 5 次有数据', all(v is not None for v in vals))
    check('模拟信号在变化', len(set(vals)) > 1, f'读到 {vals}')

    regs17 = link.read_registers(17, 1)
    check('读寄存器17 = 12345', regs17 == [12345], f'实际 {regs17}')

    # 故意读一个从站不存在的地址（寄存器表只有 0~63），看异常处理
    try:
        link.read_registers(64, 1)
        check('越界地址应报异常', False, '居然没报错')
    except mb.LinkError as e:
        check('越界地址正确报异常', True, f'-> {e}')

    # 原始帧手工发（模拟串口助手的用法）
    resp = link.send_raw(mb.build_mbap(mb.build_read_pdu(3, 19, 1), 99, 1), tag='manual')
    check('手工发原始帧有回包', len(resp) > 0, f'{len(resp)} 字节')
finally:
    link.close()
    if slave is not None:
        slave.stop()

print()
print('=' * 60)
print(f'  结果: {ok_count} 项通过 / {fail_count} 项失败')
print('=' * 60)
sys.exit(1 if fail_count else 0)
