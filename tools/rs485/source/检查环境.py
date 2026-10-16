"""
环境自检脚本 —— 跑一遍就知道环境有没有问题

作用：
  1. 列出电脑当前识别到的所有串口（插拔 USB转485 前后对比，就知道是哪个口）
  2. 检查依赖库有没有装好
  3. 检查串口是否被其他程序占用

用法：双击「运行.bat」选 3，或
     .venv\\Scripts\\python.exe 检查环境.py
"""

import sys

print("=" * 55)
print("  RS485 称重上位机 —— 环境自检")
print("=" * 55)
print()

# ---------- 1. Python 版本 ----------
print(f"[1] Python 版本: {sys.version.split()[0]}")
if sys.version_info < (3, 8):
    print("    ⚠️  版本偏低，建议 Python 3.8+")
else:
    print("    ✅ 正常")
print()

# ---------- 2. 依赖库 ----------
print("[2] 依赖库检查")
deps_ok = True
for mod_name, attr in [("serial", "__version__"),
                       ("minimalmodbus", "__version__"),
                       ("pymodbus", "__version__")]:
    try:
        mod = __import__(mod_name)
        ver = getattr(mod, attr, "未知")
        print(f"    ✅ {mod_name:<16} {ver}")
    except ImportError:
        print(f"    ❌ {mod_name:<16} 未安装")
        deps_ok = False

if not deps_ok:
    print()
    print("    修复方法（在项目目录下执行）：")
    print(r"      .venv\Scripts\python.exe -m pip install -r requirements.txt")
print()

# ---------- 3. 串口列表 ----------
print("[3] 当前识别到的串口")
try:
    from serial.tools import list_ports
    ports = list(list_ports.comports())

    if not ports:
        print("    (没有检测到任何串口)")
        print()
        print("    可能的原因：")
        print("      • USB转485 模块还没插上")
        print("      • 驱动没装 —— 去设备管理器看有没有黄色感叹号")
        print("      • 模块坏了（换一个USB口试试）")
    else:
        for p in ports:
            print(f"    ✅ {p.device}")
            print(f"       描述: {p.description}")
            if p.hwid:
                print(f"       硬件ID: {p.hwid}")
            print()

        print(f"    找到 {len(ports)} 个串口。")
        print("    代码里 PORT 填上面那个 COM 号（如 'COM3'）。")
        print()
        print("    💡 技巧：记下现在有几个，然后拔掉 USB转485 再跑一次本脚本，")
        print("       少掉的那个就是你的设备。")

        # ---------- 4. 占用检测 ----------
        print()
        print("[4] 串口占用检测（能不能打开）")
        import serial
        for p in ports:
            try:
                s = serial.Serial(p.device, 9600, timeout=0.1)
                s.close()
                print(f"    ✅ {p.device} 可以正常打开")
            except serial.SerialException as e:
                print(f"    ❌ {p.device} 打不开: {e}")
                print("       → 多半是被串口助手 / 其他程序占用了，先关掉它们")

except ImportError:
    print("    ❌ pyserial 未安装，无法检测串口")

print()
print("=" * 55)
print("  自检结束")
print("=" * 55)
