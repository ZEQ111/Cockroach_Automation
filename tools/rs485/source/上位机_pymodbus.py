"""
RS485 称重变送器 —— 上位机（pymodbus 版本）

⚠️ 重要：本文件是按 pymodbus 3.15.0 的 API 写的。
   如果你以后升级了 pymodbus，注意这两个改动：
   1) 从站地址参数叫 device_id，不叫 slave（3.7 之前叫 slave，3.8+ 改成了 device_id）
      网上大量教程还在写 slave=，在新版本上会直接 TypeError
   2) read_holding_registers() 在串口断开时是【抛异常 ConnectionException】，
      不是返回一个错误对象。所以必须用 try/except 包住，不能只靠 isError() 判断。

已在本机实测：串口连不上时会正确走到下面的异常分支并给出中文提示。
"""

from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException
from datetime import datetime
import time
import sys

# ==================== 1. 配置参数（按实测值改这里）====================
PORT         = 'COM3'        # 设备管理器里的 COM 口，如 'COM3'
BAUDRATE     = 9600          # 波特率（对照变送器说明书，常见 9600 / 19200）
BYTESIZE     = 8             # 数据位
PARITY       = 'N'           # 校验位：N=无校验 / E=偶校验 / O=奇校验
STOPBITS     = 1             # 停止位
SLAVE_ID     = 1             # 变送器设备地址（从站地址）
REG_ADDR     = 0             # 重量数据寄存器地址（0x0000 就填 0）
REG_COUNT    = 1             # 读几个寄存器：16位数据填 1，32位数据填 2
SCALE_FACTOR = 100.0         # 换算比例：重量 = 原始值 / SCALE_FACTOR
                             # 例如原始读到 1250、实际 12.50kg，就填 100.0
POLL_SEC     = 0.5           # 采集间隔（秒）
SAVE_CSV     = True          # 是否把数据存成 CSV 文件
CSV_FILE     = 'weight_log.csv'
# ====================================================================


def make_client():
    """建一个 Modbus RTU 串口客户端（注意 framer 默认就是 RTU，不用手动指定）"""
    return ModbusSerialClient(
        port=PORT,
        baudrate=BAUDRATE,
        bytesize=BYTESIZE,
        parity=PARITY,
        stopbits=STOPBITS,
        timeout=1,      # 单次读取超时 1 秒
        retries=1,      # 失败重试 1 次
    )


def read_weight(client):
    """读一次重量，返回 (重量值, 原始整数)。失败会抛异常，由调用方处理。"""
    r = client.read_holding_registers(REG_ADDR, count=REG_COUNT, device_id=SLAVE_ID)

    if r.isError():
        raise ModbusException(f"从站返回错误响应: {r}")

    if REG_COUNT == 1:
        raw = r.registers[0]
    else:
        # 32位数据：常见是「高字在前」(big-endian)，即 寄存器A 是高位
        # 如果读出来数值明显不对，把下面两个换成 [1] 和 [0] 试试（字序颠倒）
        raw = (r.registers[0] << 16) | r.registers[1]

    return raw / SCALE_FACTOR, raw


def main():
    client = make_client()

    if not client.connect():
        print(f"❌ 串口 {PORT} 连接失败。请检查：")
        print("   1) COM 口号对不对（设备管理器 -> 端口）")
        print("   2) USB转485 驱动装了吗")
        print("   3) 串口是不是被串口助手占用了")
        return 1

    print(f"✅ 串口 {PORT} 已连接 @ {BAUDRATE} {BYTESIZE}-{PARITY}-{STOPBITS}"
          f"，从站地址 {SLAVE_ID}，寄存器 {REG_ADDR}")
    print("开始采集重量，Ctrl+C 停止\n")

    csv_file = None
    if SAVE_CSV:
        csv_file = open(CSV_FILE, 'a', encoding='utf-8')
        # 文件是空的才写表头
        if csv_file.tell() == 0:
            csv_file.write('时间,重量,原始值\n')

    fail_count = 0

    try:
        while True:
            try:
                weight, raw = read_weight(client)
                fail_count = 0

                ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                print(f"[{ts}]  重量: {weight:>10.2f}   (原始值 {raw})")

                if csv_file:
                    csv_file.write(f"{ts},{weight:.2f},{raw}\n")
                    csv_file.flush()        # 立刻落盘，防止断电丢数据

                time.sleep(POLL_SEC)

            except (ModbusException, OSError, IOError) as e:
                fail_count += 1
                print(f"⚠️  第 {fail_count} 次读取失败: {type(e).__name__}: {e}")

                if fail_count >= 3:
                    print("   连续失败 3 次，尝试重连...")
                    client.close()
                    time.sleep(1)
                    client = make_client()
                    if client.connect():
                        print("   ✅ 重连成功")
                        fail_count = 0
                    else:
                        print("   ❌ 重连失败，5 秒后重试")
                        time.sleep(5)
                else:
                    time.sleep(POLL_SEC)

    except KeyboardInterrupt:
        print("\n用户停止采集")
        return 0
    finally:
        client.close()
        if csv_file:
            csv_file.close()
            print(f"数据已保存到 {CSV_FILE}")


if __name__ == '__main__':
    sys.exit(main())
