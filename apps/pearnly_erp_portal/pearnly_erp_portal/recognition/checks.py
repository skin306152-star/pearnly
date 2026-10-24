"""识别结果的确定性检查（纯函数，不读库、不联网）。

照旧线上 `pearnly-app@ec2e5c95` 发票直读的两层闸（`services/ocr/direct_read.py:429-445`
`_invoice_hard_gates`、`services/ocr/triggers.py:75-100`、`services/ocr/sanity.py:36-280`、
`services/ocr/date_sanity.py:20-46`），数值与容差照抄：

- **硬闸**（不过就换升级模型重读，仍不过则标「需复核」）：票面合计缺失；小计 − 折扣 + VAT
  ≠ 合计；负数；买卖同税号；合计小于单行；VAT 不是 7%；13 位税号 MOD-11 不过；明细和大于小计
  （含税明细的小票豁免）。
- **软标注**（只标「需复核」）：单据日期晚于今天 1 天以上或早于 5 年。

另加一条旧线上在入账确认时才做的（`services/erp/invoice_amounts.py:28-58`，不平返回 422）：
**按识别出的明细算出的合计必须等于票面合计**。这里提前到识别阶段，因为新流程的卡片
直接给出合计（09-26 真机：20% 折扣被丢、只有金额没有单价，这两次都该在这里被拦下）。
"""
from datetime import date, timedelta

from pearnly_core.tax_documents import rules as tax_rules
# 票面核对的纯规则 2026-09-27 起在共用层（Cowork 同用）；这里只留 ERP 的提示语、重读策略与提示词片段。
from pearnly_core.tax_documents.amounts import (  # noqa: F401 - ERP 各处按 checks.<名字> 调用
    TOLERANCE, VAT_RATE, computed_issues, is_discount_line, money, printed_issues, valid_tax_id,
    vat_mode, withholding_rate,
)

MAX_BACKDATE_YEARS = 5
MAX_FUTURE_DAYS = 1

# 多页 PDF 里有页几次都读不出：标在前一张单据上，只标注、不重读（ERP 旧版静默跳过那页）。
PAGE_UNREAD = "page_unread"

# 代码 → 原因（英文）：只写进复核提示词告诉模型要核对什么（`policy`），不给用户看 ——
# 用户 2026-10-02：录入侧不出识别后的错误提示，只靠提升识别质量把结果显示出来。
MESSAGES = {
    PAGE_UNREAD: "A page of this file could not be read: a document on it may be missing. Check the original",
    "total_missing": "The total printed on the document was not found",
    "printed_math": "Subtotal, discount and VAT do not add up to the printed total",
    "computed_total": "The items do not add up to the printed total",
    "computed_vat": "The VAT worked out from the items does not match the printed VAT",
    "negative": "A negative amount was read",
    "same_tax_id": "Seller and buyer have the same tax ID",
    "tax_id_checksum": "A tax ID failed its check digit",
    "vat_ratio": "VAT is not 7% of the amount before VAT",
    "line_sum": "The items add up to more than the subtotal",
    "total_below_line": "The total is less than one of the items",
    "date_future": "The document date is in the future",
    "date_old": "The document date is more than 5 years old",
    # 共用决策表（`pearnly_core.tax_documents.rules`，ADR-0012）要人看的原因：只标注、不换模型重读。
    tax_rules.REVIEW_UNKNOWN_TYPE: "The document type was not recognised, so input VAT is not claimed",
    tax_rules.REVIEW_TAX_INVOICE_WITHOUT_VAT: "This tax invoice shows no VAT, so input VAT is not claimed",
    tax_rules.REVIEW_TAX_INVOICE_WITHOUT_BUYER: "This tax invoice does not name the buyer, so input VAT is not claimed",
    tax_rules.REVIEW_CREDIT_NOTE: "This is a credit note: check the amounts and the original invoice",
}
SOFT = frozenset({PAGE_UNREAD, "date_future", "date_old", tax_rules.REVIEW_UNKNOWN_TYPE, tax_rules.REVIEW_TAX_INVOICE_WITHOUT_VAT,
                  tax_rules.REVIEW_TAX_INVOICE_WITHOUT_BUYER, tax_rules.REVIEW_CREDIT_NOTE})


# 提示词片段（采购、销售共用）：语料实测 7-Eleven 小票把门店 / POS 码当成单号；SM 小票的 3% 刷卡费没算进明细。
POS_RULES = ("On a shop or POS slip doc_no is the receipt running number (printed after R#, Rcpt, No. or เลขที่),\n"
             "never the branch, store, POS, terminal or cashier code. Discount and coupon rows (ส่วนลด ...) are not\n"
             "items: add them to discount_total. A service charge or card fee printed below the items\n"
             "(Service Charge 10%, Credit Card 3%) is an extra line with its printed amount.")


def date_issues(value, today=None) -> list:
    try:
        year, month, day = (int(part) for part in str(value or "")[:10].split("-"))
        parsed = date(year, month, day)
    except (ValueError, TypeError):
        return []
    anchor = today or date.today()
    if parsed > anchor + timedelta(days=MAX_FUTURE_DAYS):
        return ["date_future"]
    if parsed < anchor - timedelta(days=365 * MAX_BACKDATE_YEARS):
        return ["date_old"]
    return []


def hard(issues) -> list:
    return [code for code in issues if code not in SOFT]


# 值得再读一遍的只有「金额 / 结构对不上」。税号校验位不过：语料实测 3.8 重读后多半还是错，改由 OCR 原文
# 按校验位核对（`purchasing.recognition._anchor`）；贷项通知单本来就不录；票种 / 进项原因、日期软标注只标红。
NOT_REREAD = frozenset({"tax_id_checksum", "negative", "same_tax_id"})


def reread(issues) -> list:
    """值得让模型再读的问题（ADR-013）。"""
    return [code for code in hard(issues) if code not in NOT_REREAD]


def issued_by(result, own_tax, own_branch=None) -> bool:
    """这张单是不是本公司开出去的：卖方税号 = 本公司税号，买方不是本公司（10-24 验收：本公司 00001 分店开给第三方的税票、
    公司本身是 00000，按分店比会漏过）。买方也是本公司的是分店之间调拨，照常是进项。``own_branch`` 留着兼容，不再参与判断。"""
    digits = lambda value: "".join(ch for ch in str(value or "") if ch.isdigit())  # noqa: E731
    if len(own_tax or "") != 13 or digits(result.get("supplier_tax_id")) != own_tax:
        return False
    return digits(result.get("buyer_tax_id")) != own_tax
