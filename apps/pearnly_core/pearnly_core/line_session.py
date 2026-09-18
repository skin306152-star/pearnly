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
import uuid
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

    `booking_review` 这一态额外**留下认领标记** `booking_claim = <被消费的 nonce>`：
    "订车草稿被谁认领提交过"必须能从会话里读出来，否则防重复建单那一层无从判断
    （老产品叫 `_booking_claim`，语义逐字相同）。其它态不留 —— 建档那条链不需要。
    """
    sess = get_session(line_user_id)
    payload = (sess or {}).get("payload") or {}
    if not sess or sess.get("state") != expect_state:
        return None
    if not payload.get("nonce") or payload.get("nonce") != nonce:
        return None
    claimed = {**payload, "nonce": None}
    if expect_state == "booking_review":
        claimed["booking_claim"] = nonce
    set_session(line_user_id, expect_state, claimed)
    return payload


def replace_review_payload(
    line_user_id: str, expected_nonce: str, payload: dict, *, state: str = "booking_review"
) -> bool:
    """受守卫地替换一份"待确认"草稿，并轮换它的 nonce（一次写入完成）。

    返回是否真的替换成功 —— 调用方据此决定要不要说"已更新"。

    `state` 有两个合法取值（与老产品 `session_store.replace_review_payload` 一字不差）：
    `booking_review`（订车草稿）与 `reviewing`（客户档草稿 —— 浏览器编辑器改完客户资料
    后写回的就是这一态）。**别的态一律拒绝**：拿这个方法去改别的会话态会把守卫本身作废。
    """
    if state not in {"reviewing", "booking_review"}:
        return False
    if not expected_nonce or not payload.get("nonce"):
        return False
    sess = get_session(line_user_id)
    if not sess or sess.get("state") != state:
        return False
    if (sess.get("payload") or {}).get("nonce") != expected_nonce:
        return False
    set_session(line_user_id, state, payload)
    return True


# ── 订车"提交登记"：跨过写边界之前先记下来（防重复建单） ─────────────────────
#
# 这一对函数只服务一件事：**同一张订车草稿只能被提交一次**。线上是老平台的
# Postgres 单语句 CAS（`session_store.record_booking_attempt`，见 docs/p4/43）。
# 新平台的会话在 Frappe DocType 上，没有 CAS，所以改成"**Redis 锁 + 锁内读改写**"：
# 锁的粒度是 `line_user_id`（一个用户同时只可能有一次提交），锁内先读会话、
# 逐条判据、再写回去 —— 与线上那条 WHERE 的每一条判据**一一对应**。

_LOCK_PREFIX = "pearnly:line-session:lock:"
ATTEMPT_LOCK_SECONDS = 30


def _lock_scope(line_user_id: str):
    """抢这把会话锁。返回 owner（拿到）或 None（别人正在动这条会话）。

    ⚠️ 与 `adapter/master_shared` 同一套写法：Redis `SET NX PX`，进程崩了到点自己
    消失，不用清死锁。**读到的是 bytes，比较前要解码** —— 那个坑那边已经踩过。
    """
    import frappe

    owner = uuid.uuid4().hex
    try:
        ok = frappe.cache().set(
            _LOCK_PREFIX + str(line_user_id), owner,
            nx=True, px=ATTEMPT_LOCK_SECONDS * 1000,
        )
    except Exception:  # noqa: BLE001
        logger.warning("[line_session] 抢会话锁失败", exc_info=True)
        return None
    return owner if ok else None


def _unlock_scope(line_user_id: str, owner: str) -> None:
    import frappe

    try:
        current = frappe.cache().get(_LOCK_PREFIX + str(line_user_id))
        if isinstance(current, (bytes, bytearray)):
            current = current.decode("utf-8", "ignore")
        if current is not None and str(current) != str(owner):
            return  # 别人的锁不能删
        frappe.cache().delete(_LOCK_PREFIX + str(line_user_id))
    except Exception:  # noqa: BLE001
        logger.warning("[line_session] 释放会话锁失败（到点会自己过期）", exc_info=True)


def record_booking_attempt(
    line_user_id: str, nonce: str, booking_no: str, owner: str,
    previous_no: Optional[str] = None,
) -> bool:
    """把"我要提交这张草稿"登记进会话。**只有满足全部判据才写**，否则回 False。

    判据（与线上那条 CAS 的 WHERE 逐条对应）：

    1. 会话在 `booking_review` 态（提交中的草稿就是这个态）；
    2. `payload.nonce is None` —— 草稿**已被认领**（nonce 已被 `consume_nonce` 消费）；
    3. `payload.booking_claim == nonce` —— 认领的正是这一张，**换过的草稿顶不掉**；
    4. 要么**从来没登记过**（`previous_no` 为空且没有既有标记），
       要么是**同一个执行体在顺号重试**（`owner` 与 `booking_no` 都对上：
       DMS 说这个号重复了，同一个执行体换下一个号再登记，属于同一次提交）。

    任一条不满足 → 拒绝：调用方据此**不写 DMS**（宁可少写，不可重复建单）。
    """
    if not nonce or not booking_no or not owner:
        return False
    lock = _lock_scope(line_user_id)
    if not lock:
        # 抢不到锁说明另一个执行体正在登记同一张草稿 —— 这次绝不放行
        logger.warning("[line_session] 会话被占用，拒绝登记订车提交")
        return False
    try:
        sess = get_session(line_user_id)
        payload = (sess or {}).get("payload") or {}
        if not sess or sess.get("state") != "booking_review":
            return False
        if payload.get("nonce") is not None:
            return False
        if str(payload.get("booking_claim") or "") != str(nonce):
            return False
        marker = payload.get("booking_attempt") or {}
        if previous_no is None:
            if marker:
                return False  # 这一张已经登记过了
        else:
            same_execution = (
                str(marker.get("owner") or "") == str(owner)
                and str(marker.get("booking_no") or "") == str(previous_no)
            )
            if not same_execution:
                return False  # 别的执行体 / 别的号，不许顶掉
        set_session(
            line_user_id,
            "booking_review",
            {
                **payload,
                "booking_attempt": {
                    "nonce": str(nonce),
                    "booking_no": str(booking_no),
                    "owner": str(owner),
                },
            },
        )
        return True
    except Exception:  # noqa: BLE001
        logger.warning("[line_session] 订车提交登记失败；写 DMS 被拦下", exc_info=True)
        return False
    finally:
        _unlock_scope(line_user_id, lock)


def replace_claimed_booking_payload(
    line_user_id: str,
    expected_nonce: str,
    state: Optional[str],
    payload: Optional[dict],
    ttl_minutes: Optional[int] = None,
) -> bool:
    """改写 / 丢弃一张**已被认领、且还没登记过提交**的订车草稿。

    提交前的两条回头路要用它：主档在确认那一瞬间变了（改名 → 重发摘要；被删 → 退回
    对应步骤重选），以及"建单前就被 DMS 挡住、值得原样再试一次"。

    判据与线上那条 UPDATE 一一对应：会话在 `booking_review`、nonce 已被消费、
    认领标记就是这个 nonce、**且还没有登记过提交**（`booking_attempt` 为空）。
    最后一条要紧：登记过就说明请求已经发出去了，那时改草稿会让人误以为"没提交过"。

    `state=None` → 丢弃这张草稿（删会话）；否则写到 `state`。写 `booking_review`
    时必须带**新 nonce** —— 预览卡的确认按钮要能再次生效。落库前把 `booking_claim`
    与 `booking_attempt` 两个内部标记摘掉，不带到新草稿里（线上同款清理）。
    """
    if not expected_nonce or state not in {None, "booking_review", "booking_qa"}:
        return False
    if state == "booking_review" and not (payload or {}).get("nonce"):
        return False
    lock = _lock_scope(line_user_id)
    if not lock:
        logger.warning("[line_session] 会话被占用，跳过草稿改写")
        return False
    try:
        sess = get_session(line_user_id)
        current = (sess or {}).get("payload") or {}
        if not sess or sess.get("state") != "booking_review":
            return False
        if current.get("nonce") is not None:
            return False
        if str(current.get("booking_claim") or "") != str(expected_nonce):
            return False
        if current.get("booking_attempt"):
            return False  # 已经登记过提交 → 不许再动这张草稿
        if state is None:
            clear_session(line_user_id)
            return True
        clean = {
            key: value
            for key, value in (payload or {}).items()
            if key not in {"booking_claim", "booking_attempt"}
        }
        set_session(line_user_id, state, clean, ttl_minutes=ttl_minutes)
        return True
    except Exception:  # noqa: BLE001
        logger.warning("[line_session] 订车草稿改写失败", exc_info=True)
        return False
    finally:
        _unlock_scope(line_user_id, lock)


def clear_booking_attempt(line_user_id: str, nonce: str) -> bool:
    """提交收尾：**只清自己认领的那张草稿**，别人的草稿一律不动。

    判据：还在 `booking_review`、nonce 已被消费、认领标记就是这个 nonce、
    且登记用的也是这个 nonce。任一条不符 → False（不删）。
    """
    if not nonce:
        return False
    lock = _lock_scope(line_user_id)
    if not lock:
        logger.warning("[line_session] 会话被占用，跳过草稿清理")
        return False
    try:
        sess = get_session(line_user_id)
        payload = (sess or {}).get("payload") or {}
        if not sess or sess.get("state") != "booking_review":
            return False
        if payload.get("nonce") is not None:
            return False
        if str(payload.get("booking_claim") or "") != str(nonce):
            return False
        if str((payload.get("booking_attempt") or {}).get("nonce") or "") != str(nonce):
            return False
        clear_session(line_user_id)
        return True
    except Exception:  # noqa: BLE001
        logger.warning("[line_session] 订车草稿清理失败", exc_info=True)
        return False
    finally:
        _unlock_scope(line_user_id, lock)
