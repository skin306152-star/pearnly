"""POS 导出按天录销售的共用纯契约（`pos_import` 产出、`push.express` 与推送选择消费；不依赖框架、不做 IO）。

一天 = 一张现金客户的简易税票日汇总（Express 现销 HS + 销项税一行），当天的事实记在这张票主页面的
``_extraction_audit["pos"]``（识别不走、不扣费）：

- ``first`` / ``last``：当天销售票号起止（按票号自然排序，不含退款单），``count``：销售票张数；
- ``payments``：``[{label, amount, cash}]``，按付款方式汇总，金额之和恰好等于当天含税合计（Decimal 字符串）。

推送（载荷 3，Express 原生记法，用户 2026-10-13 定）：HS 借现金（小助手换成账套现金科目 ACCNUM00），每一种非现金付款方式
另记一张普通日记账凭证「借 收款科目 / 贷 现金」，收款科目按付款方式问一次、记在这份账套（推送选择 ``receipt``）。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

AUDIT_KEY = "pos"
VERSION = 1
# 带 POS 块（自编单号 + 付款方式转账凭证）的载荷：旧版小助手不认，按版本诚实拒收（preflight 版本协商）。
PAYLOAD_VERSION = 3
RECEIPT_KIND = "receipt"
REF_MAX_BYTES = 15  # 销项税记录 REFNUM C(15)：票号区间写进去的上限
LABEL_MAX = 50
SUBJECT_MAX = 200
PAYMENTS_MAX = 20
_CENT = Decimal("0.01")
# 付款方式写法归一后是这些就是现金（没有付款方式一栏时整天都按现金）。
CASH_LABELS = frozenset({"", "cash", "cash payment", "cash sale", "เงินสด", "สด", "ชำระเงินสด", "รับเงินสด", "现金",
                         "現金", "現金払い"})

# 推送映射失败的原因码（`intake.error.<码>` 有译文）
MISMATCH = "pos_payments_mismatch"
ACCOUNT_MISSING = "pos_receipt_account_missing"
FACTS_INVALID = "pos_facts_invalid"


class PosFactsError(ValueError):
    pass


def norm_label(value) -> str:
    """付款方式的比较写法：NFC、小写、去首尾与多余空白和常见标点。"""
    text = unicodedata.normalize("NFC", str(value or "")).casefold().replace("\xa0", " ")
    text = re.sub(r"[\s._:;()\[\]]+", " ", text)
    return text.strip()


def is_cash(label) -> bool:
    return norm_label(label) in CASH_LABELS


def method_subject(label) -> str:
    """推送选择里「这种付款方式记哪个收款科目」的归类键（按账套记，见 `push/express/choices.py`）。"""
    return ("pos|" + norm_label(label))[:SUBJECT_MAX]


def _bytes(text: str) -> int:
    return len(str(text or "").encode("cp874", "replace"))


def range_text(first, last) -> str:
    first, last = str(first or "").strip(), str(last or "").strip()
    return first if not last or last == first else f"{first}-{last}"


def day_ref(first, last, day: date) -> str:
    """当天这张汇总票的参考号（写进 Express 的 YOUREF 与销项税 REFNUM，≤15 字节）：

    只有一张 → 票号本身；放得下 → ``首-尾``；否则尾号去掉与首号相同的开头 → ``首-尾的不同部分``；
    还放不下（票号本身太长）→ ``ABB`` + 佛历年月日。完整起止另写在销项税记录的摘要里。"""
    first, last = str(first or "").strip(), str(last or "").strip()
    whole = range_text(first, last)
    if _usable(whole):
        return whole
    if first and last and _bytes(first) < REF_MAX_BYTES - 1:
        common = 0
        for left, right in zip(first, last):
            if left != right:
                break
            common += 1
        short = f"{first}-{last[common:] or last}"
        if _usable(short):
            return short
    return f"ABB{(day.year + 543) % 100:02d}{day.month:02d}{day.day:02d}"


# 推送映射把「像日期」的票号当没有（`push/express/field_clean.clean_invoice_no`）：这种区间不拿来当参考号
_DATE_LIKE = re.compile(r"^\d{1,4}[/\-.]\d{1,2}[/\-.]\d{2,4}$|^\d{4}-\d{2}-\d{2}$")


def _usable(ref: str) -> bool:
    return bool(ref) and _bytes(ref) <= REF_MAX_BYTES and not _DATE_LIKE.match(ref)


def _money(value) -> Decimal:
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError) as error:
        raise PosFactsError(FACTS_INVALID) from error
    if not amount.is_finite() or amount != amount.quantize(_CENT):
        raise PosFactsError(FACTS_INVALID)
    return amount


@dataclass(frozen=True)
class Payment:
    label: str
    amount: Decimal
    cash: bool

    @property
    def subject(self) -> str:
        return method_subject(self.label)


@dataclass(frozen=True)
class Facts:
    first: str
    last: str
    count: int
    payments: tuple

    @property
    def range(self) -> str:
        return range_text(self.first, self.last)

    def non_cash(self) -> list:
        return [payment for payment in self.payments if not payment.cash]


def audit(first, last, count, payments, *, layout="", rows=None) -> dict:
    """写进主页面 ``_extraction_audit`` 的那一块：``payments`` 是 ``[(label, Decimal)]``，金额原样存成两位小数串。"""
    block = {"version": VERSION, "first": str(first), "last": str(last), "count": int(count),
             "payments": [{"label": str(label)[:LABEL_MAX], "amount": format(Decimal(amount).quantize(_CENT), "f"),
                           "cash": is_cash(label)} for label, amount in payments],
             "layout": str(layout or "")}
    if rows:
        block["rows"] = [list(pair) for pair in rows][:500]
    return {AUDIT_KEY: block}


def facts(page) -> Facts | None:
    """主页面上的 POS 事实；不是 POS 导入的票回 None，形状不对抛 `PosFactsError`。"""
    block = ((page or {}).get("_extraction_audit") or {}).get(AUDIT_KEY) if isinstance(page, dict) else None
    if block is None:
        return None
    if not isinstance(block, dict) or block.get("version") != VERSION:
        raise PosFactsError(FACTS_INVALID)
    first, last, count = block.get("first"), block.get("last"), block.get("count")
    rows = block.get("payments")
    if (not isinstance(first, str) or not first.strip() or not isinstance(last, str) or not last.strip()
            or type(count) is not int or count < 1 or not isinstance(rows, list) or not 1 <= len(rows) <= PAYMENTS_MAX):
        raise PosFactsError(FACTS_INVALID)
    payments = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("label"), str):
            raise PosFactsError(FACTS_INVALID)
        payments.append(Payment(row["label"][:LABEL_MAX], _money(row.get("amount")), is_cash(row["label"])))
    return Facts(first.strip(), last.strip(), count, tuple(payments))


def check(found: Facts, total) -> str:
    """付款方式之和须恰好等于这张票的含税合计（复核页改了金额却没改分账就对不上），否则回原因码。"""
    try:
        total = Decimal(str(total))
    except (InvalidOperation, ValueError):
        return MISMATCH
    paid = sum((payment.amount for payment in found.payments), Decimal("0"))
    if paid.quantize(_CENT) != total.quantize(_CENT):
        return MISMATCH
    if any(payment.amount < 0 for payment in found.non_cash()):
        return MISMATCH
    return ""


__all__ = ["ACCOUNT_MISSING", "AUDIT_KEY", "CASH_LABELS", "FACTS_INVALID", "Facts", "MISMATCH", "PAYLOAD_VERSION",
           "Payment", "PosFactsError", "RECEIPT_KIND", "REF_MAX_BYTES", "VERSION", "audit", "check", "day_ref", "facts",
           "is_cash", "method_subject", "norm_label", "range_text"]
