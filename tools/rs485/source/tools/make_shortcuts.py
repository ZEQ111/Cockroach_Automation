# -*- coding: utf-8 -*-
"""
在桌面创建两个快捷方式（不需要管理员权限）：
    RS485 上位机  -> 直接开图形界面上位机
    RS485 工具箱  -> 按钮式启动器（演示 / 自检 / 采集器 / 说明）

用法：.venv\\Scripts\\python.exe tools\\make_shortcuts.py

实现说明：PowerShell 命令里不放中文（编码坑），中文路径通过环境变量传进去。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYW = ROOT / '.venv' / 'Scripts' / 'pythonw.exe'

SHORTCUTS = [
    ('RS485 上位机.lnk', str(ROOT / '上位机_GUI.py'),
     'RS485 测力上位机：实时曲线 + CSV + 串口助手'),
    ('RS485 工具箱.lnk', str(ROOT / 'tools' / 'launcher.py'),
     'RS485 工具箱：演示模式 / 协议自检 / 采集器 / 使用说明'),
]

PS = (
    "$ws = New-Object -ComObject WScript.Shell; "
    "$desk = [Environment]::GetFolderPath('Desktop'); "
    "$lnk = $ws.CreateShortcut((Join-Path $desk $env:LNK_NAME)); "
    "$lnk.TargetPath = $env:LNK_TARGET; "
    "$lnk.Arguments = $env:LNK_ARGS; "
    "$lnk.WorkingDirectory = $env:LNK_WORKDIR; "
    "$lnk.Description = $env:LNK_DESC; "
    "$lnk.IconLocation = $env:LNK_TARGET + ',0'; "
    "$lnk.Save(); "
    "Write-Output (Join-Path $desk $env:LNK_NAME)"
)


def make(name: str, args: str, desc: str) -> bool:
    env = os.environ.copy()
    env['LNK_NAME'] = name
    env['LNK_TARGET'] = str(PYW)
    env['LNK_ARGS'] = f'"{args}"'
    env['LNK_WORKDIR'] = str(ROOT)
    env['LNK_DESC'] = desc
    # PowerShell 的输出是系统 ANSI(GBK) 编码，显式指定，避免 UTF-8 解码报错
    r = subprocess.run(['powershell', '-NoProfile', '-Command', PS],
                       env=env, capture_output=True, text=True,
                       encoding='gbk', errors='replace')
    if r.returncode != 0:
        print(f'  [FAIL] {name}: {(r.stderr or "").strip()}')
        return False
    print(f'  [OK] {(r.stdout or "").strip()}')
    return True


def main() -> int:
    if not PYW.exists():
        print(f'找不到 {PYW}，先在项目里建好 .venv')
        return 1
    print('正在创建桌面快捷方式...')
    ok = all(make(name, args, desc) for name, args, desc in SHORTCUTS)
    print('完成' if ok else '有失败项')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
