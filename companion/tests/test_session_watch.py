"""CC 窗口开着、没人看得见时（锁屏、屏幕关了）对话页当作看不见，动画和重画停下（2026-10-18 轻量化，session_watch.py）。"""
import ctypes
from pathlib import Path
from types import SimpleNamespace

from companion import session_watch as sw

SRC = Path(__file__).resolve().parents[1] / "src" / "companion"


def _msg(message, wparam=0, lparam=0):
    msg = sw._MSG()
    msg.message, msg.wParam, msg.lParam = message, wparam, lparam
    return msg


def test_lock_unlock_and_display_messages_are_recognised():
    lock = _msg(sw.WM_WTSSESSION_CHANGE, sw.WTS_SESSION_LOCK)
    unlock = _msg(sw.WM_WTSSESSION_CHANGE, sw.WTS_SESSION_UNLOCK)
    other_session = _msg(sw.WM_WTSSESSION_CHANGE, 0x5)   # 登录这类，不管
    assert sw.read_message(ctypes.addressof(lock)) == ("lock", True)
    assert sw.read_message(ctypes.addressof(unlock)) == ("lock", False)
    assert sw.read_message(ctypes.addressof(other_session)) is None
    for data, off in ((0, True), (1, False), (2, False)):   # 关 / 开 / 变暗（变暗还看得见）
        setting = sw._PowerSetting(sw._guid(sw.DISPLAY_STATE), 4, (ctypes.c_ubyte * 4)(data, 0, 0, 0))
        msg = _msg(sw.WM_POWERBROADCAST, sw.PBT_POWERSETTINGCHANGE, ctypes.addressof(setting))
        assert sw.read_message(ctypes.addressof(msg)) == ("display", off)
    other_guid = sw._PowerSetting(sw._guid((1, 2, 3, (0,) * 8)), 4)
    msg = _msg(sw.WM_POWERBROADCAST, sw.PBT_POWERSETTINGCHANGE, ctypes.addressof(other_guid))
    assert sw.read_message(ctypes.addressof(msg)) is None
    assert sw.read_message(ctypes.addressof(_msg(0x0200))) is None   # 鼠标移动


class _Page:
    def __init__(self):
        self.visible = True
        self.changes = []

    def isVisible(self):
        return self.visible

    def setVisible(self, value):
        self.visible = value
        self.changes.append(value)


def _watch(**state):
    window = SimpleNamespace(page=_Page(), isVisible=lambda: state.get("visible", True),
                             isMinimized=lambda: state.get("minimized", False), winId=lambda: 0)
    return sw.SessionWatch(window), window


def test_a_locked_session_or_a_dark_screen_hides_the_page_and_coming_back_shows_it():
    watch, window = _watch()
    lock = _msg(sw.WM_WTSSESSION_CHANGE, sw.WTS_SESSION_LOCK)
    unlock = _msg(sw.WM_WTSSESSION_CHANGE, sw.WTS_SESSION_UNLOCK)
    watch.native_event(b"windows_generic_MSG", ctypes.addressof(lock))
    assert window.page.changes == [False]
    watch.native_event(b"windows_generic_MSG", ctypes.addressof(unlock))
    assert window.page.changes == [False, True]
    off = sw._PowerSetting(sw._guid(sw.DISPLAY_STATE), 4, (ctypes.c_ubyte * 4)(0, 0, 0, 0))
    watch.native_event(b"windows_generic_MSG", ctypes.addressof(_msg(sw.WM_POWERBROADCAST, sw.PBT_POWERSETTINGCHANGE,
                                                                      ctypes.addressof(off))))
    assert window.page.changes[-1] is False and watch.display_off
    watch.native_event(b"mac_generic_NSEvent", ctypes.addressof(unlock))   # 别的平台的事件不看
    assert len(window.page.changes) == 3


def test_a_closed_or_minimized_window_is_not_made_visible_by_an_unlock():
    for state in ({"visible": False}, {"minimized": True}):
        watch, window = _watch(**state)
        window.page.visible = False
        watch.locked = True
        watch.native_event(b"windows_generic_MSG",
                           ctypes.addressof(_msg(sw.WM_WTSSESSION_CHANGE, sw.WTS_SESSION_UNLOCK)))
        assert window.page.changes == []


def test_the_cc_window_hands_native_messages_to_the_watch_and_activation_clears_it():
    source = (SRC / "gui_tomo.py").read_text(encoding="utf-8")
    native = source.split("def nativeEvent(self, event_type, message):", 1)[1].split("\n    def ", 1)[0]
    assert "session.native_event(event_type, message)" in native
    change = source.split("def changeEvent(self, event) -> None:", 1)[1].split("\n    def ", 1)[0]
    assert "session.locked = session.display_off = False" in change and "self.isActiveWindow()" in change
    assert "self._session = SessionWatch(self)" in source and "self._session.close()" in source
