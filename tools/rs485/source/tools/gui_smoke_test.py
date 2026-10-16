# -*- coding: utf-8 -*-
"""
上位机 GUI 冒烟测试 —— 用 MockSlave(TCP) 当数据源，自动把界面点一遍。

覆盖：连接 -> 开始记录 -> 采集 -> 暂停 -> 峰值/曲线/CSV 检查 ->
      串口助手（预设帧 / 帧生成器 / 手动发送）-> 断开。

运行：.venv\\Scripts\\python.exe tools\\gui_smoke_test.py
会短暂弹出上位机窗口，跑完自动关闭；测试用的 CSV 会在检查后删掉。
"""

import importlib
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

from mock_slave import MockSlave  # noqa: E402

import wiring_info  # noqa: E402

ok_count = 0
fail_count = 0


def check(name, cond, detail=''):
    global ok_count, fail_count
    if cond:
        ok_count += 1
        print(f'  [OK] {name} {detail}')
    else:
        fail_count += 1
        print(f'  [FAIL] {name} {detail}')


def main() -> int:
    slave = MockSlave(host='127.0.0.1', port=5021, unit=1, signal='puncture', verbose=False).start()

    gui = importlib.import_module('上位机_GUI')
    gui.enable_dpi()
    app = gui.App()
    app.withdraw()  # 测试时不显示窗口，但事件循环照跑

    app.mode_var.set('tcp')
    app._on_mode_change()
    app.host_var.set('127.0.0.1')
    app.tcp_port_var.set('5021')
    app.unit_var.set('1')
    app.addr_var.set('0')
    app.count_var.set('1')
    app.scale_var.set('100')
    app.physunit_var.set('N')
    app.interval_var.set('50 ms')
    app.chartwin_var.set('10 秒')

    def pump(seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            app.update()
            time.sleep(0.01)

    rec_path = None
    try:
        # ---------- 菜单栏 ----------
        menubar = app.nametowidget(app.cget('menu'))
        labels = [menubar.entrycget(i, 'label') for i in range(menubar.index('end') + 1)]
        check('菜单栏包含 文件/工具/帮助', labels == ['文件', '工具', '帮助'], str(labels))

        # ---------- 接线说明（wiring_info 共享模块） ----------
        tabs = [app.nb.tab(i, 'text').strip() for i in range(app.nb.index('end'))]
        check('标签页包含 接线说明', '接线说明' in tabs, str(tabs))
        check('接线面板已构建', hasattr(app, 'wiring_panel') and app.wiring_panel.winfo_exists())
        check('接线表数据内嵌四张', len(wiring_info.WIRE_TABLES) == 4,
              str([t[0] for t in wiring_info.WIRE_TABLES]))
        check('find_guide 找得到开发目录的指南', wiring_info.find_guide() is not None,
              str(wiring_info.find_guide()))
        check('顶部栏有接线说明按钮', app.btn_wiring.cget('text') == '接线说明')
        app.show_wiring()
        pump(0.05)
        check('顶部按钮跳转到接线 tab', app.nb.select() == str(app.wiring_tab))
        popup = wiring_info.show_wiring_window(app)
        pump(0.05)
        check('接线弹窗能创建', popup.winfo_exists())
        popup.destroy()

        # ---------- 连接 ----------
        app.do_connect()
        pump(0.3)
        check('TCP 连接成功', app.link is not None and app.link.is_open,
              app.link.describe() if app.link else '')

        # ---------- 记录 + 采集 ----------
        app.toggle_record()
        pump(0.2)
        rec_path = app.rec_path
        check('CSV 已创建', rec_path is not None and rec_path.exists(), str(rec_path))

        app.start_poll()
        pump(3.0)
        check('采集到样本', app.n_samples > 20, f'n={app.n_samples}')
        check('曲线有数据', len(app.chart.data) > 20, f'点数={len(app.chart.data)}')
        check('数值已刷新', app.value_label.cget('text').strip() not in ('--.--', ''),
              f'显示={app.value_label.cget("text").strip()}')
        check('采样率已估算', 'Hz' in app.rate_label.cget('text'), app.rate_label.cget('text'))
        check('峰值已记录', app.peak is not None and app.peak > 0,
              f'峰值={app.peak:.3f}' if app.peak else '')

        app.pause_poll()
        pump(0.2)
        check('暂停后线程退出', app.poll_thread is None)

        # ---------- CSV 内容 ----------
        app.toggle_record()
        pump(0.2)
        if rec_path and rec_path.exists():
            lines = rec_path.read_text(encoding='utf-8-sig').strip().splitlines()
            header = lines[0] if lines else ''
            check('CSV 表头兼容 force_filter', header == 'datetime,time_s,force_N,raw', header)
            check('CSV 有数据行', len(lines) > 21, f'{len(lines) - 1} 行')
            cols = lines[1].split(',') if len(lines) > 1 else []
            check('CSV 列数正确', len(cols) == 4, f'{cols}')
        else:
            check('CSV 文件存在', False, str(rec_path))

        # ---------- 串口助手 ----------
        app.preset_fill()
        preset_text = app.asst_send_var.get()
        check('TCP 模式下预设帧自动转 MBAP', preset_text.startswith('00 00 00 00'),
              preset_text)

        app.gen_func_var.set('03 读保持寄存器')
        app.gen_addr_var.set('0x11')
        app.gen_val_var.set('1')
        app.gen_frame(send=False)
        check('帧生成器(读寄存器17)', app.asst_send_var.get().endswith('00 11 00 01'),
              app.asst_send_var.get())

        app.gen_func_var.set('06 写单个寄存器')
        app.gen_val_var.set('1234')
        app.gen_frame(send=False)
        check('帧生成器(写单个寄存器)', app.asst_send_var.get().endswith('00 11 04 D2'),
              app.asst_send_var.get())

        app.asst_clear()
        app.preset_fill()
        app.asst_send()
        pump(0.6)
        log_text = app.asst_text.get('1.0', 'end')
        check('助手日志有 TX', 'TX →' in log_text)
        check('助手日志有 RX', 'RX ←' in log_text)

        # ---------- 断开 ----------
        app.do_disconnect()
        pump(0.2)
        check('断开成功', app.link is None)
        check('断开后状态归零', app.value_label.cget('text').strip() == '--.--')
    finally:
        try:
            app.on_close()
        except Exception:
            pass
        slave.stop()
        if rec_path and rec_path.exists():
            rec_path.unlink()

    print()
    print('=' * 60)
    print(f'  结果: {ok_count} 项通过 / {fail_count} 项失败')
    print('=' * 60)
    return 1 if fail_count else 0


if __name__ == '__main__':
    sys.exit(main())
