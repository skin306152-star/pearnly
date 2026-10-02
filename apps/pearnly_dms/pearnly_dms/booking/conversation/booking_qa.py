# -*- coding: utf-8 -*-
"""DMS LINE 订车逐问状态机：在聊天里逐项采集订车资料。

照搬老产品 `services/line_dms/booking_qa.py`（线上基准 `501589f0`，逻辑未改），
async → 同步。

几条不能动的规矩：

· **会话保存**客户、答案、付款、附件与主档快照；**转账才要凭证**；收官轮换 nonce。
· **翻页令牌永不写进 `answers`** —— 它只是"在看第几页"，不是选了什么。
· **串步按钮 / 旧卡残留一律原步重问**，吃下但不推进状态（翻历史卡不会误跳步）。
· 每次输入**按序记 audit**；金额一律 `Decimal`（在 `dms_qa_util`）。
· 全局命令（`เริ่มใหม่` / `เมนู`）交上层路由，这一层不抢。

新平台的两处接口差异：

1. 会话按 **LINE 用户**存（一个站点一家租户），`line_session` 的键就是 `line_user_id`；
   老产品那套 `tenant_id` 参数在这一层只作形状保留，不再参与寻址。
2. LINE 出口走 `dms_out.send`（有 reply_token 就 reply，否则 push），没有 asyncio。
"""

from __future__ import annotations

import logging
import secrets
from datetime import date
from typing import Any, Dict, Optional

from pearnly_core import line_session as store
from pearnly_dms.booking.conversation import booking_qa_pages as booking_qa_pages
from pearnly_dms.booking.conversation import booking_qa_payment as booking_qa_payment
from pearnly_dms.booking.conversation import booking_qa_start as booking_qa_start
from pearnly_dms.booking.conversation import booking_qa_sync as booking_qa_sync
from pearnly_dms.booking.conversation import booking_qa_transfer as booking_qa_transfer
from pearnly_dms.line import commands as commands
from pearnly_dms.booking.conversation import qa_cards as qa_cards
from pearnly_dms.masters.advisor import resolve_operator_advisor
from pearnly_dms.contracts.client_base import to_be_date
from pearnly_dms.masters.master_contract import MasterSyncError
from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint
from pearnly_dms.line.out import send as _send
from pearnly_dms.booking.conversation.qa_util import (
    car_label,
    car_label_of,
    find_row,
    norm,
    norm_row,
    row_name,
)

logger = logging.getLogger(__name__)

_STATE = "booking_qa"
_CASH_WORD = "เงินสด"
# 其他语言里"现金"的写法（提示按手机语言显示，用户照着打的就是这些）。
_CASH_WORDS = frozenset({_CASH_WORD, "现金", "現金", "cash"})
_MAX_REGIS_NAME = 120


# ── 入口 ─────────────────────────────────────────────────────────────────────
def start(
    tenant_id,
    line_user_id,
    endpoint_id,
    customer_id,
    customer_name,
    id_card_mid,
    reply_token=None,
    draft=None,
    user_id="",
    summary=None,
) -> None:
    """客户档落定后，用实时主档与顾问归属开一局订车逐问。"""
    booking_qa_start.start(
        tenant_id,
        line_user_id,
        endpoint_id,
        customer_id,
        customer_name,
        id_card_mid,
        persist=_persist,
        send_step=send_step,
        resolve_endpoint=resolve_dms_endpoint,
        get_masters=booking_qa_sync.get_masters,
        resolve_advisor=resolve_operator_advisor,
        reply_token=reply_token,
        draft=draft,
        user_id=user_id,
        summary=summary,
    )


# ── 每步发问（内部 · slip_after 复用） ───────────────────────────────────────
def send_step(tenant_id, line_user_id, qa, step, reply_token=None) -> None:
    """按步发问。有 reply_token 就 reply，没有就 push（postback 与后台两条路都能调）。"""
    try:
        if step in ("place", "paint", "term", "regis", "pay_dst", "pay_bank"):
            msg = booking_qa_pages.question(
                line_user_id,
                qa,
                step,
                lambda luid, q, key: booking_qa_sync.masters(
                    tenant_id, luid, q, key, persist=_persist
                ),
                lambda luid, q: booking_qa_sync.paints(tenant_id, luid, q, persist=_persist),
            )
        else:
            msg = booking_qa_pages.static_question(step, qa)
    except MasterSyncError as exc:
        msg = qa_cards.master_problem(exc.code, exc.key)
    _send(line_user_id, msg, reply_token)


# ── webhook 入口 ─────────────────────────────────────────────────────────────
def handle_text(tenant_id, line_user_id, text, reply_token, sess=None) -> bool:
    """`state == booking_qa` 时消费文本；全局命令与其它态一律返 False 交上层。"""
    if commands.classify(text):
        return False
    qa = _qa(tenant_id, line_user_id, sess)
    if qa is None:
        return False
    step = qa.get("step") or ""
    _audit(tenant_id, line_user_id, qa, step, text)
    try:
        if step in booking_qa_transfer.TEXT_STEPS:
            # 付款资料文本步（金额/银行/渠道资料）整体归 transfer，状态机只做派发。
            booking_qa_transfer.handle_text(
                step,
                tenant_id,
                line_user_id,
                qa,
                text,
                reply_token,
                persist=_persist,
                send_step=send_step,
                reask=_reask,
            )
        else:
            _TEXT_HANDLERS.get(step, _reask)(tenant_id, line_user_id, qa, text, reply_token)
    except MasterSyncError as exc:
        _send(line_user_id, qa_cards.master_problem(exc.code, exc.key), reply_token)
    return True


def handle_image(tenant_id, line_user_id, message_id, reply_token, sess=None) -> bool:
    """`slip` / `slip_after` 步收凭证图；逐问的其它步**也接住**这张图，只回一句"这一步不需要图片"。

    以前其它步返 False，图片落到建档识别那一路：整轮订车会话被覆写、从拍身份证重来，
    还照扣一次识别费（2026-10 录制实测）。不是 `booking_qa` 态才返 False 交上层。
    """
    qa = _qa(tenant_id, line_user_id, sess)
    if qa is None:
        return False
    step = qa.get("step") or ""
    if step not in ("slip", "slip_after"):
        _send(line_user_id, {"type": "text", "text": qa_cards.TXT_NO_IMAGE_NEEDED}, reply_token)
        return True
    _audit(tenant_id, line_user_id, qa, step, f"image:{message_id}")
    qa["files"]["slip_mid"] = message_id
    if step == "slip":
        qa["step"] = "place"
        _persist(tenant_id, line_user_id, qa)
        send_step(tenant_id, line_user_id, qa, "place", reply_token)
    else:
        destination = qa.pop("after_slip", "preview")
        qa["step"] = "pay_more"
        _persist(tenant_id, line_user_id, qa)
        if destination == "pay_more":
            send_step(tenant_id, line_user_id, qa, "pay_more", reply_token)
        else:
            _to_preview(tenant_id, line_user_id, qa, reply_token)
    return True


_POSTBACK_ACTIONS = {
    "place": "place",
    "car_search": "car",
    "paint": "paint",
    "date": "date",
    "term": "term",
    "regis": "regis",
    "regis_name": "regisname",
    "pay_channel": "pay",
    "pay_bank": "paybank",
    "pay_dst": "bank",
    "pay_more": "more",
    "slip_conflict": "slipconflict",
}


def handle_postback(tenant_id, line_user_id, data, params, reply_token) -> bool:
    try:
        return _handle_postback(tenant_id, line_user_id, data, params, reply_token)
    except MasterSyncError as exc:
        _send(line_user_id, qa_cards.master_problem(exc.code, exc.key), reply_token)
        return True


def _handle_postback(tenant_id, line_user_id, data, params, reply_token) -> bool:
    """处理所有 `qa:` 前缀的 postback；datetimepicker 的 `params` 携带所选日期。"""
    qa = _qa(tenant_id, line_user_id)
    if qa is None or not (data or "").startswith("qa:"):
        return False
    parts = (data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""
    value = ":".join(parts[2:])
    step = qa.get("step") or ""
    _audit(tenant_id, line_user_id, qa, step, data)
    # 旧卡残留 / 串步按钮：吃下不推进状态，原步重问（避免翻历史卡误跳步）。
    if action != _POSTBACK_ACTIONS.get(step):
        _reask(tenant_id, line_user_id, qa, "", reply_token)
        return True
    # 翻页导航 token：只换页重发当前步，绝不当作 DMS id 写进 answers。
    page = booking_qa_pages.page_number(value)
    if page is not None:
        booking_qa_pages.flip_page(
            tenant_id,
            line_user_id,
            qa,
            action,
            page,
            reply_token,
            send_step=send_step,
            persist=_persist,
            reask=_reask,
        )
        return True
    if action in _MASTER_PICKS:  # place / term / regis 同一通用单选
        key, ans, nxt = _MASTER_PICKS[action]
        _pick_master(tenant_id, line_user_id, qa, key, ans, value, nxt, reply_token)
    elif action == "car":
        _pick_car(tenant_id, line_user_id, qa, value, reply_token)
    elif action == "paint":
        _pick_paint(tenant_id, line_user_id, qa, value, reply_token)
    elif action == "date":
        _pick_date(tenant_id, line_user_id, qa, params, reply_token)
    elif action == "regisname":
        _pick_regis_name(tenant_id, line_user_id, qa, reply_token)
    elif action in {"pay", "more", "slipconflict"}:
        booking_qa_payment.handle(
            action,
            tenant_id,
            line_user_id,
            qa,
            value,
            reply_token,
            persist=_persist,
            send_step=send_step,
            reask=_reask,
            to_preview=_to_preview,
        )
    elif action in {"bank", "paybank"}:
        # 直接把 `dms_booking_qa_sync.masters` 传下去（老产品同款）——注意它是
        # `(tenant_id, line_user_id, qa, key)` 的形状，与 `handle_text` 那条路的
        # `masters_reader` 不同。见 `dms_booking_qa_transfer` 顶部说明。
        booking_qa_transfer.pick_bank(
            tenant_id,
            line_user_id,
            qa,
            value,
            reply_token,
            masters=booking_qa_sync.masters,
            persist=_persist,
            send_step=send_step,
        )
    return True


_MASTER_PICKS = {
    "place": ("place_books", "place", "car_search"),
    "term": ("term_sales", "term", "regis"),
    "regis": ("regis_behalfs", "regis", "regis_name"),
}


# ── 文本步 ───────────────────────────────────────────────────────────────────
def _on_slip(tenant_id, line_user_id, qa, text, reply_token) -> None:
    if text.strip().casefold() in _CASH_WORDS:
        qa["step"] = "place"  # 现金声明 → 无凭证图，slip_mid 保持空
        _persist(tenant_id, line_user_id, qa)
        send_step(tenant_id, line_user_id, qa, "place", reply_token)
        return
    _send(line_user_id, qa_cards.slip_only_image(), reply_token)


def _on_car_search(tenant_id, line_user_id, qa, text, reply_token) -> None:
    rows = booking_qa_sync.masters(tenant_id, line_user_id, qa, "cars", persist=_persist)
    needle = norm(text)
    hits = [r for r in rows if needle and needle in norm_row(r)]
    if not hits:
        _send(line_user_id, qa_cards.car_none(), reply_token)
        return
    # 命中集落进会话：翻页 postback 直接取，搜索结果不随翻页丢失；选中后清理。
    qa["car_search"] = {"hits": hits, "page": 0}
    _persist(tenant_id, line_user_id, qa)
    _send(line_user_id, qa_cards.car_results(hits, len(hits)), reply_token)


def _on_paint(tenant_id, line_user_id, qa, text, reply_token) -> None:
    paints = booking_qa_sync.paints(tenant_id, line_user_id, qa, persist=_persist)
    needle = norm(text)
    hits = [r for r in paints if needle and needle in norm_row(r)]
    if len(hits) == 1:
        _commit_paint(tenant_id, line_user_id, qa, hits[0], reply_token)
    else:
        # 0 命中（重列全部）/ 多命中（列命中项）都回颜色按钮，不推进状态。
        page = (qa.get("pages") or {}).get("paints", 0)
        _send(line_user_id, qa_cards.ask_paint(car_label_of(qa), hits or paints, page), reply_token)


def _on_date(tenant_id, line_user_id, qa, text, reply_token) -> None:
    # 交车日只认 datetimepicker；打字无法解析 → 重发选择器（不推进状态）。
    send_step(tenant_id, line_user_id, qa, "date", reply_token)


def _on_regis_name(tenant_id, line_user_id, qa, text, reply_token) -> None:
    name = text.strip()
    if not name or len(name) > _MAX_REGIS_NAME:
        send_step(tenant_id, line_user_id, qa, "regis_name", reply_token)
        return
    qa["answers"]["regis_name"] = name
    qa["step"] = "pay_channel"
    _persist(tenant_id, line_user_id, qa)
    send_step(tenant_id, line_user_id, qa, "pay_channel", reply_token)


def _on_slip_after(tenant_id, line_user_id, qa, text, reply_token) -> None:
    # 用户已声明有转账渠道：打 เงินสด 也不放行，必须先送凭证图才能看总结。
    _send(line_user_id, qa_cards.need_slip(), reply_token)


def _reask(tenant_id, line_user_id, qa, text, reply_token) -> None:
    """按钮步收到打字 / 串步按钮 → 原步重问，不推进状态（audit 已在入口记过）。"""
    send_step(tenant_id, line_user_id, qa, qa.get("step") or "", reply_token)


_TEXT_HANDLERS = {
    "slip": _on_slip,
    "place": _reask,
    "car_search": _on_car_search,
    "paint": _on_paint,
    "date": _on_date,
    "term": _reask,
    "regis": _reask,
    "regis_name": _on_regis_name,
    "pay_channel": _reask,
    "pay_dst": _reask,
    "pay_more": _reask,
    "slip_after": _on_slip_after,
    "slip_conflict": _reask,
}


# ── postback 步 ──────────────────────────────────────────────────────────────
def _pick_master(
    tenant_id,
    line_user_id,
    qa,
    masters_key,
    answer_key,
    value,
    next_step,
    reply_token,
) -> None:
    """通用主档单选：id → 存 `answers.<key>={"id","name"}` → 进下一步（清该主档页码）。"""
    rows = booking_qa_sync.masters(tenant_id, line_user_id, qa, masters_key, persist=_persist)
    row = find_row(rows, value)
    if row is None:
        _reask(tenant_id, line_user_id, qa, "", reply_token)
        return
    (qa.get("pages") or {}).pop(masters_key, None)
    qa["answers"][answer_key] = {"id": str(row[0]), "name": row_name(row)}
    _advance_answer(tenant_id, line_user_id, qa, answer_key, next_step, reply_token)


def _pick_car(tenant_id, line_user_id, qa, value, reply_token) -> None:
    rows = booking_qa_sync.masters(tenant_id, line_user_id, qa, "cars", persist=_persist)
    row = find_row(rows, value)
    if row is None:
        _reask(tenant_id, line_user_id, qa, "", reply_token)
        return
    qa.pop("car_search", None)  # 选完即清命中集，不留残渣
    qa["answers"]["car"] = {"id": str(row[0]), "label": car_label(row)}
    qa["answers"].pop("paint", None)
    qa.pop("paint_snapshots", None)
    _advance_answer(tenant_id, line_user_id, qa, "car", "paint", reply_token)


def _pick_paint(tenant_id, line_user_id, qa, value, reply_token) -> None:
    rows = booking_qa_sync.paints(tenant_id, line_user_id, qa, persist=_persist)
    row = find_row(rows, value)
    if row is None:
        _reask(tenant_id, line_user_id, qa, "", reply_token)
        return
    _commit_paint(tenant_id, line_user_id, qa, row, reply_token)


def _commit_paint(tenant_id, line_user_id, qa, row, reply_token) -> None:
    (qa.get("pages") or {}).pop("paints", None)
    qa["answers"]["paint"] = {"id": str(row[0]), "name": row_name(row)}
    _advance_answer(tenant_id, line_user_id, qa, "paint", "date", reply_token)


def _advance_answer(tenant_id, line_user_id, qa, field, next_step, reply_token):
    """A stale master repair reasks its dependencies, then requires a new review."""
    repair = qa.get("repair_fields") or []
    if field in repair:
        repair.remove(field)
        if not repair:
            qa.pop("repair_fields", None)
            _to_preview(tenant_id, line_user_id, qa, reply_token)
            return
        next_step = {"car": "car_search"}.get(repair[0], repair[0])
    qa["step"] = next_step
    _persist(tenant_id, line_user_id, qa)
    send_step(tenant_id, line_user_id, qa, next_step, reply_token)


def _pick_date(tenant_id, line_user_id, qa, params, reply_token) -> None:
    picked = (params or {}).get("date") or ""
    try:
        delivery = date.fromisoformat(picked)
    except ValueError:
        _reask(tenant_id, line_user_id, qa, "", reply_token)
        return
    qa["answers"]["delivery_date_be"] = to_be_date(delivery)  # 公历 ISO → 佛历 dd/mm/yyyy
    qa["step"] = "term"
    _persist(tenant_id, line_user_id, qa)
    send_step(tenant_id, line_user_id, qa, "term", reply_token)


def _pick_regis_name(tenant_id, line_user_id, qa, reply_token) -> None:
    qa["answers"]["regis_name"] = str((qa.get("customer") or {}).get("name") or "")
    qa["step"] = "pay_channel"
    _persist(tenant_id, line_user_id, qa)
    send_step(tenant_id, line_user_id, qa, "pay_channel", reply_token)


def _to_preview(tenant_id, line_user_id, qa, reply_token) -> None:
    """逐问收官：置 `booking_review`（完整 qa + 轮换 nonce）→ 发预览卡。

    轮换 nonce 照预览卡约定：旧按钮的 postback 从此 mismatch，确认/取消仍由既有
    订车流程处理（`consume_nonce` 守卫）。
    """
    sess = store.get_session(line_user_id)
    payload = (sess or {}).get("payload") or {}
    nonce = secrets.token_hex(8)
    store.set_session(
        line_user_id,
        "booking_review",
        {**payload, "qa": qa, "nonce": nonce},
    )
    _send(line_user_id, qa_cards.preview_card(qa, nonce), reply_token)


# ── 会话 / 主档 / 出口小工具 ─────────────────────────────────────────────────
def _qa(tenant_id, line_user_id, sess=None) -> Optional[Dict[str, Any]]:
    """读当前 qa；非 `booking_qa` 态或缺 `step`（会话损坏）→ None（交上层）。

    调用方已读过会话就透传 `sess` 免二读（"入口只读一次"）。
    """
    if sess is None:
        sess = store.get_session(line_user_id)
    if not sess or sess.get("state") != _STATE:
        return None
    qa = (sess.get("payload") or {}).get("qa") or {}
    if qa.get("step") in {"pay_src", "pay_src_detail", "pay_dst_detail"}:
        qa["step"] = "pay_dst"
        _persist(tenant_id, line_user_id, qa)
    return qa if qa.get("step") else None


def _audit(tenant_id, line_user_id, qa, step, value) -> None:
    qa.setdefault("audit", []).append({"step": step, "input": value})
    _persist(tenant_id, line_user_id, qa)


def _persist(tenant_id, line_user_id, qa) -> None:
    store.set_session(line_user_id, _STATE, {"qa": qa})
