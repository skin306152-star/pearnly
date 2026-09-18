# -*- coding: utf-8 -*-
"""转账、支票、本票、银行卡这四类渠道的资料收集步进。

照搬老产品 `services/line_dms/booking_qa_transfer.py`（线上基准 `501589f0`，逻辑未改），
async → 同步。

规则一句话：**转账只收金额和收款银行，再收凭证**；支票、本票和银行卡各自保留
自己的资料（票据号、卡种……）。银行目录**有行**就给选择按钮，**权威为空**时才允许
手工填银行名称（挂 `MANUAL_BANK_FLAG`，结算时据实写 DMS）。

⚠️ 拆解用户那句话、把它变成 DMS 原生槽位的是 `dms_booking_payments`；本文件只管
"该问哪一步、收到的文字往哪走"。两处边界不要混。

⚠️ **注入的 `masters` 有两种调用形状**（老产品就这样，别把它"统一"掉）：
· `handle_text` 那条路用 `masters_reader` 造的读取器 → `(line_user_id, qa, key)`；
· `pick_bank` 由状态机直接传 `dms_booking_qa_sync.masters` → `(tenant_id, line_user_id, qa, key)`。
改签名前先看调用方是哪条路。
"""

from __future__ import annotations

from pearnly_dms import dms_booking_payments as booking_payments
from pearnly_dms import dms_booking_qa_payment as booking_qa_payment
from pearnly_dms import dms_booking_qa_sync as booking_qa_sync
from pearnly_dms import dms_qa_cards as qa_cards
from pearnly_dms.dms_company_banks import (
    PAYMENT_CHANNEL_BANKS,
    company_bank_payment_extra,
    manual_bank_allowed_for_rows,
)
from pearnly_dms.dms_out import send as _send
from pearnly_dms.dms_payments import MANUAL_BANK_FLAG
from pearnly_dms.dms_qa_util import (
    CHANNEL_EXTRA_SHAPE,
    complete_channel,
    find_row,
    parse_amount,
)

TEXT_STEPS = frozenset({"pay_amount", "pay_bank", "pay_ref"})


def masters_reader(tenant_id, *, persist):
    """会话快照主档读取器：与 `send_step` / `question` 同一条路（会话内复用，不实时读 DMS）。"""
    default_persist = persist

    def read(line_user_id, qa, key, persist=None):
        return booking_qa_sync.masters(
            tenant_id, line_user_id, qa, key, persist=persist or default_persist
        )

    return read


def manual_bank(qa, key) -> bool:
    """支票、本票和银行卡目录为空时允许手工银行名称。"""
    if key in (qa.get("manual_banks") or ()):
        return True
    return manual_bank_allowed_for_rows(
        key, ((qa.get("master_snapshot") or {}).get("rows") or {}).get(key)
    )


def _mark_manual_bank(qa, key) -> None:
    qa["manual_banks"] = sorted({*(qa.get("manual_banks") or ()), key})


def _clear_manual_bank(qa, key) -> None:
    qa["manual_banks"] = [item for item in (qa.get("manual_banks") or ()) if item != key]


def prepare_channel_bank_step(tenant_id, line_user_id, qa, *, masters, persist) -> str:
    """支票/本票/银行卡的银行步骤：目录有行 → `pay_bank`（选择）；权威为空 → `pay_ref`（手工）。"""
    key = PAYMENT_CHANNEL_BANKS[(qa.get("pending_channel") or {}).get("channel")]
    rows = masters(line_user_id, qa, key, persist=persist)
    if rows:
        _clear_manual_bank(qa, key)
        return "pay_bank"
    _mark_manual_bank(qa, key)
    return "pay_ref"


def channel_ref_question(qa) -> dict:
    """支票/本票/银行卡资料问法：该渠道目录权威为空时多问一项银行名称。"""
    channel = str((qa.get("pending_channel") or {}).get("channel") or "")
    key = PAYMENT_CHANNEL_BANKS.get(channel)
    return qa_cards.ask_pay_ref(channel, manual_bank=bool(key) and manual_bank(qa, key))


def handle_text(
    step, tenant_id, line_user_id, qa, text, reply_token, *, persist, send_step, reask
) -> None:
    """转账金额之后直接选收款银行；其它渠道保留各自资料。"""
    masters = masters_reader(tenant_id, persist=persist)
    if step == "pay_amount":
        _on_amount(
            tenant_id,
            line_user_id,
            qa,
            text,
            reply_token,
            masters=masters,
            persist=persist,
            send_step=send_step,
        )
    elif step == "pay_bank":
        _on_bank_step_text(
            tenant_id,
            line_user_id,
            qa,
            text,
            reply_token,
            masters=masters,
            persist=persist,
            send_step=send_step,
            reask=reask,
        )
    else:  # pay_ref：渠道补充资料
        _on_reference_text(
            tenant_id, line_user_id, qa, text, reply_token, persist=persist, send_step=send_step
        )


def _on_amount(
    tenant_id, line_user_id, qa, text, reply_token, *, masters, persist, send_step
) -> None:
    """金额解析失败就**原地重问**（不把非法金额带进下一步）。"""
    amount = parse_amount(text)
    if amount is None:
        _send(line_user_id, qa_cards.bad_amount(), reply_token)
        return
    pending = qa["pending_channel"]
    pending["amount"] = f"{amount:.2f}"
    shape = CHANNEL_EXTRA_SHAPE.get(pending.get("channel", ""))
    if shape == "destination":
        next_step = "pay_dst"
    elif shape == "ref":
        next_step = prepare_channel_bank_step(
            tenant_id, line_user_id, qa, masters=masters, persist=persist
        )
    elif shape == "detail":
        next_step = "pay_ref"
    else:  # cash：金额即渠道完结
        complete_channel(qa)
        next_step = "pay_more"
    qa["step"] = next_step
    persist(tenant_id, line_user_id, qa)
    send_step(tenant_id, line_user_id, qa, next_step, reply_token)


def _on_bank_step_text(
    tenant_id, line_user_id, qa, text, reply_token, *, masters, persist, send_step, reask
) -> None:
    """`pay_bank` 收到文字：目录有行 → 原步重问；权威为空 → 这条文字就是手工银行资料。"""
    next_step = prepare_channel_bank_step(
        tenant_id, line_user_id, qa, masters=masters, persist=persist
    )
    if next_step != "pay_ref":
        reask(tenant_id, line_user_id, qa, "", reply_token)
        return
    qa["step"] = next_step
    persist(tenant_id, line_user_id, qa)
    _on_reference_text(
        tenant_id, line_user_id, qa, text, reply_token, persist=persist, send_step=send_step
    )


def _on_reference_text(
    tenant_id, line_user_id, qa, text, reply_token, *, persist, send_step
) -> None:
    """渠道补充资料：该渠道银行目录权威为空时多解析一段手工银行名称。"""
    channel = str((qa.get("pending_channel") or {}).get("channel") or "")
    key = PAYMENT_CHANNEL_BANKS.get(channel)
    manual = bool(key) and manual_bank(qa, key)
    detail = booking_payments.parse_payment_detail(channel, text, manual_bank=manual)
    if detail is None:
        _send(line_user_id, qa_cards.bad_payment_detail(), reply_token)
        return
    extra = qa["pending_channel"].setdefault("extra", {})
    extra.update(detail)
    if manual:
        extra[MANUAL_BANK_FLAG] = "1"
    complete_channel(qa)
    persist(tenant_id, line_user_id, qa)
    send_step(tenant_id, line_user_id, qa, "pay_more", reply_token)


def pick_bank(
    tenant_id, line_user_id, qa, value, reply_token, *, masters, persist, send_step
):
    """选了某个银行：`pay_bank` 是渠道银行（挂 `bank_id`/`bank_name`），
    否则是收款账户（挂公司收款资料，换账户要清掉旧的分行/账号）。"""
    channel = qa["pending_channel"].get("channel")
    other = qa.get("step") == "pay_bank"
    key = PAYMENT_CHANNEL_BANKS[channel] if other else "company_banks"
    # 注意形状：这条路由状态机调用，`masters` 就是 `dms_booking_qa_sync.masters` 本身
    rows = masters(tenant_id, line_user_id, qa, key, persist=persist)
    row = find_row(rows, value)
    if row is None:
        send_step(tenant_id, line_user_id, qa, qa["step"], reply_token)
        return
    qa.setdefault("pages", {}).pop(key, None)
    extra = qa["pending_channel"].setdefault("extra", {})
    if other:
        extra.update(bank_id=str(row[0]), bank_name=str(row[2] or row[1]))
    else:
        if extra.get("dst_id") and str(extra["dst_id"]) != str(row[0]):
            extra.pop("dst_account_no", None)
            extra.pop("dst_branch_name", None)
        extra.update(company_bank_payment_extra(row, extra))
    if not other:
        _finish_destination(
            tenant_id,
            line_user_id,
            qa,
            reply_token,
            persist=persist,
            send_step=send_step,
        )
        return
    qa["step"] = "pay_ref"
    persist(tenant_id, line_user_id, qa)
    send_step(tenant_id, line_user_id, qa, qa["step"], reply_token)


def _finish_destination(tenant_id, line_user_id, qa, reply_token, *, persist, send_step):
    """转账选完收款银行就收尾；**还没凭证就先要凭证**，然后才回"还要加渠道吗"。"""
    complete_channel(qa)
    if not (qa.get("files") or {}).get("slip_mid"):
        booking_qa_payment.request_slip(
            tenant_id,
            line_user_id,
            qa,
            "pay_more",
            reply_token,
            persist=persist,
            send_step=send_step,
        )
        return
    persist(tenant_id, line_user_id, qa)
    send_step(tenant_id, line_user_id, qa, "pay_more", reply_token)
