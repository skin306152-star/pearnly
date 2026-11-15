# -*- coding: utf-8 -*-
"""PyInstaller 入口 · 云端精简包（companion-cloud.spec）：这一份只走云端模式 ``--cloud``（runtime_mode.py）。

精简包不带 PySide6 / 网页内核 / Windows 通知，托盘、CC 窗口（``--window``）、MR.ERP 网页窗口（``--mrerp-desk``）都起不来，
所以不管怎么启动都按 ``--cloud`` 跑；要那两个窗口进程的参数直接以 2 退出（同没有成员）。程序名仍是 companion.exe（桥用同一个名字启动）。
"""
import sys

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--window" in args or "--mrerp-desk" in args:
        sys.exit(2)
    if "--cloud" not in args:
        sys.argv.insert(1, "--cloud")
    from src.companion.main import main

    sys.exit(main())
