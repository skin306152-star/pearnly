"""请在线电脑立刻整份重扫并上报账套（多产品共用的 CC 桌面版机器通道，主会话 11-15）。

网页「从连接添加」弹窗右上的刷新：人刚在 CC 里加了 Express 位置、还没到上报时间，点一下就让电脑马上重扫。用的是 CC 桌面版
早就有的那条指令（Cowork「重新读取」同一条，1.1.77 起）：领单回复里带 ``master_refresh = {request_id, scope_kind: "endpoint",
account_set_key: "@endpoint"}``，电脑下一拍对给这个成员的全部 Express 位置整份重扫、整份上报，心跳里带回同一个
``master_refresh_request_id`` 算扫完（:func:`acknowledge`）。不改桌面版。

请求按电脑记在站点缓存里（:data:`TTL` 后自动作废，电脑那边 300 秒也会放弃）：同一台还有没回执的就不再下发，连点只算一次。
"""
from __future__ import annotations

import uuid

import frappe

SCOPE, KEY = "endpoint", "@endpoint"
TTL = 300  # 与 CC 桌面版放弃一条重扫请求的时限相同（projection_refresh._REFRESH_TIMEOUT_S）


def _key(device) -> str:
    return f"cc-desktop-rescan:{device}"


def request(device_names) -> dict:
    """给这几台电脑各记一条整份重扫（已有没回执的沿用原编号）：回 ``{电脑: 请求编号}``。"""
    out = {}
    for device in device_names:
        current = frappe.cache.get_value(_key(device))
        if not current:
            current = str(uuid.uuid4())
            frappe.cache.set_value(_key(device), current, expires_in_sec=TTL)
        out[device] = current
    return out


def pending(device_names) -> list:
    """这几台里还没回执的。"""
    return [device for device in device_names if frappe.cache.get_value(_key(device))]


def command(device) -> dict | None:
    """（领单回复）这台电脑有待做的整份重扫就给出指令，没有回 ``None``。"""
    current = frappe.cache.get_value(_key(device)) if device else None
    return {"request_id": current, "scope_kind": SCOPE, "account_set_key": KEY} if current else None


def acknowledge(device, body) -> bool:
    """（心跳）电脑带回了这台的重扫编号（整份扫完、或扫出错）：清掉，页面据此不再等。"""
    request_id = body.get("master_refresh_request_id") if isinstance(body, dict) else None
    if not request_id or body.get("master_refresh_scope") != SCOPE:
        return False
    if frappe.cache.get_value(_key(device)) != request_id:
        return False
    frappe.cache.delete_value(_key(device))
    return True
