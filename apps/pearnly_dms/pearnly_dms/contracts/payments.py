# -*- coding: utf-8 -*-
"""订金支付渠道 → DMS 订车单表单字段的**纯映射**（零 IO、可单测）。

逐问收上来的 payments 是 `[{channel, amount, extra}]`，而 DMS 表单是每个渠道一组
固定字段名。这层只做映射与聚合，金额一律用 `Decimal`。

⚠️ **这一层只认 DMS 的原生槽位名**（`cheque_no` / `card_type` / `dst_bank_id`…）。
把 LINE 里那句自由文本拆成原生槽位，是**上一层**的事
（老产品 `services/line_dms/booking_payments.py`）。以前这里塞过一个
`_LEGACY_EXTRA` 兼容层去拆 `extra["ref"]`/`extra["src"]`，是错位的职责，
已按线上删掉 —— 否则"文本怎么拆"会有两个地方各说各话。

照搬老产品 `services/erp/mrerp_dms_payments.py`（线上基准 `501589f0`），
字段名是**真机勘察**来的，不能改。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict

from pearnly_dms.contracts.client_base import DmsClientError

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

# 该渠道原生银行目录「权威为空」、银行名称由用户手工填写时挂在 extra 上的标记。
# 它只说明「名称来自手工输入」，自身不构成提交依据：目录非空时
# `validate_company_bank_payments` 与 `normalize_editor_payments` 都按实时目录判，
# 命中目录后会把标记就地清掉。
MANUAL_BANK_FLAG = "bank_manual"

# 目录权威为空时可省的原生字段：银行 id 由目录行产生，手工填名称时留空（名称字段照旧必填）。
# company_banks（公司收款账户）不在此列 —— 收款账户永不手工，必须实时目录选择。
_MANUAL_OPTIONAL_FIELDS = {
    "transfer": frozenset({"src_bank_id"}),
    "cheque": frozenset({"bank_id"}),
    "cashier_cheque": frozenset({"bank_id"}),
    "card": frozenset({"bank_id"}),
}

# 转账选择要认收款方银行身份。其它银行字段有就映射，但**不额外追问、也不卡住
# 一笔只填了银行的转账**。
TRANSFER_SOURCE_FIELDS = frozenset(
    {"src_bank_id", "src_bank_name", "src_account_name", "src_account_no", "src_branch_name"}
)
_OPTIONAL_FIELDS = {
    "transfer": frozenset({"dst_business_name", "dst_account_no", "dst_branch_name"})
    | TRANSFER_SOURCE_FIELDS
}


def manual_bank_entry(extra: dict) -> bool:
    """这笔付款的银行名称是否来自「目录权威为空时的手工填写」。"""
    return str((extra or {}).get(MANUAL_BANK_FLAG) or "").strip() == "1"


def missing_transfer_fields(extra: dict) -> list[str]:
    """转账缺了哪些 DMS 真正要求的原生字段。"""
    return _missing_fields("transfer", extra)


def _missing_fields(channel: str, extra: dict) -> list[str]:
    optional = _OPTIONAL_FIELDS.get(channel, frozenset()) | (
        _MANUAL_OPTIONAL_FIELDS.get(channel, frozenset())
        if manual_bank_entry(extra)
        else frozenset()
    )
    return [
        key
        for key in _PAYMENT_TEXT_FIELD.get(channel, {})
        if key not in optional
        if not str(extra.get(key) or "").strip() or str(extra.get(key)).strip() == "-"
    ]


def validate_payment_completeness(payments) -> None:
    """**在任何 DMS 写入之前**拦住不完整的原生收款资料（也管非 LINE 的调用方）。

    以前这一步在新平台是缺的：不完整的收款资料会被放行到 DMS，
    等到那时才发现就晚了（钱的事不能靠对方兜底）。
    """
    for payment in payments or ():
        channel = payment.get("channel")
        extra = payment.get("extra") or {}
        missing = _missing_fields(channel, extra)
        if missing:
            raise DmsClientError(
                str(channel) + " payment is incomplete; missing=" + ",".join(missing),
                "ERR_DMS_PAYMENT_INCOMPLETE",
            )


def payment_form_fields(payments: tuple) -> Dict[str, str]:
    """聚合订金支付渠道 → DMS 表单字段。

    DMS 每个渠道只有一组固定字段，所以**同渠道重复必须拦截** —— 拼起来会伪装成一笔，
    金额就错了。空 payments 返回空 dict，调用方保留表单默认的 `txtearnestmoney="0.00"`。
    """
    validate_payment_completeness(payments)
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
            if value and value != "-":
                fields[form_field] = str(value)
    if fields:
        fields["txtearnestmoney"] = f"{grand_total:.2f}"
    return fields
