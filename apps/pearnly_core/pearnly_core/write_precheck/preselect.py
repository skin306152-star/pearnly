"""没有记忆时的预选规则（多产品共用，纯函数；评估 `docs/work/2026-11-04-precheck-card.md` 1.1–1.6，主会话 11-04 审定）。

每条只给一个值或空串（空 = 没把握，这一项留作未定）；来源由调用方按 `contracts` 记成 ``rule`` / ``history``。
预选**不自动生效**：要人在卡片上点「开始」才算数，定时任务、自动推里也一样（Q2）。已记住、公司规则、Express 历史定的
不走这里（那是已定）。

| 规则 | 类 | 判法 |
|---|---|---|
| :func:`party` | 对方档 | 同税号的档里这本账套用得最多的；没有次数时分店一致的唯一档；只有一个同税号档就它；一个都没有就「新建」 |
| :func:`most_used` | 存货科目组等 | 次数最多的（只在给定范围里），平手取编码小的 |
| :func:`vat` | 小票 / 简易税票的税额抵不抵 | 这家以往记法一贯就照它，否则「不抵」（简易税票一般不能抵进项，CC 在对话里提醒会计核对，Q7） |
| :func:`payment` | 现购 / 赊购 | 共用付款判定漏斗（票种：收据 = 已付，只有税票 = 赊） |
| :func:`refund_account` | 收银退货科目 | 科目表里名称含「รับคืน / ลดหนี้」的唯一科目 |
| :func:`vat_registered` | 是否增值税登记户 | 公司税号 13 位、有过带税的销售 → 是 |
| :func:`receipt_account` | 非现金收款科目 | 按付款方式认类（转账 / QR、刷卡、外卖平台），科目表里对得上的唯一科目 |
| :func:`document_policy` | 转账凭条、预扣税凭证录不录 | 「不录」（与 ERP 的口径一致，Q5） |
| :func:`service_vat` | 服务品进项税口径 | 这本账套各家口径的并集一贯一种就它，否则 เกณฑ์สิทธิ（语料里多数） |
| :func:`posting_kind` | 部分行没声明服务 / 存货 | 没声明的行按识别的品类，全部一致才给 |
"""
from __future__ import annotations

import unicodedata
from typing import Any, Iterable, Mapping, Optional

from pearnly_core.express_rules.contracts import pos as pos_contract
from pearnly_core.express_rules.push import service_vat as service_vat_rules
from pearnly_core.express_rules.push.choices import NEW, PAYMENT_VALUES, VAT_VALUES, branch5, digits
from pearnly_core.express_rules.push.payment import payment_verdict

CLAIM, EXPENSE = VAT_VALUES  # 小票税额：抵进项 / 记费用
CASH, CREDIT = PAYMENT_VALUES
SKIP = "skip"  # 录不录：不录（Cowork `contracts.document_policy.SKIP` 的值）
VAT_YES = "yes"  # 增值税登记户：是（ERP `masters.company.VAT_YES` 的值）
REFUND_WORDS = ("รับคืน", "ลดหนี้")
# 付款方式 → 认它的词（付款方式的写法，`pos.norm_label` 之后）与对得上的科目名称里的词
RECEIPT_CLASSES = (
    (("promptpay", "prompt pay", "พร้อมเพย์", "qr", "คิวอาร์", "transfer", "โอน", "bank", "ธนาคาร"),
     ("เงินฝาก", "ธนาคาร", "bank")),
    (("card", "บัตร", "visa", "master", "credit card", "edc"), ("บัตร", "card", "edc")),
)
PLATFORMS = ("grab", "lineman", "line man", "foodpanda", "shopee", "robinhood", "lazada")
GOODS, SERVICE_TYPE = "goods", "service"  # 识别的品类（明细行 ``item_type``）
POSTING_BY_TYPE = {GOODS: "stock", SERVICE_TYPE: "service"}  # → 入账类型（`contracts.push.VALID_POSTING_KINDS`）


def _norm(value) -> str:
    return unicodedata.normalize("NFC", str(value or "")).casefold()


def _code(row) -> str:
    return str(row.get("code") or "").strip() if isinstance(row, Mapping) else ""


def most_used(counts: Optional[Mapping[str, Any]], allowed: Optional[Iterable[str]] = None) -> str:
    """次数最多的编码（次数 > 0，``allowed`` 给了就只在其中挑）；平手取编码小的；没有就空串。"""
    pool = set(allowed) if allowed is not None else None
    best = ("", 0)
    for code, count in sorted((counts or {}).items()):
        try:
            number = int(count)
        except (TypeError, ValueError):
            continue
        if number > best[1] and (pool is None or code in pool):
            best = (str(code), number)
    return best[0]


def party(options: Iterable[Mapping[str, Any]], counts: Optional[Mapping[str, Any]] = None, branch: Any = "") -> str:
    """对方档：``options`` 是同税号的档 ``[{code, branch}]``（共用写账规则问 party 时给的），``counts`` 是这本账套里各档用过几次。"""
    rows = [row for row in options or () if _code(row) and _code(row) != NEW]
    if not rows:
        return NEW
    codes = [_code(row) for row in rows]
    used = most_used(counts, codes)
    if used:
        return used
    exact = [_code(row) for row in rows if branch5(row.get("branch")) == branch5(branch)]
    if len(exact) == 1:
        return exact[0]
    return codes[0] if len(codes) == 1 else ""


def vat(history: Iterable[str] = ()) -> str:
    """小票 / 简易税票的税额：这家以往的记法一贯一种就照它，否则「不抵（记费用）」。"""
    seen = {str(value) for value in history or () if str(value) in VAT_VALUES}
    return seen.pop() if len(seen) == 1 else EXPENSE


def payment(fields: Mapping[str, Any], direction: str = "purchase") -> str:
    """现购 / 赊购：共用付款判定漏斗（`express_rules.push.payment.payment_verdict`，人工裁决、票面、票种……）；定不了回空串。"""
    paid, _source = payment_verdict(dict(fields or {}), direction=direction)
    if paid is True:
        return CASH
    return CREDIT if paid is False else ""


def _unique(accounts, words) -> str:
    found = [_code(row) for row in accounts or () if _code(row)
             and any(word in _norm(row.get("name")) for word in words)]
    return found[0] if len(found) == 1 else ""


def refund_account(accounts: Iterable[Mapping[str, Any]]) -> str:
    """收银退货单 SR 的退货科目：科目表 ``[{code, name}]`` 里名称含「รับคืน / ลดหนี้」的唯一科目；没有或不止一个回空串。"""
    return _unique(accounts, REFUND_WORDS)


def vat_registered(tax_id: Any, taxed_sales: bool) -> str:
    """公司是不是增值税登记户：税号 13 位、而且有过带税的销售 → 是；否则不预选（「不是」要人自己定）。"""
    return VAT_YES if len(digits(tax_id)) == 13 and taxed_sales else ""


def receipt_account(method: Any, accounts: Iterable[Mapping[str, Any]]) -> str:
    """POS 非现金付款方式（付款方式的原写法）记哪个收款科目：外卖平台找名称含平台名的科目；转账 / QR 找存款、银行科目；
    刷卡找名称含「บัตร / card」的科目。都要正好一个才给。现金不问。"""
    label = pos_contract.norm_label(method)
    if not label or pos_contract.is_cash(method):
        return ""
    accounts = list(accounts or ())
    for platform in PLATFORMS:
        if platform in label:
            return _unique(accounts, (platform, platform.replace(" ", "")))
    for words, names in RECEIPT_CLASSES:
        if any(word in label for word in words):
            return _unique(accounts, names)
    return ""


def document_policy(kind: str = "") -> str:
    """转账凭条、预扣税凭证录不录：预选「不录」（Q5）。按租户选过的是已定，不走这里。"""
    return SKIP


def service_vat(supplier_verdicts: Iterable[str] = ()) -> str:
    """服务品进项税口径（选项编码 ``accrual`` / ``cash_basis``）：这本账套各家的口径串并起来一贯一种就它，否则 ``accrual``。"""
    book = service_vat_rules.book_verdict(supplier_verdicts or ())
    if book == service_vat_rules.CASH_BASIS:
        return "cash_basis"
    return "accrual"


def posting_kind(lines: Iterable[Mapping[str, Any]]) -> str:
    """一张单部分行没声明入账类型：没声明的行（``posting_kind`` 空）按识别的品类 ``item_type``，全部一致才给 ``stock`` /
    ``service``；有读不出或不一致的回空串。"""
    kinds = set()
    for line in lines or ():
        if not isinstance(line, Mapping) or str(line.get("posting_kind") or "").strip():
            continue
        kinds.add(POSTING_BY_TYPE.get(str(line.get("item_type") or "").strip().lower(), ""))
    return kinds.pop() if len(kinds) == 1 and "" not in kinds else ""


__all__ = ["CASH", "CLAIM", "CREDIT", "EXPENSE", "PLATFORMS", "REFUND_WORDS", "SKIP", "VAT_YES", "document_policy",
           "most_used", "party", "payment", "posting_kind", "receipt_account", "refund_account", "service_vat", "vat",
           "vat_registered"]
