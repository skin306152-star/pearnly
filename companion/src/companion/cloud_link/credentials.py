# -*- coding: utf-8 -*-
"""云端 Express 的登录凭据：存进本用户的 Windows 凭据管理器，目标 ``TERMSRV/<主机>``，**域凭据类型**
（``CRED_TYPE_DOMAIN_PASSWORD``；11-15 实测：TSplus 存的旧式 Generic 凭据，开着网络级身份验证的连接用不上）。

做法同 ``express_operator.credentials``（复用它的 ``CREDENTIAL`` 结构与 advapi32 绑定，不另写一份）：用户本人在 CC 自己的界面
输一次，只在这台电脑、只给这个 Windows 用户（``CRED_PERSIST_LOCAL_MACHINE``，系统用 DPAPI 加密）；不写文件、不传服务器、
不进日志。异常只带 Win32 错误码与函数名，从不带用户名、密码。系统按域凭据保护密码，本模块也不提供读密码的函数。

不是 Windows：``store`` 抛 :class:`NotWindows`，其余回「没有」。
"""
from __future__ import annotations

import ctypes
import sys

from ..express_operator import credentials as _base
from .rdp import host as _host
from .rdp import split_account

CRED_TYPE_DOMAIN_PASSWORD = 2
PREFIX = "TERMSRV/"


class NotWindows(RuntimeError):
    """在不是 Windows 的系统上存凭据。"""


def target(host_name: str) -> str:
    """凭据目标名 ``TERMSRV/<主机>``（主机按 ``rdp.host`` 校验、转小写）。"""
    return PREFIX + _host(host_name)


def credential_user(user: str, domain: str) -> str:
    """凭据里的用户名：有域写 ``域\\用户名``，没有只写用户名（各项按 ``rdp`` 校验）。"""
    user, domain = split_account(domain + "\\" + user if domain else user)
    return domain + "\\" + user if domain else user


def store(host_name: str, user: str, domain: str, password: str) -> None:
    """写（或覆盖）``TERMSRV/<主机>`` 的域凭据。失败抛 ``OSError(错误码, "CredWriteW")``。"""
    name, account = target(host_name), credential_user(user, domain)
    if sys.platform != "win32":
        raise NotWindows("cloud_link.credentials needs Windows")
    if not isinstance(password, str) or not password:
        raise ValueError("password")
    blob = password.encode("utf-16-le")
    buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    del blob
    try:
        cred = _base.CREDENTIAL(Type=CRED_TYPE_DOMAIN_PASSWORD, TargetName=name, Comment="Pearnly CC",
                                CredentialBlobSize=len(buf),
                                CredentialBlob=ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)),
                                Persist=_base.CRED_PERSIST_LOCAL_MACHINE, UserName=account)
        ok = _base.advapi32.CredWriteW(ctypes.byref(cred), 0)
        code = ctypes.get_last_error()
    finally:
        ctypes.memset(buf, 0, len(buf))
    if not ok:
        raise OSError(code, "CredWriteW")


def user_name(host_name: str) -> str:
    """这条凭据的用户名（``域\\用户名``，不是秘密）；没有 / 不是 Windows 回空串。不读密码。"""
    if sys.platform != "win32":
        return ""
    ptr = ctypes.POINTER(_base.CREDENTIAL)()
    if not _base.advapi32.CredReadW(target(host_name), CRED_TYPE_DOMAIN_PASSWORD, 0, ctypes.byref(ptr)):
        return ""
    try:
        return str(ptr.contents.UserName or "")
    finally:
        _base.advapi32.CredFree(ptr)


def exists(host_name: str) -> bool:
    return bool(user_name(host_name))


def forget(host_name: str) -> bool:
    """删 ``TERMSRV/<主机>`` 的域凭据；删了回 True，本来没有 / 不是 Windows 回 False。"""
    if sys.platform != "win32":
        return False
    return bool(_base.advapi32.CredDeleteW(target(host_name), CRED_TYPE_DOMAIN_PASSWORD, 0))
