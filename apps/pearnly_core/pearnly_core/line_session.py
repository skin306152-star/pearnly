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
from pearnly_core.execution import state as pearnly_state

logger = logging.getLogger(__name__)

DEFAULT_TTL_MINUTES = 30
# 见模块头：这几个态的会话比面板 token 活得久是有意为之
_STATE_TTL_MINUTES = {"picking": 120, "booking_review": 120, "booking_qa": 120}

DT = "Pearnly LINE Session"


def state_ttl_minutes(state: str) -> int:
    return _STATE_TTL_MINUTES.get(state, DEFAULT_TTL_MINUTES)


def _expires_at(ttl_minutes: int) -> str:
    return frappe.utils.add_to_date(frappe.utils.now(), minutes=int(ttl_minutes))


def _row(line_user_id: str, *, for_update=False) -> Optional[dict]:
    """Expired drafts disappear; an unresolved external write must remain durable."""
    if not line_user_id:
        return None
    try:
        if for_update:
            rows = frappe.db.sql(
                "select state, payload, expires_at from `tabPearnly LINE Session` "
                "where line_user_id=%s for update",
                (line_user_id,), as_dict=True,
            )
            if not rows:
                return None
            row = rows[0]
            payload = json.loads(row.payload or "{}") if isinstance(row.payload, str) else row.payload
            payload = payload if isinstance(payload, dict) else {}
            if not payload.get("booking_attempt") and frappe.utils.get_datetime(row.expires_at) <= frappe.utils.get_datetime():
                return None
            return {"state": row.state, "payload": payload}
        name = frappe.db.get_value(DT, {"line_user_id": line_user_id}, "name")
    except frappe.QueryDeadlockError:
        # MariaDB can report 1020 when this transaction observed an older snapshot
        # before the locking read. This contender lost; never authorize or replay it.
        frappe.db.rollback()
        if for_update:
            raise  # Losing the lock cannot authorize replacing a pending booking.
        return None
    except Exception:
        if for_update:
            raise  # A failed locking read must never authorize a write.
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
    payload = payload if isinstance(payload, dict) else {}
    if not payload.get("booking_attempt") and frappe.utils.get_datetime(doc.expires_at) <= frappe.utils.get_datetime():
        return None
    return {"state": doc.state, "payload": payload}


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
        # Insert before a missing-row FOR UPDATE. Concurrent users otherwise take
        # overlapping InnoDB gap locks, then deadlock when both insert their rows.
        # The unique native name/LINE id resolves same-user races; no state is
        # overwritten until the existing row and pending-write guard are locked.
        if not frappe.db.exists(DT, line_user_id):
            frappe.get_doc({"doctype": DT, "line_user_id": line_user_id,
                            "state": "", "payload": "{}", "expires_at": _expires_at(ttl)}).insert(
                                ignore_permissions=True, ignore_if_duplicate=True)
        current = _row(line_user_id, for_update=True)
        marker = ((current or {}).get("payload") or {}).get("booking_attempt")
        if marker and marker != (payload or {}).get("booking_attempt"):
            raise RuntimeError("A submitted booking must be reconciled before replacing its session")
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
        raise


def get_session(line_user_id: str) -> Optional[dict]:
    """读未过期会话 → `{state, payload}`；过期/没有 → None。"""
    return _row(line_user_id)


def _comparison_row(line_user_id):
    """A losing CAS contender is rejected; it never proceeds to a write."""
    try:
        return _row(line_user_id, for_update=True)
    except frappe.QueryDeadlockError:
        return None  # _row already rolled back the stale MariaDB snapshot.


def replace_session_snapshot(line_user_id: str, expected: dict, state: str, payload: dict) -> bool:
    """Commit a slow read only if its source draft is still current (locking CAS)."""
    current = _comparison_row(line_user_id)
    if not expected or current != expected:
        return False
    if ((current or {}).get("payload") or {}).get("booking_attempt"):
        return False
    set_session(line_user_id, state, payload)
    return True


def clear_session(line_user_id: str, *, completed_booking_nonce: str = "") -> None:
    """删会话（会话结束 / 取消）。"""
    if not line_user_id:
        return
    try:
        current = _row(line_user_id, for_update=True)
        marker = ((current or {}).get("payload") or {}).get("booking_attempt") or {}
        if marker and (not completed_booking_nonce or marker.get("nonce") != completed_booking_nonce):
            raise RuntimeError("A submitted booking cannot be discarded before reconciliation")
        frappe.db.delete(DT, {"line_user_id": line_user_id})
        frappe.db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[line_session] 删会话失败: %s", exc)
        raise


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


def cancel_review(line_user_id: str, expect_state: str, nonce: str) -> bool:
    """Cancel only the clicked, unclaimed review; old cards cannot erase new work."""
    if expect_state not in {"reviewing", "booking_review"} or not nonce:
        return False
    sess = _comparison_row(line_user_id)
    if not verify_nonce(sess, nonce, expect_state):
        return False
    payload = sess.get("payload") or {}
    if payload.get("booking_claim") or payload.get("booking_attempt"):
        return False
    clear_session(line_user_id)
    return True


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
    sess = _comparison_row(line_user_id)
    payload = (sess or {}).get("payload") or {}
    if not sess or sess.get("state") != expect_state:
        return None
    if not payload.get("nonce") or payload.get("nonce") != nonce:
        return None
    claimed = {**payload, "nonce": None}
    if expect_state == "booking_review":
        claimed["booking_claim"] = nonce
        claimed["booking_claimed_at"] = frappe.utils.now()
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
    sess = _comparison_row(line_user_id)
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
        cache = pearnly_state.store()
        ok = cache.set(
            cache.make_key(_LOCK_PREFIX + str(line_user_id)), owner,
            nx=True, px=ATTEMPT_LOCK_SECONDS * 1000,
        )
    except Exception:  # noqa: BLE001
        logger.warning("[line_session] 抢会话锁失败", exc_info=True)
        return None
    return owner if ok else None


def _unlock_scope(line_user_id: str, owner: str) -> None:
    import frappe

    try:
        cache = pearnly_state.store()
        # A GET/DELETE pair can delete a new owner's lock after the old TTL
        # expires. Keep comparison and deletion atomic, with a Site-scoped key.
        cache.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end",
            1, cache.make_key(_LOCK_PREFIX + str(line_user_id)), owner,
        )
    except Exception:  # noqa: BLE001
        logger.warning("[line_session] 释放会话锁失败（到点会自己过期）", exc_info=True)


def record_booking_attempt(
    line_user_id: str, nonce: str, booking_no: str, owner: str,
    previous_no: Optional[str] = None, *, context: Optional[dict] = None,
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
        sess = _row(line_user_id, for_update=True)
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
        updated = {
                **payload,
                "booking_attempt": {
                    "nonce": str(nonce),
                    "booking_no": str(booking_no),
                    "owner": str(owner),
                    "recorded_at": frappe.utils.now(),
                    "context": context or {},
                },
            }
        # This CAS alone may advance a confirmed duplicate document number.
        # General set_session must continue refusing replacement of any attempt.
        name = frappe.db.get_value(DT, {"line_user_id": line_user_id}, "name")
        frappe.db.set_value(DT, name, "payload", json.dumps(updated, ensure_ascii=False))
        frappe.db.commit()
        return True
    except Exception:  # noqa: BLE001
        logger.warning("[line_session] 订车提交登记失败；写 DMS 被拦下", exc_info=True)
        return False
    finally:
        _unlock_scope(line_user_id, lock)


def update_booking_attempt(line_user_id: str, nonce: str, changes: dict) -> bool:
    """Persist reconciliation metadata without replacing the submitted draft/claim.

    Only result and delivery metadata may change; original identity and submitted
    fields stay immutable. The row lock excludes confirmation, rebind and cleanup.
    """
    if not nonce or not changes or set(changes) - {"result", "receipt", "checked_at"}:
        return False
    sess = _comparison_row(line_user_id)
    payload = (sess or {}).get("payload") or {}
    marker = payload.get("booking_attempt") or {}
    if (not sess or sess.get("state") != "booking_review" or payload.get("nonce") is not None
            or payload.get("booking_claim") != nonce or marker.get("nonce") != nonce):
        return False
    payload["booking_attempt"] = {**marker, **changes}
    name = frappe.db.get_value(DT, {"line_user_id": line_user_id}, "name")
    frappe.db.set_value(DT, name, "payload", json.dumps(payload, ensure_ascii=False))
    frappe.db.commit()
    return True


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
        sess = _row(line_user_id, for_update=True)
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
            if key not in {"booking_claim", "booking_claimed_at", "booking_attempt"}
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
        sess = _row(line_user_id, for_update=True)
        payload = (sess or {}).get("payload") or {}
        if not sess or sess.get("state") != "booking_review":
            return False
        if payload.get("nonce") is not None:
            return False
        if str(payload.get("booking_claim") or "") != str(nonce):
            return False
        if str((payload.get("booking_attempt") or {}).get("nonce") or "") != str(nonce):
            return False
        clear_session(line_user_id, completed_booking_nonce=nonce)
        return True
    except Exception:  # noqa: BLE001
        logger.warning("[line_session] 订车草稿清理失败", exc_info=True)
        return False
    finally:
        _unlock_scope(line_user_id, lock)


def release_rejected_booking_attempt(
    line_user_id: str,
    nonce: str,
    booking_no: str,
    payload: dict,
    ttl_minutes: Optional[int] = None,
) -> bool:
    """DMS **明确拒收**了这张订车单：摘掉提交登记，把草稿换上新确认号放回待确认态。

    只给"DMS 回了拒收、同一会话只读回读也确认没有这张单"那一种结局用 —— 那时单确实没建，
    操作员应当拿回原来的预览卡、改了再确认。判据全部对上才写（任一条不符 → False，不动会话）：

    1. 会话在 `booking_review`、`payload.nonce` 已被消费；
    2. 认领标记 `booking_claim` 与登记用的 nonce 都是这个 `nonce`；
    3. 登记的单号就是被拒的 `booking_no`（换过号的登记不认）；
    4. 登记里**还没有任何结果**（核实过的单绝不能被摘掉）。

    新草稿必须带**新 nonce**；`booking_claim` / `booking_claimed_at` / `booking_attempt`
    三个内部标记一律不带过去。之后再点确认就是一次全新的提交：重新认领、提交前重新登记。
    与 `record_booking_attempt` 同一把会话锁 + 行锁；现有函数一个不改，`set_session`
    仍然拒绝覆盖任何带登记的会话。
    """
    if not nonce or not booking_no or not (payload or {}).get("nonce"):
        return False
    lock = _lock_scope(line_user_id)
    if not lock:
        logger.warning("[line_session] 会话被占用，跳过拒收后的草稿放回")
        return False
    try:
        sess = _row(line_user_id, for_update=True)
        current = (sess or {}).get("payload") or {}
        marker = current.get("booking_attempt") or {}
        if not sess or sess.get("state") != "booking_review":
            return False
        if current.get("nonce") is not None:
            return False
        if str(current.get("booking_claim") or "") != str(nonce):
            return False
        if str(marker.get("nonce") or "") != str(nonce):
            return False
        if str(marker.get("booking_no") or "") != str(booking_no) or marker.get("result"):
            return False
        clean = {
            key: value
            for key, value in payload.items()
            if key not in {"booking_claim", "booking_claimed_at", "booking_attempt"}
        }
        ttl = state_ttl_minutes("booking_review") if ttl_minutes is None else int(ttl_minutes)
        name = frappe.db.get_value(DT, {"line_user_id": line_user_id}, "name")
        frappe.db.set_value(DT, name, {
            "state": "booking_review",
            "payload": json.dumps(clean, ensure_ascii=False),
            "expires_at": _expires_at(ttl),
        })
        frappe.db.commit()
        return True
    except Exception:  # noqa: BLE001
        logger.warning("[line_session] 拒收后的草稿放回失败", exc_info=True)
        return False
    finally:
        _unlock_scope(line_user_id, lock)
