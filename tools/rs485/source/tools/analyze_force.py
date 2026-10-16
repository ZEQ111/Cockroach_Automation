# -*- coding: utf-8 -*-
"""
无界面跑 force_filter.py（把 4 联图存成 PNG），用于自动化验证。

force_filter.py 属于「高精度力传感器选择方案」项目，本脚本不改动它，
只是把 plt.show() 换成 savefig，方便命令行批量分析。

用法（需要用带 numpy/pandas/matplotlib 的 Python，比如 Anaconda base）：
    python tools\\analyze_force.py <CSV路径> [alpha] [滑动平均窗口] [beta]

示例：
    python tools\\analyze_force.py demo_data\\force_demo.csv
    python tools\\analyze_force.py demo_data\\force_demo.csv 0.99 5
输出：
    <CSV同目录>/<CSV名>_analysis.png
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402

FORCE_FILTER = Path(r'C:\Users\Skyla\Desktop\2026国创赛\高精度力传感器选择方案\force_filter.py')


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    csv_path = Path(sys.argv[1])
    if not csv_path.is_absolute():
        csv_path = Path.cwd() / csv_path
    if not csv_path.exists():
        print(f'找不到 CSV: {csv_path}')
        return 2
    if not FORCE_FILTER.exists():
        print(f'找不到 force_filter.py: {FORCE_FILTER}')
        return 2

    out_png = csv_path.with_suffix('').with_name(csv_path.stem + '_analysis.png')

    def save_show(*_a, **_k):
        plt.savefig(out_png, dpi=130, bbox_inches='tight')
        print(f'[OK] 图已保存: {out_png}')

    plt.show = save_show
    sys.argv = ['force_filter.py', str(csv_path)] + sys.argv[2:]
    runpy.run_path(str(FORCE_FILTER), run_name='__main__')
    return 0


if __name__ == '__main__':
    sys.exit(main())
