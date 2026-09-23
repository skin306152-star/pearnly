# -*- coding: utf-8 -*-
"""DMS LINE 订车逐问的 Flex 卡与泰文话术（纯函数，零副作用）。

状态机的每一步「发问」在这里出消息 dict（文本 + quickReply + 预览 Flex 卡）。
按钮一律 postback，data 用 `qa:<action>[:<value>]` 编码；预览卡的确认/取消复用
`dms_cards.ACT_*` 与 `_data`，所以既有订车流程的确认/取消**不用改**就能接住。

## 文案在语言包里

一个泰文字符都不留在这份代码里 —— 改文案是改数据文件。键是 `dms.card.qa.*`
（逐字从线上搬的，见 39 号文档），取词走 `dms_qa_util.qat`。

⚠️ 两类**不例外**的细节，搬的时候最容易错：

1. **内联字面量**也要进包。线上一部分文案不是顶层常量而是函数里的字面量
   （`ask_payment_bank` 的 `เลือกธนาคาร`、重试卡的三句）—— 只按常量表搬会静默改掉它们。
2. **取自错误话术表的那几条**（`TXT_MASTER_*` / `_TH_ADVISOR_UNMATCHED`）走
   `dms.err.*`，**不在 `dms.card.qa.*` 里另存一份** —— 同一件事在两个地方有说法，
   迟早漂。

照搬老产品 `services/line_dms/qa_cards.py`（线上基准 `501589f0`），逻辑未改。
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pearnly_dms.booking import booking_car_details as booking_car_details
from pearnly_dms.editor import edit_link as dms_edit_link
from pearnly_dms import i18n
from pearnly_dms.booking.booking_payments import payment_preview_detail
from pearnly_dms.line.cards import (
    ACT_CANCEL_BOOKING,
    ACT_CONFIRM_BOOKING,
    ACT_RETRY_BOOKING,
    BTN_RETRY_BOOKING,
    TXT_NO_ENDPOINT,
    _bubble,
    _btn,
    _data,
    _kv_row,
    _qr_item,
    _summary_rows,
)
from pearnly_dms.masters.company_banks import company_bank_label
from pearnly_dms.booking.conversation.qa_payment_cards import (
    TXT_SEP_HINT,
    ask_pay_ref as ask_pay_ref,
)
from pearnly_dms.booking.conversation.qa_payment_cards import (
    ask_payment_bank as ask_payment_bank,
)
from pearnly_dms.booking.conversation.qa_util import CHANNEL_EXTRA_SHAPE  # noqa: F401  既有对外名
from pearnly_dms.booking.conversation.qa_util import car_label as _car_label
from pearnly_dms.booking.conversation.qa_util import deposit_total as deposit_total  # noqa: F401  既有对外名
from pearnly_dms.booking.conversation.qa_util import qat
from pearnly_dms.booking.conversation.qa_util import row_name as _name


def _err_text(code: str) -> str:
    """错误话术（按码取，泰文）—— 与主 UI 同一个来源，不在卡片层另存一份。"""
    return i18n.t(f"dms.err.{code}", lang="th")


def __getattr__(name: str) -> Any:
    """让**外部**用 `qa_cards.TXT_XXX` 这种写法取词（后续搬 `booking_qa` 时会用）。

    ⚠️ PEP 562 只覆盖"模块属性访问"，**管不到模块内函数里的全局名查找** ——
    本文件自己的函数里不能写裸的 `BTN_CONFIRM`（会 `NameError`，踩过）。
    所以内部要用到的名字一律**显式定义**在下面。
    """
    if name.startswith(("TXT_", "BTN_", "LBL_", "VAL_")):
        return qat(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ── 跨模块要用的那几条（下面的函数与 `dms_qa_payment_cards` 都按名字引用）─────
# 同理：线上是 `"…| 01\n" + TXT_SEP_HINT`，那个尾换行存不进语言包（读取器 `strip()`）。
TXT_ASK_CHEQUE_REF = qat("TXT_ASK_CHEQUE_REF") + "\n" + TXT_SEP_HINT
TXT_ASK_CARD_REF = qat("TXT_ASK_CARD_REF")
TXT_ASK_OTHER_REF = qat("TXT_ASK_OTHER_REF")
TXT_BAD_PAYMENT_DETAIL = qat("TXT_BAD_PAYMENT_DETAIL", hint=TXT_SEP_HINT)

# 开局闸：DMS 账号对不上顾问名册就开不了单（提成没人认）。救指引与错误码目录**同一份泰语
# 原文** —— 同一件事在 LINE 与主 UI 上说法漂了，用户会以为是两个毛病。
# 会话语气（ครับ / 账号名）只加在前缀，不改目录原文。
_TH_ADVISOR_UNMATCHED = _err_text("ERR_DMS_ADVISOR_UNMATCHED")
# 同理，尾句前那个**前导空格**也存不进 CSV（读取器 `strip()`）：线上它是
# `" แล้วลองใหม่อีกครั้ง"` 的一部分。空格在代码里补，尾句本身仍在包里。
_TH_RETRY = " " + qat("_TH_RETRY")
# ⚠️ 前缀与错误原文之间那个换行**必须在代码里补**：线上它在字面量尾部（`…»\n`），
# 而语言包读取器对每条值做 `strip()`，行尾换行存不进 CSV。补回来才是逐字一致。
TXT_ADVISOR_BLOCK = qat("TXT_ADVISOR_BLOCK") + "\n" + _TH_ADVISOR_UNMATCHED + _TH_RETRY
TXT_ADVISOR_BLOCK_NO_USER = (
    qat("TXT_ADVISOR_BLOCK_NO_USER") + "\n" + _TH_ADVISOR_UNMATCHED + _TH_RETRY
)

TXT_MASTER_UNAVAILABLE = _err_text("ERR_DMS_MASTER_UNAVAILABLE")
TXT_MASTER_EMPTY = _err_text("ERR_DMS_MASTER_EMPTY")
TXT_MASTER_CHANGED = _err_text("ERR_DMS_MASTER_CHANGED")
TXT_MASTER_UNMATCHED = _err_text("ERR_DMS_MASTER_UNMATCHED")

# 渠道名（逐问按钮与预览卡共用一份）
PAY_LABELS = {
    channel: qat(f"pay.{channel}")
    for channel in ("cash", "transfer", "cheque", "cashier_cheque", "card", "other")
}

# 预览卡行标签（与确认后建单要回显的字段一一对应）。客户资料区**不在此列**：
# 复用 `dms_cards._summary_rows` 的同一套标签，不另维护一份（防两处漂）。
LBL_ADVISOR = qat("LBL_ADVISOR")
LBL_POSTCODE = qat("LBL_POSTCODE")
LBL_PLACE = qat("LBL_PLACE")
LBL_CAR = qat("LBL_CAR")
LBL_DELIVERY = qat("LBL_DELIVERY")
LBL_TERM = qat("LBL_TERM")
LBL_REGIS = qat("LBL_REGIS")
LBL_TOTAL = qat("LBL_TOTAL")
LBL_ATTACH_CARD = qat("LBL_ATTACH_CARD")
LBL_ATTACH_SLIP = qat("LBL_ATTACH_SLIP")
VAL_ATTACHED = qat("VAL_ATTACHED")

_MAX_BTN = 20  # quick reply 按钮标签截断（display 宽度）
_MAX_FULL_LABEL = 160

# LINE quick reply 硬上限 13 项；主档每页 11 项，余 2 格给上一页/下一页导航。
# 翻页 postback 走内部 token `__page:<n>`（带本步 action）；DMS 主档 id 是纯数字，永不冲突。
QR_LIMIT = 13
QR_PAGE_SIZE = 11
PAGE_TOKEN = "__page:"  # ⚠️ 协议载荷，不是文案 —— 不进包
BTN_PAGE_PREV = qat("BTN_PAGE_PREV")
BTN_PAGE_NEXT = qat("BTN_PAGE_NEXT")

# 内部要用到的按钮与标题（**必须显式定义**：模块 `__getattr__` 不覆盖函数内的全局名查找）
TXT_PREVIEW_TITLE = qat("TXT_PREVIEW_TITLE")
BTN_PICK_DATE = qat("BTN_PICK_DATE")
BTN_REGIS_NAME_CARD = qat("BTN_REGIS_NAME_CARD")
BTN_MORE_DONE = qat("BTN_MORE_DONE")
BTN_MORE_ADD = qat("BTN_MORE_ADD")
BTN_ADD_TRANSFER = qat("BTN_ADD_TRANSFER")
BTN_REMOVE_SLIP = qat("BTN_REMOVE_SLIP")
BTN_CONFIRM = qat("BTN_CONFIRM")
BTN_EDIT = qat("BTN_EDIT")
BTN_DISCARD = qat("BTN_DISCARD")


def _msg(text: str, items: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """文本消息 + 可选 `quickReply`；无按钮时不出空 items（LINE 会拒）。"""
    msg: Dict[str, Any] = {"type": "text", "text": text}
    if items:
        msg["quickReply"] = {"items": items}
    return msg


def _pick_rows(rows: List[list], action: str, label_fn, page: int = 0) -> List[Dict[str, Any]]:
    """主档按钮第 `page` 页（0 基）：data=`qa:<action>:<id>`，label 截 20 字符。

    超过 `QR_PAGE_SIZE` 项自动分页，首尾补上一页/下一页导航，总项恒 ≤ `QR_LIMIT`（LINE 硬限）。
    页号越界（主档缩水）夹到最后一页，导航按**实际展示页**计算 ——
    不落死循环、不静默丢选项。
    """
    rows = [r for r in (rows or []) if r and r[0] is not None]
    n = len(rows)
    last_page = (n - 1) // QR_PAGE_SIZE if n else 0
    page = max(0, min(int(page or 0), last_page))
    start = page * QR_PAGE_SIZE
    items = []
    for offset, row in enumerate(rows[start : start + QR_PAGE_SIZE], start + 1):
        label = f"{offset}. {label_fn(row)}"
        items.append(_qr_item(label[:_MAX_BTN], f"qa:{action}:{row[0]}"))
    if start > 0:
        items.insert(0, _qr_item(BTN_PAGE_PREV, f"qa:{action}:{PAGE_TOKEN}{page - 1}"))
    if start + QR_PAGE_SIZE < n:
        items.append(_qr_item(BTN_PAGE_NEXT, f"qa:{action}:{PAGE_TOKEN}{page + 1}"))
    return items


def _option_text(text: str, rows: List[list], label_fn, page: int = 0) -> str:
    """正文列出本页完整值，补足 quick reply 标签的 20 字限制。"""
    rows = [row for row in (rows or []) if row and row[0] is not None]
    last_page = (len(rows) - 1) // QR_PAGE_SIZE if rows else 0
    page = max(0, min(int(page or 0), last_page))
    visible = rows[page * QR_PAGE_SIZE : (page + 1) * QR_PAGE_SIZE]
    labels = [str(label_fn(row))[:_MAX_FULL_LABEL] for row in visible]
    if not labels:
        return text
    start = page * QR_PAGE_SIZE + 1
    return text + "\n" + "\n".join(f"{index}. {label}" for index, label in enumerate(labels, start))


def _plus_years(d: date, years: int) -> date:
    """跨闰日安全加整年（datetimepicker 的 max 用）。"""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d + timedelta(days=365 * years)


# ── 开局闸 ───────────────────────────────────────────────────────────────
def no_endpoint() -> Dict[str, Any]:
    """端点没配/被停用 → 沿用全站同一句无端点话术。"""
    return _msg(TXT_NO_ENDPOINT)


def advisor_block_msg(username: str) -> Dict[str, Any]:
    """顾问认不出 → 说清哪个账号、谁能修、修完重试；连账号都解不出时退通用版。"""
    username = (username or "").strip()
    if not username:
        return _msg(TXT_ADVISOR_BLOCK_NO_USER)
    return _msg(TXT_ADVISOR_BLOCK.format(username=username))


# ── 各步发问 ─────────────────────────────────────────────────────────────
def ask_slip() -> Dict[str, Any]:
    """旧会话兼容：送转账凭证；现金可打字 เงินสด 跳过。"""
    return _msg(qat("TXT_ASK_SLIP"))


def slip_only_image() -> Dict[str, Any]:
    """slip 步收到无法解析的文本时的重发提示。"""
    return _msg(qat("TXT_SLIP_ONLY_IMAGE"))


def ask_place(place_books: List[list], page: int = 0) -> Dict[str, Any]:
    """สถานที่รับจอง 按钮（place_books 主档，分页全量可达）。"""
    return _msg(
        _option_text(qat("TXT_ASK_PLACE"), place_books, _name, page),
        _pick_rows(place_books, "place", _name, page),
    )


def ask_car() -> Dict[str, Any]:
    """打字搜车型。"""
    return _msg(qat("TXT_ASK_CAR"))


def car_results(rows: List[list], n: int, page: int = 0) -> Dict[str, Any]:
    """车型搜索结果按钮（分页）：全部命中逐页可选，一次消息恒 ≤13 项。"""
    text = qat("TXT_CAR_PICK", n=n)
    return _msg(
        _option_text(text, rows, _car_label, page), _pick_rows(rows, "car", _car_label, page)
    )


def car_none() -> Dict[str, Any]:
    return _msg(qat("TXT_CAR_NONE"))


def ask_paint(car_label: str, paints: List[list], page: int = 0) -> Dict[str, Any]:
    """第 4 问（顺序表里的 paint）：选颜色（分页全量；多页时附打字搜索提示）。"""
    text = qat("TXT_ASK_PAINT", car=car_label)
    if len(paints or []) > QR_PAGE_SIZE:
        text = f"{text}\n{qat('TXT_PAINT_MANY')}"
    return _msg(_option_text(text, paints, _name, page), _pick_rows(paints, "paint", _name, page))


def ask_date(today: date) -> Dict[str, Any]:
    """第 5 问（顺序表里的 date）：LINE datetimepicker · initial=+7 天，min=今天，max=+2 年。"""
    action = {
        "type": "datetimepicker",
        "label": BTN_PICK_DATE,
        "data": "qa:date",
        "mode": "date",
        "initial": (today + timedelta(days=7)).isoformat(),
        "min": today.isoformat(),
        "max": _plus_years(today, 2).isoformat(),
    }
    return _msg(qat("TXT_ASK_DATE"), [{"type": "action", "action": action}])


def ask_term(term_sales: List[list], page: int = 0) -> Dict[str, Any]:
    """第 6 问：เงื่อนไขการขาย 按钮（term_sales 主档，分页全量可达）。"""
    return _msg(
        _option_text(qat("TXT_ASK_TERM"), term_sales, _name, page),
        _pick_rows(term_sales, "term", _name, page),
    )


def ask_regis(regis_behalfs: List[list], page: int = 0) -> Dict[str, Any]:
    """第 7 问：จดทะเบียนในนาม 按钮（regis_behalfs 主档，分页全量可达）。"""
    return _msg(
        _option_text(qat("TXT_ASK_REGIS"), regis_behalfs, _name, page),
        _pick_rows(regis_behalfs, "regis", _name, page),
    )


def ask_regis_name() -> Dict[str, Any]:
    """登记人姓名，一个按钮用客户档姓名。"""
    return _msg(qat("TXT_ASK_REGIS_NAME"), [_qr_item(BTN_REGIS_NAME_CARD, "qa:regisname:card")])


def ask_pay_channel(payments: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """只展示尚未使用的渠道；DMS 每个渠道只有一组固定字段。"""
    used = {str(payment.get("channel") or "") for payment in payments or []}
    return _msg(
        qat("TXT_ASK_PAY_CHANNEL"),
        [
            _qr_item(label, f"qa:pay:{channel}")
            for channel, label in PAY_LABELS.items()
            if channel not in used
        ],
    )


def ask_amount(channel_label: str) -> Dict[str, Any]:
    return _msg(qat("TXT_ASK_AMOUNT", channel=channel_label))


def bad_amount() -> Dict[str, Any]:
    return _msg(qat("TXT_BAD_AMOUNT"))


def bad_payment_detail() -> Dict[str, Any]:
    return _msg(TXT_BAD_PAYMENT_DETAIL)


def ask_pay_dst(company_banks: List[list], page: int = 0) -> Dict[str, Any]:
    from pearnly_dms.masters.company_banks import sort_bank_rows

    company_banks = sort_bank_rows(company_banks)
    if not company_banks:
        return _msg(qat("TXT_NO_COMPANY_BANK"))
    return _msg(
        _option_text(qat("TXT_ASK_PAY_DST"), company_banks, company_bank_label, page),
        _pick_rows(company_banks, "bank", company_bank_label, page),
    )


def master_problem(code: str) -> Dict[str, Any]:
    """主档问题的通用卡：**按码取话术**，认不出的码退"读取不到"。"""
    from pearnly_dms.line import errors as dms_errors

    return _msg(dms_errors.friendly(code).get("th") or TXT_MASTER_UNAVAILABLE)


def master_changed() -> Dict[str, Any]:
    return _msg(TXT_MASTER_CHANGED)


def ask_more() -> Dict[str, Any]:
    """支付循环出口：ครบแล้ว / เพิ่มช่องทาง。"""
    return _msg(
        qat("TXT_ASK_MORE"),
        [_qr_item(BTN_MORE_DONE, "qa:more:done"), _qr_item(BTN_MORE_ADD, "qa:more:add")],
    )


def need_slip() -> Dict[str, Any]:
    """转账渠道没凭证 → 补要（或 slip_after 步重发）。"""
    return _msg(qat("TXT_NEED_SLIP"))


def slip_conflict() -> Dict[str, Any]:
    """凭证与付款方式矛盾：必须增加转账或删除凭证才能继续。"""
    return _msg(
        qat("TXT_SLIP_CONFLICT"),
        [
            _qr_item(BTN_ADD_TRANSFER, "qa:slipconflict:add"),
            _qr_item(BTN_REMOVE_SLIP, "qa:slipconflict:remove"),
        ],
    )


# ── 预览汇总卡 ────────────────────────────────────────────────────────────
def _car_paint_line(answers: Dict[str, Any]) -> str:
    car = str((answers.get("car") or {}).get("label") or "")
    paint = str((answers.get("paint") or {}).get("name") or "")
    return " · ".join(p for p in (car, paint) if p) or "—"


def _regis_line(answers: Dict[str, Any]) -> str:
    regis = str((answers.get("regis") or {}).get("name") or "")
    name = str(answers.get("regis_name") or "")
    if regis and name:
        return f"{regis} · {name}"
    return regis or name or "—"


def _edit_url(nonce: str) -> str:
    """本站点的编辑深链（见 `dms_edit_link`）；未配则空，调用方省略按钮。"""
    return dms_edit_link.url(nonce)


def _uri_btn(label: str, uri: str) -> Dict[str, Any]:
    return {
        "type": "button",
        "style": "secondary",
        "height": "sm",
        "action": {"type": "uri", "label": label, "uri": uri},
    }


def _address_line(draft: Dict[str, Any]) -> str:
    parts = [
        draft.get("house_no"),
        draft.get("building"),
        draft.get("village"),
        draft.get("moo"),
        draft.get("soi"),
        draft.get("road"),
        draft.get("subdistrict_name"),
        draft.get("district_name"),
        draft.get("province_name"),
    ]
    return " ".join(str(part).strip() for part in parts if str(part or "").strip()) or "—"


def _fallback_summary(qa: Dict[str, Any]) -> Dict[str, str]:
    """legacy 会话没有 `summary` 时的可读回退：从 draft + customer 拼五要素。

    地址沿用 `_address_line` 的拼接，邮编追加到地址末尾（不单列一行，与 summary 的
    地址格式对齐）；字段取值优先级照旧预览卡（`people_id`/`name` 先 draft 后 customer）。
    """
    draft = qa.get("draft") or {}
    customer = qa.get("customer") or {}
    address = _address_line(draft)
    postcode = str(draft.get("zipcode") or draft.get("zipcode_name") or "")
    if postcode:
        address = f"{address} {postcode}".strip()
    return {
        "people_id": str(draft.get("people_id") or customer.get("people_id") or ""),
        "name": str(customer.get("name") or draft.get("name") or ""),
        "birthday_be": str(draft.get("birthday_be") or ""),
        "address": address,
        "zipcode": postcode,
        "phone": str(draft.get("phone") or ""),
    }


def preview_card(qa: Dict[str, Any], nonce: str, notice: str = "") -> Dict[str, Any]:
    """逐问结束的汇总卡：全部答案 + 各渠道金额 + 合计 + 附件两行 + 确认/丢弃。

    客户资料区复用 `dms_cards._summary_rows`（与客户确认卡同一份 summary，标签/顺序/
    地址格式完全一致）；legacy 会话没有 summary 时从 draft+customer 拼可读回退。
    按钮复用 `ACT_CONFIRM_BOOKING` / `ACT_CANCEL_BOOKING` 与 `_data` 编码，
    既有订车流程的确认/取消（parse_qs + consume_nonce）**无需任何改动**即可接住。

    `notice`：把"顺带要说的一句话"（例：确认瞬间主档变了）**并进这张卡**，而不是
    先发一条文本再发这张卡 —— LINE 的额度按**消息条数**算，一次操作发两条就是两条
    （2026-09-23 省额度）。
    """
    answers = qa.get("answers") or {}
    files = qa.get("files") or {}
    payments = qa.get("payments") or []
    advisor = str((qa.get("advisor") or {}).get("name") or "")
    summary = qa.get("summary") or _fallback_summary(qa)
    customer_rows = _summary_rows(summary)
    customer_rows.insert(4, _kv_row(LBL_POSTCODE, str(summary.get("zipcode") or "")))
    rows: List[Dict[str, Any]] = [
        # 要说的话放最前面：用户一眼看到"为什么要再确认一次"。
        *([{"type": "text", "text": notice, "wrap": True, "size": "sm"}] if notice else []),
        *customer_rows,
        # 顾问 = 提成归属，确认前让销售自己看一眼落在谁头上；没匹配上时开局就拦了，不会到这。
        *([_kv_row(LBL_ADVISOR, advisor)] if advisor else []),
        _kv_row(LBL_PLACE, str((answers.get("place") or {}).get("name") or "")),
        _kv_row(LBL_CAR, _car_paint_line(answers)),
        *booking_car_details.rows(qa),
        _kv_row(LBL_DELIVERY, str(answers.get("delivery_date_be") or "")),
        _kv_row(LBL_TERM, str((answers.get("term") or {}).get("name") or "")),
        _kv_row(LBL_REGIS, _regis_line(answers)),
    ]
    for p in payments:
        label = PAY_LABELS.get(p.get("channel") or "", p.get("channel") or "")
        rows.append(_kv_row(label, payment_preview_detail(p)))
    rows.append(_kv_row(LBL_TOTAL, f"{deposit_total(payments):,.2f}"))
    rows.append({"type": "separator", "margin": "sm"})
    rows.append(_kv_row(LBL_ATTACH_CARD, VAL_ATTACHED if files.get("id_card_mid") else "—"))
    rows.append(_kv_row(LBL_ATTACH_SLIP, VAL_ATTACHED if files.get("slip_mid") else "—"))
    edit_url = _edit_url(nonce)
    footer = [
        _btn(BTN_CONFIRM, _data(ACT_CONFIRM_BOOKING, nonce=nonce), "primary"),
        *([_uri_btn(BTN_EDIT, edit_url)] if edit_url else []),
        _btn(BTN_DISCARD, _data(ACT_CANCEL_BOOKING, nonce=nonce), "secondary"),
    ]
    return _bubble(TXT_PREVIEW_TITLE, rows, footer, TXT_PREVIEW_TITLE)


def booking_retry_card(message: str, nonce: str) -> Dict[str, Any]:
    """建单在外部 DMS 写入前失败时，保留草稿并发一次性重试按钮。"""
    failed = qat("retry.failed")
    rows = [
        {"type": "text", "text": message or failed, "wrap": True},
        {"type": "text", "text": qat("retry.hint"), "size": "sm", "wrap": True},
    ]
    footer = [
        _btn(BTN_RETRY_BOOKING, _data(ACT_RETRY_BOOKING, nonce=nonce), "primary"),
        _btn(BTN_DISCARD, _data(ACT_CANCEL_BOOKING, nonce=nonce), "secondary"),
    ]
    return _bubble(failed, rows, footer, qat("retry.alt"))
