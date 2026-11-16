# -*- coding: utf-8 -*-
"""远程桌面控件（MsTscAx）的事件 / 原因码 → 会话结果。纯函数，不做 IO。

出处（微软文档，2026-11-16 核对）：
- ``IMsTscAxEvents::OnDisconnected(long discReason)``：
  https://learn.microsoft.com/en-us/windows/win32/termserv/imstscaxevents-ondisconnected
- ``IMsRdpClient::ExtendedDisconnectReason`` 的取值 ``ExtendedDisconnectReasonCode``：
  https://learn.microsoft.com/en-us/windows/win32/termserv/extendeddisconnectreasoncode
- ``IMsTscAxEvents::OnLogonError(LONG lError)``：
  https://learn.microsoft.com/en-us/windows/win32/termserv/imstscaxevents-onlogonerror
- ``IMsTscAxEvents::OnFatalError(long errorCode)``：
  https://learn.microsoft.com/en-us/windows/win32/termserv/imstscaxevents-onfatalerror

11-15 实测（mstsc 连云端）：同账号在另一处连上时，这边客户端原因码 3（``disconnectReasonByServer``），提示「你已断开连接，
因为已与远程计算机建立了另一个连接」——这句是扩展原因 5（``exDiscReasonReplacedByOtherConnection``）的说明；
只写用户名不写域时原因码 2055（``SSL_ERR_LOGON_FAILURE``）。

结果只有四种：登录失败 / 被挤 → 停住等人；普通断线 → 退避重连；用户关闭 → 不重连。
判定顺序：本机主动断开 > 扩展原因 > 客户端原因码；认不出的一律当普通断线（只会按退避重试，最多每 15 分钟一次）。
"""
from __future__ import annotations

from enum import Enum
from typing import Optional


class Outcome(str, Enum):
    LOGIN_FAILED = "login_failed"   # 账号、密码、域或账号状态不对：停住，等人点「连接」
    REPLACED = "replaced"           # 同账号在别处连上，把这边挤掉：停住，等人点「连接」
    DROPPED = "dropped"             # 普通断线（网络、超时、服务器重启……）：退避重连
    USER_CLOSED = "user_closed"     # 人主动断开 / 注销 / 关窗：不重连


# ---- OnDisconnected 的 discReason ----
DISC_NO_INFO = 0
DISC_LOCAL_NOT_ERROR = 1        # disconnectReasonLocalNotError：本机断开（调用 Disconnect、点连接栏的关闭）
DISC_REMOTE_BY_USER = 2         # disconnectReasonRemoteByUser：远端用户自己断开
DISC_BY_SERVER = 3              # disconnectReasonByServer：服务器断开（被挤时也是它，要看扩展原因）

# 认证类（SSL_ERR_*，「x07」结尾那一组）：要人改账号 / 密码 / 域或找云端管理员，重试没用
LOGIN_DISC_REASONS = {
    2055: "SSL_ERR_LOGON_FAILURE",
    2567: "SSL_ERR_NO_SUCH_USER",
    2823: "SSL_ERR_ACCOUNT_DISABLED",
    3079: "SSL_ERR_ACCOUNT_RESTRICTION",
    3335: "SSL_ERR_ACCOUNT_LOCKED_OUT",
    3591: "SSL_ERR_ACCOUNT_EXPIRED",
    3847: "SSL_ERR_PASSWORD_EXPIRED",
    4615: "SSL_ERR_PASSWORD_MUST_CHANGE",
    5639: "SSL_ERR_DELEGATION_POLICY",
    5895: "SSL_ERR_POLICY_NTLM_ONLY",
    6151: "SSL_ERR_NO_AUTHENTICATING_AUTHORITY",
    8455: "SSL_ERR_FRESH_CRED_REQUIRED_BY_SERVER",
}

# ---- ExtendedDisconnectReason ----
EXT_NO_INFO = 0
EXT_API_DISCONNECT = 1          # exDiscReasonAPIInitiatedDisconnect
EXT_API_LOGOFF = 2              # exDiscReasonAPIInitiatedLogoff
EXT_IDLE_TIMEOUT = 3            # exDiscReasonServerIdleTimeout
EXT_LOGON_TIMEOUT = 4           # exDiscReasonServerLogonTimeout
EXT_REPLACED = 5                # exDiscReasonReplacedByOtherConnection
EXT_RPC_BY_USER = 11            # exDiscReasonRpcInitiatedDisconnectByUser（远端开始菜单「断开连接」）
EXT_LOGOFF_BY_USER = 12         # exDiscReasonLogoffByUser（远端注销）

LOGIN_EXT_REASONS = {
    7: "exDiscReasonServerDeniedConnection",
    8: "exDiscReasonServerDeniedConnectionFips",
    9: "exDiscReasonServerInsufficientPrivileges",
    10: "exDiscReasonServerFreshCredsRequired",
    768: "exDiscReasonRdpEncInvalidCredentials",
}
USER_EXT_REASONS = {EXT_API_DISCONNECT, EXT_API_LOGOFF, EXT_RPC_BY_USER, EXT_LOGOFF_BY_USER}

# ---- OnLogonError 的 lError ----
# 这些是登录真失败；其余（-2 继续登录、-3 静默结束、-4 重连框、-5 会话争用框、-6 无权限框、-7 拒绝断开框、3 提示框）
# 只是 Winlogon 在显示什么，不改状态——框由控件照常弹给人看，随后的 OnDisconnected 再定结果。
LOGIN_LOGON_ERRORS = {
    -1: "ERROR_CODE_ACCESS_DENIED",
    0: "LOGON_FAILED_BAD_PASSWORD",
    1: "LOGON_FAILED_UPDATE_PASSWORD",
    2: "LOGON_FAILED_OTHER",
    -1073741714: "STATUS_ACCOUNT_RESTRICTION",   # 0xC000006E
    -1073741715: "STATUS_LOGON_FAILURE",         # 0xC000006D
    -1073741276: "STATUS_PASSWORD_MUST_CHANGE",  # 0xC0000224
}


def signed32(value: int) -> int:
    """控件按 long 交来；经 COM / Qt 有时成了无符号数，统一成有符号 32 位。"""
    value = int(value) & 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def classify_disconnect(disc_reason: int, extended_reason: Optional[int] = None, *,
                        local_requested: bool = False) -> Outcome:
    """``OnDisconnected(discReason)`` + 当时读到的 ``ExtendedDisconnectReason`` → 结果。

    ``local_requested``：断开是本机这边要的（人关窗、点「断开」，代码随后调了控件的 ``Disconnect``）。"""
    if local_requested:
        return Outcome.USER_CLOSED
    disc = int(disc_reason or 0)
    ext = int(extended_reason or 0)
    if ext == EXT_REPLACED:
        return Outcome.REPLACED
    if ext in LOGIN_EXT_REASONS or disc in LOGIN_DISC_REASONS:
        return Outcome.LOGIN_FAILED
    if ext in USER_EXT_REASONS:
        return Outcome.USER_CLOSED
    if disc in (DISC_LOCAL_NOT_ERROR, DISC_REMOTE_BY_USER):
        return Outcome.USER_CLOSED
    return Outcome.DROPPED


def logon_error_is_failure(code: int) -> bool:
    """``OnLogonError(lError)`` 是不是登录真失败（其余只是 Winlogon 在显示什么）。"""
    return signed32(code) in LOGIN_LOGON_ERRORS


def classify_fatal(error_code: int) -> Outcome:
    """``OnFatalError(errorCode)``：控件自己出错（建窗口失败、内存不够、Winsock 初始化失败……）。

    与账号无关，按普通断线退避重试（最多每 15 分钟一次），不停住。"""
    del error_code
    return Outcome.DROPPED


def reason_name(disc_reason: int, extended_reason: Optional[int] = None) -> str:
    """给日志用的原因名（只有代码与名称，不含主机、账号）。"""
    disc = int(disc_reason or 0)
    ext = int(extended_reason or 0)
    parts = [LOGIN_DISC_REASONS.get(disc) or {0: "disconnectReasonNoInfo", 1: "disconnectReasonLocalNotError",
                                              2: "disconnectReasonRemoteByUser", 3: "disconnectReasonByServer"}
             .get(disc, f"disc_{disc}")]
    if ext:
        parts.append(LOGIN_EXT_REASONS.get(ext) or {
            1: "exDiscReasonAPIInitiatedDisconnect", 2: "exDiscReasonAPIInitiatedLogoff",
            3: "exDiscReasonServerIdleTimeout", 4: "exDiscReasonServerLogonTimeout",
            5: "exDiscReasonReplacedByOtherConnection", 6: "exDiscReasonOutOfMemory",
            11: "exDiscReasonRpcInitiatedDisconnectByUser", 12: "exDiscReasonLogoffByUser",
        }.get(ext, f"ext_{ext}"))
    return "/".join(parts)
