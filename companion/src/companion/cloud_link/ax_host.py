# -*- coding: utf-8 -*-
"""云端 Express 连接窗口里的远程桌面控件宿主（只在 Windows、只在打开云端连接窗口时由 ``window`` 导入）。

为什么不用 PySide6 的 ``QAxWidget``：它能建出控件、能经 IDispatch 设属性，但拿不到控件的 IUnknown（没有
``queryInterface``，``asVariant`` 也转不出 IDispatch，Qt 的 AX 库静态编译、无导出），非脚本接口两项（只映射隧道盘、
CredSSP）做不了，窗口只能拒绝连接。这里改用 Windows 自带的 ATL 宿主：

- ``%SystemRoot%\\System32\\atl.dll`` 的 ``AtlAxWinInit`` 注册窗口类 ``AtlAxWin``；在 CC 自己的可见 Qt 窗口
  （``QWidget.winId()``）下建一个 ``AtlAxWin`` 原生子窗口，窗口文本为控件的 CLSID，ATL 据此建出控件；
  ``AtlAxGetControl`` 取控件的 IUnknown（已 AddRef，由本模块持有到关窗）。
  直接做子窗口、不用 ``QWindow.fromWinId`` + ``createWindowContainer``：连接窗口里只有这一个控件，没有别的 Qt 子部件要排版；
  少一层外来窗口的改父 / 焦点转交，生命周期由这里显式管（关窗时先断开、退订事件、放掉引用，再 ``DestroyWindow``）。
  父窗口加 ``WS_CLIPCHILDREN``，Qt 重画不盖住控件；窗口大小变了由 ``window`` 调 :meth:`AxHost.resize`。
- 控件 CLSID 用 ``MsRdpClient10NotSafeForScripting``（``{A0C63C30-F08D-4AB4-907C-34905D770C7D}``）：微软 Learn 上
  ``IMsRdpClientNonScriptable3`` 支持的 CLSID 列表里有它、没有 ``MsTscAx`` / ``MsTscAxNotSafeForScripting``；它的默认接口
  ``IMsRdpClient10`` 继承到 ``IMsRdpClient8`` 的 ``AdvancedSettings9``。
- 属性与方法：经 comtypes 的 IDispatch 动态调用（``GetIDsOfNames`` + ``Invoke``），:class:`Control` 提供与
  ``window.apply_settings`` 约定的 ``setProperty`` / ``querySubObject``，设不进去回假（由 ``apply_settings`` 抛错不连）。
- 非脚本两项不在这里：:meth:`Control.unknown_address` 把 IUnknown 裸指针（借用）交给 ``nonscriptable.apply``（ctypes 按 vtable）。
- 事件：comtypes 连接点接 ``IMsTscAxEvents``（IID ``{336D5562-EFA8-482E-8CB3-C5C0FC7A7DB6}``），只声明用到的六个
  （dispid 照微软 Learn 与类型库：1 ``OnConnecting``、2 ``OnConnected``、3 ``OnLoginComplete``、4 ``OnDisconnected``、
  10 ``OnFatalError``、22 ``OnLogonError``），转给 ``window.EventRouter``；别的事件控件照默认处理。

comtypes 只在 :class:`Com` 里导入（不连云端时 CC 不导入它，见 tests/test_cloud_lazy_import.py）；真正的 Win32 / COM 调用都经
:class:`Win32` 与 :class:`Com`，测试换成假的对象核调用顺序。密码只经 ``ClearTextPassword`` 属性交给控件、不进日志。
"""
from __future__ import annotations

import os
import sys
from typing import Any, Optional

from ..logger import get_logger

log = get_logger(__name__)

# MsRdpClient10NotSafeForScripting（见模块说明）
CONTROL_CLSID = "{A0C63C30-F08D-4AB4-907C-34905D770C7D}"
ATL_WINDOW_CLASS = "AtlAxWin"
IID_EVENTS = "{336D5562-EFA8-482E-8CB3-C5C0FC7A7DB6}"
# (dispid, 事件名, 参数名)：只声明窗口用到的六个
EVENTS = (
    (1, "OnConnecting", ()),
    (2, "OnConnected", ()),
    (3, "OnLoginComplete", ()),
    (4, "OnDisconnected", ("discReason",)),
    (10, "OnFatalError", ("errorCode",)),
    (22, "OnLogonError", ("lError",)),
)
# 事件名 → EventRouter 方法
ROUTES = {
    "OnConnecting": "on_connecting",
    "OnConnected": "on_connected",
    "OnLoginComplete": "on_login_complete",
    "OnDisconnected": "on_disconnected",
    "OnFatalError": "on_fatal_error",
    "OnLogonError": "on_logon_error",
}

WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_CLIPSIBLINGS = 0x04000000
WS_CLIPCHILDREN = 0x02000000
GWL_STYLE = -16


class AxUnavailable(RuntimeError):
    """控件宿主建不起来（不是 Windows、没有 comtypes、ATL 宿主或控件建不出）。文本只带步骤名和 HRESULT。"""


def available() -> bool:
    """这台电脑能不能建控件宿主（只看平台与 comtypes 是否装着，不导入 comtypes）。"""
    if sys.platform != "win32":
        return False
    import importlib.util

    return importlib.util.find_spec("comtypes") is not None


class EventForwarder:
    """控件事件 → ``window.EventRouter``。处理器里的异常只记类型名、不往控件抛。"""

    def __init__(self, router: Any) -> None:
        self._router = router

    def fire(self, name: str, *args: Any) -> None:
        target = ROUTES.get(name)
        if target is None:
            return
        try:
            getattr(self._router, target)(*(int(a or 0) for a in args))
        except Exception as error:  # noqa: BLE001 - 事件回调里不能让异常回到控件
            log.info("云端 Express 事件 %s 处理出错：%s", name, type(error).__name__)


def _hresult(error: BaseException) -> str:
    code = getattr(error, "hresult", None)
    if code is None and getattr(error, "args", None) and isinstance(error.args[0], int):
        code = error.args[0]
    return "%08X" % (int(code) & 0xFFFFFFFF) if isinstance(code, int) else type(error).__name__


class Control:
    """控件（或它的子对象，例如 ``AdvancedSettings9``）的 IDispatch 包装，形状同 ``window.apply_settings`` 要的。"""

    def __init__(self, com: Any, dispatch: Any, unknown_address: int = 0) -> None:
        self._com = com
        self._dispatch = dispatch
        self._address = int(unknown_address or 0)

    def setProperty(self, name: str, value: Any) -> bool:  # noqa: N802 - 与 apply_settings 的约定同名
        try:
            self._com.put(self._dispatch, name, value)
            return True
        except Exception as error:  # noqa: BLE001 - 设不进去：回假，由 apply_settings 抛错不连（不记值）
            log.info("云端 Express 控件属性 %s 设不进去：%s", name, _hresult(error))
            return False

    def querySubObject(self, name: str) -> Optional["Control"]:  # noqa: N802
        try:
            sub = self._com.get(self._dispatch, name)
        except Exception:  # noqa: BLE001 - 这一版控件没有这个子对象
            return None
        return Control(self._com, sub) if sub is not None else None

    def property(self, name: str) -> Any:
        return self._com.get(self._dispatch, name)

    def call(self, name: str, *args: Any) -> Any:
        return self._com.call(self._dispatch, name, *args)

    def unknown_address(self) -> int:
        """控件 IUnknown 裸指针（借用，``AxHost`` 关窗前一直有效）；子对象回 0。"""
        return self._address


class AxHost:
    """在 ``parent_hwnd`` 下建 ATL 宿主子窗口与控件、接上事件。:attr:`control` 给 ``window`` 设属性、连接、断开。

    构造失败（任何一步）都撤掉已做的并抛 :class:`AxUnavailable`。:meth:`close` 可重复调。"""

    def __init__(self, parent_hwnd: int, width: int, height: int, *, router: Any,
                 control_id: str = CONTROL_CLSID, win32: Any = None, com: Any = None) -> None:
        if (win32 is None or com is None) and not available():
            raise AxUnavailable("needs Windows with comtypes")
        self._closed = False
        self._hwnd = 0
        self._unknown: Any = None
        self._advise: Any = None
        self.control: Optional[Control] = None
        self._win32: Any = None
        self._com: Any = None
        try:
            self._win32 = win32 or Win32()
            self._com = com or Com()
            self._win32.init()
            self._win32.clip_children(int(parent_hwnd))
            self._hwnd = self._win32.create_child(int(parent_hwnd), control_id, int(width), int(height))
            self._unknown = self._com.adopt(self._win32.get_control(self._hwnd))
            dispatch = self._com.dispatch(self._unknown)
            self._advise = self._com.advise(self._unknown, EventForwarder(router))
            self.control = Control(self._com, dispatch, self._com.address(self._unknown))
        except BaseException as error:
            self.close()
            if isinstance(error, AxUnavailable):
                raise
            raise AxUnavailable("control host: %s" % _hresult(error)) from None

    @property
    def closed(self) -> bool:
        return self._closed

    def resize(self, width: int, height: int) -> None:
        if self._hwnd and not self._closed:
            self._win32.move(self._hwnd, max(int(width), 1), max(int(height), 1))

    def close(self) -> None:
        """退订事件、放掉 IDispatch / IUnknown、销毁子窗口（控件随之销毁）。断开由 ``window`` 在这之前做。"""
        if self._closed:
            return
        self._closed = True
        advise, self._advise = self._advise, None
        if advise is not None:
            try:
                self._com.unadvise(advise)
            except Exception as error:  # noqa: BLE001
                log.info("云端 Express 控件事件退订失败：%s", _hresult(error))
        self.control = None
        self._unknown = None
        hwnd, self._hwnd = self._hwnd, 0
        if hwnd:
            try:
                self._win32.destroy(hwnd)
            except Exception as error:  # noqa: BLE001
                log.info("云端 Express 控件窗口销毁失败：%s", _hresult(error))


# ── 真的 Win32 / COM 调用（只在 Windows 上建得出来）────────────────────────────

class Win32:
    """ATL 宿主与窗口的 ctypes 调用。atl.dll 按系统目录全路径加载，不走默认搜索路径。"""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise AxUnavailable("not windows")
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        system = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        self._atl = ctypes.WinDLL(os.path.join(system, "atl.dll"))
        self._atl.AtlAxWinInit.argtypes = []
        self._atl.AtlAxWinInit.restype = wintypes.BOOL
        self._atl.AtlAxGetControl.argtypes = [wintypes.HWND, ctypes.POINTER(ctypes.c_void_p)]
        self._atl.AtlAxGetControl.restype = ctypes.c_long
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                           wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      wintypes.BOOL]
        user32.MoveWindow.restype = wintypes.BOOL
        self._get_style = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
        self._set_style = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        self._get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        self._get_style.restype = ctypes.c_ssize_t
        self._set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self._set_style.restype = ctypes.c_ssize_t
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        self._instance = kernel32.GetModuleHandleW(None)
        self._user32 = user32

    def init(self) -> None:
        if not self._atl.AtlAxWinInit():
            raise AxUnavailable("AtlAxWinInit")

    def clip_children(self, parent: int) -> None:
        style = self._get_style(parent, GWL_STYLE)
        if not style & WS_CLIPCHILDREN:
            self._set_style(parent, GWL_STYLE, style | WS_CLIPCHILDREN)

    def create_child(self, parent: int, control_id: str, width: int, height: int) -> int:
        hwnd = self._user32.CreateWindowExW(0, ATL_WINDOW_CLASS, control_id,
                                            WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS | WS_CLIPCHILDREN,
                                            0, 0, width, height, parent, None, self._instance, None)
        if not hwnd:
            raise AxUnavailable("CreateWindowEx %d" % self._ctypes.get_last_error())
        return int(hwnd)

    def get_control(self, hwnd: int) -> int:
        out = self._ctypes.c_void_p()
        hr = self._atl.AtlAxGetControl(hwnd, self._ctypes.byref(out)) & 0xFFFFFFFF
        if hr != 0 or not out.value:
            raise AxUnavailable("AtlAxGetControl %08X" % hr)
        return int(out.value)

    def move(self, hwnd: int, width: int, height: int) -> None:
        self._user32.MoveWindow(hwnd, 0, 0, width, height, True)

    def destroy(self, hwnd: int) -> None:
        self._user32.DestroyWindow(hwnd)


class Com:
    """comtypes 那一侧：IDispatch 动态调用与连接点。导入 comtypes 在这里（界面线程，Qt 已把它初始化成 STA）。"""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise AxUnavailable("not windows")
        import ctypes

        import comtypes
        from comtypes import automation, connectionpoints

        self._ctypes = ctypes
        self._comtypes = comtypes
        self._automation = automation
        self._cp = connectionpoints
        self._events: Any = None

    def adopt(self, address: int) -> Any:
        """把 ``AtlAxGetControl`` 回的（已 AddRef 的）裸指针收成 comtypes 指针：``cast`` 不 AddRef，放掉时 Release 一次。"""
        ctypes = self._ctypes
        return ctypes.cast(ctypes.c_void_p(address), ctypes.POINTER(self._comtypes.IUnknown))

    def address(self, unknown: Any) -> int:
        return int(self._ctypes.cast(unknown, self._ctypes.c_void_p).value or 0)

    def dispatch(self, unknown: Any) -> Any:
        return unknown.QueryInterface(self._automation.IDispatch)

    @staticmethod
    def _id(dispatch: Any, name: str) -> int:
        return dispatch.GetIDsOfNames(name)[0]

    @staticmethod
    def _raw(value: Any) -> Any:
        # Invoke 回 VT_DISPATCH 时 comtypes 包成动态 Dispatch 对象，取回里面的 IDispatch 指针
        return getattr(value, "_comobj", value)

    def put(self, dispatch: Any, name: str, value: Any) -> None:
        dispatch.Invoke(self._id(dispatch, name), value, _invkind=self._automation.DISPATCH_PROPERTYPUT)

    def get(self, dispatch: Any, name: str) -> Any:
        return self._raw(dispatch.Invoke(self._id(dispatch, name), _invkind=self._automation.DISPATCH_PROPERTYGET))

    def call(self, dispatch: Any, name: str, *args: Any) -> Any:
        return self._raw(dispatch.Invoke(self._id(dispatch, name), *args, _invkind=self._automation.DISPATCH_METHOD))

    def events_interface(self) -> Any:
        """手写的 ``IMsTscAxEvents`` dispinterface（只含 :data:`EVENTS` 六个）。"""
        if self._events is None:
            comtypes = self._comtypes
            c_int = self._ctypes.c_int

            class IMsTscAxEvents(self._automation.IDispatch):
                _case_insensitive_ = False
                _iid_ = comtypes.GUID(IID_EVENTS)
                _idlflags_: list = []
                _methods_: list = []

            IMsTscAxEvents._disp_methods_ = [
                comtypes.DISPMETHOD([comtypes.dispid(number)], None, name, *(([], c_int, arg) for arg in args))
                for number, name, args in EVENTS
            ]
            self._events = IMsTscAxEvents
        return self._events

    def advise(self, unknown: Any, forwarder: EventForwarder) -> Any:
        events = self.events_interface()
        body: dict = {"_com_interfaces_": [events]}
        for _number, name, _args in EVENTS:
            body[name] = (lambda event: lambda self, *args: forwarder.fire(event, *args))(name)
        sink = type("CloudExpressEventSink", (self._comtypes.COMObject,), body)()
        container = unknown.QueryInterface(self._cp.IConnectionPointContainer)
        point = container.FindConnectionPoint(self._ctypes.byref(events._iid_))
        cookie = point.Advise(sink)
        return point, cookie, sink

    def unadvise(self, advise: Any) -> None:
        point, cookie, _sink = advise
        point.Unadvise(cookie)
