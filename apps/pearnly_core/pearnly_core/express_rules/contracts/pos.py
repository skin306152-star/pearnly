"""POS 导出按天录销售的共用纯契约（`pos_import` 产出、`push.express` 与推送选择消费；不依赖框架、不做 IO）。

一天 = 一张现金客户的简易税票日汇总（Express 现销 HS + 销项税一行），当天的事实记在这张票主页面的
``_extraction_audit["pos"]``（识别不走、不扣费）：

- ``first`` / ``last``：当天销售票号起止（按票号自然排序，不含退款单；各最长 ``NUMBER_MAX`` 字，与小助手同一上限），
  ``count``：销售票张数；
- ``payments``：``[{label, amount, cash}]``，按付款方式汇总，金额之和恰好等于当天含税合计（Decimal 字符串）。现金可以
  是负数（当天现金退款多过现金收款），非现金不能为负。

推送（载荷 3，Express 原生记法，用户 2026-10-13 定）：HS 借现金（小助手换成账套现金科目 ACCNUM00），每一种非现金付款方式
另记一张普通日记账凭证「借 收款科目 / 贷 现金」，收款科目按付款方式问一次、记在这份账套（推送选择 ``receipt``，
同一家公司别的年度账套记过、科目又在本账套科目表里的直接带出）。载荷带现金净额 ``cash``：小助手核「各转账 + 现金 =
本单含税额」。只推 Express（推往别的 ERP 拦成 ``EXPRESS_ONLY``）。

退款按天冲减（:func:`net_day`，ERP 收银日汇总与 Cowork POS 导入同一处定）：同一天的退款冲进当天，净额为 0 的行不写，一天全退
（全部为 0）不写，冲成负数停下交人。**已知差别**：Cowork 的导出表分不出原单日期，跨天的退款也冲进退款那天；ERP 跨天退货走退货单 SR。

收银退货单 SR（ERP B3b，载荷 10）：主页面 ``_extraction_audit`` 除 ``pos`` 外另带 ``refund`` 块（:func:`refund_audit`）——
``against`` = 原小票所在那张 HS 在 Express 里的单号（SR 的 SONUM）、``against_total`` = 原 HS 含税合计（IVCAMT）。
金额、税额、明细、各付款方式都记正数（退回的数量、税前额与退给客户的钱）。推送写成 Express 原生退货单（服务器测试账套实录
SR0000001）：借 退货科目（税前）、借 销项税 / 贷 现金，退现金 CNTYP 3、库存行货回仓 RETSTK Y，非库存行（服务、费用）只记金额与总账；退货科目按公司问一次（推送选择
``refund``，归类键 :data:`REFUND_SUBJECT`）；非现金退款同 HS 另记转账凭证，方向相反（借 现金 / 贷 收款科目，收款科目沿用 HS 记住的）。
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
NUMBER_MAX = 40  # 票号起止各最长这么多字（小助手 `sales_adapter._POS_NUMBER_MAX` 同一上限；摘要另按 DBF 宽度截）
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
EXPRESS_ONLY = "pos_express_only"

# 收银退货单 SR（B3b）：带 ``refund`` 块的载荷盖 10（旧版 CC 桌面版不认 SR，会当现销 HS 写成销售：诚实拒收）
REFUND_KEY = "refund"
REFUND_PAYLOAD_VERSION = 10
REFUND_KIND = "refund"          # 推送选择：退货科目（借方，税前），按公司记住
REFUND_SUBJECT = "pos_refund"   # 整家公司一个
REFUND_CNTYP = 3                # Express ARTRN.CNTYP：退现金（实录 SR0000001）
REFUND_RETSTK = "Y"             # STCRD.RETSTK：货回仓（收银退货都回仓）
REFUND_AGAINST_MAX_BYTES = 12   # 原单号写进 ARTRN.SONUM，与 DOCNUM 同宽 C(12)
REFUND_ACCOUNT_MISSING = "pos_refund_account_missing"
REFUND_ITEMS_INVALID = "pos_refund_items_invalid"  # 明细不可信（没对上 / 空）、库存行没存货码、行模式认不出；非库存行照收


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

    def cash(self) -> Decimal:
        """当天现金净额（可为负：现金退款多过现金收款）。"""
        return sum((payment.amount for payment in self.payments if payment.cash), Decimal("0"))


def audit(first, last, count, payments, *, layout="", rows=None) -> dict:
    """写进主页面 ``_extraction_audit`` 的那一块：``payments`` 是 ``[(label, Decimal)]``，金额原样存成两位小数串。"""
    block = {"version": VERSION, "first": number(first), "last": number(last), "count": int(count),
             "payments": [{"label": str(label)[:LABEL_MAX], "amount": format(Decimal(amount).quantize(_CENT), "f"),
                           "cash": is_cash(label)} for label, amount in payments],
             "layout": str(layout or "")}
    if rows:
        block["rows"] = [list(pair) for pair in rows][:500]
    return {AUDIT_KEY: block}


def number(value) -> str:
    """票号起止按小助手的上限截短（外卖平台单号、订单 UUID 这类长单号；完整起止仍在复核页与摘要里）。"""
    return str(value or "").strip()[:NUMBER_MAX]


def present(page) -> bool:
    """这一页是不是 POS 导入的日汇总票（只看有没有 POS 块，形状对不对由 `facts` 判）。"""
    audit_block = (page or {}).get("_extraction_audit") if isinstance(page, dict) else None
    return isinstance(audit_block, dict) and AUDIT_KEY in audit_block


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
    return Facts(number(first), number(last), count, tuple(payments))


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


# ── 按天冲减（主会话 10-25）：同一天的退款 / 退货冲进当天这张汇总，ERP 收银日汇总与 Cowork POS 导入都调这一个函数 ────────────
# 已知差别（登记在案）：Cowork 读的收银机导出表里退款单不带原单日期，只能按**退款那天**冲；ERP 分得出原单哪天卖的，
# 只冲同一天的退货，跨天退货另写退货单 SR（B3b），不进日汇总。
DAY_NOT_POSITIVE = "day_not_positive"
PAYMENT_NEGATIVE = "payment_negative"
ITEMS_NOT_POSITIVE = "items_not_positive"


class NetError(ValueError):
    """冲完是负数：整张停下交人（不硬写），``code`` 是上面三种之一。"""

    def __init__(self, code: str, label: str = ""):
        super().__init__(code)
        self.code, self.label = code, label


@dataclass(frozen=True)
class NetDay:
    payments: tuple  # ((写法, 净额), ...)，净额为 0 的不在里面
    items: tuple     # 调用方合好的商品行（dict，带 ``qty`` / ``amount``），净数量与净金额都是 0 的不在里面


def net_day(gross, payments, items=None) -> NetDay | None:
    """一天（销售为正、退款为负，调用方已按付款方式、按商品合好）冲减后的样子：

    - 净额为 0 的付款方式、净数量和净金额都为 0 的商品行不写；
    - **完全为 0**（当天净含税额、每种付款方式、每个商品行都是 0，一天全退）→ ``None``：这一天什么都不写；
    - 只要有一项冲成负数就抛 :class:`NetError`：当天净额 ≤ 0、非现金付款方式净额为负（现金可以为负：现金退款多过现金收款）、
      商品行净数量 ≤ 0 或净金额为负。``payments`` 是 ``[(写法, 金额)]``，``items`` 不给就不核商品。"""
    gross = Decimal(str(gross))
    paid = [(label, Decimal(str(amount))) for label, amount in payments]
    lines = list(items or [])
    kept_lines = [line for line in lines if Decimal(str(line["qty"])) != 0 or Decimal(str(line["amount"])) != 0]
    kept_paid = [(label, amount) for label, amount in paid if amount != 0]
    if gross == 0 and not kept_lines and not kept_paid:
        return None
    if gross <= 0:
        raise NetError(DAY_NOT_POSITIVE)
    for label, amount in kept_paid:
        if amount < 0 and not is_cash(label):
            raise NetError(PAYMENT_NEGATIVE, label)
    if any(Decimal(str(line["qty"])) <= 0 or Decimal(str(line["amount"])) < 0 for line in kept_lines):
        raise NetError(ITEMS_NOT_POSITIVE)
    return NetDay(tuple(kept_paid), tuple(kept_lines))


# ── 收银退货单 SR（B3b）────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Refund:
    against: str            # 原 HS 在 Express 里的单号
    against_total: Decimal  # 原 HS 含税合计


def refund_audit(against, against_total) -> dict:
    """写进主页面 ``_extraction_audit`` 的退货块（与 :func:`audit` 并列）：原单号原样、原单合计存成两位小数串。"""
    return {REFUND_KEY: {"against": str(against or "").strip(),
                         "against_total": format(Decimal(str(against_total)).quantize(_CENT), "f")}}


def refund_facts(page) -> Refund | None:
    """主页面上的退货块；不是退货单回 None，形状不对抛 `PosFactsError`（原单号空 / 超过 12 字节、原单合计不是正的两位小数）。"""
    block = ((page or {}).get("_extraction_audit") or {}).get(REFUND_KEY) if isinstance(page, dict) else None
    if block is None:
        return None
    if not isinstance(block, dict) or not isinstance(block.get("against"), str):
        raise PosFactsError(FACTS_INVALID)
    against = block["against"].strip()
    if not against or _bytes(against) > REFUND_AGAINST_MAX_BYTES:
        raise PosFactsError(FACTS_INVALID)
    total = _money(block.get("against_total"))
    if total <= 0:
        raise PosFactsError(FACTS_INVALID)
    return Refund(against, total)


def check_refund(found: Facts, total) -> str:
    """退货单的付款方式：同 :func:`check`（之和恰好等于退款含税额、非现金不为负），另外现金也不能为负（退给客户的钱都是正数）。"""
    problem = check(found, total)
    if problem:
        return problem
    return MISMATCH if found.cash() < 0 else ""


__all__ = ["ACCOUNT_MISSING", "AUDIT_KEY", "CASH_LABELS", "DAY_NOT_POSITIVE", "EXPRESS_ONLY", "FACTS_INVALID", "Facts",
           "ITEMS_NOT_POSITIVE", "MISMATCH", "NUMBER_MAX", "NetDay", "NetError", "PAYLOAD_VERSION", "PAYMENT_NEGATIVE", "Payment",
           "PosFactsError", "RECEIPT_KIND", "REFUND_ACCOUNT_MISSING", "REFUND_AGAINST_MAX_BYTES", "REFUND_CNTYP",
           "REFUND_ITEMS_INVALID", "REFUND_KEY", "REFUND_KIND", "REFUND_PAYLOAD_VERSION", "REFUND_RETSTK", "REFUND_SUBJECT",
           "REF_MAX_BYTES", "Refund", "VERSION", "audit", "check", "check_refund", "day_ref", "facts", "is_cash",
           "method_subject", "net_day", "norm_label", "number", "present", "range_text", "refund_audit", "refund_facts"]
