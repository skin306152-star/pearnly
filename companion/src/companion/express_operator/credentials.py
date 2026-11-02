# -*- coding: utf-8 -*-
"""Express 登录：用户本人授权一次，以后 CC 自动登录（试点附录 G）。

只有「CC 存、CC 填」两步：
- 存：用户本人在代录页「授权登录」输一次，存进 Windows 凭据管理器（普通凭据，``CRED_PERSIST_LOCAL_MACHINE``：只在这台电脑、
  只给这个 Windows 用户，系统用 DPAPI 加密）。不写文件、不传服务器、不进日志、不进截图；
- 填：``session`` 确认前台是 Express 登录窗口后，取出来直接打进登录框，只回成败。调用方（包括模型、服务端）拿不到内容。

每条 Express 连接一条凭据（``target(connection)``）；断开连接时 ``forget``。

MR.ERP（ADR-026 第 7 条，2026-11-02）：同样由用户本人在 CC 原生小窗输一次（``login_dialog.ask``），一条 MR.ERP 连接一条，
目标名带站点与连接号（``mrerp_target(site, connection)``，站点是这台电脑连着的 Pearnly 站点域名）。同一次输入另经机器通道
交给站点（``mrerp_login``）；本机这条只给代录的会话填 MR.ERP 登录页。心跳只报这台电脑上有哪些连接号（``mrerp_connections``），
不报账号。断开某个站点时 ``forget_site`` 一并删掉。
"""
from __future__ import annotations

import ctypes
import sys
from typing import Optional, Tuple

CRED_TYPE_GENERIC, CRED_PERSIST_LOCAL_MACHINE = 1, 2
PREFIX = "Pearnly CC/Express/"
MRERP_PREFIX = "Pearnly CC/MR.ERP/"

if sys.platform == "win32":
    from ctypes import wintypes as W

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", W.DWORD), ("Type", W.DWORD), ("TargetName", W.LPWSTR), ("Comment", W.LPWSTR),
                    ("LastWritten", W.FILETIME), ("CredentialBlobSize", W.DWORD),
                    ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", W.DWORD),
                    ("AttributeCount", W.DWORD), ("Attributes", ctypes.c_void_p), ("TargetAlias", W.LPWSTR),
                    ("UserName", W.LPWSTR)]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), W.DWORD]
    advapi32.CredReadW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    advapi32.CredDeleteW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD]
    advapi32.CredFree.argtypes = [ctypes.c_void_p]
    advapi32.CredEnumerateW.argtypes = [W.LPCWSTR, W.DWORD, ctypes.POINTER(W.DWORD),
                                        ctypes.POINTER(ctypes.POINTER(ctypes.POINTER(CREDENTIAL)))]


def target(connection: str) -> str:
    return PREFIX + str(connection or "default")


def store(target_name: str, user: str, password: str) -> None:
    blob = password.encode("utf-16-le")
    buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    cred = CREDENTIAL(Type=CRED_TYPE_GENERIC, TargetName=target_name, Comment="Pearnly CC",
                      CredentialBlobSize=len(blob), CredentialBlob=ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)),
                      Persist=CRED_PERSIST_LOCAL_MACHINE, UserName=user)
    ok = advapi32.CredWriteW(ctypes.byref(cred), 0)
    ctypes.memset(buf, 0, len(blob))
    if not ok:
        raise OSError(ctypes.get_last_error(), "CredWriteW")


def read(target_name: str) -> Optional[Tuple[str, str]]:
    """只给 ``session`` 填登录框用；不得记日志、不得往外传。"""
    if sys.platform != "win32":
        return None
    ptr = ctypes.POINTER(CREDENTIAL)()
    if not advapi32.CredReadW(target_name, CRED_TYPE_GENERIC, 0, ctypes.byref(ptr)):
        return None
    try:
        c = ptr.contents
        return c.UserName or "", ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-16-le")
    finally:
        advapi32.CredFree(ptr)


def exists(target_name: str) -> bool:
    return read(target_name) is not None


def forget(target_name: str) -> bool:
    return sys.platform == "win32" and bool(advapi32.CredDeleteW(target_name, CRED_TYPE_GENERIC, 0))


# ── MR.ERP ────────────────────────────────────────────────────────────────────

_SAFE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_:")


def _part(value: str) -> str:
    value = str(value or "").strip()
    if not value or len(value) > 140 or any(ch not in _SAFE for ch in value):
        raise ValueError("credential target")
    return value


def mrerp_target(site: str, connection: str) -> str:
    """这台电脑上这条 MR.ERP 连接的凭据目标名：``Pearnly CC/MR.ERP/<站点域名>/<连接号>``。"""
    return MRERP_PREFIX + _part(str(site or "").lower()) + "/" + _part(connection)


def targets(prefix: str) -> list:
    """凭据管理器里以 ``prefix`` 开头的目标名（只读名字，不读内容）。不是 Windows / 读不到回空表。"""
    if sys.platform != "win32":
        return []
    count, found = W.DWORD(0), ctypes.POINTER(ctypes.POINTER(CREDENTIAL))()
    if not advapi32.CredEnumerateW(prefix + "*", 0, ctypes.byref(count), ctypes.byref(found)):
        return []
    try:
        return [str(found[i].contents.TargetName or "") for i in range(count.value)]
    finally:
        advapi32.CredFree(found)


def mrerp_connections(site: str, *, listing=targets) -> list:
    """这台电脑上授权过的、属于这个站点的 MR.ERP 连接号（心跳 ``mrerp_logins``；只有连接号）。"""
    try:
        prefix = MRERP_PREFIX + _part(str(site or "").lower()) + "/"
    except ValueError:
        return []
    return sorted({name[len(prefix):] for name in listing(prefix) if name.startswith(prefix) and name[len(prefix):]
                   and "/" not in name[len(prefix):]})


def forget_site(site: str, *, listing=targets, remove=None) -> int:
    """断开这个站点（这台电脑上不再有它的成员）：删掉它的全部 MR.ERP 凭据，回删了几条。"""
    remove = remove or forget
    return sum(1 for connection in mrerp_connections(site, listing=listing)
               if remove(mrerp_target(site, connection)))
