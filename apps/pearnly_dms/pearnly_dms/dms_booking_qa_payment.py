# -*- coding: utf-8 -*-
"""LINE 订车付款方式与转账凭证的一致性步进。

照搬老产品 `services/line_dms/booking_qa_payment.py`（线上基准 `501589f0`，逻辑未改），
async → 同步。这一层是**纯步进**：状态怎么改、下一步走哪，全在这里；
发消息/存会话都靠调用方注入的回调（`persist` / `send_step` / `reask` / `to_preview`），
所以它不认识平台、也不认识 LINE。

⚠️ 这里管的是"**付款方式**"的步进（选了哪个渠道、要不要凭证、下一步问什么）；
把用户那句话拆成 DMS 原生槽位是 `dms_booking_qa_transfer` + `dms_booking_payments`
的事。两处边界不要混。
"""

from __future__ import annotations

from pearnly_dms import dms_qa_cards as qa_cards


def handle(
    action,
    tenant_id,
    line_user_id,
    qa,
    value,
    reply_token,
    *,
    persist,
    send_step,
    reask,
    to_preview,
) -> None:
    common = {
        "persist": persist,
        "send_step": send_step,
        "reask": reask,
    }
    if action == "pay":
        pick_channel(tenant_id, line_user_id, qa, value, reply_token, **common)
    elif action == "more":
        pick_more(
            tenant_id,
            line_user_id,
            qa,
            value,
            reply_token,
            to_preview=to_preview,
            **common,
        )
    else:
        pick_slip_conflict(
            tenant_id,
            line_user_id,
            qa,
            value,
            reply_token,
            to_preview=to_preview,
            **common,
        )


def pick_channel(
    tenant_id, line_user_id, qa, value, reply_token, *, persist, send_step, reask
) -> None:
    """选渠道：不认识的渠道、或这一笔里已经有的渠道，都**原步重问**（不静默接受）。"""
    if value not in qa_cards.PAY_LABELS:
        reask(tenant_id, line_user_id, qa, "", reply_token)
        return
    if value in {payment.get("channel") for payment in qa.get("payments") or []}:
        reask(tenant_id, line_user_id, qa, "", reply_token)
        return
    qa["pending_channel"] = {"channel": value}
    qa["step"] = "pay_amount"
    persist(tenant_id, line_user_id, qa)
    send_step(tenant_id, line_user_id, qa, "pay_amount", reply_token)


def request_slip(
    tenant_id, line_user_id, qa, destination, reply_token, *, persist, send_step
) -> None:
    """要凭证，并记下**拿到之后去哪**（`preview` 或 `pay_more`）。"""
    qa["step"] = "slip_after"
    qa["after_slip"] = destination
    persist(tenant_id, line_user_id, qa)
    send_step(tenant_id, line_user_id, qa, "slip_after", reply_token)


def pick_more(
    tenant_id,
    line_user_id,
    qa,
    value,
    reply_token,
    *,
    persist,
    send_step,
    reask,
    to_preview,
) -> None:
    """还要加渠道吗：加 / 齐了。收尾前要把"转账没凭证""凭证没转账"两件事先解决。"""
    if value == "add":
        if len({p.get("channel") for p in qa.get("payments") or []}) >= len(qa_cards.PAY_LABELS):
            reask(tenant_id, line_user_id, qa, "", reply_token)
            return
        qa["step"] = "pay_channel"
        persist(tenant_id, line_user_id, qa)
        send_step(tenant_id, line_user_id, qa, "pay_channel", reply_token)
        return
    if value != "done":
        reask(tenant_id, line_user_id, qa, "", reply_token)
        return
    has_transfer = "transfer" in {p.get("channel") for p in qa.get("payments") or []}
    has_slip = bool((qa.get("files") or {}).get("slip_mid"))
    if has_transfer and not has_slip:
        request_slip(
            tenant_id,
            line_user_id,
            qa,
            "preview",
            reply_token,
            persist=persist,
            send_step=send_step,
        )
        return
    if has_slip and not has_transfer:
        qa["step"] = "slip_conflict"
        persist(tenant_id, line_user_id, qa)
        send_step(tenant_id, line_user_id, qa, "slip_conflict", reply_token)
        return
    to_preview(tenant_id, line_user_id, qa, reply_token)


def pick_slip_conflict(
    tenant_id,
    line_user_id,
    qa,
    value,
    reply_token,
    *,
    persist,
    send_step,
    reask,
    to_preview,
) -> None:
    """有凭证却没有任何转账渠道：要么补一笔转账，要么把凭证删掉。"""
    if value == "add":
        qa["pending_channel"] = {"channel": "transfer"}
        qa["step"] = "pay_amount"
        persist(tenant_id, line_user_id, qa)
        send_step(tenant_id, line_user_id, qa, "pay_amount", reply_token)
        return
    if value == "remove":
        qa.setdefault("files", {})["slip_mid"] = None
        qa["step"] = "pay_more"
        persist(tenant_id, line_user_id, qa)
        to_preview(tenant_id, line_user_id, qa, reply_token)
        return
    reask(tenant_id, line_user_id, qa, "", reply_token)
