# -*- coding: utf-8 -*-
"""LINE 多轮会话的读写：状态、载荷、TTL、以及**防重复点击的 nonce**。

照搬老产品 `services/line_dms/store.py` 的会话那半边（那边还有绑定/绑定码，
我们这边已经有一套了，见 `line_binding.py`）。**一处实现差异**：老产品每张表都带
`tenant_id` 并施加行级策略；我们"每产品一个站点"本身就是隔离，所以键只有
`line_user_id` —— 这样少一个参数，也少一处能写错的地方。

### 为什么 TTL 写在状态上，而不是调用点传

老产品的原话（保留）：待选车/待确认订车这两个态里客户档已经落定，会话是**重签选车
链接的唯一凭据** —— 它要是先于面板 token 断掉，入口就等于不存在，用户只剩重拍身份证
一条路（真扣一次 OCR 费）。所以寿命是**状态的属性**，写在表里，免得哪个调用点漏传。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

import frappe

logger = logging.getLogger(__name__)

DEFAULT_TTL_MINUTES = 30
# 见模块头：这几个态的会话比面板 token 活得久是有意为之
_STATE_TTL_MINUTES = {"picking": 120, "booking_review": 120, "booking_qa": 120}

DT = "Pearnly LINE Session"


def state_ttl_minutes(state: str) -> int:
    return _STATE_TTL_MINUTES.get(state, DEFAULT_TTL_MINUTES)


def _expires_at(ttl_minutes: int) -> str:
    return frappe.utils.add_to_date(frappe.utils.now(), minutes=int(ttl_minutes))


def _row(line_user_id: str) -> Optional[dict]:
    """读一行，**过期视为不存在**。"""
    if not line_user_id:
        return None
    try:
        name = frappe.db.get_value(DT, {"line_user_id": line_user_id, "expires_at": (">", frappe.utils.now())}, "name")
    except Exception:
        logger.warning("[line_session] 读会话失败，按无会话处理", exc_info=True)
        return None
    if not name:
        return None
    doc = frappe.get_doc(DT, name)
    payload = doc.payload
    if isinstance(payload, str):
        try:
            payload = json.loads(payload or "{}")
        except ValueError:
            payload = {}
    return {"state": doc.state, "payload": payload if isinstance(payload, dict) else {}}


def set_session(
    line_user_id: str,
    state: str,
    payload: Optional[Dict[str, Any]] = None,
    ttl_minutes: Optional[int] = None,
) -> None:
    """存/覆盖会话（upsert）。`ttl_minutes` 缺省按 state 查表。

    ⚠️ 哨兵用 `None` 不用 `0`：**`0` 是"立刻过期"这个合法值**（测试和强制失效在用），
    拿 0 当"没传"会把一个合法的强制失效悄悄改成默认 TTL。
    """
    if not line_user_id:
        return
    ttl = state_ttl_minutes(state) if ttl_minutes is None else int(ttl_minutes)
    try:
        name = frappe.db.get_value(DT, {"line_user_id": line_user_id}, "name")
        if name:
            doc = frappe.get_doc(DT, name)
        else:
            doc = frappe.new_doc(DT)
            doc.line_user_id = line_user_id
        doc.state = state
        doc.payload = json.dumps(payload or {}, ensure_ascii=False)
        doc.expires_at = _expires_at(ttl)
        doc.flags.ignore_permissions = True
        doc.save(ignore_permissions=True)
        frappe.db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[line_session] 写会话失败: %s", exc)


def get_session(line_user_id: str) -> Optional[dict]:
    """读未过期会话 → `{state, payload}`；过期/没有 → None。"""
    return _row(line_user_id)


def clear_session(line_user_id: str) -> None:
    """删会话（会话结束 / 取消）。"""
    if not line_user_id:
        return
    try:
        frappe.db.delete(DT, {"line_user_id": line_user_id})
        frappe.db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[line_session] 删会话失败: %s", exc)


def verify_nonce(
    sess: Optional[dict], nonce: Optional[str], expect_state: str = "reviewing"
) -> bool:
    """只校验不消费的那半边守卫（`consume_nonce` 是"校验 + 消费"的孪生）。

    读操作（开编辑菜单 / 重拍 / 保留旧档）**不该吃掉 nonce**，但也不能没有守卫 ——
    判据只此一份，别再在各 handler 里就地手写状态与 nonce 比较。
    """
    payload = (sess or {}).get("payload") or {}
    return bool(
        sess and sess.get("state") == expect_state and nonce and payload.get("nonce") == nonce
    )


def consume_nonce(
    line_user_id: str, expect_state: str, nonce: str
) -> Optional[dict]:
    """确认执行的守卫：会话须在 `expect_state` 且 `payload.nonce` 吻合 → **原子清 nonce**
    并返回 payload；态不符 / 无会话 / 无 nonce / 不吻合 → None（**绝不写**）。

    一次性 token：清掉 nonce 后同一 nonce 的二次确认必然 mismatch
    （防双击双写 / 双建单）。
    """
    sess = get_session(line_user_id)
    payload = (sess or {}).get("payload") or {}
    if not sess or sess.get("state") != expect_state:
        return None
    if not payload.get("nonce") or payload.get("nonce") != nonce:
        return None
    set_session(line_user_id, expect_state, {**payload, "nonce": None})
    return payload


def replace_review_payload(line_user_id: str, expected_nonce: str, payload: dict) -> bool:
    """受守卫地替换一份"待确认订车"草稿，并轮换它的 nonce（一次写入完成）。

    返回是否真的替换成功 —— 调用方据此决定要不要说"已更新"。
    """
    if not expected_nonce or not payload.get("nonce"):
        return False
    sess = get_session(line_user_id)
    if not sess or sess.get("state") != "booking_review":
        return False
    if (sess.get("payload") or {}).get("nonce") != expected_nonce:
        return False
    set_session(line_user_id, "booking_review", payload)
    return True
