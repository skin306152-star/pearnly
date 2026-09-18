# -*- coding: utf-8 -*-
"""DMS 订车单的自动编号，以及"这个号是不是已经被占了"的扫描。

为什么不能直接用 DMS 的 autonum：它的计数器**按分店分段**，跟全局唯一约束会失步 ——
它回的"下一个号"可能已被别的分店/历史单占用，提交就报「单号重复」。
所以要能顺号重试、并先扫一遍现有号。

照搬老产品 `services/erp/mrerp_dms_docno.py`，数字没改。
"""

from __future__ import annotations

import logging
import re
from typing import Callable, Dict

logger = logging.getLogger(__name__)

BOOKING_DOCNO_MAX_TRIES = 25
BOOKING_LIST_PAGE_SIZE = 100
BOOKING_LIST_MAX_PAGES = 100


def is_duplicate_docno_error(body: str) -> bool:
    """DMS 单号重复的报错（`err::"เลขที่ใบจอง" ซ้ำ`）。`ซ้ำ` = 泰语"重复"。"""
    return body.startswith("err::") and "ซ้ำ" in body


def bump_docno(docno: str) -> str:
    """末尾连续数字段 +1，并保持位宽：BK2606000001 → BK2606000002。"""
    index = len(docno)
    while index > 0 and docno[index - 1].isdigit():
        index -= 1
    head, tail = docno[:index], docno[index:]
    if not tail:
        return docno + "1"
    return head + str(int(tail) + 1).zfill(len(tail))


def listing_docnos(body: str, prefix: str, digits: int) -> set[str]:
    """从订车列表 HTML 里挑出与当前编号系列**完全同形**的单号。"""
    if not prefix or digits <= 0:
        return set()
    pattern = rf"(?<![A-Z0-9]){re.escape(prefix)}\d{{{digits}}}(?!\d)"
    return set(re.findall(pattern, body or ""))


def next_unoccupied_docno(
    candidate: str,
    digits: int,
    post_text: Callable[[str, Dict[str, str]], str],
) -> str:
    """从订车列表里现有的最大流水号之后开始，绕过失步的 autonum。

    列表取数失败就**退回候选号**（不抛错）：这一层是"尽量少撞"，撞了还有顺号重试兜底，
    不该因为在列表上翻页失败就把整件事挂掉。
    """
    if digits <= 0 or len(candidate) <= digits:
        return candidate
    prefix, suffix = candidate[:-digits], candidate[-digits:]
    if not suffix.isdigit():
        return candidate

    highest = int(suffix) - 1
    seen: set[str] = set()
    try:
        for page in range(1, BOOKING_LIST_MAX_PAGES + 1):
            body = post_text(
                "drfcbc/component/showdata.php",
                {
                    "sdtamt": str(BOOKING_LIST_PAGE_SIZE),
                    "sdtpage": str(page),
                    "sd": prefix,
                    "ftd": "1",
                    "selcolsort": "1",
                    "selcolsorttype": "2",
                },
            )
            page_docnos = listing_docnos(body, prefix, digits)
            new_docnos = page_docnos - seen
            if not new_docnos:
                break
            seen.update(new_docnos)
            highest = max(highest, *(int(value[-digits:]) for value in new_docnos))
            if len(page_docnos) < BOOKING_LIST_PAGE_SIZE:
                break
    except Exception as exc:
        logger.warning("[dms] 订车列表取数失败，先用 autonum 候选号：%s", exc)
        return candidate

    next_number = max(int(suffix), highest + 1)
    return f"{prefix}{str(next_number).zfill(digits)}"
