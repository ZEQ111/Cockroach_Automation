# -*- coding: utf-8 -*-
"""
无界面采集器 —— 长时间采集用（比 GUI 省资源），CSV 格式和上位机 GUI 完全一致。

两种用法：
  1) 无硬件演示（默认）：自动起模拟从站，采一段穿刺曲线
       .venv\\Scripts\\python.exe tools\\collect_demo.py --seconds 16 --hz 20

  2) 连真设备：指定 --com 就不起模拟从站了
       .venv\\Scripts\\python.exe tools\\collect_demo.py --com COM3 --baud 9600 ^
           --unit 1 --addr 0 --scale 100 --seconds 60

产出的 CSV 可以直接丢给力传感器项目的 force_filter.py 做 FDSS/卡尔曼分析。
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

import modbus_link as mb  # noqa: E402
from mock_slave import MockSlave  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description='无界面 Modbus 采集器')
    ap.add_argument('--com', default=None, help='真设备串口，如 COM3（不填则用模拟从站）')
    ap.add_argument('--baud', type=int, default=9600)
    ap.add_argument('--unit', type=int, default=1, help='从站地址')
    ap.add_argument('--addr', type=int, default=0, help='寄存器地址')
    ap.add_argument('--count', type=int, default=1, help='寄存器数量（32位数据填2）')
    ap.add_argument('--scale', type=float, default=100.0, help='换算比例：力 = 原始值 / 比例')
    ap.add_argument('--unit-name', default='N', help='物理单位（写进CSV表头）')
    ap.add_argument('--hz', type=float, default=20.0, help='采样率（默认20Hz）')
    ap.add_argument('--seconds', type=float, default=16.0, help='采集时长（秒）')
    ap.add_argument('--host', default='127.0.0.1', help='模拟从站地址（--com 为空时有效）')
    ap.add_argument('--port', type=int, default=5022, help='模拟从站端口')
    ap.add_argument('--signal', choices=['puncture', 'sine', 'static'], default='puncture')
    ap.add_argument('--out', default=None, help='输出CSV路径（默认 demo_data/force_demo.csv）')
    args = ap.parse_args()

    if args.out is None:
        out = ROOT / 'demo_data' / 'force_demo.csv'
    else:
        out = Path(args.out)
        if not out.is_absolute():
            out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)

    slave = None
    if args.com:
        link = mb.ModbusLink(mode='serial', port=args.com, baudrate=args.baud,
                             unit=args.unit, timeout=0.3)
        print(f'[采集] 真设备模式 {args.com} @ {args.baud}，从站 {args.unit}，寄存器 {args.addr}')
    else:
        slave = MockSlave(host=args.host, port=args.port, unit=args.unit,
                          signal=args.signal, verbose=False).start()
        link = mb.ModbusLink(mode='tcp', host=args.host, tcp_port=args.port,
                             unit=args.unit, timeout=0.3)
        print(f'[采集] 模拟模式 {args.host}:{args.port}，信号 {args.signal}')

    interval = 1.0 / args.hz
    n = 0
    t0 = time.perf_counter()

    try:
        link.open()
        with open(out, 'w', newline='', encoding='utf-8-sig') as f:
            f.write(f'datetime,time_s,force_{args.unit_name},raw\n')
            next_t = t0
            while True:
                now = time.perf_counter()
                if now - t0 >= args.seconds:
                    break
                if now < next_t:
                    time.sleep(min(next_t - now, 0.05))
                    continue
                next_t += interval
                try:
                    regs = link.read_registers(args.addr, args.count, 3)
                    raw = regs[0] if len(regs) == 1 else (regs[0] << 16) | regs[1]
                    value = raw / args.scale
                except mb.LinkError as e:
                    print(f'  [警告] 读取失败: {e}')
                    continue
                ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                t = time.perf_counter() - t0
                f.write(f'{ts},{t:.3f},{value:.4f},{raw}\n')
                n += 1
                if n % max(1, int(args.hz * 2)) == 0:
                    print(f'  {t:6.1f}s  采样 {n} 点  当前 {value:.3f} {args.unit_name}')
    except mb.LinkError as e:
        print(f'[错误] {e}')
        return 1
    finally:
        link.close()
        if slave is not None:
            slave.stop()

    span = time.perf_counter() - t0
    print(f'[完成] {n} 点 / {span:.1f}s（实际 {n / span:.1f} Hz）-> {out}')
    print(f'        分析: .venv\\Scripts\\python.exe tools\\analyze_force.py "{out}"')
    return 0


if __name__ == '__main__':
    sys.exit(main())
