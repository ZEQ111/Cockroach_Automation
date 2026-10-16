# -*- coding: utf-8 -*-
"""
RS485 测力/称重上位机（图形界面版）
====================================

功能：
  · 串口(RTU) / TCP 两种连接方式，串口自动扫描
  · 实时采集：大数字显示 + 滚动曲线 + 峰值保持 + 采样率
  · CSV 记录（列格式直接兼容力传感器项目的 force_filter.py）
  · 断线自动重连
  · 内置「串口助手」：HEX 收发、Modbus 帧一键生成（自动 CRC）、预设帧、定时发送
  · 无硬件也能用：配合 tools/mock_slave.py 或 pymodbus 模拟器走 TCP 模式

依赖：pyserial（项目 .venv 已装），协议代码在 modbus_link.py，零额外依赖。
启动：双击 运行.bat 选 4，或 .venv\\Scripts\\python.exe 上位机_GUI.py
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# 打包成 exe 后 __file__ 在临时解包目录：资源(指南/mock_slave)用 RESOURCE_DIR，
# 数据(CSV/日志)用 exe 所在目录 PROJECT_DIR，用户能找到。
IS_FROZEN = bool(getattr(sys, 'frozen', False))
if IS_FROZEN:
    RESOURCE_DIR = Path(getattr(sys, '_MEIPASS', Path(sys.executable).resolve().parent))
    PROJECT_DIR = Path(sys.executable).resolve().parent
else:
    RESOURCE_DIR = Path(__file__).resolve().parent
    PROJECT_DIR = RESOURCE_DIR

sys.path.insert(0, str(RESOURCE_DIR))
sys.path.insert(0, str(RESOURCE_DIR / 'tools'))
import modbus_link as mb
from wiring_info import GUIDE_NAME, build_wiring_panel, find_guide

# ==================== 配置 ====================

FORCE_FILTER = Path(r'C:\Users\Skyla\Desktop\2026国创赛\高精度力传感器选择方案\force_filter.py')

PRESET_FRAMES = [
    ('从站1 读保持寄存器0 ×1（最通用起手式）', '01 03 00 00 00 01 84 0A'),
    ('从站1 读保持寄存器0 ×2', '01 03 00 00 00 02 C4 0B'),
    ('从站1 读寄存器17', '01 03 00 11 00 01 D4 0F'),
    ('从站1 读输入寄存器0（功能码04）', '01 04 00 00 00 01 31 CA'),
    ('从站111 读寄存器17（原仓库配置）', '6F 03 00 11 00 01 DC 81'),
    ('从站111 读寄存器19（原仓库配置）', '6F 03 00 13 00 01 7D 41'),
]

C_BG = '#eef1f5'
C_PANEL = '#ffffff'
C_ACCENT = '#1565c0'
C_OK = '#2e7d32'
C_WARN = '#ef6c00'
C_ERR = '#c62828'
C_MUTED = '#64748b'
C_TEXT = '#1f2937'
FONT_UI = ('Microsoft YaHei UI', 10)
FONT_MONO = ('Consolas', 10)
FONT_VALUE = ('Consolas', 44, 'bold')

CHART_WINDOWS = ['10 秒', '20 秒', '30 秒', '60 秒', '120 秒']
POLL_INTERVALS = ['20 ms', '50 ms', '100 ms', '200 ms', '500 ms', '1000 ms']


def enable_dpi() -> None:
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def fmt_hex(data: bytes) -> str:
    return ' '.join(f'{b:02X}' for b in data) if data else ''


def parse_int(text: str) -> int:
    """支持 12 / 0x0C / 12,34(多值暂不支持) 的整数解析。"""
    t = text.strip().replace('_', '')
    return int(t, 16) if t.lower().startswith('0x') else int(t, 10)


# ==================== 曲线控件 ====================

class StripChart(tk.Canvas):
    """滚动曲线：只画最近 window_s 秒，纵轴自动缩放。"""

    def __init__(self, master, window_s: float = 20.0):
        super().__init__(master, bg=C_PANEL, highlightthickness=1,
                         highlightbackground='#d5d9e0')
        self.window_s = window_s
        self.data: deque[tuple[float, float]] = deque(maxlen=200000)
        self.peak: float | None = None
        self.bind('<Configure>', lambda _e: self.redraw())

    def add(self, t: float, v: float) -> None:
        self.data.append((t, v))

    def clear(self) -> None:
        self.data.clear()
        self.peak = None
        self.redraw()

    def redraw(self) -> None:
        self.delete('all')
        w, h = self.winfo_width(), self.winfo_height()
        if w < 80 or h < 60:
            return
        ml, mr, mt, mbd = 62, 16, 14, 28
        x0, y0, x1, y1 = ml, mt, w - mr, h - mbd

        now = self.data[-1][0] if self.data else 0.0
        t_lo = now - self.window_s
        pts = [(t, v) for t, v in self.data if t >= t_lo]

        vals = [v for _, v in pts] or [0.0]
        v_min, v_max = min(vals), max(vals)
        if v_max - v_min < 0.2:
            mid = (v_max + v_min) / 2.0
            v_min, v_max = mid - 0.1, mid + 0.1
        else:
            pad = (v_max - v_min) * 0.12
            v_min -= pad
            v_max += pad
        if self.peak is not None and self.peak > v_max:
            v_max = self.peak * 1.05

        def mx(t: float) -> float:
            return x0 + (t - t_lo) / self.window_s * (x1 - x0)

        def my(v: float) -> float:
            return y1 - (v - v_min) / (v_max - v_min) * (y1 - y0)

        # 网格 + 纵轴刻度
        for i in range(5):
            v = v_min + (v_max - v_min) * i / 4.0
            y = my(v)
            self.create_line(x0, y, x1, y, fill='#e8ecf1')
            self.create_text(x0 - 6, y, text=f'{v:.2f}', anchor='e',
                             fill=C_MUTED, font=('Consolas', 8))
        # 横轴刻度
        for i in range(5):
            t = t_lo + self.window_s * i / 4.0
            x = mx(t)
            self.create_line(x, y0, x, y1, fill='#e8ecf1')
            label = f'{t - now:.0f}s' if i < 4 else '现在'
            self.create_text(x, y1 + 14, text=label, fill=C_MUTED,
                             font=('Microsoft YaHei UI', 8))

        self.create_line(x0, y0, x0, y1, fill='#b9c2cd')
        self.create_line(x0, y1, x1, y1, fill='#b9c2cd')

        if not pts:
            self.create_text((x0 + x1) / 2, (y0 + y1) / 2, text='等待数据…',
                             fill='#9aa4b2', font=('Microsoft YaHei UI', 12))
            return

        # 数据抽稀：窗口内超过 3000 点就隔点画，界面不卡
        stride = max(1, len(pts) // 3000)
        coords: list[float] = []
        for t, v in pts[::stride]:
            coords += [mx(t), my(v)]
        if len(coords) >= 4:
            self.create_line(*coords, fill=C_ACCENT, width=1.6, smooth=False)

        # 峰值线
        if self.peak is not None and self.peak > v_min:
            yp = my(self.peak)
            self.create_line(x0, yp, x1, yp, fill=C_ERR, dash=(4, 3))
            self.create_text(x1 - 4, yp - 8, text=f'峰值 {self.peak:.2f}', anchor='e',
                             fill=C_ERR, font=('Microsoft YaHei UI', 8))

        # 当前点
        t_last, v_last = pts[-1]
        self.create_oval(mx(t_last) - 3, my(v_last) - 3,
                         mx(t_last) + 3, my(v_last) + 3,
                         fill=C_ACCENT, outline='')


# ==================== 主程序 ====================

class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title('RS485 测力上位机')
        self.geometry('1120x760')
        self.minsize(1000, 640)
        self.configure(bg=C_BG)

        # --- 运行状态 ---
        self.link: mb.ModbusLink | None = None
        self.q: queue.Queue = queue.Queue()
        self.poll_thread: threading.Thread | None = None
        self.poll_stop = threading.Event()
        self.poll_cfg: dict = {}
        self.fail_count = 0
        self.t_connect = 0.0
        self.sample_times: deque[float] = deque(maxlen=30)
        self.peak: float | None = None
        self.n_samples = 0
        self._dirty = False

        # --- 记录状态 ---
        self.rec_file = None
        self.rec_path: Path | None = None
        self.rec_t0 = 0.0

        # --- 界面变量 ---
        self.mode_var = tk.StringVar(value='serial')
        self.port_var = tk.StringVar()
        self.baud_var = tk.StringVar(value='9600')
        self.host_var = tk.StringVar(value='127.0.0.1')
        self.tcp_port_var = tk.StringVar(value='5020')
        self.unit_var = tk.StringVar(value='1')
        self.addr_var = tk.StringVar(value='0')
        self.count_var = tk.StringVar(value='1')
        self.order_var = tk.StringVar(value='高字在前')
        self.signed_var = tk.BooleanVar(value=False)
        self.scale_var = tk.StringVar(value='1.0')
        self.physunit_var = tk.StringVar(value='N')
        self.interval_var = tk.StringVar(value='200 ms')
        self.autorec_var = tk.BooleanVar(value=False)
        self.chartwin_var = tk.StringVar(value='20 秒')

        self.asst_send_var = tk.StringVar()
        self.asst_hex_var = tk.BooleanVar(value=True)
        self.asst_timer_var = tk.StringVar(value='500 ms')
        self.asst_show_hex_var = tk.BooleanVar(value=True)
        self.asst_ts_var = tk.BooleanVar(value=True)
        self.asst_scroll_var = tk.BooleanVar(value=True)
        self.asst_poll_traffic_var = tk.BooleanVar(value=False)
        self.gen_unit_var = tk.StringVar(value='1')
        self.gen_func_var = tk.StringVar(value='03 读保持寄存器')
        self.gen_addr_var = tk.StringVar(value='0')
        self.gen_val_var = tk.StringVar(value='1')
        self.preset_var = tk.StringVar(value=PRESET_FRAMES[0][0])

        self._setup_style()
        self._build_menubar()
        self._build_ui()
        self.refresh_ports()
        self._on_mode_change()

        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self.after(30, self._drain)
        self.after(120, self._tick_chart)
        self._log('info', '就绪。串口模式直接连设备；无硬件可选 TCP 模拟，先运行 tools/mock_slave.py')

    # ---------------- 样式 ----------------

    def _setup_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', background=C_BG, foreground=C_TEXT, font=FONT_UI)
        style.configure('TFrame', background=C_BG)
        style.configure('TLabel', background=C_BG, foreground=C_TEXT)
        style.configure('TLabelframe', background=C_BG, bordercolor='#c9d2dc')
        style.configure('TLabelframe.Label', background=C_BG, foreground=C_MUTED,
                        font=('Microsoft YaHei UI', 9))
        style.configure('TButton', padding=(10, 4))
        style.configure('TNotebook', background=C_BG, borderwidth=0)
        style.configure('TNotebook.Tab', padding=(16, 6), font=FONT_UI)
        style.configure('TCheckbutton', background=C_BG)
        style.configure('TRadiobutton', background=C_BG)
        style.configure('Status.TLabel', background='#dde3ea', foreground=C_TEXT)
        style.configure('Accent.TButton', foreground='white', background=C_ACCENT)
        style.map('Accent.TButton', background=[('active', '#0d47a1')])
        style.configure('Danger.TButton', foreground='white', background=C_ERR)
        style.map('Danger.TButton', background=[('active', '#8e1b1b')])

    # ---------------- 菜单栏 ----------------

    def _build_menubar(self) -> None:
        menubar = tk.Menu(self, tearoff=0)

        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label='打开数据目录', command=self.open_data_dir)
        m_file.add_separator()
        m_file.add_command(label='退出', command=self.on_close)
        menubar.add_cascade(label='文件', menu=m_file)

        m_tool = tk.Menu(menubar, tearoff=0)
        m_tool.add_command(label='无硬件演示（新窗口）', command=self.launch_demo)
        m_tool.add_separator()
        m_tool.add_command(label='协议自检（CRC / 组帧 / 收发）',
                           command=lambda: self._launch_console('tools/loopback_test.py'))
        m_tool.add_command(label='环境自检（串口 / 依赖）',
                           command=lambda: self._launch_console('检查环境.py'))
        m_tool.add_command(label='启动模拟从站（TCP 5020）',
                           command=lambda: self._launch_console('tools/mock_slave.py'))
        m_tool.add_separator()
        m_tool.add_command(label='分析 CSV（force_filter）', command=self.run_analysis)
        menubar.add_cascade(label='工具', menu=m_tool)

        m_help = tk.Menu(menubar, tearoff=0)
        m_help.add_command(label='接线说明（在软件里查看）', command=self.show_wiring)
        m_help.add_command(label='使用说明（打开调试指南）', command=self.open_guide)
        m_help.add_command(label='关于', command=self.show_about)
        menubar.add_cascade(label='帮助', menu=m_help)

        self.configure(menu=menubar)

    @staticmethod
    def _venv_python(console: bool) -> str:
        """pythonw 启动时 sys.executable 是 pythonw.exe，需要控制台的换成 python.exe。"""
        exe = Path(sys.executable)
        cand = exe.with_name('python.exe' if console else 'pythonw.exe')
        return str(cand if cand.exists() else exe)

    def _launch_console(self, rel_script: str, extra: list[str] | None = None) -> None:
        """在独立控制台窗口里跑一个脚本（自检/模拟从站这类要看输出的）。"""
        if IS_FROZEN:
            messagebox.showinfo(
                '打包版不支持',
                f'「{rel_script}」需要开发环境的 Python 运行。\n\n'
                '打包版只带采集功能；无硬件想看效果，请用「工具 → 无硬件演示」。')
            return
        script = PROJECT_DIR / rel_script
        if not script.exists():
            messagebox.showerror('找不到脚本', str(script))
            return
        flags = getattr(subprocess, 'CREATE_NEW_CONSOLE', 0) if os.name == 'nt' else 0
        try:
            subprocess.Popen([self._venv_python(True), str(script), *(extra or [])],
                             cwd=str(PROJECT_DIR), creationflags=flags)
        except OSError as e:
            messagebox.showerror('启动失败', str(e))
            return
        self._log('info', f'已启动: {rel_script}')

    def launch_demo(self) -> None:
        try:
            if IS_FROZEN:
                subprocess.Popen([sys.executable, '--demo'], cwd=str(PROJECT_DIR))
            else:
                subprocess.Popen([self._venv_python(False),
                                  str(PROJECT_DIR / '上位机_GUI.py'), '--demo'],
                                 cwd=str(PROJECT_DIR))
        except OSError as e:
            messagebox.showerror('启动失败', str(e))
            return
        self._log('info', '已启动无硬件演示（新窗口）')

    def open_guide(self) -> None:
        guide = find_guide()
        if guide is not None:
            os.startfile(str(guide))  # noqa: S606
        else:
            messagebox.showinfo('使用说明', f'没找到「{GUIDE_NAME}」')

    def show_about(self) -> None:
        messagebox.showinfo(
            '关于',
            'RS485 称重/测力上位机\n\n'
            '· 协议底座 modbus_link.py（自实现 RTU/TCP）\n'
            '· 实时曲线 + CSV + 内置串口助手\n'
            '· 无硬件可用 TCP 模拟模式（tools/mock_slave.py）\n\n'
            f'项目目录：{PROJECT_DIR}')

    # ---------------- 界面搭建 ----------------

    def _build_ui(self) -> None:
        self._build_topbar()
        self._build_statusbar()  # 先占住底部，再让 Notebook 填中间，否则状态栏会被挤没
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill='both', expand=True, padx=10, pady=(0, 6))
        self._build_collect_tab()
        self._build_assistant_tab()
        self._build_wiring_tab()

    def _build_topbar(self) -> None:
        bar = ttk.LabelFrame(self, text='连接设置', padding=(10, 6))
        bar.pack(fill='x', padx=10, pady=(8, 6))

        r0 = ttk.Frame(bar)
        r0.grid(row=0, column=0, sticky='w')
        ttk.Label(r0, text='连接方式:').pack(side='left')
        ttk.Radiobutton(r0, text='串口', value='serial', variable=self.mode_var,
                        command=self._on_mode_change).pack(side='left', padx=(4, 0))
        ttk.Radiobutton(r0, text='TCP 模拟', value='tcp', variable=self.mode_var,
                        command=self._on_mode_change).pack(side='left')

        ttk.Label(r0, text='  串口:').pack(side='left')
        self.port_cb = ttk.Combobox(r0, textvariable=self.port_var, width=10, state='readonly')
        self.port_cb.pack(side='left')
        ttk.Button(r0, text='刷新', width=5, command=self.refresh_ports).pack(side='left', padx=(4, 0))

        ttk.Label(r0, text='  波特率:').pack(side='left')
        ttk.Combobox(r0, textvariable=self.baud_var, width=8, state='readonly',
                     values=[str(b) for b in mb.BAUDRATES]).pack(side='left')

        ttk.Label(r0, text='  TCP 地址:').pack(side='left')
        self.host_entry = ttk.Entry(r0, textvariable=self.host_var, width=12)
        self.host_entry.pack(side='left')
        ttk.Label(r0, text='端口:').pack(side='left')
        self.tcpport_entry = ttk.Entry(r0, textvariable=self.tcp_port_var, width=6)
        self.tcpport_entry.pack(side='left')

        r1 = ttk.Frame(bar)
        r1.grid(row=1, column=0, sticky='w', pady=(6, 0))
        ttk.Label(r1, text='从站地址:').pack(side='left')
        ttk.Entry(r1, textvariable=self.unit_var, width=5).pack(side='left')
        ttk.Label(r1, text='  寄存器:').pack(side='left')
        ttk.Entry(r1, textvariable=self.addr_var, width=6).pack(side='left')
        ttk.Label(r1, text='  数量:').pack(side='left')
        ttk.Combobox(r1, textvariable=self.count_var, width=3, state='readonly',
                     values=['1', '2', '4']).pack(side='left')
        ttk.Label(r1, text='  字序:').pack(side='left')
        ttk.Combobox(r1, textvariable=self.order_var, width=8, state='readonly',
                     values=['高字在前', '低字在前']).pack(side='left')
        ttk.Checkbutton(r1, text='有符号', variable=self.signed_var).pack(side='left', padx=(6, 0))
        ttk.Label(r1, text='  比例:').pack(side='left')
        ttk.Entry(r1, textvariable=self.scale_var, width=7).pack(side='left')
        ttk.Label(r1, text='单位:').pack(side='left')
        ttk.Entry(r1, textvariable=self.physunit_var, width=4).pack(side='left')

        r2 = ttk.Frame(bar)
        r2.grid(row=2, column=0, sticky='w', pady=(8, 0))
        ttk.Label(r2, text='采集间隔:').pack(side='left')
        ttk.Combobox(r2, textvariable=self.interval_var, width=8, state='readonly',
                     values=POLL_INTERVALS).pack(side='left')
        self.btn_connect = ttk.Button(r2, text='连接', style='Accent.TButton',
                                      command=self.do_connect)
        self.btn_connect.pack(side='left', padx=(16, 0))
        self.btn_disconnect = ttk.Button(r2, text='断开', command=self.do_disconnect)
        self.btn_disconnect.pack(side='left', padx=(6, 0))
        self.btn_start = ttk.Button(r2, text='▶ 开始采集', command=self.start_poll)
        self.btn_start.pack(side='left', padx=(16, 0))
        self.btn_pause = ttk.Button(r2, text='⏸ 暂停采集', command=self.pause_poll)
        self.btn_pause.pack(side='left', padx=(6, 0))
        self.btn_wiring = ttk.Button(r2, text='接线说明', style='Accent.TButton',
                                     command=self.show_wiring)
        self.btn_wiring.pack(side='left', padx=(24, 0))

    def _build_collect_tab(self) -> None:
        tab = ttk.Frame(self.nb, padding=(4, 8))
        self.nb.add(tab, text='  实时采集  ')

        # 数值面板
        panel = tk.Frame(tab, bg=C_PANEL, highlightthickness=1, highlightbackground='#d5d9e0')
        panel.pack(fill='x')
        inner = tk.Frame(panel, bg=C_PANEL)
        inner.pack(fill='x', padx=16, pady=10)

        left = tk.Frame(inner, bg=C_PANEL)
        left.pack(side='left')
        self.value_label = tk.Label(left, text='--.--', font=FONT_VALUE,
                                    bg=C_PANEL, fg=C_MUTED)
        self.value_label.pack(side='left')
        self.unit_label = tk.Label(left, text=' N', font=('Microsoft YaHei UI', 16),
                                   bg=C_PANEL, fg=C_MUTED)
        self.unit_label.pack(side='left', anchor='s', pady=(0, 12))

        right = tk.Frame(inner, bg=C_PANEL)
        right.pack(side='right', anchor='e')
        self.raw_label = tk.Label(right, text='原始值: --', font=FONT_MONO,
                                  bg=C_PANEL, fg=C_MUTED)
        self.raw_label.pack(anchor='e')
        self.rate_label = tk.Label(right, text='采样率: -- Hz', font=FONT_MONO,
                                   bg=C_PANEL, fg=C_MUTED)
        self.rate_label.pack(anchor='e')
        peak_row = tk.Frame(right, bg=C_PANEL)
        peak_row.pack(anchor='e')
        self.peak_label = tk.Label(peak_row, text='峰值: --', font=FONT_MONO,
                                   bg=C_PANEL, fg=C_ERR)
        self.peak_label.pack(side='left')
        ttk.Button(peak_row, text='清零峰值', command=self.reset_peak).pack(side='left', padx=(8, 0))

        # 记录条（先占住底部，再让曲线填中间）
        rec = ttk.Frame(tab)
        rec.pack(side='bottom', fill='x', pady=(8, 0))
        ttk.Checkbutton(rec, text='连接后自动记录', variable=self.autorec_var).pack(side='left')
        self.btn_rec = ttk.Button(rec, text='● 开始记录', command=self.toggle_record)
        self.btn_rec.pack(side='left', padx=(10, 0))
        ttk.Button(rec, text='打开数据目录', command=self.open_data_dir).pack(side='left', padx=(6, 0))
        ttk.Button(rec, text='分析 CSV (force_filter)', command=self.run_analysis).pack(side='left', padx=(6, 0))
        self.rec_label = ttk.Label(rec, text='未记录', foreground=C_MUTED)
        self.rec_label.pack(side='left', padx=(12, 0))

        # 曲线
        chart_row = ttk.Frame(tab)
        chart_row.pack(fill='both', expand=True, pady=(8, 0))
        self.chart = StripChart(chart_row, window_s=20.0)
        self.chart.pack(fill='both', expand=True, side='left')
        side = ttk.Frame(chart_row)
        side.pack(side='right', fill='y', padx=(8, 0))
        ttk.Label(side, text='时间窗口').pack(anchor='w')
        ttk.Combobox(side, textvariable=self.chartwin_var, width=7, state='readonly',
                     values=CHART_WINDOWS).pack(anchor='w', pady=(2, 8))
        ttk.Button(side, text='清空曲线', command=self.chart.clear).pack(anchor='w')

    def _build_assistant_tab(self) -> None:
        tab = ttk.Frame(self.nb, padding=(4, 8))
        self.nb.add(tab, text='  串口助手  ')

        # 预设帧
        r0 = ttk.LabelFrame(tab, text='预设帧（来自调试指南，含 CRC）', padding=(8, 6))
        r0.pack(fill='x')
        ttk.Combobox(r0, textvariable=self.preset_var, width=44, state='readonly',
                     values=[p[0] for p in PRESET_FRAMES]).pack(side='left')
        ttk.Button(r0, text='填入发送框', command=self.preset_fill).pack(side='left', padx=(8, 0))
        ttk.Button(r0, text='直接发送', command=self.preset_send).pack(side='left', padx=(6, 0))

        # 帧生成器
        r1 = ttk.LabelFrame(tab, text='Modbus 帧生成器（自动算 CRC / MBAP）', padding=(8, 6))
        r1.pack(fill='x', pady=(6, 0))
        ttk.Label(r1, text='从站:').pack(side='left')
        ttk.Entry(r1, textvariable=self.gen_unit_var, width=5).pack(side='left')
        ttk.Label(r1, text='  功能:').pack(side='left')
        ttk.Combobox(r1, textvariable=self.gen_func_var, width=18, state='readonly',
                     values=['03 读保持寄存器', '04 读输入寄存器', '06 写单个寄存器']).pack(side='left')
        ttk.Label(r1, text='  地址:').pack(side='left')
        ttk.Entry(r1, textvariable=self.gen_addr_var, width=6).pack(side='left')
        ttk.Label(r1, text='数量/值:').pack(side='left')
        ttk.Entry(r1, textvariable=self.gen_val_var, width=6).pack(side='left')
        ttk.Button(r1, text='生成并填入', command=lambda: self.gen_frame(False)).pack(side='left', padx=(8, 0))
        ttk.Button(r1, text='生成并发送', command=lambda: self.gen_frame(True)).pack(side='left', padx=(6, 0))

        # 发送
        r2 = ttk.LabelFrame(tab, text='发送', padding=(8, 6))
        r2.pack(fill='x', pady=(6, 0))
        self.send_entry = ttk.Entry(r2, textvariable=self.asst_send_var, font=FONT_MONO)
        self.send_entry.pack(side='left', fill='x', expand=True)
        self.send_entry.bind('<Return>', lambda _e: self.asst_send())
        ttk.Checkbutton(r2, text='HEX', variable=self.asst_hex_var).pack(side='left', padx=(8, 0))
        ttk.Button(r2, text='发送', style='Accent.TButton',
                   command=self.asst_send).pack(side='left', padx=(6, 0))
        ttk.Label(r2, text='定时:').pack(side='left', padx=(10, 0))
        ttk.Combobox(r2, textvariable=self.asst_timer_var, width=8, state='readonly',
                     values=['100 ms', '200 ms', '500 ms', '1000 ms', '2000 ms']).pack(side='left')
        self.btn_timer = ttk.Button(r2, text='开始定时发送', command=self.toggle_timer)
        self.btn_timer.pack(side='left', padx=(6, 0))

        # 日志
        r3 = ttk.Frame(tab)
        r3.pack(fill='x', pady=(6, 0))
        ttk.Checkbutton(r3, text='HEX 显示', variable=self.asst_show_hex_var).pack(side='left')
        ttk.Checkbutton(r3, text='时间戳', variable=self.asst_ts_var).pack(side='left', padx=(6, 0))
        ttk.Checkbutton(r3, text='自动滚动', variable=self.asst_scroll_var).pack(side='left', padx=(6, 0))
        ttk.Checkbutton(r3, text='显示轮询流量', variable=self.asst_poll_traffic_var).pack(side='left', padx=(6, 0))
        ttk.Button(r3, text='清空日志', command=self.asst_clear).pack(side='right')

        box = ttk.Frame(tab)
        box.pack(fill='both', expand=True, pady=(4, 0))
        self.asst_text = tk.Text(box, bg='#0f172a', fg='#cbd5e1', font=FONT_MONO,
                                 wrap='none', insertbackground='#cbd5e1',
                                 relief='flat', padx=8, pady=6)
        sb = ttk.Scrollbar(box, command=self.asst_text.yview)
        self.asst_text.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.asst_text.pack(fill='both', expand=True)
        self.asst_text.tag_config('tx', foreground='#60a5fa')
        self.asst_text.tag_config('rx', foreground='#4ade80')
        self.asst_text.tag_config('err', foreground='#f87171')
        self.asst_text.tag_config('info', foreground='#94a3b8')
        self.asst_text.configure(state='disabled')

    def _build_wiring_tab(self) -> None:
        """接线说明：内容来自 wiring_info（内嵌数据），可滚动，小屏 / 高 DPI 下也不会被裁掉。"""
        tab = ttk.Frame(self.nb, padding=(4, 8))
        self.wiring_tab = tab
        self.nb.add(tab, text='  接线说明  ')
        self.wiring_panel = build_wiring_panel(tab, on_open_guide=self.open_guide)

    def show_wiring(self) -> None:
        self.nb.select(self.wiring_tab)

    def _build_statusbar(self) -> None:
        bar = tk.Frame(self, bg='#dde3ea')
        bar.pack(fill='x', side='bottom')
        self.dot = tk.Canvas(bar, width=14, height=14, bg='#dde3ea',
                             highlightthickness=0)
        self.dot.pack(side='left', padx=(10, 4), pady=4)
        self.dot_id = self.dot.create_oval(3, 3, 11, 11, fill='#9aa4b2', outline='')
        self.status_label = tk.Label(bar, text='未连接', bg='#dde3ea', fg=C_TEXT,
                                     font=FONT_UI)
        self.status_label.pack(side='left')
        self.info_label = tk.Label(bar, text='', bg='#dde3ea', fg=C_MUTED, font=FONT_UI)
        self.info_label.pack(side='right', padx=10)

    # ---------------- 基础工具 ----------------

    def set_status(self, text: str, color: str = C_MUTED) -> None:
        self.status_label.configure(text=text)
        self.dot.itemconfigure(self.dot_id, fill=color)

    def _log(self, kind: str, text: str) -> None:
        """往串口助手日志里写一行（kind: tx/rx/err/info）。"""
        ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]
        prefix = {'tx': 'TX →', 'rx': 'RX ←', 'err': 'ERR ', 'info': '·   '}.get(kind, '    ')
        line = f'[{ts}] {prefix} {text}\n' if self.asst_ts_var.get() else f'{prefix} {text}\n'
        self.asst_text.configure(state='normal')
        self.asst_text.insert('end', line, kind)
        if int(self.asst_text.index('end-1c').split('.')[0]) > 3000:
            self.asst_text.delete('1.0', '500.0')
        self.asst_text.configure(state='disabled')
        if self.asst_scroll_var.get():
            self.asst_text.see('end')

    def refresh_ports(self) -> None:
        try:
            from serial.tools import list_ports
            ports = [p.device for p in list_ports.comports()]
        except Exception:
            ports = []
        self.port_cb['values'] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])

    def _on_mode_change(self) -> None:
        is_serial = self.mode_var.get() == 'serial'
        self.port_cb.configure(state='readonly' if is_serial else 'disabled')
        self.host_entry.configure(state='normal' if not is_serial else 'disabled')
        self.tcpport_entry.configure(state='normal' if not is_serial else 'disabled')

    # ---------------- 连接 / 断开 ----------------

    def _read_config(self) -> dict | None:
        try:
            unit = parse_int(self.unit_var.get())
            addr = parse_int(self.addr_var.get())
            count = int(self.count_var.get())
            scale = float(self.scale_var.get())
            interval = int(self.interval_var.get().split()[0]) / 1000.0
        except ValueError as e:
            messagebox.showerror('参数错误', f'参数填得不对：{e}')
            return None
        if not 1 <= unit <= 247:
            messagebox.showerror('参数错误', '从站地址应在 1~247')
            return None
        if not 0 <= addr <= 65535:
            messagebox.showerror('参数错误', '寄存器地址应在 0~65535')
            return None
        if scale == 0:
            messagebox.showerror('参数错误', '比例不能为 0')
            return None
        return dict(unit=unit, addr=addr, count=count, scale=scale, interval=interval)

    def do_connect(self) -> None:
        if self.link is not None and self.link.is_open:
            return
        cfg = self._read_config()
        if cfg is None:
            return
        if self.mode_var.get() == 'serial':
            if not self.port_var.get():
                messagebox.showerror('没有串口', '没选串口。没插 USB转485 的话，改用「TCP 模拟」模式。')
                return
            self.link = mb.ModbusLink(mode='serial', port=self.port_var.get(),
                                      baudrate=int(self.baud_var.get()),
                                      unit=cfg['unit'],
                                      on_tx=self._on_tx, on_rx=self._on_rx)
        else:
            try:
                tcp_port = int(self.tcp_port_var.get())
            except ValueError:
                messagebox.showerror('参数错误', 'TCP 端口必须是数字')
                return
            self.link = mb.ModbusLink(mode='tcp', host=self.host_var.get(),
                                      tcp_port=tcp_port, unit=cfg['unit'],
                                      on_tx=self._on_tx, on_rx=self._on_rx)
        try:
            self.link.open()
        except mb.LinkError as e:
            self.link = None
            messagebox.showerror('连接失败', str(e))
            self._log('err', f'连接失败: {e}')
            return
        self.t_connect = time.perf_counter()
        self.fail_count = 0
        self.set_status(f'已连接 {self.link.describe()}', C_OK)
        self._log('info', f'已连接 {self.link.describe()}，从站 {cfg["unit"]}')
        if self.autorec_var.get():
            self.toggle_record()

    def do_disconnect(self) -> None:
        self.pause_poll()
        if self.rec_file is not None:
            self.toggle_record()
        if self.link is not None:
            self.link.close()
            self.link = None
        self.set_status('未连接', C_MUTED)
        self.value_label.configure(fg=C_MUTED, text='--.--')
        self._log('info', '已断开')

    # ---------------- 采集线程 ----------------

    def start_poll(self) -> None:
        if self.link is None or not self.link.is_open:
            messagebox.showwarning('还没连接', '先点「连接」')
            return
        if self.poll_thread is not None and self.poll_thread.is_alive():
            return
        cfg = self._read_config()
        if cfg is None:
            return
        # 界面变量只允许主线程读，这里一次性快照给采集线程用
        cfg['order'] = self.order_var.get()
        cfg['signed'] = self.signed_var.get()
        self.poll_cfg = cfg
        self.poll_stop.clear()
        self.fail_count = 0
        self.sample_times.clear()
        self.poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.poll_thread.start()
        self.set_status(f'采集中 {self.link.describe()}', C_OK)
        self._log('info', f'开始采集：寄存器 {cfg["addr"]} × {cfg["count"]}，'
                          f'间隔 {cfg["interval"] * 1000:.0f}ms')

    def pause_poll(self) -> None:
        if self.poll_thread is not None and self.poll_thread.is_alive():
            self.poll_stop.set()
            self.poll_thread.join(timeout=2.0)
            self.poll_thread = None
            self._log('info', '已暂停采集')
            if self.link is not None and self.link.is_open:
                self.set_status(f'已连接（未采集） {self.link.describe()}', C_WARN)

    def _poll_loop(self) -> None:
        cfg = self.poll_cfg
        interval = cfg['interval']
        while not self.poll_stop.is_set():
            t0 = time.perf_counter()
            try:
                regs = self.link.read_registers(cfg['addr'], cfg['count'], 3)
                raw = self._decode(regs, cfg)
                value = raw / cfg['scale']
                self.q.put(('sample', time.perf_counter() - self.t_connect, value, raw))
                self.fail_count = 0
            except mb.LinkError as e:
                self.fail_count += 1
                self.q.put(('poll_err', str(e), self.fail_count))
                if self.fail_count >= 3:
                    self.q.put(('log', 'err', '连续 3 次失败，尝试重连…'))
                    try:
                        self.link.close()
                        time.sleep(0.5)
                        self.link.open()
                        self.fail_count = 0
                        self.q.put(('log', 'info', '重连成功'))
                    except mb.LinkError as e2:
                        self.q.put(('log', 'err', f'重连失败: {e2}，2 秒后再试'))
                        if self.poll_stop.wait(2.0):
                            return
                        continue
            dt = interval - (time.perf_counter() - t0)
            if dt > 0 and self.poll_stop.wait(dt):
                return

    @staticmethod
    def _decode(regs: list[int], cfg: dict) -> int:
        if len(regs) == 1:
            raw = regs[0]
        else:
            hi, lo = (0, 1) if cfg['order'] == '高字在前' else (1, 0)
            raw = (regs[hi] << 16) | regs[lo]
        if cfg['signed'] and raw >= (1 << (16 * len(regs) - 1)):
            raw -= 1 << (16 * len(regs))
        return raw

    # ---------------- 链路回调（工作线程里执行，只能塞队列） ----------------

    def _on_tx(self, data: bytes, tag: str) -> None:
        self.q.put(('tx', data, tag))

    def _on_rx(self, data: bytes, tag: str, ms: float) -> None:
        self.q.put(('rx', data, tag, ms))

    # ---------------- 队列处理（主线程） ----------------

    def _drain(self) -> None:
        try:
            for _ in range(300):
                ev = self.q.get_nowait()
                self._handle_event(ev)
        except queue.Empty:
            pass
        self.after(30, self._drain)

    def _handle_event(self, ev: tuple) -> None:
        kind = ev[0]
        if kind == 'sample':
            _, t, value, raw = ev
            self._on_sample(t, value, raw)
        elif kind == 'poll_err':
            _, text, n = ev
            self._log('err', f'读取失败(第{n}次): {text}')
        elif kind == 'log':
            _, k, text = ev
            self._log(k, text)
        elif kind in ('tx', 'rx'):
            if not self.asst_poll_traffic_var.get() and ev[2] == 'poll':
                return
            if kind == 'tx':
                _, data, tag = ev
                self._log('tx', f'{fmt_hex(data)}   [{tag}]')
            else:
                _, data, tag, ms = ev
                self._log('rx', f'{fmt_hex(data)}   ({ms:.0f}ms) [{tag}]')
        elif kind == 'status':
            _, text, color = ev
            self.set_status(text, color)

    def _on_sample(self, t: float, value: float, raw: int) -> None:
        self.n_samples += 1
        self.sample_times.append(t)
        self.chart.add(t, value)
        self._dirty = True

        unit = self.physunit_var.get() or ''
        self.value_label.configure(text=f'{value:10.3f}', fg=C_TEXT)
        self.unit_label.configure(text=f' {unit}')
        self.raw_label.configure(text=f'原始值: {raw}')

        if len(self.sample_times) >= 2:
            span = self.sample_times[-1] - self.sample_times[0]
            rate = (len(self.sample_times) - 1) / span if span > 0 else 0.0
            self.rate_label.configure(text=f'采样率: {rate:.1f} Hz')

        if self.peak is None or value > self.peak:
            self.peak = value
            self.chart.peak = value
            self.peak_label.configure(text=f'峰值: {self.peak:.3f}')

        if self.rec_file is not None:
            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
            self.rec_file.write(f'{ts},{t:.3f},{value:.4f},{raw}\n')
            self.rec_file.flush()

        self.info_label.configure(text=f'采样 {self.n_samples} 点')

    def _tick_chart(self) -> None:
        self.chart.window_s = float(self.chartwin_var.get().split()[0])
        if self._dirty:
            self._dirty = False
            self.chart.redraw()
        self.after(120, self._tick_chart)

    def reset_peak(self) -> None:
        self.peak = None
        self.chart.peak = None
        self.peak_label.configure(text='峰值: --')
        self.chart.redraw()

    # ---------------- 记录 / 分析 ----------------

    def toggle_record(self) -> None:
        if self.rec_file is None:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            path = PROJECT_DIR / f'force_{ts}.csv'
            try:
                self.rec_file = open(path, 'w', newline='', encoding='utf-8-sig')
            except OSError as e:
                messagebox.showerror('打不开文件', str(e))
                return
            unit = (self.physunit_var.get() or 'N').replace(' ', '')
            self.rec_file.write(f'datetime,time_s,force_{unit},raw\n')
            self.rec_path = path
            self.rec_t0 = time.perf_counter()
            self.btn_rec.configure(text='■ 停止记录')
            self.rec_label.configure(text=f'记录中 → {path.name}', foreground=C_OK)
            self._log('info', f'开始记录: {path}')
        else:
            self.rec_file.close()
            self.rec_file = None
            self.btn_rec.configure(text='● 开始记录')
            self.rec_label.configure(text=f'已保存 {self.rec_path.name if self.rec_path else ""}',
                                     foreground=C_MUTED)
            self._log('info', f'记录结束: {self.rec_path}')

    def open_data_dir(self) -> None:
        os.startfile(str(PROJECT_DIR))  # noqa: S606 - Windows 本地工具

    def run_analysis(self) -> None:
        csv_path = self.rec_path if self.rec_path and self.rec_path.exists() else None
        if csv_path is None:
            chosen = filedialog.askopenfilename(title='选择要分析的 CSV',
                                                initialdir=str(PROJECT_DIR),
                                                filetypes=[('CSV 文件', '*.csv'), ('所有文件', '*.*')])
            if not chosen:
                return
            csv_path = Path(chosen)
        if not FORCE_FILTER.exists():
            messagebox.showerror('找不到分析脚本', f'{FORCE_FILTER}\n请检查路径或改文件顶部的 FORCE_FILTER 常量。')
            return
        exe = self._find_sci_python()
        if exe is None:
            messagebox.showerror('缺分析环境',
                                 '没找到带 numpy/pandas/matplotlib 的 Python。\n'
                                 '用 Anaconda 的 python 跑一次即可，或先装这三个库。')
            return
        try:
            subprocess.Popen([exe, str(FORCE_FILTER), str(csv_path)],
                             cwd=str(FORCE_FILTER.parent))
        except OSError as e:
            messagebox.showerror('启动失败', str(e))
            return
        self._log('info', f'已启动 force_filter 分析: {csv_path.name}')
        messagebox.showinfo('分析已启动', f'已用 force_filter 打开：\n{csv_path.name}\n\n'
                                          '会弹出 4 联图窗口（原始力 / FDSS / 滑动平均 / 卡尔曼）。')

    @staticmethod
    def _find_sci_python() -> str | None:
        candidates = [shutil.which('python'), shutil.which('py')]
        if not IS_FROZEN:
            candidates.insert(0, sys.executable)
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
        for exe in dict.fromkeys(c for c in candidates if c):
            try:
                r = subprocess.run([exe, '-c', 'import numpy,pandas,matplotlib'],
                                   capture_output=True, timeout=30, creationflags=flags)
                if r.returncode == 0:
                    return exe
            except Exception:
                continue
        return None

    # ---------------- 串口助手 ----------------

    def asst_clear(self) -> None:
        self.asst_text.configure(state='normal')
        self.asst_text.delete('1.0', 'end')
        self.asst_text.configure(state='disabled')

    def _current_mode(self) -> str:
        return self.mode_var.get()

    def _build_frame(self, unit: int, func: int, addr: int, val: int) -> bytes:
        if func in (3, 4):
            pdu = mb.build_read_pdu(func, addr, val)
        elif func == 6:
            pdu = mb.build_write_single_pdu(addr, val)
        else:
            raise ValueError(f'不支持的功能码 {func}')
        if self._current_mode() == 'tcp':
            return mb.build_mbap(pdu, 0, unit)
        return mb.build_rtu(unit, pdu)

    def gen_frame(self, send: bool = False) -> None:
        try:
            unit = parse_int(self.gen_unit_var.get())
            func = int(self.gen_func_var.get().split()[0], 16)
            addr = parse_int(self.gen_addr_var.get())
            val = parse_int(self.gen_val_var.get())
            frame = self._build_frame(unit, func, addr, val)
        except (ValueError, KeyError) as e:
            messagebox.showerror('生成失败', f'参数不对：{e}')
            return
        self.asst_send_var.set(fmt_hex(frame))
        if send:
            self.asst_send()

    def preset_fill(self) -> None:
        name = self.preset_var.get()
        for label, hexstr in PRESET_FRAMES:
            if label == name:
                data = bytes.fromhex(hexstr.replace(' ', ''))
                if self._current_mode() == 'tcp':
                    data = mb.build_mbap(data[1:-2], 0, data[0])
                self.asst_send_var.set(fmt_hex(data))
                return

    def preset_send(self) -> None:
        self.preset_fill()
        self.asst_send()

    def asst_send(self) -> None:
        if self.link is None or not self.link.is_open:
            self._log('err', '没连接：先点上面的「连接」')
            return
        text = self.asst_send_var.get().strip()
        if not text:
            return
        if self.asst_hex_var.get():
            try:
                data = bytes.fromhex(text.replace(' ', '').replace(',', ''))
            except ValueError:
                self._log('err', f'HEX 格式不对: {text}')
                return
        else:
            data = text.encode('utf-8')
        threading.Thread(target=self._send_worker, args=(data,), daemon=True).start()

    def _send_worker(self, data: bytes) -> None:
        try:
            resp = self.link.send_raw(data, tag='manual')
        except mb.LinkError as e:
            self.q.put(('log', 'err', f'发送失败: {e}'))
            return
        if not resp:
            self.q.put(('log', 'info', '发送完成，无响应（超时）'))

    def toggle_timer(self) -> None:
        if getattr(self, '_timer_on', False):
            self._timer_on = False
            self.btn_timer.configure(text='开始定时发送')
            self._log('info', '定时发送已停止')
            return
        if not self.asst_send_var.get().strip():
            messagebox.showwarning('没内容', '发送框是空的')
            return
        self._timer_on = True
        self.btn_timer.configure(text='停止定时发送')
        interval = int(self.asst_timer_var.get().split()[0])
        self._log('info', f'定时发送开始，间隔 {interval}ms')
        self._timer_tick(interval)

    def _timer_tick(self, interval: int) -> None:
        if not getattr(self, '_timer_on', False):
            return
        self.asst_send()
        self.after(interval, lambda: self._timer_tick(interval))

    # ---------------- 关闭 ----------------

    def on_close(self) -> None:
        self._timer_on = False
        self.pause_poll()
        if self.rec_file is not None:
            try:
                self.rec_file.close()
            except OSError:
                pass
        if self.link is not None:
            self.link.close()
        self.destroy()


def main() -> None:
    """--demo：一键演示 —— 自动起一个模拟从站并连上，无硬件也能看完整效果。"""
    enable_dpi()
    demo = '--demo' in sys.argv
    slave = None
    if demo:
        sys.path.insert(0, str(PROJECT_DIR / 'tools'))
        from mock_slave import MockSlave
        slave = MockSlave(port=0, signal='puncture', verbose=False).start()

    app = App()
    if demo:
        app.mode_var.set('tcp')
        app._on_mode_change()
        app.host_var.set('127.0.0.1')
        app.tcp_port_var.set(str(slave.port))
        app.scale_var.set('100')
        app.physunit_var.set('N')
        app.interval_var.set('100 ms')
        app.after(300, app.do_connect)
        app.after(600, app.start_poll)

        def close_demo() -> None:
            if slave is not None:
                slave.stop()
            app.on_close()

        app.protocol('WM_DELETE_WINDOW', close_demo)
    app.mainloop()


if __name__ == '__main__':
    main()
