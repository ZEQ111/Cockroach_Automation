# -*- coding: utf-8 -*-
"""
Modbus 通讯底座 —— 串口(RTU) / TCP 两种模式，供上位机和串口助手共用。

为什么不直接用 pymodbus 的客户端？
  1) 上位机的「串口助手」标签页需要看到总线上原始的 HEX 字节，库封装之后拿不到；
  2) 采集线程和手工发帧必须共用同一个串口对象（串口是独占资源，开不了两个）；
  3) 自己实现协议更透明：CRC、异常码、原始帧都看得见，出问题好排查。
  （main.py / 上位机_pymodbus.py 两个库版本仍然保留，可互相印证。）

帧格式备忘：
  RTU : [从站地址][功能码][数据...][CRC低][CRC高]
  TCP : [事务号×2][协议号×2=0][长度×2][单元号×1][PDU...]（长度 = 单元号 + PDU 字节数）
"""

from __future__ import annotations

import socket
import struct
import threading
import time

try:
    import serial
except ImportError:  # pyserial 没装时只影响串口模式，TCP 模式还能用
    serial = None


# ==================== 常量 ====================

BAUDRATES = [4800, 9600, 19200, 38400, 57600, 115200]

EXCEPTION_TEXT = {
    0x01: '非法功能码（换个功能码试试，03 <-> 04）',
    0x02: '非法数据地址（寄存器地址超范围，查说明书）',
    0x03: '非法数据值（读的数量超了）',
    0x04: '从站设备故障（变送器内部错误，断电重启）',
    0x05: '从站已确认（正在处理）',
    0x06: '从站设备忙（发太快了，加大间隔）',
    0x08: '存储奇偶校验错误',
    0x0A: '网关路径不可用',
    0x0B: '网关目标设备无响应（多半是接线或地址问题）',
}


class LinkError(Exception):
    """链路层错误：打不开串口、超时、CRC 错、帧格式错等。"""


class ModbusExceptionResponse(LinkError):
    """从站返回了异常帧（功能码最高位置 1），异常码见 EXCEPTION_TEXT。"""

    def __init__(self, code: int):
        self.code = code
        text = EXCEPTION_TEXT.get(code, '未知异常')
        super().__init__(f'从站异常码 0x{code:02X}：{text}')


# ==================== CRC 与组帧 ====================

def crc16(data: bytes) -> bytes:
    """Modbus CRC16，返回 2 字节，低字节在前（总线上就是这个顺序）。"""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return struct.pack('<H', crc)


def build_rtu(unit: int, pdu: bytes) -> bytes:
    """把 PDU 包成 RTU 帧（加从站地址 + CRC）。"""
    body = bytes([unit & 0xFF]) + pdu
    return body + crc16(body)


def build_mbap(pdu: bytes, txn: int = 0, unit: int = 1) -> bytes:
    """把 PDU 包成 Modbus TCP 帧（加 MBAP 头）。"""
    return struct.pack('>HHHB', txn & 0xFFFF, 0, len(pdu) + 1, unit & 0xFF) + pdu


def build_read_pdu(func: int, addr: int, count: int) -> bytes:
    """读寄存器请求 PDU：功能码 03/04 + 起始地址 + 数量。"""
    return struct.pack('>BHH', func, addr, count)


def build_write_single_pdu(addr: int, value: int) -> bytes:
    """写单个寄存器请求 PDU：功能码 06 + 地址 + 值。"""
    return struct.pack('>BHH', 0x06, addr, value & 0xFFFF)


def parse_read_response(resp: bytes, mode: str, unit: int, func: int) -> list[int]:
    """解析读寄存器响应，返回寄存器值列表。任何不对劲都抛 LinkError。"""
    if mode == 'tcp':
        if len(resp) < 9:
            raise LinkError(f'TCP 响应太短：{hex_frame(resp)}')
        _txn, proto, length, r_unit = struct.unpack('>HHHB', resp[:7])
        if proto != 0:
            raise LinkError(f'MBAP 协议号异常（应为 0）：{hex_frame(resp)}')
        pdu = resp[7:7 + length - 1]
    else:
        if len(resp) < 5:
            raise LinkError(f'RTU 响应太短：{hex_frame(resp)}')
        expect = crc16(resp[:-2])
        if expect != resp[-2:]:
            raise LinkError(f'CRC 校验失败（应为 {expect.hex(" ").upper()}）：{hex_frame(resp)}')
        r_unit = resp[0]
        pdu = resp[1:-2]

    if r_unit != unit:
        raise LinkError(f'从站地址不匹配：期望 {unit}，收到 {r_unit}')
    if not pdu:
        raise LinkError('响应 PDU 为空')

    r_func = pdu[0]
    if r_func == (func | 0x80):
        raise ModbusExceptionResponse(pdu[1] if len(pdu) > 1 else -1)
    if r_func != func:
        raise LinkError(f'功能码不匹配：期望 {func}，收到 {r_func}')

    n = pdu[1]
    data = pdu[2:2 + n]
    if len(data) < n:
        raise LinkError(f'数据长度不足：声明 {n} 字节，实际 {len(data)} 字节')
    return [struct.unpack('>H', data[i:i + 2])[0] for i in range(0, n - n % 2, 2)]


def hex_frame(data: bytes) -> str:
    """字节转 '01 03 00 00' 这种大写 HEX 串。"""
    return ' '.join(f'{b:02X}' for b in data) if data else '(空)'


# ==================== 链路对象 ====================

class ModbusLink:
    """一条 Modbus 链路：串口 RTU 或 TCP。所有收发都走内部锁，线程安全。

    on_tx(data, tag) / on_rx(data, tag, ms) 回调在调用线程里执行，
    上位机只往 queue 里塞事件，不直接碰界面。
    """

    def __init__(self, mode: str = 'serial', *, port: str = 'COM3', baudrate: int = 9600,
                 bytesize: int = 8, parity: str = 'N', stopbits: int = 1,
                 host: str = '127.0.0.1', tcp_port: int = 5020, unit: int = 1,
                 timeout: float = 0.3, on_tx=None, on_rx=None):
        self.mode = mode
        self.port = port
        self.baudrate = baudrate
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.host = host
        self.tcp_port = tcp_port
        self.unit = unit
        self.timeout = timeout
        self.on_tx = on_tx
        self.on_rx = on_rx

        self._ser = None
        self._sock = None
        self._lock = threading.RLock()
        self._txn = 0

    # ---------- 开关 ----------

    @property
    def is_open(self) -> bool:
        if self.mode == 'tcp':
            return self._sock is not None
        return self._ser is not None and self._ser.is_open

    def open(self) -> None:
        """打开链路，失败抛 LinkError（带人话提示）。"""
        self.close()
        if self.mode == 'tcp':
            try:
                s = socket.create_connection((self.host, self.tcp_port), timeout=3.0)
            except OSError as e:
                raise LinkError(f'连接 {self.host}:{self.tcp_port} 失败：{e}') from e
            s.settimeout(self.timeout)
            self._sock = s
            return
        if serial is None:
            raise LinkError('未安装 pyserial，无法使用串口模式')
        try:
            self._ser = serial.Serial(
                self.port, self.baudrate, bytesize=self.bytesize,
                parity=self.parity, stopbits=self.stopbits,
                timeout=0.03, write_timeout=0.5,
            )
        except Exception as e:
            raise LinkError(f'打开串口 {self.port} 失败：{e}') from e

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def describe(self) -> str:
        if self.mode == 'tcp':
            return f'TCP {self.host}:{self.tcp_port}'
        return f'{self.port} @ {self.baudrate} {self.bytesize}-{self.parity}-{self.stopbits}'

    # ---------- 收发 ----------

    def _write(self, data: bytes) -> None:
        if self.mode == 'tcp':
            if self._sock is None:
                raise LinkError('链路未打开')
            try:
                self._sock.sendall(data)
            except OSError as e:
                raise LinkError(f'TCP 发送失败：{e}') from e
            return
        if self._ser is None:
            raise LinkError('链路未打开')
        try:
            self._ser.write(data)
            self._ser.flush()
        except Exception as e:
            raise LinkError(f'串口发送失败：{e}') from e

    def _read(self, timeout: float) -> bytes:
        """收一帧：RTU 用「收到数据后等 30ms 静默」判帧尾，TCP 按 MBAP 长度读。"""
        if self.mode == 'tcp':
            if self._sock is None:
                raise LinkError('链路未打开')
            self._sock.settimeout(timeout)
            try:
                head = self._recv_exact(7)
            except socket.timeout:
                return b''
            except OSError as e:
                raise LinkError(f'TCP 接收失败：{e}') from e
            _txn, _proto, length, _unit = struct.unpack('>HHHB', head)
            body = self._recv_exact(max(0, length - 1))
            return head + body

        if self._ser is None:
            raise LinkError('链路未打开')
        deadline = time.monotonic() + timeout
        buf = bytearray()
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                break
            self._ser.timeout = min(0.03, max(0.005, remain))
            try:
                chunk = self._ser.read(256)
            except Exception as e:
                raise LinkError(f'串口接收失败：{e}') from e
            if chunk:
                buf += chunk
                deadline = time.monotonic() + 0.03  # 有数据就续命，等帧结束
            elif buf:
                break  # 已经有数据且静默了 30ms -> 帧结束
        return bytes(buf)

    def _recv_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise LinkError('TCP 连接被对端关闭')
            buf += chunk
        return bytes(buf)

    def send_raw(self, data: bytes, tag: str = 'manual', timeout: float | None = None) -> bytes:
        """把 data 原样写出去并收集回包（可能为空）。线程安全。"""
        with self._lock:
            if not self.is_open:
                raise LinkError('链路未打开')
            t0 = time.perf_counter()
            if self.on_tx:
                self.on_tx(data, tag)
            self._write(data)
            resp = self._read(timeout if timeout is not None else self.timeout)
            ms = (time.perf_counter() - t0) * 1000
            if resp and self.on_rx:
                self.on_rx(resp, tag, ms)
            return resp

    # ---------- Modbus 读 ----------

    def read_registers(self, addr: int, count: int = 1, func: int = 3, tag: str = 'poll') -> list[int]:
        """读保持寄存器(03) / 输入寄存器(04)，返回寄存器值列表。"""
        if not 1 <= count <= 125:
            raise LinkError(f'寄存器数量 {count} 不合法（1~125）')
        pdu = build_read_pdu(func, addr, count)
        if self.mode == 'tcp':
            self._txn = (self._txn + 1) & 0xFFFF
            frame = build_mbap(pdu, self._txn, self.unit)
        else:
            frame = build_rtu(self.unit, pdu)

        resp = self.send_raw(frame, tag=tag)
        if not resp:
            raise LinkError(f'超时无响应（{self.timeout * 1000:.0f}ms 内没收到任何字节）')
        return parse_read_response(resp, self.mode, self.unit, func)
