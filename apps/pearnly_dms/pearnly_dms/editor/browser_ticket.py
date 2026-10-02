# -*- coding: utf-8 -*-
"""浏览器编辑器页的**一次性票据**：LINE 验过身份之后，页面上每个请求拿它当凭证。

## 为什么不是"给页面开一个站点会话"

老产品那条链（`routes/line_dms_booking_edit_routes.py`，线上基准 501589f0）是"LIFF
`id_token` → 换一个只属于这次编辑的 **JWT**"，页面把它放在 `Authorization` 头里，
**从不建立站点会话**。新平台照这个来：

  · 票据不是登录态 —— 它**只能**调 `browser_*` 那组接口，进不了 Desk、也读不到别的数据；
  · 票据只绑 `line_user_id` + 编辑模式（订车/客户档），**不绑 nonce** —— nonce 每次保存
    都会轮换，绑了反而会在用户改两遍之后失效；真正的"这份草稿是不是你的、还新不新"
    由每个请求里的会话 + nonce 守卫判（见 `dms_booking_edit._review`）；
  · 解绑 / 会话过期立刻失效：每个请求都重新查一次绑定与会话，**票据本身不缓存任何权限**。

## 两种取得方式（同一张票据）

1. **LINE 内（正式）**：LIFF `id_token` 经 LINE 验签 → 拿到 LINE 用户 id。
2. **本站点已登录用户（回落）**：请求带着本站点的会话 cookie，且这个账号名下有生效的
   LINE 绑定 → 用他自己那条绑定。这一条是给"本机浏览器直接打开验收"和"没有 LIFF 的
   站点走网页回落链接"用的；它没有放宽任何权限 —— 拿到的仍然只是**他自己**那份草稿。

两种都不成立 → 不发票据（`dms_booking.line_auth_required`）。
"""

from __future__ import annotations

import logging
import secrets
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

PREFIX = "pearnly:dms:browser-ticket:"
# 30 分钟：够打开页面、改一遍、保存；和 `booking_review` 的会话 TTL 同量级。
TTL_SECONDS = 30 * 60

MODES = ("booking", "customer", "account")


def _state_store():
    """不淘汰的状态存储（``pearnly_core.execution.state``；用到时才导入）。"""
    from pearnly_core.execution import state as pearnly_state

    return pearnly_state.store()


def _key(ticket: str) -> str:
    return f"{PREFIX}{ticket}"


def normalize_mode(mode: str) -> str:
    """编辑模式归一：只认订车与客户档，别的值一律当订车（老产品 `_editor(mode)` 同款判据）。"""
    value = str(mode or "").strip()
    return value if value in MODES else "booking"


def _binding_identity(line_user_id: str):
    from pearnly_dms.line.identity import current
    return current(line_user_id)


def issue(line_user_id: str, mode: str) -> str:
    """发一张票据给这个 LINE 用户（按模式）。发不出去就抛，**不返回空串**。"""
    import frappe

    if not line_user_id:
        raise ValueError("ticket requires a line_user_id")
    identity = _binding_identity(line_user_id)
    if not identity:
        frappe.throw("dms_booking.not_bound", frappe.PermissionError)
    ticket = secrets.token_urlsafe(32)
    _state_store().set_value(
        _key(ticket),
        {"line_user_id": str(line_user_id), "mode": normalize_mode(mode), **identity},
        expires_in_sec=TTL_SECONDS,
    )
    return ticket


def resolve(ticket: str) -> Optional[Dict[str, Any]]:
    """票据 → `{line_user_id, mode}`；过期/伪造/读缓存出错 → None（fail closed）。"""
    import frappe

    token = str(ticket or "").strip()
    if not token:
        return None
    try:
        blob = _state_store().get_value(_key(token))
    except Exception:
        logger.warning("[dms browser] 读票据失败，按无效处理", exc_info=True)
        return None
    if not isinstance(blob, dict):
        return None
    line_user_id = str(blob.get("line_user_id") or "")
    if not line_user_id:
        return None
    identity = _binding_identity(line_user_id)
    if not identity or any(blob.get(key) != value for key, value in identity.items()):
        return None
    return {"line_user_id": line_user_id, "mode": normalize_mode(blob.get("mode")), **identity}


def revoke(ticket: str) -> None:
    """主动作废（页面关掉时不必调，TTL 会兜；这是给"改完就想立刻失效"留的口子）。"""
    import frappe

    token = str(ticket or "").strip()
    if not token:
        return
    try:
        _state_store().delete_value(_key(token))
    except Exception:
        logger.warning("[dms browser] 作废票据失败", exc_info=True)


__all__ = ["MODES", "TTL_SECONDS", "issue", "normalize_mode", "resolve", "revoke"]
