# -*- coding: utf-8 -*-
"""订车逐问里需要读 DMS 主档的那几步 + 翻页。

照搬老产品 `services/line_dms/booking_qa_pages.py`（线上基准 `501589f0`，逻辑未改），
async → 同步。

分两份：

· `static_question(step, qa)` —— **不需要查主档**的问法（凭证、车型搜索、日期…），
  纯查表，随时可出；
· `question(line_user_id, qa, step, masters, paints)` —— 需要读主档的（取车点、销售条件、
  注册名义、颜色、收款银行），主档读取器由调用方注入（会话快照，见 `dms_booking_qa_sync`）。

翻页是**只翻看**：改的只有页码，选中某一行是另一条路（postback 的 `pick` 那条）。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pearnly_dms import dms_qa_cards as qa_cards
from pearnly_dms import dms_booking_qa_transfer as booking_qa_transfer
from pearnly_dms.dms_company_banks import PAYMENT_CHANNEL_BANKS
from pearnly_dms.dms_out import send as _send
from pearnly_dms.dms_qa_util import car_label_of

# 哪一步用哪一类主档（与 DMS 侧键名一一对应）
PAGED_MASTER = {
    "place": "place_books",
    "term": "term_sales",
    "regis": "regis_behalfs",
    "bank": "company_banks",
    "paint": "paints",
}


def static_question(step, qa) -> Optional[Dict[str, Any]]:
    """不需要查主档的问法（查表即出，不碰 DMS）。"""
    from datetime import date

    channel = (qa.get("pending_channel") or {}).get("channel", "")
    return {
        "slip": qa_cards.ask_slip(),
        "car_search": qa_cards.ask_car(),
        "date": qa_cards.ask_date(date.today()),
        "regis_name": qa_cards.ask_regis_name(),
        "pay_channel": qa_cards.ask_pay_channel(qa.get("payments") or []),
        "pay_amount": qa_cards.ask_amount(qa_cards.PAY_LABELS.get(channel, "")),
        "pay_ref": booking_qa_transfer.channel_ref_question(qa),
        "pay_more": qa_cards.ask_more(),
        "slip_after": qa_cards.need_slip(),
        "slip_conflict": qa_cards.slip_conflict(),
    }.get(step)


def question(line_user_id, qa, step, masters, paints) -> Optional[Dict[str, Any]]:
    """读当前会话的主档快照，渲染这一步（页号取自 `qa["pages"]`）。"""
    pages = qa.get("pages") or {}
    if step == "place":
        return qa_cards.ask_place(
            masters(line_user_id, qa, "place_books"), pages.get("place_books", 0)
        )
    if step == "term":
        return qa_cards.ask_term(
            masters(line_user_id, qa, "term_sales"), pages.get("term_sales", 0)
        )
    if step == "regis":
        return qa_cards.ask_regis(
            masters(line_user_id, qa, "regis_behalfs"), pages.get("regis_behalfs", 0)
        )
    if step == "paint":
        return qa_cards.ask_paint(car_label_of(qa), paints(line_user_id, qa), pages.get("paints", 0))
    if step == "pay_bank":
        channel = (qa.get("pending_channel") or {}).get("channel")
        key = PAYMENT_CHANNEL_BANKS[channel]
        return qa_cards.ask_payment_bank(
            masters(line_user_id, qa, key), pages.get(key, 0), channel
        )
    if step == "pay_dst":
        return qa_cards.ask_pay_dst(
            masters(line_user_id, qa, "company_banks"), pages.get("company_banks", 0)
        )
    return None


def page_number(value: str) -> Optional[int]:
    """解析翻页令牌 —— **不把 DMS 的 id 当成翻页**（id 是选中那一行的事）。"""
    if not (value or "").startswith(qa_cards.PAGE_TOKEN):
        return None
    try:
        return int(value[len(qa_cards.PAGE_TOKEN) :])
    except ValueError:
        return None


def flip_page(
    tenant_id,
    line_user_id,
    qa,
    action,
    page,
    reply_token,
    *,
    send_step,
    persist,
    reask,
) -> None:
    """只改"在看第几页"；选中 DMS 的某一行是另一条路。"""
    if action == "car":
        search = qa.get("car_search") or {}
        hits = search.get("hits") or []
        if not hits:
            reask(tenant_id, line_user_id, qa, "", reply_token)
            return
        last = (len(hits) - 1) // qa_cards.QR_PAGE_SIZE
        search["page"] = max(0, min(page, last))
        persist(tenant_id, line_user_id, qa)
        # 车型搜索结果是本地命中的快照，不读主档 —— 直接回卡片
        _send(line_user_id, qa_cards.car_results(hits, len(hits), search["page"]), reply_token)
        return
    key = (
        PAYMENT_CHANNEL_BANKS.get((qa.get("pending_channel") or {}).get("channel"))
        if action == "paybank"
        else PAGED_MASTER.get(action)
    )
    if not key:
        reask(tenant_id, line_user_id, qa, "", reply_token)
        return
    qa.setdefault("pages", {})[key] = page
    persist(tenant_id, line_user_id, qa)
    send_step(tenant_id, line_user_id, qa, qa.get("step") or "", reply_token)
