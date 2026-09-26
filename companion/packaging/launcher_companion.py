# -*- coding: utf-8 -*-
"""PyInstaller 入口 · 64 位主程序(顶层 import 包 → 避开相对 import 打包问题)。"""
import sys

from src.companion.main import main

if __name__ == "__main__":
    sys.exit(main())
