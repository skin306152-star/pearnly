"""MR.ERP 导入后的回读判定：列表页能不能用、列表里有没有这张单。纯函数，不做 IO。

来源（MR.ERP 源码只读核实，2026-09-28）：
  · `{aptran,artran,arse}/allview.php` 先按 `?idmenu=N` 把菜单写进会话（`component/php/sessionmenu.php:3-8`），
    这个菜单当前年度没关联日记账时整页只显示「กรุณากำหนดการเชื่อมโยง "สมุดรายวัน"」（`allview.php:109`），
    缺参考科目表 / 商品设置时是另两句「กรุณาป้อนข้อมูล …」；
  · `component/showdata.php` 只认会话里的菜单，POST `searchdataval` / `showdata_numrows` / `showdata_pages`，
    单号按 `LIKE %值%` 模糊查（`:25`、`:56`），每行一个 `<p>`，第一格是单号、第二格日期、第三格名称、第四格金额；
    查不到回 `<p id="nodata">`；
  · 导入写进去的单号 = 菜单前缀（`tbl_menu.codes`，可能为空、公司可改）+ 我方单号（`importpc.php:798`）。
所以判定是：列表里**以我方单号结尾**的那一行才算这张单，前缀从回读结果取，记下实际单号。
"""
from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass
from typing import List, Optional

NOT_LINKED_MARK = "กรุณากำหนดการเชื่อมโยง"
SETUP_MISSING_MARK = "กรุณาป้อนข้อมูล"
PAGE_ROWS = 30  # 与 MR.ERP 前端一页的行数相同

_ROW = re.compile(r"<p\b(?![^>]*\bid=[\"']nodata[\"'])[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)
_SPAN = re.compile(r"<span\b[^>]*>(.*?)</span>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_PREFIX = re.compile(r"^[A-Za-z0-9\-/.]{0,12}$")


def _text(fragment: str) -> str:
    return " ".join(_html.unescape(_TAG.sub(" ", fragment or "")).split())


@dataclass(frozen=True)
class ListedRow:
    docno: str
    date: str
    name: str
    amount: str


def listing_problem(page: str) -> Optional[str]:
    """allview 页不能当回读依据的原因：没关联日记账 / 未完成设置；能用返回 None。"""
    text = page or ""
    if NOT_LINKED_MARK in text:
        return "not_linked"
    if SETUP_MISSING_MARK in text:
        return "setup_missing"
    return None


def parse_rows(page: str) -> List[ListedRow]:
    """showdata 片段 → 每张单的前四格。行里嵌的 URA 小表不算单独的行（只取外层 `<p>` 的前四格）。"""
    rows = []
    for body in _ROW.findall(page or ""):
        cells = [_text(cell) for cell in _SPAN.findall(body)]
        if len(cells) >= 4 and cells[0]:
            rows.append(ListedRow(*cells[:4]))
    return rows


def find(rows: List[ListedRow], invoice_no: str) -> Optional[ListedRow]:
    """以我方单号结尾、前面只多一个菜单前缀的那一行；没有或不止一行（认不准）都返回 None。"""
    ours = str(invoice_no or "").strip()
    if not ours:
        return None
    hits = [row for row in rows if row.docno.endswith(ours) and _PREFIX.match(row.docno[:-len(ours)])]
    return hits[0] if len(hits) == 1 else None


__all__ = ["ListedRow", "NOT_LINKED_MARK", "PAGE_ROWS", "find", "listing_problem", "parse_rows"]
