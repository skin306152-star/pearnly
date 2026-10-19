"""一张票原件的 PDF 文字层（数字会计员第 3 期第 2 条「原文对得上」）：库里不存，自动推前现抽一次，零费用（不调识别、不调模型）。

只给已核过这张票读取范围的服务端调用（`push.application.assess`）；不是 PDF、读不出、页码对不上都回 None（交给调用方按「找不到
原文」处理，归 C）。
"""
import json

import frappe

from pearnly_cowork.files import service as originals
from pearnly_cowork.files.access import SOURCE
from pearnly_cowork.history.pdf_text import page_texts

# 一张票最多抽这么多页的文字（发票一般一两页；挂了长附件的也只看前面这些页）
PAGES_MAX = 20


def _pages(doc) -> list:
    try:
        indices = json.loads(doc.get("source_page_indices") or "[]")
    except (TypeError, ValueError):
        return []
    return sorted({index for index in indices if type(index) is int and index >= 1})[:PAGES_MAX]


def pages(doc):
    """票 ``doc``（`Cowork OCR History`）所在原件这几页**各自**的文字层（按页序的字符串表）；带隐形文字（``3 Tr``，扫描件上
    叠的识别文字层）的页给空串，按图片算；pypdf 抽出的某页 ToUnicode 坏了（满是控制字符）时那一页改用 pymupdf 抽
    （`history.pdf_text.page_texts`，回放同一条）。不是 PDF、读不出回 None。"""
    if not doc.get("source_file"):
        return None
    source = frappe.get_doc(SOURCE, doc.source_file)
    if source.kind != "pdf":
        return None
    indices = _pages(doc)
    if not indices:
        return None
    try:
        found = page_texts(originals.read_bytes(source), indices)
    except Exception:  # noqa: BLE001 — 读不出就当没有原文（归 C），不挡判定
        frappe.clear_messages()
        return None
    return found


def text(doc, found=None):
    """票 ``doc`` 所在原件这几页的文字层，连成一段；没有为 None。``found`` 是已取好的 :func:`pages`（省一次读原件）。"""
    found = pages(doc) if found is None else found
    joined = "\n".join(found or ()).strip()
    return joined or None


__all__ = ["PAGES_MAX", "pages", "text"]
