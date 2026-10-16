# -*- coding: utf-8 -*-
"""
一键打包脚本 —— 生成 RS485上位机.exe 与 RS485工具箱.exe，并整理发布包。

用法：.venv\\Scripts\\python.exe tools\\build_exe.py
产物：
  dist\\RS485上位机.exe        上位机主程序（演示模式 --demo 可用）
  dist\\RS485工具箱.exe        按钮式入口（演示 / 接线说明 / 使用说明 / 串口检查）
  dist\\RS485发布包\\           两个 exe + 指南 + 使用说明.txt
  dist\\RS485发布包.zip        发布包压缩文件

脚本幂等，重复运行会覆盖旧产物（--clean 每次重建）。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = ROOT / '.venv' / 'Scripts' / 'python.exe'
DIST = ROOT / 'dist'
RELEASE = DIST / 'RS485发布包'
ZIP_PATH = DIST / 'RS485发布包.zip'
GUIDE_NAME = '接线与调试指南.md'

GUI_BUILD = [
    '--noconfirm', '--clean', '--onefile', '--windowed',
    '--name', 'RS485上位机',
    '--add-data', f'{GUIDE_NAME};.',
    '--add-data', 'tools\\mock_slave.py;tools',
    '--hidden-import', 'wiring_info',
    '--hidden-import', 'serial.tools.list_ports',
    '上位机_GUI.py',
]

LAUNCHER_BUILD = [
    '--noconfirm', '--clean', '--onefile', '--windowed',
    '--name', 'RS485工具箱',
    '--paths', '.',
    '--add-data', f'{GUIDE_NAME};.',
    '--hidden-import', 'wiring_info',
    '--hidden-import', 'serial.tools.list_ports',
    'tools\\launcher.py',
]

README_TXT = """RS485 称重 / 测力工具 —— 使用说明
====================================

一、双击哪个文件
  RS485工具箱.exe    推荐先开这个：按钮式入口，演示 / 接线说明 / 使用说明 / 串口检查都在这
  RS485上位机.exe    上位机主程序（实时曲线 / CSV 记录 / 内置串口助手）

二、没有硬件也能看效果（演示模式）
  方式1：双击 RS485工具箱.exe → 点「无硬件演示模式」按钮
  方式2：命令行执行 RS485上位机.exe --demo
  演示模式会自动连一个模拟变送器，曲线里能看到 8 秒一轮的穿刺波形。

三、接线说明在哪（不用额外文件）
  RS485工具箱.exe → 「接线说明（不用额外文件）」按钮，弹窗内嵌示意图和四张表；
  RS485上位机.exe → 连接设置栏的「接线说明」按钮、顶部「接线说明」标签页、帮助菜单。
  以上全部随 exe 打包，删掉外面的 md 文件也能看。

四、使用说明
  RS485工具箱.exe → 「使用说明」按钮，软件内置阅读器直接显示指南内容；
  同目录这份「接线与调试指南.md」也可以用记事本 / VS Code 打开。

五、数据文件
  CSV 记录存在 exe 旁边（软件里「打开数据目录」直达）。

常见问题
  · 首次运行可能被 SmartScreen / 杀软拦一下（exe 未签名），选「仍要运行」即可。
  · 首次启动会自解压到临时目录，约 1~2 秒。
  · 找不到串口？装 USB转485 驱动（CH340 / CP2102），用工具箱的「检查串口」看 COM 口。
"""


def run_build(args: list[str], what: str) -> None:
    cmd = [str(PYTHON), '-m', 'PyInstaller', *args]
    print(f'\n=== 开始打包 {what} ===')
    print('>', subprocess.list2cmdline(cmd))
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        raise SystemExit(f'打包失败（exit {result.returncode}）：{what}')


def make_release() -> None:
    if RELEASE.exists():
        shutil.rmtree(RELEASE)
    RELEASE.mkdir(parents=True)

    for name in ('RS485上位机.exe', 'RS485工具箱.exe'):
        src = DIST / name
        if not src.exists():
            raise SystemExit(f'缺少产物：{src}')
        shutil.copy2(src, RELEASE / name)
    shutil.copy2(ROOT / GUIDE_NAME, RELEASE / GUIDE_NAME)
    (RELEASE / '使用说明.txt').write_text(README_TXT, encoding='utf-8-sig')

    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(RELEASE.iterdir()):
            zf.write(f, f'{RELEASE.name}/{f.name}')


def main() -> int:
    if not PYTHON.exists():
        print(f'找不到虚拟环境 Python：{PYTHON}', file=sys.stderr)
        return 1
    if not (ROOT / GUIDE_NAME).exists():
        print(f'找不到 {GUIDE_NAME}，请从项目根目录运行', file=sys.stderr)
        return 1

    run_build(GUI_BUILD, 'RS485上位机')
    run_build(LAUNCHER_BUILD, 'RS485工具箱')
    make_release()

    print('\n=== 发布包已生成 ===')
    for path in (RELEASE / 'RS485上位机.exe', RELEASE / 'RS485工具箱.exe',
                 RELEASE / GUIDE_NAME, RELEASE / '使用说明.txt', ZIP_PATH):
        size = path.stat().st_size / 1024 / 1024
        print(f'  {path}  ({size:.2f} MB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
