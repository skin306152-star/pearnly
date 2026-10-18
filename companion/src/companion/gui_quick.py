# -*- coding: utf-8 -*-
"""托盘进程里的两样小界面（不带网页内核，2026-10-18 从 gui_tomo.py 挪出来）：

- 快捷输入条（Ctrl+Alt+Space，tomo_hotkey.py）：输入一句回车，交给 CC 窗口开新对话发出去；
- ``reveal``：在资源管理器里选中一个文件（对话页里下载完的文件，点通知时用）。

CC 窗口本身（网页内核）在另一个进程里，见 window_main.py。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit, QWidget

from .i18n import t


def reveal(path: Path) -> None:
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))


class Reveal:
    """通知点了在资源管理器里选中这个文件（窗口进程把路径交给托盘，托盘那边照样能做，窗口关了也行）。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def __call__(self) -> None:
        reveal(self.path)


class QuickAsk(QWidget):
    """快捷输入条：屏幕上方居中的一条圆角输入框，回车交给 Tomo 窗口，Esc 或点别处就收起。"""

    submitted = Signal(str)

    def __init__(self, *, lang: str, icon) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        frame = QFrame(self)
        frame.setObjectName("quick")
        frame.setStyleSheet("#quick{background:#ffffff;border:1px solid #e5e5e5;border-radius:18px}"
                            "QLineEdit{border:0;background:transparent;font-size:17px;color:#1a1a18;padding:0}")
        shadow = QGraphicsDropShadowEffect(frame)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 8)
        shadow.setColor(Qt.GlobalColor.gray)
        frame.setGraphicsEffect(shadow)
        mark = QLabel(frame)
        mark.setPixmap(icon.pixmap(30, 30))
        self.input = QLineEdit(frame)
        self.input.setPlaceholderText(t("tomo_ask_placeholder", lang))
        self.input.returnPressed.connect(self._submit)
        row = QHBoxLayout(frame)
        row.setContentsMargins(16, 14, 18, 14)
        row.setSpacing(12)
        row.addWidget(mark)
        row.addWidget(self.input, 1)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)   # 给阴影留位置
        outer.addWidget(frame)
        self.resize(680, 112)

    def popup(self) -> None:
        from PySide6.QtGui import QCursor, QGuiApplication

        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self.move(area.x() + (area.width() - self.width()) // 2, area.y() + int(area.height() * 0.24))
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def _submit(self) -> None:
        text = self.input.text().strip()
        self.input.clear()
        self.hide()
        if text:
            self.submitted.emit(text)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.input.clear()
            self.hide()
            return
        super().keyPressEvent(event)

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            self.hide()
        super().changeEvent(event)
