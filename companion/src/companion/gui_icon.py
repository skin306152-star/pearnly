# -*- coding: utf-8 -*-
"""CC 的程序图标（任务栏 / 窗口 / 通知用，与 exe 内嵌图标一致）：托盘进程与窗口进程共用，不带进别的界面模块。"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon, QPixmap


def asset_path(name: str) -> Path:
    """资源定位:frozen exe 走 PyInstaller 解包目录(_MEIPASS),否则 repo assets/。"""
    base = getattr(sys, "_MEIPASS", None)
    return (
        (Path(base) if base else Path(__file__).resolve().parents[2]) / "assets" / name
    )


_BASE_PM: QPixmap | None = None


def pearnly_pixmap() -> QPixmap:
    global _BASE_PM
    if _BASE_PM is None:
        p = asset_path("cc.png")
        _BASE_PM = QPixmap(str(p)) if p.exists() else QPixmap()
    return _BASE_PM


def pearnly_icon() -> QIcon:
    """CC 图标（带胡须原版）。"""
    return QIcon(pearnly_pixmap())
