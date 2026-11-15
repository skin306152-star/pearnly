"""叫人的统一判据：人此刻是不是正看着某段对话（多产品共用；总设计第 15.2 节，用户 11-14 口径记忆 `cc-popups-into-conversation`）。

规矩：CC 发起的事，内容都在触发它的那段对话里；**人正看着那段对话就不响、不通知**，不在才叫（提示音 + 通知，通知只说
「那段对话有东西等你」）。以前没有人知道用户在看哪段：推送每条必弹、回复完成只看整窗焦点。

- 页面在三种时刻报一次 :func:`seen`：切对话、显示 / 隐藏、可见期间每 30 秒续一次；隐藏时报一次「不在」就停（空闲不产生请求）。
- 服务端只记在缓存里（不落库）：每个人一份 ``{设备: {对话, 时刻}}``，:data:`FRESH_SECONDS` 没续就算不在。
- :func:`watching`：这个人任一设备此刻正看着这段对话（人已经看到了，所有设备都不叫）。
- :func:`call`：叫人的唯一入口。正看着 → 什么都不发；不在 → 记一条挂在这段对话上的提醒（`notices.post`，提交后推送）。
- 推送发出前再判一次（`push.send_notice`）：提醒记下到推送发出之间人回到了那段对话，就不推（iPhone 的后台脚本收到就必须弹，
  不能收到后再判）。
"""
from __future__ import annotations

import re
import time

import frappe

FRESH_SECONDS = 75          # 页面可见期间每 30 秒续一次；两次没续上就算不在
KEY = "pearnly_assistant:attention:"
DEVICES_MAX = 20            # 一个人同时记几台设备（多了丢最旧的）
_DEVICE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _key(user: str) -> str:
    return KEY + str(user or "")


def _devices(user: str) -> dict:
    try:
        found = frappe.cache.get_value(_key(user))
    except Exception:  # noqa: BLE001 — 缓存读不出：当不在（照旧叫人，宁可多叫一次也不漏）
        return {}
    return dict(found) if isinstance(found, dict) else {}


def seen(session_id: str, visible: bool, device: str, user: str | None = None, now: float | None = None) -> dict:
    """页面报一次：这台设备此刻开着 ``session_id`` 这段对话、页面可见（``visible``）；隐藏 / 离开对话页报 ``visible=False``。"""
    user = user or frappe.session.user
    device = str(device or "")
    if not user or user == "Guest" or not _DEVICE.match(device):
        frappe.throw(frappe._("Invalid request"))
    now = time.time() if now is None else now
    devices = {name: item for name, item in _devices(user).items()
               if isinstance(item, dict) and now - float(item.get("at") or 0) <= FRESH_SECONDS}
    if visible and session_id:
        devices[device] = {"session": str(session_id), "at": now}
    else:
        devices.pop(device, None)
    if len(devices) > DEVICES_MAX:
        devices = dict(sorted(devices.items(), key=lambda pair: pair[1]["at"])[-DEVICES_MAX:])
    frappe.cache.set_value(_key(user), devices, expires_in_sec=FRESH_SECONDS)
    return {"watching": [item["session"] for item in devices.values()]}


def watching(user: str, session_id: str, now: float | None = None) -> bool:
    """这个人此刻有没有哪台设备正看着这段对话（没续上的设备不算）。"""
    if not user or not session_id:
        return False
    now = time.time() if now is None else now
    return any(isinstance(item, dict) and item.get("session") == str(session_id)
               and now - float(item.get("at") or 0) <= FRESH_SECONDS for item in _devices(user).values())


def call(user: str, session_id: str, kind: str, text: str, dedup_key: str, title: str = "") -> dict:
    """叫人：这段对话有东西等这个人（出卡等人答、结果晚到、出错结束）。正看着就不叫；不在就记一条挂在这段对话上的提醒
    （`notices.post`，去重键相同只记一次；推送在提交后发）。``text`` 只说状态（沿用现有的状态词），内容留在对话里。"""
    if watching(user, session_id):
        return {"called": False, "reason": "watching"}
    from pearnly_core.assistant import notices

    posted = notices.post(user, kind, text, dedup_key, title=title, conversation=session_id)
    return {"called": bool(posted.get("created")), "notice": posted.get("name") or ""}


__all__ = ["FRESH_SECONDS", "call", "seen", "watching"]
