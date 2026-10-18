# -*- coding: utf-8 -*-
"""CC 窗口开着、却没人看得见时让对话页停画（窗口进程，2026-10-18 轻量化；10-18 测试机复测后重写）。

Qt 的网页内核只看窗口自己藏没藏起来：关窗口、最小化时 Qt 自己把页面设成看不见（QWebEngineView 的 hide / show 事件），
但不像 Chrome 那样在锁屏、屏幕关了、被别的窗口完全挡住时停画——页面上一直在动的东西就每秒画几十次（10-18 测试机：窗口开着
不动约 0.8 核，锁屏后照样）。这里补上 Windows 上的三种：

- **锁屏 / 解锁**（``WTSRegisterSessionNotification``）、**屏幕关 / 开**（``RegisterPowerSettingNotification`` 的
  ``GUID_CONSOLE_DISPLAY_STATE``）。登记在一个**单独的、从不显示的 QWindow**（``_MessageWindow``）上，不登记在 CC 窗口身上：
  对话页的渲染控件挂进 CC 窗口时，Qt 要把窗口从普通绘制换成 GPU 合成，会把窗口的系统句柄销毁再新建（Qt 6.11
  ``QWidget::setParent`` → ``QWidgetPrivate::recreate()`` = ``destroy(); create()``），登记在旧句柄上的通知从此收不到——
  这就是 08792e73 在测试机上锁屏不停画的原因（见 docs/work/2026-10-15-multi-desktop-build.md「锁屏 / 最小化停画」）。
- **被别的窗口完全挡住**：从 CC 窗口往 Z 序上面数，看得见、不透明、不是工具窗 / 弹出窗、没被系统藏起来（cloaked）、形状是
  矩形的窗口，把 CC 窗口在屏幕工作区里的部分全盖住才算（``occluding_rect`` / ``uncovered``，和 Chromium 的 Windows 遮挡判断同一套
  规则，拿不准的窗口一律不算挡住）；连续挡住 3 秒才停（来回切窗口不闪）。CC 窗口在最前面（或它的对话框在最前面）时不算。
  什么时候重算：前台窗口换了、有窗口最小化 / 还原、拖动 / 拉伸结束、虚拟桌面切换（系统事件钩子，频率很低）；挡住期间另盯挡着它
  的那几个进程的窗口移动 / 关闭 / 藏起；兜底再算：挡住时每 2 秒、没挡住又不在最前面时每 3 秒（例如前台窗口原地最大化）。
  只是失焦、还露着一块时照画（边看 CC 边在 Express 里操作）。
- 暂停用 ``page.setVisible(False)``：页面 ``visibilityState`` 变 hidden，``requestAnimationFrame``、动画、重画都停；**脚本照跑**
  （回复的流式输出、通知、关窗时问 ``window.tomo.busy()`` 都照常，计时器按浏览器规则降到每秒一次），看得见时网页内核按最新内容
  重画，页面自己的 ``visibilitychange`` 也会去补取数据。不用 ``Frozen``：冻结会停掉页面脚本，回复、通知、``busy`` 都会卡住。
  暂停时网页那块是窗口底色（解开后几十毫秒内画回来），所以只在能确定没人看得见时才停。

最小化、关窗口照旧由 Qt 自己设页面看不见，这里只记原因、不去改。状态变化写一行日志（window.log），并随 ``status`` 报给托盘，
托盘写进 companion.log（window_link.WindowHost）。非 Windows 只有关窗 / 最小化（Qt 自己处理）。
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QWindow

from .logger import get_logger
from .window_link import PAUSE_HIDDEN, PAUSE_LABELS, PAUSE_MINIMIZED

log = get_logger(__name__)

LOCKED, DISPLAY_OFF, OCCLUDED = "locked", "display_off", "occluded"

OCCLUDED_GRACE_MS = 3000      # 连续挡住这么久才停
CHECK_DELAY_MS = 150          # 系统事件来了等这么久再算（拖动时事件一串一串来）
POLL_VISIBLE_MS = 3000        # 不在最前面、没挡住：兜底多久重算一次
POLL_OCCLUDED_MS = 2000       # 挡住了（或快要算挡住）：兜底多久重算一次
MAX_WINDOWS_ABOVE = 2000      # Z 序上面最多看这么多个窗口，再多当作拿不准

# ── 锁屏 / 屏幕通知 ──
WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
NOTIFY_FOR_THIS_SESSION = 0
WM_POWERBROADCAST = 0x0218
PBT_POWERSETTINGCHANGE = 0x8013
DEVICE_NOTIFY_WINDOW_HANDLE = 0
# GUID_CONSOLE_DISPLAY_STATE {6FE69556-704A-47A0-8F24-C28D936FDA47}：数据 0 = 关、1 = 开、2 = 变暗
DISPLAY_STATE = (0x6FE69556, 0x704A, 0x47A0, (0x8F, 0x24, 0xC2, 0x8D, 0x93, 0x6F, 0xDA, 0x47))

# ── 遮挡 ──
GW_HWNDPREV = 3
GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_POPUP = 0x80000000
WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_LAYERED = 0x20, 0x80, 0x80000
LWA_COLORKEY, LWA_ALPHA = 0x1, 0x2
COMPLEXREGION = 3
DWMWA_EXTENDED_FRAME_BOUNDS, DWMWA_CLOAKED = 9, 14
MONITOR_DEFAULTTONEAREST = 2
WINEVENT_OUTOFCONTEXT = 0x0
EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_SYSTEM_MOVESIZEEND = 0x000B
EVENT_SYSTEM_MINIMIZESTART, EVENT_SYSTEM_MINIMIZEEND = 0x0016, 0x0017
EVENT_OBJECT_DESTROY, EVENT_OBJECT_HIDE = 0x8001, 0x8003
EVENT_OBJECT_LOCATIONCHANGE = 0x800B
EVENT_OBJECT_CLOAKED, EVENT_OBJECT_UNCLOAKED = 0x8017, 0x8018
OBJID_WINDOW, CHILDID_SELF = 0, 0
# 全局只听这几种（都很少发生）；挡住期间另按进程听挡着的那几个进程的窗口移动 / 关闭 / 藏起
GLOBAL_EVENTS = ((EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND), (EVENT_SYSTEM_MOVESIZEEND, EVENT_SYSTEM_MOVESIZEEND),
                 (EVENT_SYSTEM_MINIMIZESTART, EVENT_SYSTEM_MINIMIZEEND), (EVENT_OBJECT_CLOAKED, EVENT_OBJECT_UNCLOAKED))
OCCLUDER_EVENTS = ((EVENT_OBJECT_DESTROY, EVENT_OBJECT_HIDE), (EVENT_OBJECT_LOCATIONCHANGE, EVENT_OBJECT_LOCATIONCHANGE))

Rect = tuple   # (左, 上, 右, 下)，物理像素


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


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", ctypes.c_uint32)]


_FUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
_WINEVENTPROC = _FUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_long, ctypes.c_long,
                          ctypes.c_uint32, ctypes.c_uint32)
_MONITORENUMPROC = _FUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_RECT), ctypes.c_ssize_t)


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


def pause_reason(*, shown: bool, minimized: bool, locked: bool, display_off: bool, occluded: bool) -> str:
    """页面为什么该停画（空串 = 该画）。"""
    if not shown:
        return PAUSE_HIDDEN
    if minimized:
        return PAUSE_MINIMIZED
    if locked:
        return LOCKED
    if display_off:
        return DISPLAY_OFF
    if occluded:
        return OCCLUDED
    return ""


# ── 矩形算术（遮挡判断用，纯函数） ──

def _intersect(a: Rect, b: Rect) -> Optional[Rect]:
    left, top, right, bottom = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    return (left, top, right, bottom) if left < right and top < bottom else None


def _subtract(piece: Rect, cover: Rect) -> list:
    """``piece`` 去掉被 ``cover`` 盖住的部分，剩下的拆成最多四个矩形。"""
    overlap = _intersect(piece, cover)
    if overlap is None:
        return [piece]
    left, top, right, bottom = piece
    out = []
    if top < overlap[1]:
        out.append((left, top, right, overlap[1]))
    if overlap[3] < bottom:
        out.append((left, overlap[3], right, bottom))
    if left < overlap[0]:
        out.append((left, overlap[1], overlap[0], overlap[3]))
    if overlap[2] < right:
        out.append((overlap[2], overlap[1], right, overlap[3]))
    return out


def uncovered(area: Iterable[Rect], covers: Iterable[Rect]) -> list:
    """``area``（一组不重叠的矩形）被 ``covers`` 盖完以后还露着的部分。"""
    pieces = [tuple(r) for r in area]
    for cover in covers:
        pieces = [rest for piece in pieces for rest in _subtract(piece, tuple(cover))]
        if not pieces:
            break
    return pieces


def on_screen(rect: Rect, work_areas: Iterable[Rect]) -> list:
    """窗口落在各屏幕工作区（去掉任务栏）里的部分；屏幕之间不重叠，切出来的各块也不重叠。"""
    return [part for part in (_intersect(rect, work) for work in work_areas) if part is not None]


@dataclass
class WindowFacts:
    """CC 窗口上面的一个顶层窗口挡不挡得住东西要看的事（只对看得见、没最小化的窗口取）。"""

    rect: Optional[Rect]               # 看得见的边框（DWM 扩展边框，取不到用 GetWindowRect）
    style: int = 0
    ex_style: int = 0
    cloaked: bool = False              # 系统藏起来了（别的虚拟桌面、挂起的商店应用）
    layered: Optional[tuple] = None    # 分层窗口的 (flags, alpha)；取不到 = None（按透明算）
    complex_region: bool = False       # 不是矩形的窗口
    maximized_work: Optional[Rect] = None   # 最大化时它所在屏幕的工作区（最大化窗口的边框会伸到工作区外）


def occluding_rect(facts: WindowFacts) -> Optional[Rect]:
    """这个窗口能实打实挡住的矩形；拿不准（透明、分层半透明、工具窗、弹出窗、不规则、被藏起）一律 None。"""
    if facts.rect is None or facts.cloaked or facts.complex_region:
        return None
    if facts.ex_style & (WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW) or facts.style & WS_POPUP:
        return None
    if facts.ex_style & WS_EX_LAYERED:
        if facts.layered is None:
            return None
        flags, alpha = facts.layered
        if flags & LWA_COLORKEY or (flags & LWA_ALPHA and alpha < 255):
            return None
    rect = tuple(facts.rect)
    if facts.maximized_work is not None:
        rect = _intersect(rect, tuple(facts.maximized_work))
    return rect if rect is not None and rect[0] < rect[2] and rect[1] < rect[3] else None


# ── Windows 接口 ──

class Win32System:
    """（窗口进程，界面线程）真的 Windows 接口：登记锁屏 / 屏幕通知、系统窗口事件钩子、算 CC 窗口有没有被完全挡住。
    用自己的一份 DLL 句柄（``ctypes.WinDLL``），设函数签名不影响进程里别处用 ``ctypes.windll`` 的代码。"""

    def __init__(self, dlls: Optional[dict] = None) -> None:
        if dlls is None:
            dlls = {name: ctypes.WinDLL(name) for name in ("user32", "wtsapi32", "dwmapi", "gdi32")}
        user32, wts, dwm, gdi = dlls["user32"], dlls["wtsapi32"], dlls["dwmapi"], dlls["gdi32"]
        P, U, I = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int

        def sig(fn, restype, *argtypes):
            fn.restype, fn.argtypes = restype, list(argtypes)
            return fn

        self._register_session = sig(wts.WTSRegisterSessionNotification, I, P, U)
        self._unregister_session = sig(wts.WTSUnRegisterSessionNotification, I, P)
        self._register_power = sig(user32.RegisterPowerSettingNotification, P, P, ctypes.POINTER(_GUID), U)
        self._unregister_power = sig(user32.UnregisterPowerSettingNotification, I, P)
        self._set_hook = sig(user32.SetWinEventHook, P, U, U, P, _WINEVENTPROC, U, U, U)
        self._unhook = sig(user32.UnhookWinEvent, I, P)
        self._get_window = sig(user32.GetWindow, P, P, U)
        self._is_window = sig(user32.IsWindow, I, P)
        self._is_visible = sig(user32.IsWindowVisible, I, P)
        self._is_iconic = sig(user32.IsIconic, I, P)
        self._is_zoomed = sig(user32.IsZoomed, I, P)
        self._get_long = sig(user32.GetWindowLongW, ctypes.c_long, P, I)
        self._window_rect = sig(user32.GetWindowRect, I, P, ctypes.POINTER(_RECT))
        self._window_pid = sig(user32.GetWindowThreadProcessId, U, P, ctypes.POINTER(U))
        self._layered = sig(user32.GetLayeredWindowAttributes, I, P, ctypes.POINTER(U), ctypes.POINTER(ctypes.c_ubyte),
                            ctypes.POINTER(U))
        self._window_rgn = sig(user32.GetWindowRgn, I, P, P)
        self._monitor_from_window = sig(user32.MonitorFromWindow, P, P, U)
        self._monitor_info = sig(user32.GetMonitorInfoW, I, P, ctypes.POINTER(_MONITORINFO))
        self._enum_monitors = sig(user32.EnumDisplayMonitors, I, P, P, _MONITORENUMPROC, ctypes.c_ssize_t)
        self._dwm_attribute = sig(dwm.DwmGetWindowAttribute, ctypes.c_long, P, U, P, U)
        self._create_rgn = sig(gdi.CreateRectRgn, P, I, I, I, I)
        self._delete_object = sig(gdi.DeleteObject, I, P)
        self._on_event: Callable[[], None] = lambda: None
        self._event_proc = _WINEVENTPROC(self._event)   # 留住引用：钩子在就不能被回收
        self._global_hooks: list = []
        self._process_hooks: dict = {}
        self._power: list = []
        self._session_hwnd = None
        self._own_pid = os.getpid()
        self._told_space = False

    # 锁屏 / 屏幕
    def watch_session(self, hwnd: int) -> tuple:
        """在 ``hwnd`` 上登记锁屏 / 解锁与屏幕开 / 关通知；返回 (锁屏登记成功, 屏幕登记成功)。"""
        session = bool(self._register_session(hwnd, NOTIFY_FOR_THIS_SESSION))
        if session:
            self._session_hwnd = hwnd
        guid = _guid(DISPLAY_STATE)
        handle = self._register_power(hwnd, ctypes.byref(guid), DEVICE_NOTIFY_WINDOW_HANDLE)
        if handle:
            self._power.append(handle)
        return session, bool(handle)

    # 窗口事件
    def watch_windows(self, on_event: Callable[[], None]) -> int:
        """登记全局的几种低频窗口事件，来了就调 ``on_event``（界面线程，消息循环里）；返回登记上的个数。"""
        self._on_event = on_event
        for low, high in GLOBAL_EVENTS:
            hook = self._set_hook(low, high, None, self._event_proc, 0, 0, WINEVENT_OUTOFCONTEXT)
            if hook:
                self._global_hooks.append(hook)
        return len(self._global_hooks)

    def watch_processes(self, pids: Iterable[int]) -> None:
        """另外盯这几个进程（挡着 CC 窗口的）的窗口移动 / 关闭 / 藏起；不在里面的进程不再盯。"""
        wanted = {int(pid) for pid in pids if pid and int(pid) != self._own_pid}
        for pid in [pid for pid in self._process_hooks if pid not in wanted]:
            for hook in self._process_hooks.pop(pid):
                self._unhook(hook)
        for pid in wanted - set(self._process_hooks):
            hooks = []
            for low, high in OCCLUDER_EVENTS:
                hook = self._set_hook(low, high, None, self._event_proc, pid, 0, WINEVENT_OUTOFCONTEXT)
                if hook:
                    hooks.append(hook)
            self._process_hooks[pid] = hooks

    def _event(self, _hook, _event, hwnd, id_object, id_child, _thread, _time) -> None:
        if hwnd and id_object == OBJID_WINDOW and id_child == CHILDID_SELF:   # 光标、输入光标这些不管
            try:
                self._on_event()
            except Exception:
                pass

    def close(self) -> None:
        self.watch_processes(())
        for hook in self._global_hooks:
            self._unhook(hook)
        self._global_hooks = []
        if self._session_hwnd is not None:
            self._unregister_session(self._session_hwnd)
            self._session_hwnd = None
        for handle in self._power:
            self._unregister_power(handle)
        self._power = []

    # 遮挡
    def _cloaked(self, hwnd) -> bool:
        value = ctypes.c_uint32(0)
        ok = self._dwm_attribute(hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value)) == 0
        return bool(ok and value.value)

    def _bounds(self, hwnd) -> Optional[Rect]:
        rect = _RECT()
        if self._dwm_attribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)) != 0 \
                and not self._window_rect(hwnd, ctypes.byref(rect)):
            return None
        value = (rect.left, rect.top, rect.right, rect.bottom)
        return value if value[0] < value[2] and value[1] < value[3] else None

    def _same_space(self, hwnd, bounds: Rect) -> bool:
        """CC 窗口没有系统边框，DWM 边框与 GetWindowRect 应当重合；对不上（进程不是按每屏 DPI 算坐标）就别比了。"""
        rect = _RECT()
        if not self._window_rect(hwnd, ctypes.byref(rect)):
            return False
        same = max(abs(a - b) for a, b in zip(bounds, (rect.left, rect.top, rect.right, rect.bottom))) <= 2
        if not same and not self._told_space:
            self._told_space = True
            log.info("CC 窗口两种边框坐标对不上（%s / %s）：不判断遮挡", bounds,
                     (rect.left, rect.top, rect.right, rect.bottom))
        return same

    def _work(self, monitor) -> Optional[Rect]:
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not monitor or not self._monitor_info(monitor, ctypes.byref(info)):
            return None
        work = info.rcWork
        return work.left, work.top, work.right, work.bottom

    def _work_areas(self) -> list:
        areas: list = []

        def each(monitor, _dc, _rect, _data):
            work = self._work(monitor)
            if work is not None:
                areas.append(work)
            return 1

        self._enum_monitors(None, None, _MONITORENUMPROC(each), 0)
        return areas

    def _facts(self, hwnd, region) -> Optional[WindowFacts]:
        if not self._is_visible(hwnd) or self._is_iconic(hwnd):
            return None
        ex_style = self._get_long(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
        layered = None
        if ex_style & WS_EX_LAYERED:
            key, alpha, flags = ctypes.c_uint32(0), ctypes.c_ubyte(0), ctypes.c_uint32(0)
            if self._layered(hwnd, ctypes.byref(key), ctypes.byref(alpha), ctypes.byref(flags)):
                layered = (flags.value, alpha.value)
        maximized = self._work(self._monitor_from_window(hwnd, MONITOR_DEFAULTTONEAREST)) \
            if self._is_zoomed(hwnd) else None
        return WindowFacts(rect=self._bounds(hwnd), style=self._get_long(hwnd, GWL_STYLE) & 0xFFFFFFFF,
                           ex_style=ex_style, cloaked=self._cloaked(hwnd), layered=layered,
                           complex_region=bool(region) and self._window_rgn(hwnd, region) == COMPLEXREGION,
                           maximized_work=maximized)

    def occlusion(self, hwnd: int) -> Optional[tuple]:
        """CC 窗口（``hwnd``）是不是被完全挡住：(是否挡住, 挡着它的进程号)；拿不准返回 None（按看得见算）。"""
        if not hwnd or not self._is_window(hwnd):
            return None
        if self._cloaked(hwnd):
            return True, frozenset()          # 在别的虚拟桌面上
        rect = self._bounds(hwnd)
        if rect is None or not self._same_space(hwnd, rect):
            return None
        area = on_screen(rect, self._work_areas())
        if not area:
            return True, frozenset()          # 整个在屏幕外
        pids: set = set()
        region = self._create_rgn(0, 0, 0, 0)
        try:
            above, seen = self._get_window(hwnd, GW_HWNDPREV), 0
            while above and area:
                seen += 1
                if seen > MAX_WINDOWS_ABOVE:
                    return None
                facts = self._facts(above, region)
                cover = occluding_rect(facts) if facts is not None else None
                if cover is not None:
                    rest = uncovered(area, [cover])
                    if rest != area:
                        pid = ctypes.c_uint32(0)
                        self._window_pid(above, ctypes.byref(pid))
                        pids.add(pid.value)
                    area = rest
                above = self._get_window(above, GW_HWNDPREV)
        finally:
            if region:
                self._delete_object(region)
        return (not area), frozenset(pids)


def default_system():
    return Win32System() if sys.platform == "win32" else None


class _MessageWindow(QWindow):
    """只用来收锁屏 / 屏幕通知的窗口：从不显示、里面没有控件，系统句柄一直不变（CC 窗口的句柄 Qt 会换掉）。"""

    def __init__(self, on_message: Callable[[bytes, int], None]) -> None:
        super().__init__()
        self._on_message = on_message
        self.setFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.create()

    def nativeEvent(self, event_type, message):
        try:
            self._on_message(bytes(event_type), int(message))
        except Exception:
            pass
        return False, 0


class VisibilityWatch(QObject):
    """（窗口进程，界面线程）CC 窗口有没有人看得见 → 对话页停画 / 照画。``window`` 要有 ``page``（isVisible /
    setVisible）、``isVisible()``、``isMinimized()``、``isActiveWindow()``、``winId()``。``on_change(原因)`` 在原因变了时调。
    ``system``：None = 按平台（Windows 用 ``Win32System``，别的平台没有）；False = 不用；也可以给替身。"""

    def __init__(self, window, *, on_change: Optional[Callable[[str], None]] = None, system=None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__()
        self._window = window
        self._on_change = on_change or (lambda reason: None)
        self._clock = clock
        self._system = (default_system() if system is None else system) or None   # False = 不用系统接口（测试）
        self.locked = False
        self.display_off = False
        self.occluded = False
        self.reason: Optional[str] = None
        self._paused_at = clock()
        self._covered_since: Optional[float] = None
        self._occluders: frozenset = frozenset()
        self._closed = False
        self._messages: Optional[_MessageWindow] = None
        self._apply_soon = self._timer(0, self.apply)
        self._check = self._timer(CHECK_DELAY_MS, self._check_occlusion)
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._check_occlusion)
        window.installEventFilter(self)
        self._signals = []
        for name in ("visibleChanged", "loadFinished"):   # 页面被别处设回看得见（视图的 show 事件）、刚载完：再对一次
            signal = getattr(window.page, name, None)
            if signal is not None:
                signal.connect(self._page_changed)
                self._signals.append(signal)
        if self._system is not None:
            self._start_system()
        self.apply()

    def _page_changed(self, *_args) -> None:
        if not self._closed:
            self._apply_soon.start()

    def _timer(self, interval: int, slot) -> QTimer:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(interval)
        timer.timeout.connect(slot)
        return timer

    def _start_system(self) -> None:
        try:
            self._messages = _MessageWindow(self.native_event)
            hwnd = int(self._messages.winId())
            session, display = self._system.watch_session(hwnd)
            hooks = self._system.watch_windows(self._window_event)
            log.info("CC 窗口看不见时停画：锁屏通知%s、屏幕通知%s、窗口事件 %s 个（登记在消息窗口 %#x 上）",
                     "已登记" if session else "登记失败", "已登记" if display else "登记失败", hooks, hwnd)
        except Exception:
            log.warning("登记锁屏 / 屏幕 / 窗口事件失败（窗口照常用，只是看不见时不一定停画）", exc_info=True)

    # ── 收到的 ──
    def native_event(self, event_type: bytes, message: int) -> None:
        """消息窗口收到的系统消息：是锁屏 / 屏幕的就更新。"""
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
        if not value:
            self._soon()   # 解锁 / 亮屏：重算一次挡没挡住

    def present(self) -> None:
        """有人在用 CC 窗口（激活了、从托盘 / 通知 / 快捷键打开）：一定没锁屏、屏幕亮着、没被挡住（万一漏了通知，也不会把
        正在看的页面当成看不见）。"""
        self.locked = self.display_off = False
        self._uncover()
        self.apply()

    def _uncover(self) -> None:
        self.occluded = False
        self._covered_since = None
        self._occluders = frozenset()

    def _window_event(self) -> None:
        self._soon()

    def eventFilter(self, watched, event) -> bool:
        # Python 回收到一半（循环引用清理先清掉了属性、Qt 对象还挂着过滤器）时 Qt 仍可能递来事件：这时什么都不做
        window = self.__dict__.get("_window")
        if window is not None and watched is window:
            kind = event.type()
            if kind == QEvent.Type.ActivationChange:
                if self._window.isActiveWindow():
                    self.present()
                else:
                    self._soon()
            elif kind in (QEvent.Type.Show, QEvent.Type.WindowStateChange):
                self._uncover()   # 刚显示 / 刚还原 / 刚最大化：先当看得见，再重算
                self._soon()
            if kind in (QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.WindowStateChange,
                        QEvent.Type.ActivationChange):
                self._apply_soon.start()   # Qt 自己处理完（视图的 show / hide 会改页面）再对一次
        return False

    # ── 遮挡 ──
    def _watching(self) -> bool:
        window = self._window
        return bool(self._system is not None and not self._closed and window.isVisible() and not window.isMinimized()
                    and not self.locked and not self.display_off and not window.isActiveWindow())

    def _soon(self) -> None:
        if self._watching() and (not self._check.isActive() or self._check.remainingTime() > CHECK_DELAY_MS):
            self._check.start(CHECK_DELAY_MS)

    def _check_occlusion(self) -> None:
        if not self._watching():
            return
        try:
            result = self._system.occlusion(int(self._window.winId()))
        except Exception:
            log.debug("算 CC 窗口有没有被挡住失败", exc_info=True)
            result = None
        covered, pids = result if result is not None else (False, frozenset())
        if not covered:
            self._uncover()
        else:
            self._occluders = frozenset(pids)
            if not self.occluded:
                now = self._clock()
                if self._covered_since is None:
                    self._covered_since = now
                waited = (now - self._covered_since) * 1000
                if waited + CHECK_DELAY_MS >= OCCLUDED_GRACE_MS:
                    self.occluded = True
                else:
                    self._check.start(int(OCCLUDED_GRACE_MS - waited))
        self.apply()

    def _plan(self) -> None:
        """按现在的状态开 / 停兜底重算与对挡着的进程的盯梢。"""
        if self._system is None:
            return
        if not self._watching():
            self._poll.stop()
            self._check.stop()
            self._system.watch_processes(())
            return
        covering = self.occluded or self._covered_since is not None
        interval = POLL_OCCLUDED_MS if covering else POLL_VISIBLE_MS
        if not self._poll.isActive() or self._poll.interval() != interval:
            self._poll.start(interval)
        self._system.watch_processes(self._occluders if covering else ())

    # ── 设页面 ──
    def apply(self) -> None:
        if self._closed:
            return
        window = self._window
        shown, minimized = bool(window.isVisible()), bool(window.isMinimized())
        reason = pause_reason(shown=shown, minimized=minimized, locked=self.locked, display_off=self.display_off,
                              occluded=self.occluded)
        if shown and not minimized:   # 关着 / 最小化时 Qt 自己设页面看不见，不去动
            want = not reason
            try:
                if bool(window.page.isVisible()) != want:
                    window.page.setVisible(want)
            except RuntimeError:
                return   # 页面已经删了（窗口进程正在退出）
        try:
            self._plan()
        except Exception:
            log.debug("开 / 停遮挡重算失败", exc_info=True)
        if reason != self.reason:
            self._changed(reason)

    def _changed(self, reason: str) -> None:
        previous, self.reason = self.reason, reason
        now = self._clock()
        if reason and previous in (None, "", PAUSE_HIDDEN):
            self._paused_at = now   # 从照画（或窗口刚开）变成停画；换原因不算重新开始
        if previous is not None and PAUSE_HIDDEN not in (previous, reason):   # 开 / 关窗口另有日志
            if not reason:
                log.info("CC 窗口看得见：页面已恢复（停了 %d 秒）", round(now - self._paused_at))
            else:
                log.info("CC 窗口看不见（%s）：页面已暂停", PAUSE_LABELS.get(reason, reason))
        try:
            self._on_change(reason)
        except Exception:
            log.exception("报告页面暂停状态失败")

    def close(self) -> None:
        """窗口进程要退出了：撤掉登记、钩子与消息窗口。"""
        if self._closed:
            return
        self._closed = True
        for timer in (self._apply_soon, self._check, self._poll):
            timer.stop()
        try:
            self._window.removeEventFilter(self)
            for signal in self._signals:
                signal.disconnect(self._page_changed)
        except Exception:
            pass
        self._signals = []
        if self._system is not None:
            try:
                self._system.close()
            except Exception:
                pass
        if self._messages is not None:
            self._messages.destroy()
            self._messages = None
