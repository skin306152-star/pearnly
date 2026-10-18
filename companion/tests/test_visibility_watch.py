"""CC 窗口开着、没人看得见时对话页停画（visibility_watch.py；2026-10-18 轻量化，10-18 测试机复测后重写）。

10-18 复测：窗口开着锁屏后网页内核照样约 0.8 核。根因是锁屏 / 屏幕通知登记在 CC 窗口自己的系统句柄上，而对话页的渲染控件挂进
窗口时 Qt 把窗口换成 GPU 合成、销毁重建了句柄（``QWidgetPrivate::recreate()``），通知再也到不了。现在登记在一个从不显示的
消息窗口上；另外补上「被别的窗口完全挡住」与最小化的记录。Windows 接口用假的 user32 / dwmapi / gdi32 / wtsapi32 模拟一张桌面，
在 Mac / Linux 上也能跑；真网页内核那条在子进程里跑（offscreen）。"""
import ctypes
import logging
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from companion import visibility_watch as vw  # noqa: E402
from companion import window_link  # noqa: E402

COMPANION = Path(__file__).resolve().parents[1]
OBJID_CURSOR = -9


def _app():
    return QApplication.instance() or QApplication([])


def _until(predicate, seconds=3.0):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    return predicate()


def _pump(seconds):
    _until(lambda: False, seconds)


def _msg(message, wparam=0, lparam=0):
    msg = vw._MSG()
    msg.message, msg.wParam, msg.lParam = message, wparam, lparam
    return msg


def _send(target, message):
    """把一条 Windows 消息交给收它的窗口（消息窗口的 nativeEvent，或直接给监视器）。"""
    keep = message
    target(b"windows_generic_MSG", ctypes.addressof(keep))


LOCK = _msg(vw.WM_WTSSESSION_CHANGE, vw.WTS_SESSION_LOCK)
UNLOCK = _msg(vw.WM_WTSSESSION_CHANGE, vw.WTS_SESSION_UNLOCK)
_DISPLAY = {}


def _display(state):
    setting = vw._PowerSetting(vw._guid(vw.DISPLAY_STATE), 4, (ctypes.c_ubyte * 4)(state, 0, 0, 0))
    _DISPLAY[state] = setting   # 留住：消息里只放地址
    return _msg(vw.WM_POWERBROADCAST, vw.PBT_POWERSETTINGCHANGE, ctypes.addressof(setting))


# ── 替身 ──

class _Page(QObject):
    """QWebEnginePage 的替身：只有看不看得见。"""

    visibleChanged = Signal(bool)

    def __init__(self):
        super().__init__()
        self.visible = False
        self.changes = []

    def isVisible(self):
        return self.visible

    def setVisible(self, value):
        value = bool(value)
        if value != self.visible:
            self.visible = value
            self.changes.append(value)
            self.visibleChanged.emit(value)


class _CC(QWidget):
    """CC 窗口的替身：像 QWebEngineView 那样，窗口显示 / 藏起、最小化 / 还原时 Qt 自己改页面看不看得见。"""

    def __init__(self):
        super().__init__()
        self.page = _Page()
        self.resize(400, 300)

    def showEvent(self, event):
        self.page.setVisible(True)

    def hideEvent(self, event):
        self.page.setVisible(False)

    def recreate(self):
        """Qt 在对话页的渲染控件挂进来、窗口要换成 GPU 合成时做的事（qwidget_p.h ``recreate()``）。"""
        self.destroy()
        self.create()
        self.show()


class _System:
    """VisibilityWatch 用的系统接口替身（遮挡结果由测试设）。"""

    def __init__(self):
        self.covered = False
        self.pids = frozenset({4242})
        self.checks = 0
        self.sessions = []
        self.watched = []
        self.on_event = None
        self.closed = False

    def watch_session(self, hwnd):
        self.sessions.append(hwnd)
        return True, True

    def watch_windows(self, on_event):
        self.on_event = on_event
        return 4

    def watch_processes(self, pids):
        pids = frozenset(pids)
        if not self.watched or self.watched[-1] != pids:
            self.watched.append(pids)

    def occlusion(self, hwnd):
        self.checks += 1
        return self.covered, (self.pids if self.covered else frozenset())

    def close(self):
        self.closed = True


def _shown_cc():
    _app()
    window = _CC()
    window.show()
    window.activateWindow()
    assert _until(window.isActiveWindow)
    return window


def _steal_focus():
    """别的窗口（例如 Express）到了最前面：CC 窗口失焦但还开着。"""
    other = QWidget()
    other.resize(100, 100)
    other.show()
    other.activateWindow()
    assert _until(other.isActiveWindow)
    return other


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(vw, "OCCLUDED_GRACE_MS", 600)
    monkeypatch.setattr(vw, "CHECK_DELAY_MS", 20)
    monkeypatch.setattr(vw, "POLL_VISIBLE_MS", 120)
    monkeypatch.setattr(vw, "POLL_OCCLUDED_MS", 80)


# ── 消息 ──

def test_lock_unlock_and_display_messages_are_recognised():
    other_session = _msg(vw.WM_WTSSESSION_CHANGE, 0x5)   # 登录这类，不管
    assert vw.read_message(ctypes.addressof(LOCK)) == ("lock", True)
    assert vw.read_message(ctypes.addressof(UNLOCK)) == ("lock", False)
    assert vw.read_message(ctypes.addressof(other_session)) is None
    for data, off in ((0, True), (1, False), (2, False)):   # 关 / 开 / 变暗（变暗还看得见）
        assert vw.read_message(ctypes.addressof(_display(data))) == ("display", off)
    other_guid = vw._PowerSetting(vw._guid((1, 2, 3, (0,) * 8)), 4)
    msg = _msg(vw.WM_POWERBROADCAST, vw.PBT_POWERSETTINGCHANGE, ctypes.addressof(other_guid))
    assert vw.read_message(ctypes.addressof(msg)) is None
    assert vw.read_message(ctypes.addressof(_msg(0x0200))) is None   # 鼠标移动


# ── 假的 Windows 桌面（给真的 Win32System 用） ──

class _Fn:
    """假的 DLL 函数：Win32System 会给它设 restype / argtypes。"""

    def __init__(self, fn):
        self._fn = fn

    def __call__(self, *args):
        return self._fn(*args)


class _Desk:
    """一张假桌面：屏幕工作区、按 Z 序（上 → 下）排的顶层窗口、登记过的通知与事件钩子。"""

    def __init__(self, work=((0, 0, 1920, 1040),)):
        self.work = list(work)
        self.windows = {}
        self.z = []
        self.session, self.unsession, self.power, self.unpower = [], [], [], []
        self.hooks, self.unhooked = {}, []

    def add(self, hwnd, rect, *, pid=100, frame=None, visible=True, iconic=False, zoomed=False, style=0x14CF0000,
            ex_style=0x100, cloaked=False, layered=None, complex_region=False):
        """加一个窗口放到最上面；``frame`` = DWM 看得见的边框（不给就同 rect）。"""
        self.windows[hwnd] = dict(rect=rect, frame=frame or rect, pid=pid, visible=visible, iconic=iconic,
                                  zoomed=zoomed, style=style, ex_style=ex_style, cloaked=cloaked, layered=layered,
                                  complex=complex_region)
        self.z.insert(0, hwnd)
        return hwnd

    def fire(self, event, hwnd, pid, id_object=vw.OBJID_WINDOW):
        for handle, (low, high, hook_pid, proc) in list(self.hooks.items()):
            if low <= event <= high and hook_pid in (0, pid):
                proc(handle, event, hwnd, id_object, 0, 0, 0)

    def _set(self, ref, rect):
        ref._obj.left, ref._obj.top, ref._obj.right, ref._obj.bottom = rect
        return 1

    def dlls(self):
        w = self.windows

        def get_window(hwnd, cmd):
            assert cmd == vw.GW_HWNDPREV
            i = self.z.index(hwnd)
            return self.z[i - 1] if i > 0 else None

        def get_long(hwnd, index):
            value = w[hwnd]["style"] if index == vw.GWL_STYLE else w[hwnd]["ex_style"]
            return ctypes.c_long(value - (1 << 32) if value >= 1 << 31 else value).value   # 真的接口返回有符号数

        def layered(hwnd, key, alpha, flags):
            if w[hwnd]["layered"] is None:
                return 0
            flags._obj.value, alpha._obj.value = w[hwnd]["layered"]
            return 1

        def monitor_info(monitor, ref):
            ref._obj.rcWork.left, ref._obj.rcWork.top, ref._obj.rcWork.right, ref._obj.rcWork.bottom = \
                self.work[monitor - 1]
            return 1

        def monitor_from_window(hwnd, _flags):
            left, top, right, bottom = w[hwnd]["rect"]
            cx, cy = (left + right) // 2, (top + bottom) // 2
            for i, (l, t, r, b) in enumerate(self.work):
                if l <= cx < r and t <= cy < b:
                    return i + 1
            return 1

        def enum_monitors(_dc, _clip, proc, _data):
            for i in range(len(self.work)):
                proc(i + 1, None, None, 0)
            return 1

        def dwm(hwnd, attribute, ref, _size):
            if attribute == vw.DWMWA_CLOAKED:
                ref._obj.value = int(w[hwnd]["cloaked"])
                return 0
            assert attribute == vw.DWMWA_EXTENDED_FRAME_BOUNDS
            return 0 if self._set(ref, w[hwnd]["frame"]) else 1

        def pid_of(hwnd, ref):
            ref._obj.value = w[hwnd]["pid"]
            return 1

        def power(hwnd, guid, _flags):
            assert bytes(guid._obj) == bytes(vw._guid(vw.DISPLAY_STATE))
            self.power.append(hwnd)
            return 0x7700 + len(self.power)

        def set_hook(low, high, _module, proc, pid, _thread, flags):
            assert flags == vw.WINEVENT_OUTOFCONTEXT
            handle = 0x500 + len(self.hooks) + len(self.unhooked) + 1
            self.hooks[handle] = (low, high, pid, proc)
            return handle

        def unhook(handle):
            self.hooks.pop(handle)
            self.unhooked.append(handle)
            return 1

        user32 = SimpleNamespace(
            RegisterPowerSettingNotification=_Fn(power),
            UnregisterPowerSettingNotification=_Fn(lambda handle: self.unpower.append(handle) or 1),
            SetWinEventHook=_Fn(set_hook), UnhookWinEvent=_Fn(unhook),
            GetWindow=_Fn(get_window), IsWindow=_Fn(lambda hwnd: int(hwnd in w)),
            IsWindowVisible=_Fn(lambda hwnd: int(w[hwnd]["visible"])), IsIconic=_Fn(lambda hwnd: int(w[hwnd]["iconic"])),
            IsZoomed=_Fn(lambda hwnd: int(w[hwnd]["zoomed"])), GetWindowLongW=_Fn(get_long),
            GetWindowRect=_Fn(lambda hwnd, ref: self._set(ref, w[hwnd]["rect"])),
            GetWindowThreadProcessId=_Fn(pid_of), GetLayeredWindowAttributes=_Fn(layered),
            GetWindowRgn=_Fn(lambda hwnd, _rgn: 3 if w[hwnd]["complex"] else 0),
            MonitorFromWindow=_Fn(monitor_from_window), GetMonitorInfoW=_Fn(monitor_info),
            EnumDisplayMonitors=_Fn(enum_monitors))
        wts = SimpleNamespace(
            WTSRegisterSessionNotification=_Fn(lambda hwnd, flags: self.session.append(hwnd) or 1),
            WTSUnRegisterSessionNotification=_Fn(lambda hwnd: self.unsession.append(hwnd) or 1))
        dwmapi = SimpleNamespace(DwmGetWindowAttribute=_Fn(dwm))
        gdi = SimpleNamespace(CreateRectRgn=_Fn(lambda *rect: 0x99), DeleteObject=_Fn(lambda handle: 1))
        return {"user32": user32, "wtsapi32": wts, "dwmapi": dwmapi, "gdi32": gdi}


# ── 根因：Qt 重建 CC 窗口的系统句柄以后，锁屏通知还收得到 ──

def test_lock_notifications_survive_qt_recreating_the_cc_window():
    """08792e73 把通知登记在 CC 窗口自己的句柄上；对话页一载入 Qt 就销毁重建那个句柄（Qt 6.11 ``QWidget::setParent`` 里
    「Recreating … to support」那段），通知从此送到一个已经不存在的窗口。改前（旧 SessionWatch，同样的假接口）这里第一条断言失败：
    登记的句柄重建后不在任何活着的窗口上。"""
    window = _shown_cc()
    desk = _Desk()
    watch = vw.VisibilityWatch(window, system=vw.Win32System(dlls=desk.dlls()))
    registered = desk.session[0]
    assert desk.power == [registered] and registered != int(window.winId())
    window.recreate()   # 对话页的渲染控件挂进来了
    assert _until(window.isVisible)
    owner = [w for w in QGuiApplication.allWindows() if int(w.winId()) == registered]
    assert owner, "登记锁屏通知的窗口在 Qt 重建 CC 窗口以后不在了"
    assert not owner[0].isVisible()   # 消息窗口从不显示
    _send(owner[0].nativeEvent, LOCK)
    assert window.page.visible is False and watch.reason == vw.LOCKED
    _send(owner[0].nativeEvent, UNLOCK)
    assert window.page.visible is True and watch.reason == ""
    watch.close()
    assert desk.unsession == [registered] and desk.unpower == [0x7701] and desk.hooks == {}


# ── 锁屏 / 屏幕 / 最小化 ──

def test_locking_or_a_dark_screen_pauses_the_open_page_and_coming_back_resumes_it(caplog):
    window = _shown_cc()
    changes = []
    watch = vw.VisibilityWatch(window, system=False, on_change=changes.append)
    assert window.page.visible and watch.reason == ""
    with caplog.at_level(logging.INFO):
        _send(watch.native_event, LOCK)
        assert window.page.visible is False and watch.reason == vw.LOCKED
        _send(watch.native_event, UNLOCK)
        assert window.page.visible is True and watch.reason == ""
        _send(watch.native_event, _display(0))
        assert window.page.visible is False and watch.reason == vw.DISPLAY_OFF
        _send(watch.native_event, _display(2))   # 变暗：还看得见
        assert window.page.visible is True
        _send(watch.native_event, _display(0))
        _send(watch.native_event, LOCK)          # 关屏又锁屏：原因换成锁屏，页面仍停着
        assert window.page.visible is False and watch.reason == vw.LOCKED
        _send(watch.native_event, _display(1))   # 亮屏但还锁着：仍停着
        assert window.page.visible is False and watch.reason == vw.LOCKED
        _send(watch.native_event, UNLOCK)
        assert window.page.visible is True
    lines = [r.getMessage() for r in caplog.records if r.name.endswith("visibility_watch")]
    assert lines[0] == "CC 窗口看不见（锁屏）：页面已暂停"
    assert lines[1].startswith("CC 窗口看得见：页面已恢复（停了 ")
    assert "CC 窗口看不见（屏幕关了）：页面已暂停" in lines
    assert changes[-1] == "" and vw.LOCKED in changes and vw.DISPLAY_OFF in changes
    _send(watch.native_event, _msg(0x0200))   # 别的消息不管
    assert window.page.visible is True


def test_minimizing_is_left_to_qt_and_an_unlock_does_not_wake_a_minimized_or_closed_window(caplog):
    window = _shown_cc()
    watch = vw.VisibilityWatch(window, system=False)
    with caplog.at_level(logging.INFO):
        window.showMinimized()
        assert _until(lambda: watch.reason == window_link.PAUSE_MINIMIZED)
        assert window.page.visible is False          # Qt 自己藏的
        _send(watch.native_event, LOCK)
        _send(watch.native_event, UNLOCK)            # 解锁也不把最小化的窗口弄成看得见
        _pump(0.05)
        assert window.page.visible is False and watch.reason == window_link.PAUSE_MINIMIZED
        window.showNormal()
        assert _until(lambda: watch.reason == "") and window.page.visible is True
    lines = [r.getMessage() for r in caplog.records if r.name.endswith("visibility_watch")]
    assert lines[0] == "CC 窗口看不见（最小化）：页面已暂停" and lines[-1].startswith("CC 窗口看得见：页面已恢复")
    window.hide()                                    # 关窗口（窗口进程等回复时藏着）
    assert _until(lambda: watch.reason == window_link.PAUSE_HIDDEN)
    caplog.clear()
    with caplog.at_level(logging.INFO):
        _send(watch.native_event, LOCK)
        _send(watch.native_event, UNLOCK)
        _pump(0.05)
    assert window.page.visible is False
    assert not [r for r in caplog.records if r.name.endswith("visibility_watch")]   # 开 / 关窗口不另记


def test_a_page_qt_shows_again_while_nobody_can_see_it_is_paused_again():
    window = _shown_cc()
    watch = vw.VisibilityWatch(window, system=False)
    _send(watch.native_event, LOCK)
    assert window.page.visible is False
    window.page.setVisible(True)   # 例如 QWebEngineView 的 show 事件
    assert _until(lambda: window.page.visible is False)
    assert watch.reason == vw.LOCKED


def test_someone_using_the_window_resumes_it_at_once():
    window = _shown_cc()
    watch = vw.VisibilityWatch(window, system=False)
    _send(watch.native_event, LOCK)   # 漏了解锁通知
    watch.present()                   # 从托盘 / 通知 / 快捷键打开
    assert window.page.visible is True and not watch.locked and watch.reason == ""
    _send(watch.native_event, _display(0))
    other = _steal_focus()
    window.activateWindow()           # 又点回 CC：激活就一定看得见
    assert _until(window.isActiveWindow)
    assert window.page.visible is True and not watch.display_off
    other.close()


# ── 被挡住 ──

def test_a_fully_covered_window_pauses_after_the_grace_and_resumes_when_uncovered(quick, caplog, monkeypatch):
    monkeypatch.setattr(vw, "POLL_OCCLUDED_MS", 10_000)   # 只看事件钩子：挡着的窗口一移开马上恢复，不等兜底
    window = _shown_cc()
    system = _System()
    watch = vw.VisibilityWatch(window, system=system)
    assert system.sessions and system.on_event is not None
    assert system.checks == 0                       # 在最前面：不算
    other = _steal_focus()
    system.covered = True
    assert _until(lambda: system.checks >= 1)
    assert window.page.visible is True              # 刚挡住：先等一等（来回切窗口不闪）
    with caplog.at_level(logging.INFO):
        assert _until(lambda: watch.reason == vw.OCCLUDED, 2.0)
    assert window.page.visible is False
    assert frozenset({4242}) in system.watched      # 盯着挡住它的那个进程
    assert any(r.getMessage() == "CC 窗口看不见（被别的窗口完全挡住）：页面已暂停" for r in caplog.records)
    system.covered = False
    before = system.checks
    system.on_event()                               # 挡着的窗口移开 / 关了（事件钩子）
    assert _until(lambda: window.page.visible is True, 1.0)
    assert system.checks == before + 1 and watch.reason == ""
    assert system.watched[-1] == frozenset()
    other.close()


def test_coming_back_and_forth_within_the_grace_never_pauses(quick, monkeypatch):
    monkeypatch.setattr(vw, "OCCLUDED_GRACE_MS", 1500)   # 每次只挡 0.1 秒：慢机器上也远不到宽限
    window = _shown_cc()
    system = _System()
    vw.VisibilityWatch(window, system=system)
    for _ in range(3):
        other = _steal_focus()
        system.covered = True
        _pump(0.1)
        window.activateWindow()
        assert _until(window.isActiveWindow)
        other.close()
    assert False not in window.page.changes


def test_an_unfocused_window_that_still_shows_keeps_painting(quick):
    window = _shown_cc()
    system = _System()
    watch = vw.VisibilityWatch(window, system=system)
    other = _steal_focus()                       # 边看 CC 边在 Express 里操作
    assert _until(lambda: system.checks >= 3, 2.0)   # 兜底重算照常
    assert window.page.visible is True and watch.reason == "" and False not in window.page.changes
    other.close()


def test_the_window_in_front_is_never_paused_for_being_covered(quick):
    window = _shown_cc()
    system = _System()
    system.covered = True
    watch = vw.VisibilityWatch(window, system=system)
    system.on_event()
    _pump(0.6)
    assert system.checks == 0 and window.page.visible is True and watch.reason == ""


def test_locking_stops_the_cover_checks_and_unlocking_rechecks(quick):
    window = _shown_cc()
    system = _System()
    watch = vw.VisibilityWatch(window, system=system)
    other = _steal_focus()
    assert _until(lambda: system.checks >= 1)
    _send(watch.native_event, LOCK)
    count = system.checks
    _pump(0.4)
    assert system.checks == count                # 锁着：不重算
    _send(watch.native_event, UNLOCK)
    assert window.page.visible is True
    assert _until(lambda: system.checks > count, 1.0)
    watch.close()
    assert system.closed
    other.close()


# ── 遮挡判断（真的 Win32System，假桌面） ──

CC_RECT = (200, 100, 1000, 700)


def _occlusion(desk, cc=1):
    return vw.Win32System(dlls=desk.dlls()).occlusion(cc)


def test_a_maximized_window_on_top_covers_cc_and_only_windows_above_count():
    desk = _Desk()
    desk.add(9, (0, 0, 1920, 1040), pid=7)       # 在 CC 下面的大窗口不算
    desk.add(1, CC_RECT, pid=1)
    assert _occlusion(desk) == (False, frozenset())
    desk.add(2, (-8, -8, 1928, 1048), frame=(0, 0, 1920, 1040), zoomed=True, pid=55)   # 最大化的 Express
    assert _occlusion(desk) == (True, frozenset({55}))


def test_cc_peeking_out_anywhere_is_not_covered():
    desk = _Desk()
    desk.add(1, CC_RECT)
    desk.add(2, (190, 90, 993, 710), pid=55)         # 右边露出 7 像素
    assert _occlusion(desk) == (False, frozenset({55}))
    # 系统边框的看不见的拉伸边（GetWindowRect 比看得见的大 7 像素）不算挡住
    desk.add(3, (993, 93, 1207, 707), frame=(1000, 100, 1200, 700), pid=66)
    assert _occlusion(desk)[0] is False


def test_two_windows_together_cover_cc():
    desk = _Desk()
    desk.add(1, CC_RECT)
    desk.add(2, (0, 0, 600, 1040), pid=55)
    assert _occlusion(desk)[0] is False
    desk.add(3, (600, 0, 1920, 1040), pid=66)
    assert _occlusion(desk) == (True, frozenset({55, 66}))


@pytest.mark.parametrize("facts", [
    {"visible": False}, {"iconic": True}, {"cloaked": True},
    {"ex_style": 0x100 | vw.WS_EX_TRANSPARENT}, {"ex_style": 0x100 | vw.WS_EX_TOOLWINDOW},
    {"style": vw.WS_POPUP | 0x10000000},
    {"ex_style": vw.WS_EX_LAYERED}, {"ex_style": vw.WS_EX_LAYERED, "layered": (vw.LWA_ALPHA, 200)},
    {"ex_style": vw.WS_EX_LAYERED, "layered": (vw.LWA_COLORKEY, 255)}, {"complex_region": True},
])
def test_windows_that_may_let_cc_show_through_never_count_as_covering(facts):
    desk = _Desk()
    desk.add(1, CC_RECT)
    desk.add(2, (0, 0, 1920, 1040), pid=55, **facts)
    assert _occlusion(desk) == (False, frozenset())


def test_an_opaque_layered_window_covers():
    desk = _Desk()
    desk.add(1, CC_RECT)
    desk.add(2, (0, 0, 1920, 1040), pid=55, ex_style=vw.WS_EX_LAYERED, layered=(vw.LWA_ALPHA, 255))
    assert _occlusion(desk)[0] is True


def test_only_the_part_of_cc_on_a_screen_work_area_has_to_be_covered():
    desk = _Desk(work=((0, 0, 1920, 1040), (1920, 0, 3840, 1040)))
    desk.add(1, (1500, 600, 2300, 1080))             # 跨两块屏，下面一截压在任务栏下面
    desk.add(2, (1920, 0, 3840, 1040), zoomed=True, pid=66)   # 右屏最大化
    assert _occlusion(desk)[0] is False              # 左屏那块还露着
    # 左屏最大化的窗口边框伸进右屏：按它所在屏幕的工作区截掉，不盖右屏
    desk.add(3, (-8, -8, 1940, 1048), zoomed=True, pid=55)
    assert _occlusion(desk) == (True, frozenset({55, 66}))
    desk2 = _Desk(work=((0, 0, 1920, 1040), (1920, 0, 3840, 1040)))
    desk2.add(1, (1500, 600, 1935, 1000))            # 右屏只伸进去 15 像素
    desk2.add(3, (-8, -8, 1940, 1048), zoomed=True, pid=55)
    assert _occlusion(desk2)[0] is False             # 右屏那一截没被盖


def test_cc_off_screen_or_on_another_virtual_desktop_counts_as_covered_and_unknowns_do_not():
    desk = _Desk()
    desk.add(1, (3000, 100, 3400, 500))
    assert _occlusion(desk) == (True, frozenset())
    desk = _Desk()
    desk.add(1, CC_RECT, cloaked=True)
    assert _occlusion(desk) == (True, frozenset())
    desk = _Desk()
    desk.add(1, CC_RECT, frame=(300, 100, 1000, 700))    # 两种坐标对不上：不判断
    desk.add(2, (0, 0, 1920, 1040), pid=55)
    assert _occlusion(desk) is None
    assert _occlusion(_Desk(), cc=12345) is None         # 窗口没了


def test_window_event_hooks_are_cheap_and_follow_the_covering_processes():
    desk = _Desk()
    system = vw.Win32System(dlls=desk.dlls())
    events = []
    assert system.watch_windows(lambda: events.append(1)) == len(vw.GLOBAL_EVENTS)
    assert sorted((low, high, pid) for low, high, pid, _ in desk.hooks.values()) == \
        sorted((low, high, 0) for low, high in vw.GLOBAL_EVENTS)
    desk.fire(vw.EVENT_SYSTEM_FOREGROUND, 0x10, 55)
    desk.fire(vw.EVENT_SYSTEM_FOREGROUND, 0x10, 55, id_object=OBJID_CURSOR)   # 光标的不管
    desk.fire(vw.EVENT_OBJECT_LOCATIONCHANGE, 0x10, 55)                       # 没盯这个进程
    assert events == [1]
    system.watch_processes({55, 66})
    assert len(desk.hooks) == len(vw.GLOBAL_EVENTS) + 2 * len(vw.OCCLUDER_EVENTS)
    desk.fire(vw.EVENT_OBJECT_LOCATIONCHANGE, 0x10, 55)
    desk.fire(vw.EVENT_OBJECT_DESTROY, 0x11, 66)
    desk.fire(vw.EVENT_OBJECT_LOCATIONCHANGE, 0x10, 77)
    assert events == [1, 1, 1]
    system.watch_processes({66})
    assert len(desk.hooks) == len(vw.GLOBAL_EVENTS) + len(vw.OCCLUDER_EVENTS)
    system.close()
    assert desk.hooks == {}


def test_the_rect_arithmetic():
    assert vw.uncovered([(0, 0, 10, 10)], [(2, 2, 8, 8)]) == [(0, 0, 10, 2), (0, 8, 10, 10), (0, 2, 2, 8),
                                                               (8, 2, 10, 8)]
    assert vw.uncovered([(0, 0, 10, 10)], [(0, 0, 5, 10), (5, 0, 10, 10)]) == []
    assert vw.uncovered([(0, 0, 10, 10)], [(20, 20, 30, 30)]) == [(0, 0, 10, 10)]
    assert vw.on_screen((-5, -5, 5, 5), [(0, 0, 100, 100), (100, 0, 200, 100)]) == [(0, 0, 5, 5)]
    assert vw.occluding_rect(vw.WindowFacts(rect=(0, 0, 100, 100), maximized_work=(0, 0, 50, 100))) == (0, 0, 50, 100)
    assert vw.occluding_rect(vw.WindowFacts(rect=None)) is None


# ── 真的网页内核（子进程，offscreen）：停画是真的停、脚本照跑、看得见时画最新的 ──

_REAL = r"""
import ctypes, json, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, "src")
tmp = Path(tempfile.mkdtemp())
from companion.gui_font import qt_app
app = qt_app()
from PySide6.QtCore import QSettings
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QWidget
from companion import gui_tomo, visibility_watch as vw
gui_tomo.APPDATA_DIR = tmp
gui_tomo.QSettings = lambda *args: QSettings(str(tmp / "tomo.ini"), QSettings.Format.IniFormat)
gui_tomo.tomo_links.resume_url = lambda saved, env=None: "about:blank"
gui_tomo.tomo_links.route = lambda url: "load"   # 这页测试内容不是 Pearnly 的地址
vw.OCCLUDED_GRACE_MS, vw.CHECK_DELAY_MS, vw.POLL_OCCLUDED_MS, vw.POLL_VISIBLE_MS = 300, 20, 80, 120
class System:
    covered = False
    def watch_session(self, hwnd): return True, True
    def watch_windows(self, on_event): return 4
    def watch_processes(self, pids): pass
    def occlusion(self, hwnd): return self.covered, frozenset({7})
    def close(self): pass
system = System()
window = gui_tomo.TomoWindow(lang="zh", icon=QIcon(), notify=lambda *a, **k: None, bridge=None)
watch = vw.VisibilityWatch(window, system=system)
def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents(); time.sleep(0.005)
def js(code):
    box = {}
    window.page.runJavaScript("JSON.stringify(" + code + ") || 'null'", 0, lambda v: box.setdefault("v", v))
    end = time.monotonic() + 5
    while "v" not in box and time.monotonic() < end:
        app.processEvents(); time.sleep(0.005)
    return json.loads(box["v"])
window.show(); window.activateWindow()
window.page.setHtml("<div id=t>0</div><script>window.raf=0;(function f(){window.raf++;requestAnimationFrame(f)})();"
                    "window.chunk=function(s){document.getElementById('t').textContent=s}</script>")
end = time.monotonic() + 20
while not (js("window.raf || 0") > 0) and time.monotonic() < end:   # 先载的是 about:blank，等这一页
    pump(0.05)
pump(0.5)
def frames(seconds=0.6):
    a = js("window.raf"); pump(seconds); return js("window.raf") - a
out = {}
out["active"] = frames()
other = QWidget(); other.resize(50, 50); other.show(); other.activateWindow(); pump(0.3)
out["unfocused_visible"] = (frames(), window.isActiveWindow())
system.covered = True; pump(0.8)
out["covered"] = (frames(), js("document.visibilityState"), watch.reason)
window.page.runJavaScript("window.chunk('streamed while hidden')")   # 回复的流式输出照样进页面
pump(0.2)
out["text_hidden"] = js("document.getElementById('t').textContent")
window.activateWindow(); pump(0.3)
out["back"] = (frames(), js("document.visibilityState"), js("document.getElementById('t').textContent"), watch.reason)
vw_msg = vw._MSG(); vw_msg.message, vw_msg.wParam = vw.WM_WTSSESSION_CHANGE, vw.WTS_SESSION_LOCK
watch._messages.nativeEvent(b"windows_generic_MSG", ctypes.addressof(vw_msg))
out["locked"] = (frames(), watch.reason)
vw_msg.wParam = vw.WTS_SESSION_UNLOCK
watch._messages.nativeEvent(b"windows_generic_MSG", ctypes.addressof(vw_msg))
out["unlocked"] = frames()
window.showMinimized(); pump(0.3)
out["minimized"] = (frames(), watch.reason)
window.showNormal(); window.activateWindow(); pump(0.3)
out["restored"] = (frames(), watch.reason)
watch.close()
window.shutdown()
print("RESULT " + json.dumps(out))
"""


def test_the_real_web_engine_stops_painting_while_hidden_and_paints_the_latest_when_seen_again():
    pytest.importorskip("PySide6.QtWebEngineWidgets")
    import json
    import os

    out = subprocess.run([sys.executable, "-c", _REAL], cwd=COMPANION, capture_output=True, text=True, timeout=180,
                         env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    lines = [line for line in out.stdout.splitlines() if line.startswith("RESULT ")]
    assert lines, out.stderr[-3000:]
    result = json.loads(lines[0][len("RESULT "):])
    assert result["active"] > 5                                     # 看得见：每帧都在画
    frames, active = result["unfocused_visible"]
    assert frames > 5 and active is False                           # 失焦但看得见：照画
    assert result["covered"] == [0, "hidden", "occluded"]            # 完全挡住：一帧都不画
    assert result["text_hidden"] == "streamed while hidden"          # 脚本照跑
    frames, state, text, reason = result["back"]
    assert frames > 5 and state == "visible" and text == "streamed while hidden" and reason == ""
    assert result["locked"] == [0, "locked"]
    assert result["unlocked"] > 5
    assert result["minimized"] == [0, "minimized"]
    frames, reason = result["restored"]
    assert frames > 5 and reason == ""
