# -*- coding: utf-8 -*-
"""CC 窗口开着、却没人看得见时（锁屏、屏幕关了），让对话页当作「看不见」（2026-10-18 轻量化）。

浏览器（Chrome / Edge）锁屏、关屏时会把页面当作被遮住，动画和重画都停；Qt 的网页内核没有这一层，只看窗口自己有没有
藏起来——CC 窗口开着离开电脑，锁屏以后页面上的动画（登录页背景、对话页的小猫）照样每秒画几十次。10-18 凌晨两台测试机
上 CC 窗口都开着没人用，网页内核一直占着约一个核、风扇不停（见 docs/work/2026-10-15-multi-desktop-build.md）。

所以 Windows 上窗口登记两样系统通知：会话锁屏 / 解锁（``WTSRegisterSessionNotification``）、屏幕开 / 关
（``RegisterPowerSettingNotification`` 的 ``GUID_CONSOLE_DISPLAY_STATE``）。锁着或屏幕关着就 ``page.setVisible(False)``
（页面的 visibilityState 变成 hidden，动画、重画停下，后台的回复、通知照常），解锁 / 亮屏且窗口开着没最小化时再设回看得见。
非 Windows 什么都不登记。
"""

from __future__ import annotations

import ctypes
import sys
from typing import Optional

from .logger import get_logger

log = get_logger(__name__)

WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
NOTIFY_FOR_THIS_SESSION = 0
WM_POWERBROADCAST = 0x0218
PBT_POWERSETTINGCHANGE = 0x8013
DEVICE_NOTIFY_WINDOW_HANDLE = 0
# GUID_CONSOLE_DISPLAY_STATE {6FE69556-704A-47A0-8F24-C28D936FDA47}：数据 0 = 关、1 = 开、2 = 变暗
DISPLAY_STATE = (0x6FE69556, 0x704A, 0x47A0, (0x8F, 0x24, 0xC2, 0x8D, 0x93, 0x6F, 0xDA, 0x47))


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]


def _guid(value) -> _GUID:
    return _GUID(value[0], value[1], value[2], (ctypes.c_ubyte * 8)(*value[3]))


class _MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint), ("wParam", ctypes.c_size_t),
                ("lParam", ctypes.c_ssize_t), ("time", ctypes.c_uint32), ("x", ctypes.c_long), ("y", ctypes.c_long)]


class _PowerSetting(ctypes.Structure):
    _fields_ = [("PowerSetting", _GUID), ("DataLength", ctypes.c_uint32), ("Data", ctypes.c_ubyte * 4)]


def page_visible(*, window_visible: bool, minimized: bool, locked: bool, display_off: bool) -> bool:
    return bool(window_visible and not minimized and not locked and not display_off)


def read_message(address: int) -> Optional[tuple]:
    """Qt nativeEvent 交来的 Windows 消息 → ('lock' | 'display', 值)；别的消息是 None。"""
    msg = _MSG.from_address(int(address))
    if msg.message == WM_WTSSESSION_CHANGE and msg.wParam in (WTS_SESSION_LOCK, WTS_SESSION_UNLOCK):
        return "lock", msg.wParam == WTS_SESSION_LOCK
    if msg.message == WM_POWERBROADCAST and msg.wParam == PBT_POWERSETTINGCHANGE and msg.lParam:
        setting = _PowerSetting.from_address(msg.lParam)
        if bytes(setting.PowerSetting) == bytes(_guid(DISPLAY_STATE)) and setting.DataLength >= 1:
            return "display", setting.Data[0] == 0
    return None


class SessionWatch:
    """（窗口进程，界面线程）登记、收消息、按锁屏 / 关屏设页面看不看得见。``window`` 要有 ``page`` 与 ``winId()``。"""

    def __init__(self, window) -> None:
        self._window = window
        self.locked = False
        self.display_off = False
        self._handles: list = []
        if sys.platform == "win32":
            self._register()

    def _register(self) -> None:
        try:
            hwnd = ctypes.c_void_p(int(self._window.winId()))
            if not ctypes.windll.wtsapi32.WTSRegisterSessionNotification(hwnd, NOTIFY_FOR_THIS_SESSION):
                log.warning("登记锁屏通知失败")
            register = ctypes.windll.user32.RegisterPowerSettingNotification
            register.restype = ctypes.c_void_p
            guid = _guid(DISPLAY_STATE)
            handle = register(hwnd, ctypes.byref(guid), DEVICE_NOTIFY_WINDOW_HANDLE)
            if handle:
                self._handles.append(handle)
        except Exception:
            log.warning("登记锁屏 / 关屏通知失败（窗口照常用）", exc_info=True)

    def native_event(self, event_type, message) -> None:
        """TomoWindow.nativeEvent 里调：是锁屏 / 关屏的消息就更新页面看不看得见。"""
        if bytes(event_type) != b"windows_generic_MSG":
            return
        try:
            change = read_message(int(message))
        except Exception:
            return
        if change is None:
            return
        kind, value = change
        if kind == "lock":
            self.locked = value
        else:
            self.display_off = value
        self.apply()

    def apply(self) -> None:
        window = self._window
        visible = page_visible(window_visible=window.isVisible(), minimized=window.isMinimized(),
                               locked=self.locked, display_off=self.display_off)
        if window.page.isVisible() != visible:
            window.page.setVisible(visible)

    def close(self) -> None:
        if sys.platform != "win32":
            return
        try:
            hwnd = ctypes.c_void_p(int(self._window.winId()))
            ctypes.windll.wtsapi32.WTSUnRegisterSessionNotification(hwnd)
            unregister = ctypes.windll.user32.UnregisterPowerSettingNotification
            for handle in self._handles:
                unregister(ctypes.c_void_p(handle))
        except Exception:
            pass
        self._handles = []
