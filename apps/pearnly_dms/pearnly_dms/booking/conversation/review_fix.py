# -*- coding: utf-8 -*-
"""开单回执差异卡的三个按钮：อัปเดตข้อมูล（按确认时的值改回）/ ใช้ข้อมูลเดิม（以 DMS 为准）/ แก้ไข（编辑页）。

用户 2026-10-16："这批把差异卡三个按钮一起做"；方案见
`docs/work/2026-10-16-dms-booking-receipt-review.md`。

## 状态放哪

差异卡的状态（这张卡的确认号、差异项、开着 / 处理中 / 已完结）**单独存**在不淘汰的状态存储里
（`pearnly:dms:review-card:<投递记录>`，23 小时，与回执投递窗口一致），不写进回执投递记录
—— 那条记录由 `delivery` 在补附件时整条覆盖写，两边一起写会互相吃掉。

## 一张卡只处理一次

点按钮（或编辑页保存）先抢这张卡的认领键（`SET NX`）：抢到的那一下才往下走；同一张卡的
再次点击什么都不回（与订车确认的连点同一口径）；换过的旧卡、过期的卡才回「已过期」。

## 改单

改的永远是**同一张单**，只改有差异的那几项（`booking_review.compare` 带出的 `write`），
用开单那个销售自己的 DMS 账号（与补附件同一把账号锁，二者不会同时改这张单），
写之前登记"已发出"，之后任何失败都只读回、绝不重发；写完逐项再核对一遍。
不改客户主档，不开新单，不碰开单写入与"结果未知"那一套。
"""

from __future__ import annotations

import json
import logging
import secrets
from typing import Any, Dict, List, Optional

from pearnly_dms import i18n
from pearnly_dms.line import cards
from pearnly_dms.line import out as _out

logger = logging.getLogger(__name__)

ACTIONS = frozenset({cards.ACT_REVIEW_UPDATE, cards.ACT_REVIEW_KEEP})

_CARD = "pearnly:dms:review-card:"
_CLAIM = "pearnly:dms:review-claim:"
_TTL = 23 * 3600
_MAX_INPUT = 200
OPEN, WORKING, POSTED, DONE = "open", "working", "posted", "done"


def _state():
    from pearnly_core.execution import state

    return state.store()


def _load(record: str) -> Optional[Dict[str, Any]]:
    if not record:
        return None
    raw = _state().get_value(_CARD + record)
    if not raw:
        return None
    try:
        return json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (TypeError, ValueError):
        return None


def _save(card: Dict[str, Any]) -> None:
    _state().set_value(_CARD + card["record"], json.dumps(card, ensure_ascii=False), expires_in_sec=_TTL)


def _claim_key(record: str, nonce: str):
    store = _state()
    return store, store.make_key(f"{_CLAIM}{record}:{nonce}")


def _claim(record: str, nonce: str) -> bool:
    store, key = _claim_key(record, nonce)
    return bool(store.set(key, "1", nx=True, ex=_TTL))


def _claimed(record: str, nonce: str) -> bool:
    store, key = _claim_key(record, nonce)
    return bool(store.get(key))


def _in_flight(record: str, nonce: str) -> bool:
    """这次点的就是这张卡、而它已经被认领过（处理中或已处理完）：不回话，结果由认领到的那一下回。

    换过的旧卡（确认号不是当前这张）、过期的卡不算 —— 那些回「已过期」。
    """
    card = _load(record) or {}
    return bool(nonce) and card.get("nonce") == nonce and _claimed(record, nonce)


def _release(record: str, nonce: str) -> None:
    store, key = _claim_key(record, nonce)
    store.delete(key)


def open_card(record: str, line_user_id: str, booking_no: str, booking_id: str,
              diffs: List[Dict[str, Any]]) -> str:
    """登记一张新的差异卡（旧卡随之失效），返回它的确认号。"""
    nonce = secrets.token_hex(8)
    _save({"record": record, "nonce": nonce, "line_user_id": line_user_id, "booking_no": booking_no,
           "booking_id": str(booking_id), "diffs": diffs, "status": OPEN})
    return nonce


def _current(record: str, nonce: str, line_user_id: str) -> Optional[Dict[str, Any]]:
    card = _load(record)
    if (not card or not nonce or card.get("nonce") != nonce or card.get("line_user_id") != line_user_id
            or card.get("status") != OPEN):
        return None
    return card


# ── LINE 按钮 ────────────────────────────────────────────────────────────
def handle_postback(binding: dict, line_user_id: str, reply_token: str, action: str, pb: dict) -> None:
    record, nonce = str(pb.get("r") or ""), str(pb.get("n") or "")
    card = _current(record, nonce, line_user_id)
    if card is None or not _claim(record, nonce):
        if not _in_flight(record, nonce):
            _out.reply_text(reply_token, cards.TXT_EXPIRED)
        return
    if action == cards.ACT_REVIEW_KEEP:
        _save({**card, "status": DONE, "choice": "keep"})
        _out.reply_text(reply_token, cards.TXT_REVIEW_KEPT)
        return
    _queue(card, line_user_id, None, reply_token)


def _queue(card: Dict[str, Any], line_user_id: str, choices: Optional[dict], reply_token: str) -> None:
    from pearnly_dms.booking import delivery

    doc = delivery.record(card["record"])
    binding = ((json.loads(doc.attempt or "{}").get("context") or {}).get("binding") or {}) if doc else {}
    _save({**card, "status": WORKING})
    try:
        _out.enqueue("review", apply, binding, line_user_id, card["record"], card["nonce"], choices or {},
                     reply_token)
    except Exception:
        # 没排上队：卡还给他，再点一次即可（还没碰 DMS）。
        logger.exception("[dms review] 改单任务入队失败")
        _save({**card, "status": OPEN})
        _release(card["record"], card["nonce"])
        _out.push_or_reply_text(line_user_id, cards.TXT_SYSTEM_ERROR, reply_token)


# ── 编辑页 ───────────────────────────────────────────────────────────────
def _split(draft: str):
    record, _, nonce = str(draft or "").partition(".")
    return record, nonce


def page_load(line_user_id: str, draft: str) -> Dict[str, Any]:
    """编辑页首屏：有差异的各项（确认时 / DMS 现值）与可手改的文字栏。

    标签给**文案键**（`dms.card.*`），页面按自己的语言开关取词，与卡片同一份叫法。
    """
    record, nonce = _split(draft)
    card = _current(record, nonce, line_user_id)
    if card is None or _claimed(record, nonce):
        raise PermissionError("dms_booking.expired")
    items = [{
        "key": d["item"], "label": f"dms.card.{d['item']}", "sent": d.get("sent") or "",
        "dms": d.get("dms") or "",
        "inputs": [{"field": field, "label": f"dms.card.{key}",
                    "value": str((d.get("write") or {}).get(field) or "")}
                   for field, key in d.get("inputs") or []],
    } for d in card.get("diffs") or []]
    return {"booking_no": card.get("booking_no") or "", "items": items}


def page_save(line_user_id: str, draft: str, choices: Any) -> None:
    """编辑页保存：`choices = {项目键: {"use": "sent"|"dms", "inputs": {字段: 值}}}` → 排队改单。"""
    record, nonce = _split(draft)
    card = _current(record, nonce, line_user_id)
    if card is None or not _claim(record, nonce):
        raise PermissionError("dms_booking.expired")
    _queue(card, line_user_id, choices if isinstance(choices, dict) else {}, "")


def _clean(value: Any) -> str:
    text = " ".join(str(value if value is not None else "").split())
    return text[:_MAX_INPUT]


def targets(diffs: List[Dict[str, Any]], choices: Optional[dict]) -> List[Dict[str, Any]]:
    """要写回的项与值：没选或选「确认时」→ 写确认时的值（编辑页手改过的文字栏用手改的值）；选「DMS」→ 不动。"""
    out = []
    for diff in diffs or []:
        choice = (choices or {}).get(diff.get("item")) or {}
        if choice.get("use") == "dms":
            continue
        write = dict(diff.get("write") or {})
        typed = choice.get("inputs") or {}
        for field, _label in diff.get("inputs") or []:
            if field in typed:
                write[field] = _clean(typed[field])
        out.append({**diff, "write": write})
    return out


# ── 后台改单 ─────────────────────────────────────────────────────────────
def apply(binding: dict, line_user_id: str, record: str, nonce: str, choices: dict,
          reply_token: str = "") -> None:
    """后台任务：把选定的值写进同一张单，读回逐项核对，结果发回 LINE（令牌由 `run_guarded` 合并使用）。"""
    card = _load(record)
    if not card or card.get("nonce") != nonce or card.get("status") not in (WORKING, POSTED):
        return
    if card.get("status") == POSTED:
        # 上一次已经把改单发出去、没等到结果就中断了：不重发，只告诉他去核对。
        _finish(card, cards.TXT_REVIEW_UNVERIFIED)
        return
    todo = targets(card.get("diffs") or [], choices)
    if not todo:
        _save({**card, "status": DONE, "choice": "keep"})
        _out.push_or_reply_text(line_user_id, cards.TXT_REVIEW_KEPT)
        return
    from pearnly_dms.booking import delivery
    from pearnly_dms.booking.booking_amend import LOCKED, REJECTED, UPDATED, amend
    from pearnly_dms.booking.booking_review import unresolved
    from pearnly_dms.adapter.operations import run_logged_in

    endpoint = delivery.sales_endpoint(record)
    if not endpoint:
        _finish(card, cards.TXT_REVIEW_FAILED)
        return
    writes: Dict[str, str] = {}
    for target in todo:
        writes.update(target["write"])

    def posted():
        _save({**card, "status": POSTED})

    result = run_logged_in(endpoint, lambda client, adapter: amend(
        client, booking_id=card["booking_id"], booking_no=card["booking_no"], writes=writes,
        on_posted=posted))
    status = result.get("status") if isinstance(result, dict) else None
    sent = (_load(record) or {}).get("status") == POSTED
    if status is not None or sent:
        _audit(card, todo, status or (result or {}).get("error_code") or "unknown", binding)
    if status is None:
        if sent:
            _finish(card, cards.TXT_REVIEW_UNVERIFIED)
            return
        # 还没发出就失败（连不上、被挤下线……）：卡还给他，同一张卡再点一次即可。
        _save({**card, "status": OPEN})
        _release(record, nonce)
        friendly = (result or {}).get("error_friendly") or {}
        _out.push_or_reply_text(line_user_id, friendly.get(i18n.line_lang()) or cards.TXT_BOOKING_FAIL)
        return
    if status == LOCKED:
        _finish(card, cards.TXT_REVIEW_LOCKED)
        return
    if status == REJECTED:
        text = cards.TXT_REVIEW_FAILED
        _finish(card, f"{text}\n{result['message']}" if result.get("message") else text)
        return
    if status != UPDATED:
        _finish(card, cards.TXT_REVIEW_UNVERIFIED)
        return
    remaining = unresolved(todo, result.get("form") or {})
    if not remaining:
        _finish(card, cards.review_updated_text(card["booking_no"]))
        return
    _save({**card, "status": DONE})
    fresh = open_card(record, line_user_id, card["booking_no"], card["booking_id"], remaining)
    _out.send(line_user_id, cards.booking_review_card(card["booking_no"], remaining, record=record, nonce=fresh))


def _finish(card: Dict[str, Any], text: str) -> None:
    _save({**card, "status": DONE})
    _out.push_or_reply_text(card["line_user_id"], text)


def _audit(card: Dict[str, Any], todo: List[Dict[str, Any]], outcome: str, binding: dict) -> None:
    """推送台账一行（只记项目名与字段名，不记值）：改单是对外部系统的写入，要留痕。"""
    from pearnly_dms.booking import booking_ledger, delivery

    booking_ledger.log_amend(endpoint=delivery.endpoint_of(card["record"]), booking_no=card["booking_no"],
                             booking_id=card["booking_id"], items=[t["item"] for t in todo],
                             fields=sorted({f for t in todo for f in t["write"]}), outcome=outcome,
                             actor=(binding or {}).get("user_id"))


__all__ = ["ACTIONS", "apply", "handle_postback", "open_card", "page_load", "page_save", "targets"]
