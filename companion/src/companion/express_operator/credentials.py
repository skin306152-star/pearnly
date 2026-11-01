# -*- coding: utf-8 -*-
"""Express 登录：用户本人授权一次，以后 CC 自动登录（试点附录 G）。

只有「CC 存、CC 填」两步：
- 存：用户本人在代录页「授权登录」输一次，存进 Windows 凭据管理器（普通凭据，``CRED_PERSIST_LOCAL_MACHINE``：只在这台电脑、
  只给这个 Windows 用户，系统用 DPAPI 加密）。不写文件、不传服务器、不进日志、不进截图；
- 填：``session`` 确认前台是 Express 登录窗口后，取出来直接打进登录框，只回成败。调用方（包括模型、服务端）拿不到内容。

每条 Express 连接一条凭据（``target(connection)``）；断开连接时 ``forget``。
"""
from __future__ import annotations

import ctypes
import sys
from typing import Optional, Tuple

CRED_TYPE_GENERIC, CRED_PERSIST_LOCAL_MACHINE = 1, 2
PREFIX = "Pearnly CC/Express/"

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
