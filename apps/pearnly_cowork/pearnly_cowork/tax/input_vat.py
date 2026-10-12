"""ภ.พ.30 草稿的核查：进项能不能抵、销项期间与税率。纯函数：只用标准库与共用票种规则（`pearnly_core.tax_documents`）。

只标出问题，不改草稿数字。依据（税务局原件）：
- 进项默认在票面月份抵扣；有正当理由时可延后，但不超过开票次月起 6 个月（DG 公告 VAT 第 4 号经第 76 号修订），
  延后抵扣的票上要注明「ถือเป็นภาษีซื้อในเดือนภาษี…」：开票月 M 的票，税期在 M+1…M+6 记 info 提醒注记，
  M+7 起记 error，早于 M 记 error。
- 简易税票（ใบกำกับภาษีอย่างย่อ）的进项不能抵（DG 公告 VAT 第 42 号 ข้อ 2(2)）；没有税票或税票要项不全的也不能抵（82/5）。
- ISVAT 进项行的 TAXID 是卖方税号，买方（本公司）税号只在识别记录里：能按「卖方税号 + 票号」关联到识别记录的行
  才核票种与买方；关联不到的记 info「没有识别记录」。
- 销项按纳税义务发生日查税率（`periods.vat_rate`），单行差额超过 0.02 记 warn；POS 按天汇总的简易税票行
  （摘要含「อย่างย่อ」）照常计入合计，但不逐行核税率（逐票税额加总与「净额 × 7%」会差几铢）。
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal

from pearnly_core.tax_documents import amounts as tax_amounts
from pearnly_core.tax_documents import rules as tax_rules
from pearnly_core.tax_documents.codes import CREDIT_NOTE, OTHER, SIMPLIFIED_TAX_INVOICE, TAX_INVOICE
from pearnly_cowork.tax import periods, pp30, thai_words

CARRY_MONTHS = 6
RATE_TOLERANCE = Decimal("0.02")
LEVELS = ("error", "warn", "info")
CODES = {
    "input_tax_id_missing": "warn",
    "input_tax_id_invalid": "warn",
    "input_claimed_later": "info",
    "input_over_six_months": "error",
    "input_before_invoice": "error",
    "input_abbreviated": "error",
    "input_not_tax_invoice": "error",
    "input_buyer_missing": "warn",
    "input_buyer_other": "error",
    "input_duplicate": "error",
    "input_no_document": "info",
    "output_outside_period": "error",
    "output_rate": "warn",
    "output_rate_unknown": "info",
    "output_zero_vat": "info",
    "output_tax_id_invalid": "info",
    "output_duplicate": "warn",
    "row_voided": "info",
    "row_deleted": "info",
    "row_other_period": "info",
    "row_period_missing": "warn",
    "row_unreadable": "error",
}


def issue(code: str, record=None, **detail) -> dict:
    out = {"code": code, "level": CODES[code]}
    if record is not None:
        out.update({"side": record.side, "index": record.index, "docnum": record.docnum, "refnum": record.refnum,
                    "tax_id": record.tax_id, "name": record.name, "amount": str(record.amount),
                    "vat": str(record.vat),
                    "date": record.invoice_date.isoformat() if record.invoice_date else "",
                    "period": periods.text(*record.period) if record.period else ""})
        if record.history:
            out["history"] = record.history
    if detail:
        out["detail"] = {key: str(value) for key, value in detail.items()}
    return out


def tax_id_state(value) -> str:
    """ok / missing / invalid（不是 13 位或 MOD-11 校验位不对）。读丢开头 0 的 12 位按共用规则补回再判。"""
    digits = tax_amounts.digits(tax_amounts.repaired_tax_id(str(value or "").strip()))
    if not digits:
        return "missing"
    if len(digits) != 13 or not tax_amounts.valid_tax_id(digits):
        return "invalid"
    return "ok"


def document_key(tax_id, number) -> str:
    """识别记录与 ISVAT 行关联的键：卖方税号的数字 + 票号去空白转大写（同查重口径）。"""
    return tax_amounts.digits(tax_id) + "|" + re.sub(r"\s+", "", str(number or "")).upper()


def _linked(record, documents: Mapping):
    if record.history and record.history in documents:
        return documents[record.history]
    return documents.get(document_key(record.tax_id, record.refnum)) if record.refnum else None


def _month_detail(invoice_month, target) -> dict:
    last = periods.shift(*invoice_month, CARRY_MONTHS)
    return {"invoice_month": periods.text(*invoice_month), "last_month": periods.text(*last),
            "claimed_month": periods.text(*target)}


def _purchase(record, documents, company, target, seen) -> list:
    out = []
    state = tax_id_state(record.tax_id)
    if state != "ok":
        out.append(issue("input_tax_id_" + state, record))
    invoice_month = periods.month_of(record.invoice_date)
    if invoice_month:
        months = periods.between(invoice_month, target)
        if months < 0:
            out.append(issue("input_before_invoice", record, **_month_detail(invoice_month, target)))
        elif months > CARRY_MONTHS:
            out.append(issue("input_over_six_months", record, **_month_detail(invoice_month, target)))
        elif months > 0:
            out.append(issue("input_claimed_later", record, **_month_detail(invoice_month, target)))
    abbreviated = thai_words.ABBREVIATED in record.name
    fields = _linked(record, documents)
    if fields is not None:
        kind = tax_rules.normalize(fields.get("document_type")) or OTHER
        buyer = tax_amounts.digits(tax_amounts.repaired_tax_id(str(fields.get("buyer_tax") or "").strip()))
        if kind == SIMPLIFIED_TAX_INVOICE:
            abbreviated = True
        elif kind == TAX_INVOICE:
            if not tax_rules.has_buyer_identity(buyer, fields.get("buyer_name")):
                out.append(issue("input_buyer_missing", record))
            elif company and len(buyer) == 13 and buyer != company:
                out.append(issue("input_buyer_other", record, buyer_tax_id=buyer, company_tax_id=company))
        elif kind != CREDIT_NOTE:
            out.append(issue("input_not_tax_invoice", record, document_type=kind))
    elif not abbreviated:
        out.append(issue("input_no_document", record))
    if abbreviated:
        out.append(issue("input_abbreviated", record))
    if record.refnum:
        key = document_key(record.tax_id, record.refnum)
        if key in seen:
            out.append(issue("input_duplicate", record, first_row=seen[key]))
        else:
            seen[key] = record.index
    return out


def _sale(record, target, seen) -> tuple[list, bool]:
    """(问题, 税率表里有没有这一天)。"""
    out, known = [], True
    month = periods.month_of(record.invoice_date)
    if month and month != target:
        out.append(issue("output_outside_period", record, invoice_month=periods.text(*month),
                         claimed_month=periods.text(*target)))
    if thai_words.ABBREVIATED not in record.name:
        rate = periods.vat_rate(record.invoice_date or periods.bounds(*target)[0])
        if rate is None:
            known = False
        elif record.vat == 0 and record.amount != 0:
            out.append(issue("output_zero_vat", record))
        else:
            expected = (record.amount * rate).quantize(pp30.CENT)
            if abs(expected - record.vat) > RATE_TOLERANCE:
                out.append(issue("output_rate", record, expected_vat=expected, rate=rate * 100))
    if tax_amounts.digits(record.tax_id) and tax_id_state(record.tax_id) == "invalid":
        out.append(issue("output_tax_id_invalid", record))
    key = re.sub(r"\s+", "", record.refnum or record.docnum).upper()
    if key:
        if key in seen:
            out.append(issue("output_duplicate", record, first_row=seen[key]))
        else:
            seen[key] = record.index
    return out, known


def check(selection: Mapping, documents: Mapping, company_tax_id, year: int, month: int) -> list:
    """全部问题（按进项、销项、不计入的行排）。``documents``：{识别记录名 或 document_key: 主页字段}。"""
    company = tax_amounts.digits(company_tax_id)
    target = (year, month)
    issues, seen = [], {}
    for record in selection.get("purchases") or []:
        issues.extend(_purchase(record, documents or {}, company, target, seen))
    seen, unknown = {}, False
    for record in selection.get("sales") or []:
        found, known = _sale(record, target, seen)
        issues.extend(found)
        unknown = unknown or not known
    if unknown:
        issues.append(issue("output_rate_unknown"))
    for record, reason in selection.get("excluded") or []:
        issues.append(issue(reason, record))
    return issues


def unreadable(errors) -> list:
    """回读里读不了的行（`pp30.records` 的错误）也逐行列出：草稿合计没算它们，与 Express 对不上时据此说明。"""
    return [{"code": "row_unreadable", "level": CODES["row_unreadable"], "index": error["index"],
             "docnum": error.get("docnum", "")} for error in errors or []]


def claimable(purchases, issues) -> tuple[Decimal, Decimal]:
    """剔除带 error 的进项行后的 (金额, 税额)：只作提示，草稿第 6 / 7 栏仍按 Express 记录。"""
    flagged = {item["index"] for item in issues if item.get("side") == pp30.PURCHASE and item["level"] == "error"}
    kept = [record for record in purchases if record.index not in flagged]
    return pp30.total(kept)


def counts(issues) -> dict:
    found = {}
    for item in issues:
        found[item["code"]] = found.get(item["code"], 0) + 1
    return found
