# -*- coding: utf-8 -*-
"""成员连的是哪一种服务端通道（ADR-025）：按配对码 / 密钥的前缀认，不另存。

- ``exp_…``：事务所的 Cowork 站点（``pearnly_cowork.companion.*``，原来那一套）；
- ``pcc_…``：共用机器通道（``pearnly_core.cc_desktop.machine.*``），ERP 站点用：员工用 ERP 账号在 CC 窗口里登录 ERP，
  对话页登记这台电脑后交来的配对码就是这一种。

两种通道的请求体与回复逐字段相同（服务端共用 ``cc_desktop.wire`` 的线上约定），这里只换方法路径；文件夹自动录入、认电脑
（``device_api.hello``）只有 Cowork 有。同一台电脑可以同时有一个 Cowork 成员和一个 ERP 成员，写账仍在同一条线程里一件一件写。
"""

from __future__ import annotations

COWORK = "cowork"
CORE = "core"
PREFIXES = {"exp_": COWORK, "pcc_": CORE}

_COWORK = "/api/method/pearnly_cowork.companion."
_CORE = "/api/method/pearnly_core.cc_desktop.machine."
PATHS = {
    COWORK: {
        "heartbeat": _COWORK + "heartbeat.report",
        "lease": _COWORK + "queue_api.lease",
        "ack": _COWORK + "queue_api.acknowledge",
        "request_next": _COWORK + "request_api.next_request",
        "request_complete": _COWORK + "request_api.complete_request",
        "hello": _COWORK + "device_api.hello",
        "leave": _COWORK + "device_api.leave",
    },
    CORE: {
        "heartbeat": _CORE + "heartbeat",
        "lease": _CORE + "lease",
        "ack": _CORE + "acknowledge",
        "request_next": _CORE + "next_request",
        "request_complete": _CORE + "complete_request",
        "leave": _CORE + "leave",
    },
}


def of_token(token: str) -> str:
    """密钥属于哪种通道；认不出（空、旧的手抄码之外的形状）当 Cowork，与以前一样。"""
    value = str(token or "").strip()
    return next((name for prefix, name in PREFIXES.items() if value.startswith(prefix)), COWORK)


def known(token: str) -> bool:
    value = str(token or "").strip()
    return any(value.startswith(prefix) for prefix in PREFIXES)


def path(token: str, operation: str) -> str:
    """这把密钥的成员调 ``operation`` 走的路径；这种通道没有这个方法回空串（调用方不调）。"""
    return PATHS[of_token(token)].get(operation, "")


def folders(token: str) -> bool:
    """这个成员有没有文件夹自动录入（只有 Cowork 有）。"""
    return of_token(token) == COWORK
