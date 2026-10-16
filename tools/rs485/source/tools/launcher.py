# -*- coding: utf-8 -*-
"""
RS485 工具箱 —— 按钮式启动器（替代控制台菜单，双击桌面快捷方式即用）

开发模式：用 .venv 的 pythonw 启动脚本；需要看输出的工具单独开控制台窗口。
打包模式（PyInstaller onefile）：主按钮启动同目录的 RS485上位机.exe，
自检类按钮换成「检查串口」；接线说明和使用说明全部内嵌，不依赖外部文件。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

# 打包成 exe 后 __file__ 在临时解包目录：资源用 RESOURCE_DIR，数据/脚本用 exe 旁边。
IS_FROZEN = bool(getattr(sys, 'frozen', False))
if IS_FROZEN:
    RESOURCE_DIR = Path(getattr(sys, '_MEIPASS', Path(sys.executable).resolve().parent))
    PROJECT_DIR = Path(sys.executable).resolve().parent
else:
    RESOURCE_DIR = Path(__file__).resolve().parent.parent
    PROJECT_DIR = RESOURCE_DIR

sys.path.insert(0, str(RESOURCE_DIR))
import wiring_info  # noqa: E402 - 必须先补 sys.path

ROOT = PROJECT_DIR  # 兼容旧名字；打包后就是 exe 所在目录
PY = ROOT / '.venv' / 'Scripts' / 'python.exe'
PYW = ROOT / '.venv' / 'Scripts' / 'pythonw.exe'
GUI_EXE = PROJECT_DIR / 'RS485上位机.exe'
GUI_SCRIPT = RESOURCE_DIR / '上位机_GUI.py'

C_BG = '#eef1f5'
C_PANEL = '#ffffff'
C_ACCENT = '#1565c0'
C_TEXT = '#1f2937'
C_MUTED = '#64748b'
FONT_UI = ('Microsoft YaHei UI', 10)
FONT_TITLE = ('Microsoft YaHei UI', 15, 'bold')
FONT_BTN = ('Microsoft YaHei UI', 11)

NEW_CONSOLE = getattr(subprocess, 'CREATE_NEW_CONSOLE', 0) if os.name == 'nt' else 0
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0


def enable_dpi() -> None:
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


class Launcher(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title('RS485 工具箱')
        self.resizable(False, False)
        self.configure(bg=C_BG)
        self._status = tk.StringVar(value='就绪')
        self._wiring_win: tk.Toplevel | None = None
        self._guide_win: tk.Toplevel | None = None
        self._build()
        self.update_idletasks()
        self.geometry(f'560x{self.winfo_reqheight()}')  # 按内容自动定高，防止按钮被裁

    # ---------- 界面 ----------

    def _build(self) -> None:
        head = tk.Frame(self, bg=C_PANEL)
        head.pack(fill='x')
        tk.Label(head, text='RS485 称重 / 测力工具', font=FONT_TITLE,
                 bg=C_PANEL, fg=C_TEXT).pack(anchor='w', padx=18, pady=(14, 0))
        tk.Label(head, text='无硬件也能玩：演示模式自带模拟变送器（穿刺曲线）',
                 font=FONT_UI, bg=C_PANEL, fg=C_MUTED).pack(anchor='w', padx=18, pady=(2, 14))

        body = tk.Frame(self, bg=C_BG)
        body.pack(fill='both', expand=True, padx=16, pady=12)

        self._big_btn(body, '打开上位机（推荐）', self.open_gui, primary=True)
        self._big_btn(body, '无硬件演示模式', self.open_demo)
        self._big_btn(body, '接线说明（不用额外文件）', self.show_wiring)

        if IS_FROZEN:
            self._section(body, '串口检查')
            self._big_btn(body, '检查串口（COM 口列表）', self.check_ports)
        else:
            self._section(body, '调试 / 自检')
            row = tk.Frame(body, bg=C_BG)
            row.pack(fill='x', pady=2)
            self._half_btn(row, '协议自检', lambda: self.run_console('tools/loopback_test.py'), 'left')
            self._half_btn(row, '环境自检', lambda: self.run_console('检查环境.py'), 'left')
            row2 = tk.Frame(body, bg=C_BG)
            row2.pack(fill='x', pady=2)
            self._half_btn(row2, '模拟从站 (TCP 5020)', lambda: self.run_console('tools/mock_slave.py'), 'left')
            self._half_btn(row2, '无界面采集器', lambda: self.run_console('tools/collect_demo.py'), 'left')

        self._section(body, '数据 / 帮助')
        row3 = tk.Frame(body, bg=C_BG)
        row3.pack(fill='x', pady=2)
        self._half_btn(row3, '打开数据目录', self.open_data_dir, 'left')
        self._half_btn(row3, '使用说明', self.open_guide, 'left')

        bar = tk.Frame(self, bg='#dde3ea')
        bar.pack(fill='x', side='bottom')
        tk.Label(bar, textvariable=self._status, bg='#dde3ea', fg=C_MUTED,
                 font=FONT_UI, anchor='w').pack(fill='x', padx=12, pady=5)
        self._big_btn(body, '退出', self.destroy)

    def _section(self, parent, text: str) -> None:
        tk.Label(parent, text=text, bg=C_BG, fg=C_MUTED,
                 font=('Microsoft YaHei UI', 9)).pack(anchor='w', pady=(12, 2))

    def _big_btn(self, parent, text: str, cmd, primary: bool = False) -> None:
        btn = tk.Button(parent, text=text, font=FONT_BTN, command=cmd,
                        bg=C_ACCENT if primary else C_PANEL,
                        fg='white' if primary else C_TEXT,
                        activebackground='#0d47a1' if primary else '#e2e8f0',
                        activeforeground='white' if primary else C_TEXT,
                        relief='flat', cursor='hand2', pady=9)
        btn.pack(fill='x', pady=3)

    def _half_btn(self, parent, text: str, cmd, side: str) -> None:
        btn = tk.Button(parent, text=text, font=FONT_UI, command=cmd,
                        bg=C_PANEL, fg=C_TEXT, activebackground='#e2e8f0',
                        relief='flat', cursor='hand2', pady=7)
        btn.pack(side=side, fill='x', expand=True, padx=2)

    # ---------- 动作 ----------

    def _popen(self, exe: Path, args: list[str], flags: int, what: str) -> None:
        if not exe.exists():
            self._status.set(f'找不到 {exe}')
            return
        try:
            subprocess.Popen([str(exe), *args], cwd=str(PROJECT_DIR), creationflags=flags)
        except OSError as e:
            self._status.set(f'启动失败: {e}')
            return
        self._status.set(f'已启动: {what}')

    def open_gui(self) -> None:
        if IS_FROZEN:
            self._popen(GUI_EXE, [], 0, '上位机')
        else:
            self._popen(PYW, [str(GUI_SCRIPT)], 0, '上位机')

    def open_demo(self) -> None:
        if IS_FROZEN:
            self._popen(GUI_EXE, ['--demo'], 0, '演示模式')
        else:
            self._popen(PYW, [str(GUI_SCRIPT), '--demo'], 0, '演示模式')

    def run_console(self, rel_script: str) -> None:
        if IS_FROZEN:
            messagebox.showinfo(
                '打包版不支持',
                f'「{rel_script}」需要开发环境的 Python 运行。\n\n'
                '想看效果请用「无硬件演示模式」；想排查接线请用「检查串口」。')
            return
        self._popen(PY, [str(PROJECT_DIR / rel_script)], NEW_CONSOLE, rel_script)

    def check_ports(self) -> None:
        """打包版的自检入口：列出 COM 口和驱动信息。"""
        try:
            from serial.tools import list_ports
            ports = list(list_ports.comports())
        except Exception as e:
            messagebox.showerror('检查失败', f'读取串口列表出错：{e}')
            self._status.set('检查串口失败')
            return
        if not ports:
            messagebox.showinfo(
                '串口列表',
                '没有发现串口。\n\n'
                '· USB转485 没插好或没装驱动（CH340 / CP2102）\n'
                '· 装好驱动后，在「设备管理器 → 端口(COM 和 LPT)」里能看到 COM 号\n'
                '· 没硬件也能用：点「无硬件演示模式」先看效果')
            self._status.set('没有发现串口')
            return
        detail = '\n\n'.join(f'{p.device}    {p.description}\n    {p.hwid}' for p in ports)
        messagebox.showinfo(f'串口列表（共 {len(ports)} 个）',
                            detail + '\n\n提示：串口是独占的，同一端口一次只能被一个程序打开。')
        self._status.set('发现串口: ' + ', '.join(p.device for p in ports))

    def show_wiring(self) -> None:
        if self._wiring_win is not None and self._wiring_win.winfo_exists():
            self._wiring_win.lift()
            return
        self._wiring_win = wiring_info.show_wiring_window(self, on_open_guide=self.open_guide)
        self._status.set('已打开接线说明（内置，不依赖外部文件）')

    def open_guide(self) -> None:
        """内置 md 阅读器窗口；找不到 md 时降级打开接线说明，不弹「找不到」。"""
        guide = wiring_info.find_guide()
        if guide is None:
            self._status.set('没找到指南文件，改看内置接线说明')
            self.show_wiring()
            return
        try:
            content = guide.read_text(encoding='utf-8')
        except OSError:
            self._status.set('指南读取失败，改看内置接线说明')
            self.show_wiring()
            return

        if self._guide_win is not None and self._guide_win.winfo_exists():
            self._guide_win.lift()
        else:
            win = tk.Toplevel(self)
            self._guide_win = win
            win.title('使用说明 —— 接线与调试指南')
            win.geometry('880x680')
            win.minsize(640, 420)
            win.configure(bg=C_BG)

            bar = tk.Frame(win, bg=C_BG)
            bar.pack(fill='x', padx=10, pady=(8, 4))
            tk.Label(bar, text=f'来源：{guide}', bg=C_BG, fg=C_MUTED,
                     font=('Microsoft YaHei UI', 9), anchor='w').pack(side='left')
            ttk.Button(bar, text='关闭', command=win.destroy).pack(side='right')
            ttk.Button(bar, text='用系统程序打开',
                       command=lambda: wiring_info.open_guide_file(guide)).pack(
                side='right', padx=(0, 6))

            box = tk.Frame(win, bg=C_BG)
            box.pack(fill='both', expand=True, padx=10, pady=(0, 10))
            text = tk.Text(box, wrap='word', bg=C_PANEL, fg=C_TEXT, font=FONT_UI,
                           relief='flat', padx=12, pady=10)
            sb = ttk.Scrollbar(box, command=text.yview)
            text.configure(yscrollcommand=sb.set)
            sb.pack(side='right', fill='y')
            text.pack(side='left', fill='both', expand=True)
            text.insert('1.0', content)
            text.configure(state='disabled')
        self._guide_win.lift()
        self._status.set('已打开使用说明（内置阅读器）')

    def open_data_dir(self) -> None:
        os.startfile(str(PROJECT_DIR))  # noqa: S606
        self._status.set('已打开数据目录')


def selftest() -> int:
    """无交互自检：把内置接线窗口和说明阅读器各创建一遍，报告写到 exe 旁边。"""
    enable_dpi()
    app = Launcher()
    app.withdraw()
    app.update()
    app.show_wiring()
    app.update()
    app.open_guide()
    app.update()
    shown: list = []
    orig_showinfo = messagebox.showinfo
    messagebox.showinfo = lambda *a, **k: shown.append(a)
    try:
        app.check_ports()
    finally:
        messagebox.showinfo = orig_showinfo
    guide = wiring_info.find_guide()
    lines = [
        'SELFTEST OK',
        f'frozen={IS_FROZEN}',
        f'script_dir={PROJECT_DIR}',
        f'guide={"found: " + str(guide) if guide else "not found (fallback to wiring)"}',
        f'wiring_window={"open" if app._wiring_win and app._wiring_win.winfo_exists() else "FAILED"}',
        f'guide_window={"open" if app._guide_win and app._guide_win.winfo_exists() else "none"}',
        f'ports_dialog={"shown" if shown else "no showinfo called"}',
    ]
    try:
        (PROJECT_DIR / 'selftest_report.txt').write_text(
            '\n'.join(lines) + '\n', encoding='utf-8')
    except OSError:
        pass
    app.destroy()
    return 0


def main() -> None:
    enable_dpi()
    if '--selftest' in sys.argv:
        sys.exit(selftest())
    Launcher().mainloop()


if __name__ == '__main__':
    main()
