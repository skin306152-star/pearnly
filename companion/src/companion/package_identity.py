# -*- coding: utf-8 -*-
"""CC 桌面版是不是以 MSIX 包身份运行（从 Microsoft Store 装的那一份，packaging/msix/）。

教程问题第 1 条：开着「智能应用控制」的 Windows 11 拦下没签名的 CC-Setup.exe 与 companion.exe。不买证书的出路是上架
Microsoft Store：交 MSIX、由商店重新签名（docs/work/2026-10-20-cc-installer-fixes.md 第 1 条）。同一份程序既可能由
Inno 安装包装（主发布方式，照旧），也可能由商店装。以包身份运行时几处要换做法：

- 更新交给商店：不下载、不启动自己的安装包（gui_tray）；
- 开机自启用清单里的 StartupTask（autostart.py）：包内进程写 HKCU 的 Run 会被系统按包虚拟化，登录时不生效；
- 通知用包自己的 AppUserModelID（toast.py / main.py / window_main.py）：显式设的「Pearnly.Companion」在包里没有对应的
  开始菜单快捷方式，系统不收。

判据只用系统的 ``GetCurrentPackageFullName``：没有包身份时回 APPMODEL_ERROR_NO_PACKAGE（15700）。非 Windows、
接口不在（Windows 7）一律当没有包身份。结果在进程内不变，只查一次。
"""
from __future__ import annotations

import sys
from functools import lru_cache

APPMODEL_ERROR_NO_PACKAGE = 15700
ERROR_INSUFFICIENT_BUFFER = 122
STARTUP_TASK_ID = "CCStartup"   # 与 packaging/msix/AppxManifest.xml 的 desktop:StartupTask TaskId 一致


@lru_cache(maxsize=None)
def full_name() -> str:
    """包全名（``<名称>_<版本>_<架构>__<发布者号>``）；没有包身份是空串。"""
    if sys.platform != "win32":
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        func = kernel32.GetCurrentPackageFullName
    except (AttributeError, OSError):
        return ""
    func.argtypes = [ctypes.POINTER(wintypes.UINT), wintypes.LPWSTR]
    func.restype = wintypes.LONG
    length = wintypes.UINT(0)
    code = func(ctypes.byref(length), None)
    if code != ERROR_INSUFFICIENT_BUFFER or not length.value:
        return ""
    buffer = ctypes.create_unicode_buffer(length.value)
    if func(ctypes.byref(length), buffer) != 0:
        return ""
    return buffer.value


def packaged() -> bool:
    return bool(full_name())


@lru_cache(maxsize=None)
def app_user_model_id() -> str:
    """包内这个应用的 AppUserModelID（``<包族名>!<应用 Id>``）；没有包身份是空串。"""
    if not packaged():
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        func = ctypes.WinDLL("kernel32", use_last_error=True).GetCurrentApplicationUserModelId
    except (AttributeError, OSError):
        return ""
    func.argtypes = [ctypes.POINTER(wintypes.UINT), wintypes.LPWSTR]
    func.restype = wintypes.LONG
    length = wintypes.UINT(0)
    if func(ctypes.byref(length), None) != ERROR_INSUFFICIENT_BUFFER or not length.value:
        return ""
    buffer = ctypes.create_unicode_buffer(length.value)
    if func(ctypes.byref(length), buffer) != 0:
        return ""
    return buffer.value
