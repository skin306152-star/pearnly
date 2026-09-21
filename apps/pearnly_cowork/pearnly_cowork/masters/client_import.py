# -*- coding: utf-8 -*-
"""客户名录导入的归一（纯逻辑）。

来源：ec2e5c957e6b ``services/workspace/client_import.py``（160 行）与它用到的
``services/purchase/field_clean.py`` 里两个清洗函数。旧侧的路由只做"上传 → 薄层编排"，
真正决定"哪些列是客户名、哪些行要导、导出来的字段长什么样"的是这些纯函数 —— 本模块照搬。

**表格解析（xlsx/csv → 表头 + 行）由调用方给**：旧侧依赖 ``summary_import.parse_table``
那个器件；这里接收 ``headers`` 与 ``rows``（每行 ``{index, cells, is_summary}``），因此可
完全离线差分。

保留的口径（部署版逐条）：

· 表头同义词泰/英/中三语，**子串**匹配（`"client name"`/`"ชื่อลูกค้า"`/`"客户名称"`…），
  同一字段多列命中取**靠前**那列；
· **认不出客户名列就不导**（``name_column_found=False``，诚实报"表头认不出来"，不瞎猜）；
  其余列（税号/分店/电话/地址）缺了整批留空，不算失败；
· 汇总行（``is_summary``）跳过；超过 ``MAX_ROWS`` 截断并置 ``truncated``；
· 泰国税号 = **恰好 13 位数字**（剥掉所有非数字；给文字也取数字），否则判非法返回空串；
· 分店号 = 最多 5 位数字 → 左补零到 5 位；**空或超 5 位返回空串**（未提供必须留空，
  不能归一成总店 ``00000`` —— 连锁同税号多分店靠它区分）；
· 行级结构校验：缺客户名 → ``client_import.err_missing_name``；税号给了但不合法 →
  ``client_import.err_bad_tax_id``（税号可选，空值不算错）。
"""

import re
from typing import Any, Dict, List, Optional

# 与旧侧 summary_import 同口径的上限（异常输入判错，不吞内存）。
MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 500

ERR_MISSING_NAME = "client_import.err_missing_name"
ERR_BAD_TAX_ID = "client_import.err_bad_tax_id"

# 表头同义词（泰/英/中）。子串匹配；字段命中顺序即下表的顺序。
HEADER_SYNONYMS: Dict[str, List[str]] = {
    "name": ["client name", "company name", "customer name", "name", "ชื่อลูกค้า",
        "ชื่อบริษัท", "ชื่อกิจการ", "ชื่อ", "客户名称", "客户名", "公司名称", "公司名", "名称"],
    "tax_id": ["tax id", "tax_id", "taxid", "vat id", "vat no", "เลขประจำตัวผู้เสียภาษี",
        "เลขที่ผู้เสียภาษี", "เลขผู้เสียภาษี", "税务登记号", "纳税人识别号", "税号"],
    "branch": ["branch no", "branch", "สาขา", "分公司", "分支机构"],
    "phone": ["telephone", "phone", "tel", "mobile", "โทรศัพท์", "เบอร์โทรศัพท์", "เบอร์โทร",
        "联系电话", "电话", "手机"],
    "address": ["address", "ที่อยู่", "地址"],
}
IMPORT_FIELDS = ("name", "tax_id", "branch", "phone", "address")


def clean_tax_id(raw: Any) -> str:
    """泰国税号：剥掉所有非数字后**恰好 13 位**才算合法，否则空串。"""
    digits = re.sub(r"\D", "", str(raw or ""))
    return digits if len(digits) == 13 else ""


def clean_branch_no(raw: Any) -> str:
    """分店号：最多 5 位数字，左补零；空或超 5 位 → 空串（未提供不是总店）。"""
    digits = re.sub(r"\D", "", str(raw or ""))
    if not digits or len(digits) > 5:
        return ""
    return digits.zfill(5)


def match_column(header_cell: Any) -> Optional[str]:
    """单个表头格 → 字段名；都不命中返回 None。"""
    text = str(header_cell or "").strip().lower()
    if not text:
        return None
    for field, synonyms in HEADER_SYNONYMS.items():
        for synonym in synonyms:
            if synonym in text:
                return field
    return None


def match_headers(headers: List[Any]) -> Dict[str, int]:
    """表头行 → ``{字段: 列下标}``；同一字段多列命中取靠前那列。"""
    matched: Dict[str, int] = {}
    for index, cell in enumerate(headers or []):
        field = match_column(cell)
        if field and field not in matched:
            matched[field] = index
    return matched


def cell_at(cells: List[Any], index: Optional[int]) -> str:
    """取某列的值（去空白）；列不存在或越界就是空串。"""
    if index is None or index >= len(cells):
        return ""
    return str(cells[index] or "").strip()


def structural_error(name: Any, tax_id: Any) -> Optional[str]:
    """行级最基础的结构校验：缺名 → 错误码；税号给了但不合法 → 错误码。"""
    if not str(name or "").strip():
        return ERR_MISSING_NAME
    if str(tax_id or "").strip() and not clean_tax_id(tax_id):
        return ERR_BAD_TAX_ID
    return None


def import_rows(headers: List[Any], table_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """表头 + 解析好的行 → 导入预览（与部署版同形）。

    ``table_rows`` 每行形如 ``{"index": int, "cells": [...], "is_summary": bool}``
    （旧侧 ``parse_table`` 的输出形状）。认不出客户名列时返回空结果 + ``matched``，
    但 ``name_column_found=False``。
    """
    headers = list(headers or [])
    empty: Dict[str, Any] = {"headers": headers, "matched": {}, "rows": [], "row_count": 0,
        "truncated": False, "name_column_found": False}
    if not headers:
        return empty
    matched = match_headers(headers)
    if "name" not in matched:
        return {**empty, "matched": matched}

    raw_rows = [row for row in (table_rows or []) if not row.get("is_summary")]
    truncated = bool(len(raw_rows) > MAX_ROWS)
    rows: List[Dict[str, Any]] = []
    for row in raw_rows[:MAX_ROWS]:
        cells = row.get("cells") or []
        rows.append({"row_index": row.get("index", len(rows)),
            **{field: cell_at(cells, matched.get(field)) for field in IMPORT_FIELDS}})
    return {"headers": headers, "matched": matched, "rows": rows, "row_count": len(rows),
        "truncated": truncated, "name_column_found": True}
