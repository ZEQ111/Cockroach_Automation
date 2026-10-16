"""""
Code by: Charanpreet Singh
Email: Charanmakkar@gmail.com
Linkedin profile: https://www.linkedin.com/in/charanpreet-singh-a29949133/
DM @linkedin for query or help (Alway open to share and learn)
Development date Aug 19, 2020
Final editing done on Aug 25, 2020
Github Upload: Dec 27, 2020

===== 2026-09-18 修改记录 =====
原始版本有 4 处问题，已修复：
  1. [致命] 原第 54 行 print(c) —— c 从未定义，每轮循环必抛 NameError，
     被外层裸 except 吞掉，表现为每次都多打一行假的 "Error while data"。
     实际数据早就读到了，纯属自己吓自己。已改为 print(val)。
  2. [致命] setParameters() 失败时只打印一句提示后返回 None，
     但调用方不检查就继续用，导致后续 AttributeError 被裸 except 吞掉，
     表现为无限刷 "Error while data from register"，根本看不出是串口没开。
     现已在入口处判空并退出，并给出排查清单。
  3. 裸 except 会掩盖真实故障原因。现改为打印异常类型和内容。
  4. 硬编码的端口/波特率/地址/寄存器集中到文件顶部配置区，换设备只改一处。

注意：本文件用 minimalmodbus 库（原仓库的选择，API 多年未变，稳定）。
     另一个 pymodbus 版本见同目录「上位机_pymodbus.py」。
"""""

##Importing required libraries
import minimalmodbus as mm, serial, time, sys


# ==================== 配置区（换设备只改这里）====================
PORT      = 'COM21'      # 设备管理器 -> 端口 里看到的 COM 口，如 'COM3'
BAUDRATE  = 19200        # 波特率，常见 9600 / 19200 / 4800
SLAVE_ID  = 111          # 从站地址（变送器的设备地址）
REGISTERS = [17, 19]     # 要轮询的寄存器地址，按变送器说明书填
POLL_SEC  = 1            # 轮询间隔（秒）
# ==============================================================


####Function to set parameters for communicattion
def setParameters(port, baudrate, address):
    try:
        instrument = mm.Instrument(port, 3, debug=False)
        instrument.serial.baudrate = baudrate
        instrument.address = address
        instrument.serial.timeout  = 1
        instrument.serial.parity   = serial.PARITY_NONE
        instrument.mode = mm.MODE_RTU
        print(instrument)
        return instrument
    except Exception as e:
        #if no device found
        print(f"Device NOT connected / 串口打开失败: {type(e).__name__}: {e}")
        return None


####Function to read data from RS485 connnection made at a specific register
def readint(instrument, a):
    try:
        data = instrument.read_register(a)
        return data
    except Exception as e:
        print(f"[读寄存器 {a} 失败] {type(e).__name__}: {e}")
        return None


if __name__ == '__main__':
    #Set Parameter of RS485 communication here
    instrument = setParameters(PORT, BAUDRATE, SLAVE_ID)

    if instrument is None:
        print("\n串口初始化失败，已退出。请依次检查：")
        print("  1) COM 口号是否和「设备管理器 -> 端口」里的一致")
        print("  2) USB转485 驱动是否装好（设备管理器有没有黄色感叹号）")
        print("  3) 串口是否被别的软件占用（串口助手一定要先关掉！）")
        sys.exit(1)

    print(f"开始轮询寄存器 {REGISTERS}，Ctrl+C 停止\n")

    ####Loop that repeats infinite times
    try:
        while True:
            for reg in REGISTERS:
                #Put register value inside the brackets (As per given in datasheet of controller/PLC)
                val = readint(instrument, reg)
                if val is not None:
                    print(f"寄存器 {reg} = {val}")

            #Time delay of (1 = 1 second) 1 second after each run
            time.sleep(POLL_SEC)

            #To seperate readings after each run
            print("__" * 40)
    except KeyboardInterrupt:
        print("\n已手动停止")
