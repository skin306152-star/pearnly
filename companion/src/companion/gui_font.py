# -*- coding: utf-8 -*-
"""小助手的 Qt 应用对象（全进程一个），顺带修正泰文 Windows 上的界面字体。

泰文 Windows 的系统界面字体是 Leelawadee UI，Qt 默认就拿它当界面字体；锁定的 Qt 6.11 用这个字体画泰文时
字形错乱（2026-10-08 用户截图；测试机上用同版本 Qt 复现：Leelawadee UI 深浅色、100% / 150% 缩放都乱，
Segoe UI 与 Tahoma 都正常）。所以默认字体是 Leelawadee 时换成 Segoe UI（字号沿用系统设置），泰文由系统
字体回退补字；中文、英文等其它 Windows 不受影响。
"""

from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

BROKEN_UI_FONTS = frozenset({"leelawadee ui", "leelawadee"})
REPLACEMENT_UI_FONT = "Segoe UI"


def fix_ui_font(app: QApplication) -> bool:
    """默认界面字体是 Leelawadee 时换成 Segoe UI；返回是否换了。"""
    current = app.font()
    if current.family().strip().casefold() not in BROKEN_UI_FONTS:
        return False
    replacement = QFont(current)
    replacement.setFamilies([REPLACEMENT_UI_FONT])
    app.setFont(replacement)
    return True


def qt_app() -> QApplication:
    """已有就用已有的，没有就建一个；两种情况都核对一次界面字体。"""
    app = QApplication.instance() or QApplication([])
    fix_ui_font(app)
    return app
