# -*- coding: utf-8 -*-
"""远程桌面控件的**非脚本接口**两项（只在 Windows 上用）：开网络级身份验证（CredSSP）、驱动器只映射隧道盘。

这两项在 ``IMsRdpClientNonScriptable3`` 上（``EnableCredSspSupport``、``DriveCollection``），IDispatch 够不着，
只能按 vtable 调。安装包不带 comtypes / pywin32（见 companion.spec），所以这里只用 ctypes。

接口 IID、基类和 vtable 下标取自 SDK 的 ``mstsax.idl``，并与 mstscax.dll 类型库反编译出的 IDL 逐条核对一致：
- ``IMsRdpClientNonScriptable3`` ``{B3378D90-0728-45C7-8ED7-B6159FB92219}``：23 ``put_EnableCredSspSupport``、
  25 ``put_RedirectDynamicDrives``、30 ``get_DriveCollection``；
- ``IMsRdpDriveCollection`` ``{7FF17599-DA2C-4677-AD35-F60C04FE1585}``（基类 IUnknown）：3 ``RescanDrives``、
  4 ``get_DriveByIndex``、5 ``get_DriveCount``；
- ``IMsRdpDrive`` ``{D28B5458-F694-47A8-8E61-40356A767E46}``（基类 IUnknown）：3 ``get_Name``、4 ``put_RedirectionState``。

只设这几项，别的非脚本属性（``SendKeys``、各种警告框开关、密码相关）一律不碰：控件自己弹的框照常弹给人看。

做法：先把动态盘关掉、重扫一遍本机盘，再逐个盘只让隧道盘的 ``RedirectionState`` 为真，其余全部为假；
找不到隧道盘、任何一步 HRESULT 失败都抛 :class:`NonScriptableError`，窗口就不连（不退回「映射全部盘」）。
调用方先把 ``AdvancedSettings.RedirectDrives`` 设成假，盘只由这里逐个打开。

ctypes 的调用都经 :class:`ComCalls`；测试换成假的对象，只核调用顺序与取值。
"""
from __future__ import annotations

import re
import sys
from typing import Any, Callable, List, Optional

IID_NONSCRIPTABLE3 = "{B3378D90-0728-45C7-8ED7-B6159FB92219}"
IID_UNKNOWN = "{00000000-0000-0000-C000-000000000046}"

# IMsRdpClientNonScriptable3
V_PUT_ENABLE_CREDSSP = 23
V_PUT_REDIRECT_DYNAMIC_DRIVES = 25
V_GET_DRIVE_COLLECTION = 30
# IMsRdpDriveCollection
V_RESCAN_DRIVES = 3
V_GET_DRIVE_BY_INDEX = 4
V_GET_DRIVE_COUNT = 5
# IMsRdpDrive
V_GET_NAME = 3
V_PUT_REDIRECTION_STATE = 4

VARIANT_TRUE = -1
VARIANT_FALSE = 0
# 本机盘数的上限（防止控件回一个离谱的数时死循环）
MAX_DRIVES = 64


class NonScriptableError(RuntimeError):
    """非脚本设置没做成（文本只带步骤名和 HRESULT，不带路径以外的内容）。"""


def drive_letter(name: str) -> str:
    """控件给的盘名（例如 ``X:``、``Local Disk (X:)``、``Pearnly (X:)``）里的盘符；认不出回空串。"""
    found = re.findall(r"(?<![A-Za-z])([A-Za-z]):", str(name or ""))
    return found[-1].upper() if found else ""


class ComCalls:
    """真正的 ctypes 调用（只在 Windows 上建得出来）。所有方法收发裸指针（int）。"""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise NonScriptableError("not windows")
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._oleaut32 = ctypes.WinDLL("oleaut32")
        self._oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]
        self._oleaut32.SysFreeString.restype = None
        self._ole32 = ctypes.WinDLL("ole32")

    def _guid(self, text: str) -> Any:
        ctypes = self._ctypes

        class GUID(ctypes.Structure):
            _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]

        guid = GUID()
        hr = self._ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(guid))
        if hr != 0:
            raise NonScriptableError("guid")
        return guid

    def _method(self, ptr: int, index: int, *argtypes: Any) -> Callable[..., int]:
        ctypes = self._ctypes
        vtbl = ctypes.cast(ctypes.c_void_p(ptr), ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
        fn = proto(vtbl[index])
        return lambda *args: fn(ctypes.c_void_p(ptr), *args) & 0xFFFFFFFF

    def query(self, ptr: int, iid: str) -> int:
        ctypes = self._ctypes
        out = ctypes.c_void_p()
        hr = self._method(ptr, 0, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
            ctypes.byref(self._guid(iid)), ctypes.byref(out))
        if hr != 0 or not out.value:
            raise NonScriptableError("QueryInterface %08X" % hr)
        return int(out.value)

    def release(self, ptr: int) -> None:
        if ptr:
            self._method(ptr, 2)()

    def put_bool(self, ptr: int, index: int, value: bool) -> int:
        return self._method(ptr, index, self._ctypes.c_short)(VARIANT_TRUE if value else VARIANT_FALSE)

    def get_ulong(self, ptr: int, index: int) -> tuple:
        out = self._wintypes.ULONG()
        hr = self._method(ptr, index, self._ctypes.POINTER(self._wintypes.ULONG))(self._ctypes.byref(out))
        return hr, int(out.value)

    def get_ptr(self, ptr: int, index: int, *head: int) -> tuple:
        ctypes = self._ctypes
        out = ctypes.c_void_p()
        argtypes = [self._wintypes.ULONG] * len(head) + [ctypes.POINTER(ctypes.c_void_p)]
        hr = self._method(ptr, index, *argtypes)(*head, ctypes.byref(out))
        return hr, int(out.value or 0)

    def get_bstr(self, ptr: int, index: int) -> tuple:
        ctypes = self._ctypes
        out = ctypes.c_void_p()
        hr = self._method(ptr, index, ctypes.POINTER(ctypes.c_void_p))(ctypes.byref(out))
        text = ""
        if out.value:
            text = ctypes.wstring_at(out.value)
            self._oleaut32.SysFreeString(out)
        return hr, text


def _ok(hr: int, step: str) -> None:
    if hr != 0:
        raise NonScriptableError("%s %08X" % (step, hr))


def apply(control: int, *, drive: str, enable_credssp: bool = True, calls: Optional[Any] = None) -> List[str]:
    """在控件（``control``：控件的 IUnknown 裸指针）上开 CredSSP、只映射 ``drive`` 一个盘。回本机盘的盘符清单（只为记日志）。"""
    letter = str(drive or "").strip().rstrip(":\\").upper()
    if len(letter) != 1 or not letter.isalpha():
        raise NonScriptableError("drive")
    calls = calls or ComCalls()
    owned: List[int] = []
    try:
        ns3 = calls.query(control, IID_NONSCRIPTABLE3)
        owned.append(ns3)
        _ok(calls.put_bool(ns3, V_PUT_ENABLE_CREDSSP, bool(enable_credssp)), "EnableCredSspSupport")
        _ok(calls.put_bool(ns3, V_PUT_REDIRECT_DYNAMIC_DRIVES, False), "RedirectDynamicDrives")
        hr, drives = calls.get_ptr(ns3, V_GET_DRIVE_COLLECTION)
        if drives:
            owned.append(drives)  # 失败时万一也回了指针，照样 Release
        _ok(hr, "DriveCollection")
        if not drives:
            raise NonScriptableError("DriveCollection empty")
        _ok(calls.put_bool(drives, V_RESCAN_DRIVES, False), "RescanDrives")
        hr, count = calls.get_ulong(drives, V_GET_DRIVE_COUNT)
        _ok(hr, "DriveCount")
        if count > MAX_DRIVES:
            raise NonScriptableError("DriveCount %d" % count)
        seen: List[str] = []
        matched = 0
        for index in range(count):
            hr, one = calls.get_ptr(drives, V_GET_DRIVE_BY_INDEX, index)
            if not one:
                _ok(hr, "DriveByIndex")
                raise NonScriptableError("DriveByIndex empty")
            try:
                _ok(hr, "DriveByIndex")
                hr, name = calls.get_bstr(one, V_GET_NAME)
                _ok(hr, "Drive.Name")
                found = drive_letter(name)
                seen.append(found or "?")
                wanted = found == letter
                matched += 1 if wanted else 0
                _ok(calls.put_bool(one, V_PUT_REDIRECTION_STATE, wanted), "Drive.RedirectionState")
            finally:
                calls.release(one)
        if matched != 1:
            raise NonScriptableError("tunnel drive matched %d" % matched)
        return seen
    finally:
        for ptr in reversed(owned):
            calls.release(ptr)


def control_unknown(ax: Any) -> int:
    """从 ``QAxWidget`` 取控件的 IUnknown 裸指针（调用方用完要 Release，见 :func:`for_widget`）。

    PySide6 的 ``QAxBase.queryInterface(QUuid, void**)`` 在 Python 里怎么回值要在 Windows 上实测定；
    这里按两种常见形状收，都不是就抛错（不连）。"""
    from PySide6.QtCore import QUuid

    result = ax.queryInterface(QUuid(IID_UNKNOWN))
    if isinstance(result, tuple):
        hr, ptr = result[0], result[-1]
        if int(hr or 0) != 0:
            raise NonScriptableError("control QueryInterface %08X" % (int(hr) & 0xFFFFFFFF))
        result = ptr
    ptr = int(result or 0)
    if not ptr:
        raise NonScriptableError("control pointer")
    return ptr


def for_widget(calls_factory: Callable[[], Any] = ComCalls) -> Callable[[Any, Any], None]:
    """给 ``window.CloudWindow(nonscriptable=...)`` 用的函数：``(ax, NonScriptable) -> None``。"""

    def run(ax: Any, settings: Any) -> None:
        calls = calls_factory()
        unknown = control_unknown(ax)
        try:
            apply(unknown, drive=settings.drive, enable_credssp=settings.enable_credssp, calls=calls)
        finally:
            calls.release(unknown)

    return run
