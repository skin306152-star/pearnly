# -*- coding: utf-8 -*-
"""订金支付渠道 → DMS 订车单表单字段的**纯映射**（零 IO、可单测）。

逐问收上来的 payments 是 `[{channel, amount, extra}]`，而 DMS 表单是每个渠道一组
固定字段名。这层只做映射与聚合，金额一律用 `Decimal`。

照搬老产品 `services/erp/mrerp_dms_payments.py`，字段名是**真机勘察**来的，不能改。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict

# 订金支付渠道闭集 —— 未知渠道**必须报错，不许静默丢**（丢钱这种事不能静默）。
_PAYMENT_CHANNELS = ("cash", "transfer", "cheque", "cashier_cheque", "card", "other")

# 每渠道在 DMS 订车单表单上的金额字段（真机勘察字段名）。
_PAYMENT_MONEY_FIELD = {
    "cash": "txtmoneycash",
    "transfer": "txtmoneytfmon",
    "cheque": "txtmoneycheque",
    "cashier_cheque": "txtmoneycashiercq",
    "card": "txtmoneycddbc",
    "other": "txtmoneyother",
}

# 每渠道的结构化 extra 槽位 → DMS 真正的表单字段。
_PAYMENT_TEXT_FIELD = {
    "transfer": {
        "src_account_name": "txtowneraccnametffrom",
        "src_account_no": "txtaccountnumtffrom",
        "src_bank_name": "txtbanknametffrom",
        "src_bank_id": "banktffromval",
        "src_branch_name": "txtbranchnametffrom",
        "src_time": "txttimetffrom",
        "dst_business_name": "txtbusinessnametfmon",
        "dst_account_no": "txtaccountnumtfmon",
        "dst_bank_name": "txtbanknametfmon",
        "dst_bank_id": "banktfmonval",
        "dst_branch_name": "txtbranchnametfmon",
    },
    "cheque": {
        "cheque_no": "txtchequeno",
        "cheque_book_no": "txtbooknocheque",
        "bank_name": "txtbanknamecheque",
        "bank_id": "bankchequeval",
    },
    "cashier_cheque": {
        "cashier_no": "txtcashiercqno",
        "cashier_book_no": "txtbooknocashiercq",
        "bank_name": "txtbanknamecashiercq",
        "bank_id": "bankcashiercqval",
    },
    "card": {
        "bank_name": "txtbanknamecddbc",
        "bank_id": "bankcddbcval",
        "card_type": "txttypenamecddbc",
    },
    "other": {"detail": "txtdetailother"},
}

# 早期调用方把票据号塞在 extra["ref"] 里，这里兼容读。
_LEGACY_EXTRA = {
    "cheque": {"cheque_no": "ref"},
    "cashier_cheque": {"cashier_no": "ref"},
    "card": {"card_type": "ref"},
}


def payment_form_fields(payments: tuple) -> Dict[str, str]:
    """聚合订金支付渠道 → DMS 表单字段。

    DMS 每个渠道只有一组固定字段，所以**同渠道重复必须拦截** —— 拼起来会伪装成一笔，
    金额就错了。空 payments 返回空 dict，调用方保留表单默认的 `txtearnestmoney="0.00"`。
    """
    totals: Dict[str, Decimal] = {}
    extras: Dict[str, dict] = {}
    for pay in payments:
        channel = pay.get("channel")
        if channel not in _PAYMENT_CHANNELS:
            raise ValueError(f"unknown payment channel: {channel!r}")
        if channel in totals:
            raise ValueError(f"duplicate payment channel: {channel!r}")
        amount = str(pay.get("amount") or "0").replace(",", "")
        totals[channel] = Decimal(amount)
        extra = dict(pay.get("extra") or {})
        # 转账的"来源"是一整行文字（可能写"银行名 账号"），拆成两个字段。
        # 拿不准就别硬拆 —— 只在明显有账号时才拆。
        if (
            channel == "transfer"
            and extra.get("src")
            and not (extra.get("src_account_no") or extra.get("src_bank_name"))
        ):
            source = str(extra["src"]).strip()
            parts = source.split()
            if source != "-" and len(parts) > 1 and any(ch.isdigit() for ch in parts[-1]):
                extra["src_bank_name"] = " ".join(parts[:-1])
                extra["src_account_no"] = parts[-1]
            elif source != "-" and any(ch.isdigit() for ch in source):
                extra["src_account_no"] = source
            elif source != "-":
                extra["src_bank_name"] = source
        extras[channel] = extra

    fields: Dict[str, str] = {}
    grand_total = Decimal("0")
    for channel in _PAYMENT_CHANNELS:  # 固定顺序，输出确定、可断言
        if channel not in totals:
            continue
        grand_total += totals[channel]
        fields[_PAYMENT_MONEY_FIELD[channel]] = f"{totals[channel]:.2f}"
        for slot, form_field in _PAYMENT_TEXT_FIELD.get(channel, {}).items():
            extra = extras.get(channel, {})
            value = extra.get(slot)
            if not value:
                value = extra.get((_LEGACY_EXTRA.get(channel) or {}).get(slot, ""))
            if value and value != "-":
                fields[form_field] = str(value)
    if fields:
        fields["txtearnestmoney"] = f"{grand_total:.2f}"
    return fields
