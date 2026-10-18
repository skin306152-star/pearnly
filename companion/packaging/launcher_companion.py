# -*- coding: utf-8 -*-
"""PyInstaller 入口 · 64 位主程序(顶层 import 包 → 避开相对 import 打包问题)。"""
import sys

if __name__ == "__main__":
    if "--window" in sys.argv[1:]:
        # CC 窗口进程(托盘拉起,window_main.py):直接进窗口,不导入取任务 / 直写那一套(启动快、占内存少)
        from src.companion.window_main import run

        sys.exit(run())
    from src.companion.main import main

    sys.exit(main())
