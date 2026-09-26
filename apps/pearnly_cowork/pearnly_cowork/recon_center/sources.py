"""收入对账的两份文件 → 解析器要的“已抽取内容”（照旧线上 ec2e5c95 的分派）。

旧线上 `gl_vat_parse_excel.parse_gl` / `vat_report_parser.parse_vat_report` 先按扩展名读文件，
再把抽出来的内容交给解析；新平台已迁的是后一半（`income_parse.decode`、`vat_parse.decode`），
这里补前一半：
- 总账：Excel / CSV 直读单元格；PDF 旧线上先走识别管线（general_ledger），读不出再退到文字行。
- 税表：Excel / CSV 直读；PDF 先读表、再读文字行（`vat_parse` 内部判定），都不行就是扫描件。

与旧线上的差别（如实记录）：新平台只有 pypdf，没有 pdfplumber 的表格抽取，PDF 只走文字层；
税表的模型读取（扫描件 / 图片）还没迁（识别层只有 bank_statement / general_ledger 两种文档模型），
这类文件会明确报“读不出来”，不会存一份空结果。
"""
import frappe

from pearnly_cowork.ocr.engine import document_tier
from pearnly_cowork.tabular import readers

EXCEL = ("xlsx", "xls", "xlsm")
CSV = ("csv", "tsv")


def extension(filename):
    return (filename or "").lower().rsplit(".", 1)[-1]


def _lines(pages):
    return [line for page in pages for line in page.splitlines() if line.strip()]


def _model_ledger(text):
    """总账 PDF 的识别管线读（旧线上的首选路）。站点没配识别就返回 None，退回文字行。"""
    from pearnly_cowork.ocr.engine.errors import ContentError
    from pearnly_cowork.ocr.providers.client import ProviderFailure
    from pearnly_cowork.pearnly_cowork.doctype.cowork_bank_reconciliation.cowork_bank_reconciliation import (
        configured_model_call,
    )

    try:
        call, model, escalated = configured_model_call()
    except Exception:
        return None
    try:
        _document, legacy, _warnings = document_tier.read_document("general_ledger", text, call,
            model=model, escalated_model=escalated)
    except (ContentError, ProviderFailure) as error:
        frappe.log_error(title="cowork income ledger model", message=f"{type(error).__name__}: {error}")
        return None
    return legacy


def ledger_source(content, filename):
    ext = extension(filename)
    if ext in EXCEL or ext in CSV:
        loaded = readers.load_workbook_sheets(content) if ext in EXCEL else readers.load_csv_sheets(content)
        if not loaded:
            frappe.throw(frappe._("reconciliation.gl_parse_failed"))
        return {"kind": "excel", "sheets": [rows for _name, rows in loaded]}
    if ext == "pdf":
        pages = document_tier.text_pages(content)
        if not any(page.strip() for page in pages):
            frappe.throw(frappe._("reconciliation.gl_parse_failed"))
        decoded = _model_ledger("\n".join(pages))
        if decoded:
            return {"kind": "pipeline", "decoded": decoded}
        return {"kind": "pdf", "pages": [{"tables": [], "text": page} for page in pages], "lines": _lines(pages)}
    frappe.throw(frappe._("reconciliation.file_type_unsupported"))


def report_source(content, filename):
    ext = extension(filename)
    if ext in EXCEL:
        sheets = readers.load_workbook_sheets(content)
        if not sheets or not sheets[0][1]:
            frappe.throw(frappe._("reconciliation.vat_report_unparsed"))
        return {"kind": "excel", "cells": sheets[0][1]}
    if ext in CSV:
        for encoding in ("utf-8-sig", "cp874", "gbk", "latin-1"):
            try:
                return {"kind": "csv", "text": content.decode(encoding), "delimiter": "\t" if ext == "tsv" else ","}
            except UnicodeDecodeError:
                continue
    if ext == "pdf":
        pages = document_tier.text_pages(content)
        if not any(page.strip() for page in pages):
            frappe.throw(frappe._("reconciliation.vat_report_unparsed"))
        return {"kind": "pdf", "pages": [{"tables": [], "text": page} for page in pages], "lines": _lines(pages)}
    frappe.throw(frappe._("reconciliation.file_type_unsupported"))
