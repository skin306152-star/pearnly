"""随票附属页：同一份文件里已经有正式票据时，模型记成「其他」的那几页不另算一张票（纯函数，不连库、不调 API）。

2026-10-07 独立金标（谷歌现行读法）：多出来的票 27 张里大半是这种页——送货单 / 明细续页（单号与正式票相同、
或没有合计）、同一开票方的附件、连开票方都没有的单据页。零费用重放：多出 27→9 张，漏票 0、真票被拒 0。
只动「其他」类，正式票（税票 / 简易税票 / 收据 / 贷项单）一张不碰；文件里没有正式票时整份不动。
这是识别多出来的条目，不是记账口径，所以不问人（用户 2026-10-07 分类：口径类按账套选、拿不准的单张标疑点）。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

PROPER = frozenset({"tax_invoice", "simplified_tax_invoice", "receipt", "credit_note"})
ATTACHMENT_PREFIX = "attachment_page:"


def _key(value) -> str:
    return "".join(str(value or "").split()).upper()


def _no_total(value) -> bool:
    text = str(value or "").replace(",", "").strip()
    if not text:
        return True
    try:
        return Decimal(text) == 0
    except InvalidOperation:
        return False


def attachment_reasons(invoices) -> list[str | None]:
    """同一份文件里还算票的读数（逐页、同页多张展开）→ 等长列表：附属页给原因，其余 ``None``。

    原因：``same_number`` 单号与文件里另一条相同；``no_total`` 没有合计；``same_seller`` 开票方（税号或名称）
    与正式票相同；``no_issuer`` 税号和名称都没有。"""
    invoices = list(invoices)
    proper = [inv for inv in invoices if inv.document_type in PROPER]
    if not proper:
        return [None] * len(invoices)
    numbers = [_key(inv.invoice_number) for inv in invoices]
    sellers = {_key(value) for inv in proper for value in (inv.seller_tax, inv.seller_name) if _key(value)}
    reasons = []
    for inv, number in zip(invoices, numbers):
        tax, name = _key(inv.seller_tax), _key(inv.seller_name)
        if inv.document_type != "other":
            reasons.append(None)
        elif number and numbers.count(number) > 1:
            reasons.append("same_number")
        elif _no_total(inv.total_amount):
            reasons.append("no_total")
        elif (tax and tax in sellers) or (name and name in sellers):
            reasons.append("same_seller")
        elif not tax and not name:
            reasons.append("no_issuer")
        else:
            reasons.append(None)
    return reasons


def drop_attachments(pages) -> None:
    """按 `attachment_reasons` 就地处理引擎的页：同页的附属读数从 additional_invoices 去掉；页首那张是附属页时，
    同页还有别的票就把下一张提上来，没有就整页记成非发票。每处理一条在该页记一条 ``attachment_page:`` 警示。"""
    slots = []
    for page in pages:
        if page.invoice.is_not_invoice:
            continue
        slots.append((page, None, page.invoice))
        slots.extend((page, index, child) for index, child in enumerate(page.invoice.additional_invoices or [])
                     if not child.is_not_invoice)
    reasons = attachment_reasons(invoice for _, _, invoice in slots)
    dropped = {}
    for (page, index, invoice), reason in zip(slots, reasons):
        if reason:
            dropped.setdefault(id(page), (page, set()))[1].add(index)
            page.validation_warnings.append(f"{ATTACHMENT_PREFIX} {reason} {invoice.invoice_number or ''}".rstrip())
    for page, indexes in dropped.values():
        top = page.invoice
        children = [child for index, child in enumerate(top.additional_invoices or []) if index not in indexes]
        if None not in indexes:
            page.invoice = top.model_copy(update={"additional_invoices": children})
        elif children:
            page.invoice = children[0].model_copy(update={"additional_invoices": children[1:]})
        else:
            page.invoice = top.model_copy(update={"is_not_invoice": True, "additional_invoices": []})


__all__ = ["ATTACHMENT_PREFIX", "PROPER", "attachment_reasons", "drop_attachments"]
