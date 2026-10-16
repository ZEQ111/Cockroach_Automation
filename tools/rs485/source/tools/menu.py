# -*- coding: utf-8 -*-
"""
启动菜单 —— 被「运行.bat」调用。

为什么菜单写在 Python 里而不是 bat 里？
  中文写进 .bat 后，cmd.exe 按系统 ANSI 代码页(GBK)解析文件，UTF-8 的中文
  会被拆坏导致整行命令执行失败（踩过坑）。bat 里只留 ASCII，中文交给 Python。

菜单会循环：跑完一个程序自动回到菜单，选 0 退出。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / '.venv' / 'Scripts' / 'python.exe'

ITEMS = {
    '1': ('图形界面上位机        推荐：实时曲线 + CSV + 串口助手',
          [str(PY), str(ROOT / '上位机_GUI.py')]),
    '2': ('图形界面演示模式      无硬件，自动起模拟从站看效果',
          [str(PY), str(ROOT / '上位机_GUI.py'), '--demo']),
    '3': ('命令行采集 pymodbus   带 CSV 记录和断线重连',
          [str(PY), str(ROOT / '上位机_pymodbus.py')]),
    '4': ('命令行采集 main.py    原仓库修复版，逻辑简单',
          [str(PY), str(ROOT / 'main.py')]),
    '5': ('检查串口环境          确认 COM 口和依赖是否正常',
          [str(PY), str(ROOT / '检查环境.py')]),
    '6': ('无界面采集器          模拟数据，输出 force_filter 兼容 CSV',
          [str(PY), str(ROOT / 'tools' / 'collect_demo.py')]),
    '7': ('协议自检              CRC / 组帧 / 收发 全套自动化测试',
          [str(PY), str(ROOT / 'tools' / 'loopback_test.py')]),
    '8': ('无界面采集器(真设备)  交互输入 COM 口等参数，长时采集',
          None),  # 特殊处理：要问参数
}


def show_menu() -> None:
    print('=' * 58)
    print('   RS485 称重/测力上位机')
    print('=' * 58)
    for key, (desc, _) in ITEMS.items():
        print(f'   [{key}] {desc}')
    print('   [0] 退出')
    print('=' * 58)
    print('   无硬件先选 2 看演示；连真设备选 1')
    print('=' * 58)


def ask(prompt: str, default: str) -> str:
    text = input(f'{prompt} [{default}]: ').strip()
    return text or default


def collect_real() -> int:
    print()
    print('--- 无界面采集器（真设备） ---')
    com = input('COM 口（如 COM3，直接回车取消）: ').strip()
    if not com:
        print('已取消')
        return 0
    baud = ask('波特率', '9600')
    unit = ask('从站地址', '1')
    addr = ask('寄存器地址', '0')
    scale = ask('换算比例（力 = 原始值 / 比例）', '100')
    hz = ask('采样率 Hz', '20')
    seconds = ask('采集时长（秒）', '60')
    out = ask('输出文件', 'demo_data/real_capture.csv')
    cmd = [str(PY), str(ROOT / 'tools' / 'collect_demo.py'),
           '--com', com, '--baud', baud, '--unit', unit, '--addr', addr,
           '--scale', scale, '--hz', hz, '--seconds', seconds, '--out', out]
    print()
    try:
        return subprocess.call(cmd, cwd=str(ROOT))
    except OSError as e:
        print(f'启动失败: {e}')
        return 1


def main() -> int:
    while True:
        show_menu()
        try:
            choice = input('请输入 1-8 然后回车（0=退出）: ').strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if choice in ('0', 'q', 'Q'):
            print('再见')
            return 0

        if choice == '8':
            collect_real()
        elif choice in ITEMS:
            _desc, cmd = ITEMS[choice]
            print()
            try:
                subprocess.call(cmd, cwd=str(ROOT))
            except OSError as e:
                print(f'启动失败: {e}')
        else:
            print('\n  输入不对，请重新选\n')
            continue

        print()
        print('-' * 58)
        print('  已返回菜单（选 0 退出）')
        print('-' * 58)
        print()


if __name__ == '__main__':
    sys.exit(main())
