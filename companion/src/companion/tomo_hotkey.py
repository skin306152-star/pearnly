# -*- coding: utf-8 -*-
"""全局快捷键 Ctrl+Alt+Space：在任何程序里按下都呼出 Tomo 的快捷输入条（同 Claude 桌面端）。

Windows 用 RegisterHotKey 挂在一个不显示的小窗口上，WM_HOTKEY 由它的 nativeEvent 收；
快捷键被别的程序占了就不注册（只记日志，托盘 / 开始菜单照样能打开 Tomo）。其他系统不注册。
"""
from __future__ import annotations

import sys

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from .logger import get_logger

log = get_logger(__name__)

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
VK_SPACE = 0x20
HOTKEY_ID = 0x7A01


class GlobalHotkey(QWidget):
    pressed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.registered = False
        if sys.platform != "win32":
            return
        import ctypes

        self._user32 = ctypes.windll.user32
        hwnd = int(self.winId())   # 建出窗口句柄，但不显示
        self.registered = bool(self._user32.RegisterHotKey(hwnd, HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_SPACE))
        if not self.registered:
            log.warning("Ctrl+Alt+Space 已被其他程序占用，Tomo 快捷输入条不用快捷键")

    def nativeEvent(self, event_type, message):
        if self.registered and event_type == b"windows_generic_MSG":
            import ctypes.wintypes

            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self.pressed.emit()
                return True, 0
        return super().nativeEvent(event_type, message)

    def release(self) -> None:
        if self.registered:
            self._user32.UnregisterHotKey(int(self.winId()), HOTKEY_ID)
            self.registered = False
