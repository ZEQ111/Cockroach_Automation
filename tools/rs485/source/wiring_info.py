# -*- coding: utf-8 -*-
"""
接线说明共享模块 —— 上位机接线标签页 / 工具箱弹窗共用。
内容全部内嵌（示意图 Canvas + 四张表），不依赖外部 md 文件；
find_guide() 提供「exe 旁边优先、打包内置兜底」的指南查找。

被 上位机_GUI.py 和 tools/launcher.py 共同 import，不反向依赖它们，避免循环导入。
"""

from __future__ import annotations

import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

IS_FROZEN = bool(getattr(sys, 'frozen', False))
if IS_FROZEN:
    RESOURCE_DIR = Path(getattr(sys, '_MEIPASS', Path(sys.executable).resolve().parent))
    PROJECT_DIR = Path(sys.executable).resolve().parent
else:
    RESOURCE_DIR = Path(__file__).resolve().parent
    PROJECT_DIR = RESOURCE_DIR

GUIDE_NAME = '接线与调试指南.md'

C_BG = '#eef1f5'
C_PANEL = '#ffffff'
C_ACCENT = '#1565c0'
C_OK = '#2e7d32'
C_WARN = '#ef6c00'
C_ERR = '#c62828'
C_MUTED = '#64748b'
C_TEXT = '#1f2937'
FONT_UI = ('Microsoft YaHei UI', 10)
FONT_SM = ('Microsoft YaHei UI', 9)


def find_guide() -> Path | None:
    """指南优先用 exe 旁边的（方便随包更新），其次用打包内置的。"""
    for base in (PROJECT_DIR, RESOURCE_DIR):
        path = base / GUIDE_NAME
        if path.exists():
            return path
    return None


def open_guide_file(path: Path) -> bool:
    """用系统默认程序打开指南，成功返回 True。"""
    try:
        os.startfile(str(path))  # noqa: S606 - Windows 本地工具
        return True
    except OSError:
        return False


# ==================== 接线示意图控件 ====================

class WiringDiagram(tk.Canvas):
    """接线示意图：称重传感器 → 变送器 → USB转485 → 电脑。随窗口宽度自适应重画。"""

    def __init__(self, master, height: int = 248):
        super().__init__(master, bg=C_PANEL, height=height,
                         highlightthickness=1, highlightbackground='#d5d9e0')
        self.bind('<Configure>', lambda _e: self.redraw())
        self.after(60, self.redraw)

    def redraw(self) -> None:
        self.delete('all')
        w, h = self.winfo_width(), self.winfo_height()
        if w < 360 or h < 140:
            return
        margin, gap = 14, 66
        bw = (w - 2 * margin - 3 * gap) / 4
        y0, y1 = 16, h - 118
        xs = [margin + i * (bw + gap) for i in range(4)]

        def box(i: int, title: str, lines: list[str], color: str, y_a: float, y_b: float) -> None:
            x0, x1 = xs[i], xs[i] + bw
            self.create_rectangle(x0, y_a, x1, y_b, outline=color, width=1.6, fill='#f8fafc')
            self.create_rectangle(x0, y_a, x1, y_a + 26, outline=color, width=1.6, fill=color)
            self.create_text((x0 + x1) / 2, y_a + 13, text=title, fill='white',
                             font=('Microsoft YaHei UI', 9, 'bold'))
            step = min(17.0, (y_b - y_a - 34) / max(1, len(lines)))
            for j, line in enumerate(lines):
                self.create_text((x0 + x1) / 2, y_a + 34 + j * step, text=line,
                                 fill=C_TEXT, font=('Microsoft YaHei UI', 8))

        parts = [
            ('称重传感器', ['电阻应变式 · 4线制', '红 E+   黑 E-', '绿 S+   白 S-'], C_ACCENT),
            ('称重变送器', ['24V 供电', '内部输出 5V 激励', 'Modbus RTU 从站'], C_OK),
            ('USB转485', ['A+ / B-  接线端子', 'CH340 / CP2102', '插电脑 USB 口'], C_WARN),
            ('电脑 · 上位机', ['自动扫描 COM 口', '实时曲线 / CSV', '内置串口助手'], C_MUTED),
        ]
        for i, (title, lines, color) in enumerate(parts):
            box(i, title, lines, color, y0, y1)

        def arrow(i: int, label: str, color: str = C_MUTED, sub: str = '') -> None:
            xa, xb = xs[i] + bw, xs[i + 1]
            y = (y0 + y1) / 2 + 4
            self.create_line(xa + 3, y, xb - 3, y, arrow='last', fill=color, width=1.5)
            self.create_text((xa + xb) / 2, y - 12, text=label,
                             fill=color, font=('Microsoft YaHei UI', 8, 'bold'))
            if sub:
                self.create_text((xa + xb) / 2, y + 12, text=sub,
                                 fill=color, font=('Microsoft YaHei UI', 7))

        arrow(0, '4线制', C_ACCENT, 'E+ E- S+ S-')
        arrow(1, 'RS485', C_WARN, 'A+→A+  B-→B-')
        arrow(2, 'USB', C_MUTED, '虚拟串口')

        # 24V 电源（接变送器）
        py0, py1 = y1 + 34, h - 22
        px0, px1 = xs[1], xs[1] + bw
        cx = (px0 + px1) / 2
        self.create_rectangle(px0, py0, px1, py1, outline=C_ERR, width=1.5, fill='#fff5f5')
        self.create_text(cx, py0 + 14, text='24V 直流电源适配器', fill=C_ERR,
                         font=('Microsoft YaHei UI', 9, 'bold'))
        self.create_text(cx, py0 + 32, text='24V+ → 24V+    24V- → 24V-', fill=C_TEXT,
                         font=('Microsoft YaHei UI', 8))
        self.create_line(cx, py0 - 3, cx, y1 + 2, arrow='last', fill=C_ERR, width=1.5)

        self.create_text(w - margin, py0 + 14, anchor='e',
                         text='⚠ A/B 接反 = 完全不通讯（头号故障）',
                         fill=C_ERR, font=('Microsoft YaHei UI', 8, 'bold'))
        self.create_text(w - margin, py0 + 32, anchor='e',
                         text='⚠ 通电前确认电源正负极没接反（可能烧变送器）',
                         fill=C_ERR, font=('Microsoft YaHei UI', 8))


# ==================== 接线表格 ====================

WIRE_TABLES = [
    ('供电（先接这个）', [
        ('24V+', '→ 变送器 24V+'),
        ('24V-', '→ 变送器 24V-'),
        ('注意', '变送器内部已输出 5V 激励给传感器，传感器不用另供电'),
        ('注意', '通电前务必确认正负极没接反，可能烧变送器'),
    ]),
    ('传感器 → 变送器（四线制）', [
        ('红', '→ E+（激励+）'),
        ('黑', '→ E-（激励-）'),
        ('绿', '→ S+（信号+）'),
        ('白', '→ S-（信号-）'),
        ('黄/灰（屏蔽）', '→ 变送器金属外壳接地螺丝'),
        ('注意', '线色不是行业标准，务必以传感器说明书为准'),
    ]),
    ('变送器 → USB转485', [
        ('A+', '→ A+'),
        ('B-', '→ B-'),
        ('注意', 'A/B 接反是完全不通讯的头号原因；参数都对仍无回复，先对调 A/B'),
        ('注意', 'USB转485 要装驱动（CH340 / CP2102），设备管理器里能看到 COM 口'),
    ]),
    ('通讯参数 / 调试顺序', [
        ('协议', 'Modbus RTU，8-N-1'),
        ('波特率', '先试 9600，再试 19200 / 4800'),
        ('从站地址', '出厂常为 1；原仓库代码用 111'),
        ('调试顺序', '先用「串口助手」标签页发 01 03 00 00 00 01 84 0A，'
                     '看到有数据返回（链路通了）再点「连接」「开始采集」'),
    ]),
]


def _wire_table(parent, title: str, rows: list[tuple[str, str]],
                row: int, col: int) -> None:
    lf = ttk.LabelFrame(parent, text=title, padding=(10, 6))
    lf.grid(row=row, column=col, sticky='nsew',
            padx=(0 if col == 0 else 6, 0), pady=(0, 6))
    for i, (a, b) in enumerate(rows):
        warn = a == '注意'
        tk.Label(lf, text='⚠' if warn else a, bg=C_BG,
                 fg=C_ERR if warn else C_ACCENT,
                 font=FONT_SM, anchor='nw').grid(
            row=i, column=0, sticky='nw', pady=1)
        tk.Label(lf, text=b, bg=C_BG, fg=C_ERR if warn else C_TEXT,
                 font=FONT_SM, anchor='nw',
                 wraplength=430, justify='left').grid(
            row=i, column=1, sticky='nw', padx=(8, 0), pady=1)
    lf.columnconfigure(1, weight=1)


# ==================== 对外接口 ====================

def build_wiring_panel(parent, on_open_guide=None, diagram_height: int = 232) -> tk.Frame:
    """可滚动接线面板：示意图 + 四张表，pack 进 parent。

    on_open_guide 非空时底部会显示「打开完整《接线与调试指南》」按钮。
    返回外层 Frame；面板完全由内嵌数据构建，不读任何外部文件。
    """
    outer = ttk.Frame(parent, padding=(4, 8))
    outer.pack(fill='both', expand=True)

    vsb = ttk.Scrollbar(outer, orient='vertical')
    vsb.pack(side='right', fill='y')
    scroll = tk.Canvas(outer, bg=C_BG, highlightthickness=0, yscrollcommand=vsb.set)
    scroll.pack(side='left', fill='both', expand=True)
    vsb.configure(command=scroll.yview)

    inner = ttk.Frame(scroll)
    inner_id = scroll.create_window((0, 0), window=inner, anchor='nw')
    inner.bind('<Configure>',
               lambda _e: scroll.configure(scrollregion=scroll.bbox('all')))
    scroll.bind('<Configure>',
                lambda e: scroll.itemconfigure(inner_id, width=e.width))
    scroll.bind('<Enter>', lambda _e: scroll.bind_all(
        '<MouseWheel>',
        lambda ev: scroll.yview_scroll(int(-ev.delta / 120), 'units')))
    scroll.bind('<Leave>', lambda _e: scroll.unbind_all('<MouseWheel>'))

    diagram = WiringDiagram(inner, height=diagram_height)
    diagram.pack(fill='x')

    cols = ttk.Frame(inner)
    cols.pack(fill='x', pady=(8, 0))
    cols.columnconfigure(0, weight=1, uniform='wire')
    cols.columnconfigure(1, weight=1, uniform='wire')
    for i, (title, rows) in enumerate(WIRE_TABLES):
        _wire_table(cols, title, rows, row=i // 2, col=i % 2)

    bottom = ttk.Frame(inner)
    bottom.pack(fill='x', pady=(10, 4))
    if on_open_guide is not None:
        ttk.Button(bottom, text='打开完整《接线与调试指南》',
                   command=on_open_guide).pack(side='left')
    ttk.Label(bottom, text='提示：串口是独占的，开着上位机时 SSCOM 打不开同一个 COM 口',
              foreground=C_MUTED, font=FONT_SM).pack(
        side='left', padx=(12, 0))
    return outer


def show_wiring_window(parent, on_open_guide=None) -> tk.Toplevel:
    """接线说明弹窗（Toplevel 版），给工具箱等入口用；不依赖外部 md。"""
    win = tk.Toplevel(parent)
    win.title('接线说明 —— RS485 称重 / 测力')
    win.geometry('900x680')
    win.minsize(720, 500)
    win.configure(bg=C_BG)
    build_wiring_panel(win, on_open_guide=on_open_guide, diagram_height=248)
    win.lift()
    win.focus_force()
    return win
