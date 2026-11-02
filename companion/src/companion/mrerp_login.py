# -*- coding: utf-8 -*-
"""MR.ERP 登录在 CC 桌面版里授权（ADR-026 第 7 条，评估 §14.4，2026-11-02）：同一次输入存两处。

对话页点「在 CC 桌面版中授权」→ 站点发一张一次性票交给桥（``tomo_bridge`` 的 ``mrerp_authorize``）→ 桥弹原生小窗
（``login_dialog.ask``）收用户名和密码 → 这里：

1. 经这个站点成员的机器通道（HTTPS、机器令牌、精确 POST、不带会话）把票、用户名、密码交给站点，站点按发票的人当前的权限
   写进 MR.ERP 连接的 Password 字段，回连接号与「已授权：用户名 · 电脑 · 时间」（不回密码）；
2. 站点收下了才存进这台电脑的凭据管理器（``credentials.mrerp_target(站点, 连接号)``，代录填 MR.ERP 登录页用）。站点没收下
   本机就不存，两处不会一边有一边没有。

密码不进日志、不进回执、不进报错；出错只回码：``network``（连不上 / 超时 / 5xx）、``unauthorized``（这台电脑的密钥站点不认）、
站点给的码（``ticket_invalid`` 票过期或用过、``denied`` 没有权限、``login_invalid`` 报文不对、``unsupported`` 站点没接）、
``store_failed``（站点收下了、本机存不进去）。只在用户点授权时导入（不放在代录包里：代录包不带网络库，见
tests/test_operator_parts.py 的轻量检查）。
"""
from __future__ import annotations

import re
from typing import Callable, Optional

from . import channel
from .logger import get_logger

log = get_logger(__name__)

_CONNECTION = re.compile(r"^[A-Za-z0-9._:-]{1,140}$")
_TICKET = re.compile(r"^mrl_[A-Za-z0-9_-]{32,64}$")


def valid_ticket(value) -> bool:
    return isinstance(value, str) and bool(_TICKET.fullmatch(value))


def _post(backend_url: str, token: str, path: str, body: dict, timeout: float) -> tuple[int, dict]:
    import httpx

    from . import http_tls
    from .queue_client import unwrap

    with httpx.Client(base_url=backend_url, headers={"Authorization": f"Bearer {token}"},
                      timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)), verify=http_tls.context()) as client:
        response = client.post(path, json=body)
    if response.status_code != 200:
        return response.status_code, {}
    return 200, unwrap(response)


def authorize(*, site: str, backend_url: str, token: str, ticket: str, username: str, password: str,
              post: Optional[Callable] = None, store: Optional[Callable] = None, timeout: float = 20.0) -> dict:
    """交给站点、站点收下了再存本机。回 ``{ok: True, connection, authorized}`` 或 ``{ok: False, code}``；从不回密码。"""
    from .express_operator import credentials

    post = post or _post
    store = store or credentials.store
    path = channel.path(token, "mrerp_login") if channel.known(token) else ""
    if not path or not backend_url or not valid_ticket(ticket):
        return {"ok": False, "code": "login_invalid"}
    username = str(username or "").strip()
    try:
        status, reply = post(backend_url, token, path, {"ticket": ticket, "username": username, "password": password},
                             timeout)
    except Exception as error:  # noqa: BLE001 — 只记类型：异常文字里可能带请求体
        log.warning("MR.ERP 授权交给站点失败：%s", type(error).__name__)
        return {"ok": False, "code": "network"}
    if status in (401, 403):
        return {"ok": False, "code": "unauthorized"}
    if status == 400:
        return {"ok": False, "code": "login_invalid"}
    if status != 200:
        log.warning("MR.ERP 授权交给站点失败：HTTP %s", status)
        return {"ok": False, "code": "network"}
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        code = str((reply or {}).get("code") or "failed")[:40] if isinstance(reply, dict) else "failed"
        return {"ok": False, "code": code}
    connection = str(reply.get("connection") or "")
    if not _CONNECTION.fullmatch(connection):
        return {"ok": False, "code": "failed"}
    try:
        store(credentials.mrerp_target(site, connection), username, password)
    except Exception as error:  # noqa: BLE001
        log.warning("MR.ERP 登录存本机失败：%s", type(error).__name__)
        return {"ok": False, "code": "store_failed"}
    shown = reply.get("authorized") if isinstance(reply.get("authorized"), dict) else {}
    log.info("MR.ERP 登录已授权：连接 %s", connection)
    return {"ok": True, "connection": connection,
            "authorized": {key: str(shown.get(key) or "") for key in ("username", "device", "at")}}
