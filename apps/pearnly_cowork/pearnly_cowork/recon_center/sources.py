"""收入对账与销项税核查的文件 → 解析器要的“已抽取内容”（照旧线上 ec2e5c95 的分派）。

旧线上 `gl_vat_parse_excel.parse_gl` / `vat_report_parser.parse_vat_report` 先按扩展名读文件，
再把抽出来的内容交给解析；新平台已迁的是后一半（`income_parse.decode`、`vat_parse.decode`），
这里补前一半：
- 总账：Excel / CSV 直读单元格；PDF 旧线上先走识别管线（general_ledger），读不出再退到文字行；
  扫描 PDF 与照片只能走识别管线。
- 税表：Excel / CSV 直读；带文字层的 PDF 先读表、再读文字行，读出不足 3 行（旧线上 `MIN_DETERMINISTIC_ROWS`）
  才走识别管线（vat_report）；扫描 PDF 与照片直接走识别管线。

识别管线（`read`）由对账后台任务注入：每份文件一个持久识别任务（`recon_center.document_ocr`），
走旧线上的企业档（Document AI 读字 + 本地拼行，算不平 / 税报才 3.8 看图抽取；2026-09-29 迁入；原来扫描件与照片一律报
“读不出来”，带文字层的总账 PDF 调模型又不在识别任务里，整单失败）。没注入（不在后台任务里）就只有免费读取。

与旧线上的差别（如实记录）：新平台只有 pypdf，没有 pdfplumber 的表格抽取，PDF 只走文字层。
"""
import frappe

from pearnly_cowork.ocr.engine import document_tier
from pearnly_cowork.reconciliation import vat_parse
from pearnly_cowork.tabular import readers

EXCEL = ("xlsx", "xls", "xlsm")
CSV = ("csv", "tsv")


def extension(filename):
    return (filename or "").lower().rsplit(".", 1)[-1]


def _lines(pages):
    return [line for page in pages for line in page.splitlines() if line.strip()]


IMAGES = ("png", "jpg", "jpeg", "webp", "tiff", "tif", "bmp", "gif")


def _pipeline(read, document_type, content, filename):
    decoded = read(document_type, content, filename) if read is not None else None
    if not decoded:
        return None
    # 企业档结果带引擎名：税表解码按旧线上口径保留没有字母数字单号的行（`vat_parse.pipeline_rows`）。
    return {"kind": "pipeline", "decoded": decoded, "engine": decoded.get("engine") or ""}


def ledger_source(content, filename, read=None):
    ext = extension(filename)
    if ext in EXCEL or ext in CSV:
        loaded = readers.load_workbook_sheets(content) if ext in EXCEL else readers.load_csv_sheets(content)
        if not loaded:
            frappe.throw(frappe._("reconciliation.gl_parse_failed"))
        return {"kind": "excel", "sheets": [rows for _name, rows in loaded]}
    if ext == "pdf":
        pages = document_tier.text_pages(content)
        model = _pipeline(read, "general_ledger", content, filename)
        if model:
            return model
        if not any(page.strip() for page in pages):
            frappe.throw(frappe._("reconciliation.gl_parse_failed"))
        return {"kind": "pdf", "pages": [{"tables": [], "text": page} for page in pages], "lines": _lines(pages)}
    if ext in IMAGES and read is not None:
        model = _pipeline(read, "general_ledger", content, filename)
        if model:
            return model
        frappe.throw(frappe._("reconciliation.gl_parse_failed"))
    frappe.throw(frappe._("reconciliation.file_type_unsupported"))


def report_source(content, filename, read=None):
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
        if any(page.strip() for page in pages):
            source = {"kind": "pdf", "pages": [{"tables": [], "text": page} for page in pages], "lines": _lines(pages)}
            parsed = vat_parse.decode(source)
            if parsed and len(parsed.get("rows") or []) >= vat_parse.MIN_DETERMINISTIC_ROWS:
                return source
            return _pipeline(read, "vat_report", content, filename) or source
        model = _pipeline(read, "vat_report", content, filename)
        if model:
            return model
        frappe.throw(frappe._("reconciliation.vat_report_unparsed"))
    if ext in IMAGES and read is not None:
        model = _pipeline(read, "vat_report", content, filename)
        if model:
            return model
        frappe.throw(frappe._("reconciliation.vat_report_unparsed"))
    frappe.throw(frappe._("reconciliation.file_type_unsupported"))
