# -*- coding: utf-8 -*-
"""DMS 订车单自动编号与"已占用号"扫描。

## MR.ERP 原生取号协议（2026-09-13 只读实测 · drfcbc 模块）

| 端点 | 返回 |
|---|---|
| `component/php/autonum.php` | `[idautonum, enabled, prefix, digits, format]` —— 当前实例 `[16, 1, "BK", 6, 1]`。`prefix` 来自**配置**（不是写死的 BK），`digits` 是流水位宽，启用位不是 `"1"` 表示没开自动编号 |
| `component/php/autonumdetail.php` | `[idautonumdetail, nextautonum, lastautonum]` —— 当前分店 `[27, 1, "BK000002609000001"]` |

DMS 自带的 `mtform.js` 把三个值写进隐藏字段 `idatndt` / `natn`，并把 `lastautonum`
拆成「前缀栏 + 数字主体栏」（`txtprefixautonum` + `txtdocno`），保存时按这两栏分别提交。

## 为什么还要自己扫一遍"未占号"

自动编号器**按分店分段**，与全局唯一约束会失步 —— 它回的"下一个号"可能已被别的分店
或历史单占用，提交就报「单号重复」（`err::"เลขที่ใบจอง" ซ้ำ`）。所以要能顺号重试，
并先扫一遍现有号。

⚠️ **任一必需字段缺失/无效就抛 `ValueError`，由调用方 fail closed** ——
绝不拿空串兜底，也绝不自行拼一个 `booking_no`。

照搬老产品 `services/erp/mrerp_dms_docno.py`（线上基准 `501589f0`），逻辑未改。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

BOOKING_DOCNO_MAX_TRIES = 25
BOOKING_LIST_PAGE_SIZE = 100
BOOKING_LIST_MAX_PAGES = 100

# DMS 自带的隐藏字段名（mtform.js 写的）—— 提交时要带着，计数器才不会脱节。
HIDDEN_IDAUTNUMDETAIL = "idatndt"
HIDDEN_NEXTAUTONUM = "natn"


@dataclass(frozen=True)
class DMSAutonumDetail:
    idautonumdetail: Any
    nextautonum: Any
    lastautonum: Any


@dataclass(frozen=True)
class DMSBookingAutonumState:
    """一次建单要用的原生取号状态（前缀 / 完整号 / 位宽 / 两个隐藏字段值）。"""

    prefix: str
    docno: str
    digits: int
    idautonumdetail: str
    nextautonum: int

    @property
    def body(self) -> str:
        """数字主体（＝完整号去掉前缀），提交到 `txtdocno` 那一栏。"""
        return self.docno[len(self.prefix) :]

    @property
    def tail_serial(self) -> str:
        return self.docno[-self.digits :]

    def with_docno(self, docno: str, *, digits: Optional[int] = None) -> "DMSBookingAutonumState":
        """换一个号，并把 `natn` **同量前移** —— 数字主体与计数器绝不脱节。"""
        width = self.digits if digits is None else digits
        old_serial = int(self.tail_serial)
        new_serial = int(docno[-width:])
        nextautonum = self.nextautonum + (new_serial - old_serial)
        if nextautonum < 0:
            raise ValueError("autonum nextautonum would become negative")
        return replace(self, docno=docno, digits=width, nextautonum=nextautonum)


def parse_autonum_detail(detail_body: str) -> DMSAutonumDetail:
    """`autonumdetail.php` 响应 → 原始三元组；形状不对即抛 `ValueError`。"""
    detail = json.loads(detail_body)
    if not isinstance(detail, list) or len(detail) < 3:
        raise ValueError("autonumdetail is not the native 3-field tuple")
    return DMSAutonumDetail(idautonumdetail=detail[0], nextautonum=detail[1], lastautonum=detail[2])


def autonum_state(cfg_body: str, detail_body: str) -> DMSBookingAutonumState:
    """解析 `autonum.php` + `autonumdetail.php` 响应为原生取号状态。

    任一必需字段缺失/无效即抛 `ValueError`，由调用方 fail closed —— 绝不拿空串兜底，
    也绝不自行拼一个 `booking_no`。校验项：

    · `cfg[0]` 编号器 id 存在；`cfg[1]` 必须是启用位 `1`；
    · `cfg[2]` 前缀非空（任意字符，**不假定 ASCII 字母、不假定长度**）；
    · `cfg[3]` 流水位宽是正整数；
    · `detail[2]` 完整号以 `cfg[2]` 开头、余下主体非空、末 `digits` 位是数字；
    · `detail[0]` / `detail[1]`（隐藏字段 `idatndt` / `natn`）存在且有效。
    """
    cfg = json.loads(cfg_body)
    if not isinstance(cfg, list) or len(cfg) < 5:
        raise ValueError("autonum config is not the native 5-field tuple")
    if cfg[0] is None or str(cfg[0]) == "":
        raise ValueError("autonum id is missing")
    if str(cfg[1]) != "1":
        raise ValueError("autonum is disabled for this menu")
    prefix = str(cfg[2] or "")
    if not prefix:
        raise ValueError("autonum prefix is empty")
    try:
        digits = int(cfg[3])
    except (TypeError, ValueError) as exc:
        raise ValueError("autonum digit width is not an integer") from exc
    if digits <= 0:
        raise ValueError("autonum digit width must be positive")

    detail = parse_autonum_detail(detail_body)
    idautonumdetail = str(detail.idautonumdetail or "").strip()
    if not idautonumdetail:
        raise ValueError("autonumdetail idatndt is missing")
    try:
        nextautonum = int(str(detail.nextautonum).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("autonumdetail natn is not a non-negative integer") from exc
    if nextautonum < 0:
        raise ValueError("autonumdetail natn must not be negative")
    docno = str(detail.lastautonum or "").strip()
    if not docno.startswith(prefix) or len(docno) <= len(prefix):
        raise ValueError("autonumdetail number does not start with the configured prefix")
    if not docno[-digits:].isdigit():
        raise ValueError("autonumdetail number does not end with the configured digit width")
    return DMSBookingAutonumState(
        prefix=prefix,
        docno=docno,
        digits=digits,
        idautonumdetail=idautonumdetail,
        nextautonum=nextautonum,
    )


def is_duplicate_docno_error(body: str) -> bool:
    """DMS 单号重复报错（`err::"เลขที่ใบจอง" ซ้ำ`）。`ซ้ำ` = 泰语"重复"。"""
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


@dataclass(frozen=True)
class DMSBookingDocnoScan:
    """列表扫描结果：绕过跟全局唯一约束失步的 DMS 自动编号器。

    `docno` 是本次要提交的完整号，`numeric_tail` 是它末尾 `digits` 位流水，
    `delta` 是相对 DMS 候选号的流水前移量（`natn` 必须同量前移）。
    """

    docno: str
    numeric_tail: str
    delta: int


def scan_unoccupied_docno(
    candidate: str,
    digits: int,
    post_text: Callable[[str, Dict[str, str]], str],
) -> DMSBookingDocnoScan:
    """从订车列表现有最大流水号之后开始，绕过失步的 DMS 自动编号器。

    **只读**：仅调 `showdata.php` 列表。拿不到列表（瞬时故障）或候选号形状不合协议时
    原样返回候选号（`delta=0`），**绝不为了"顺号"去猜一个号**。

    `delta` 只作为 `DMSBookingAutonumState.with_docno` 的输入（它把 `natn` 前移这个
    差值）；本函数不碰计数器，也不假设 `natn` 等于候选号尾号。
    """
    if digits <= 0 or len(candidate) <= digits:
        return DMSBookingDocnoScan(candidate, candidate[-digits:] if digits > 0 else "", 0)
    prefix, suffix = candidate[:-digits], candidate[-digits:]
    if not suffix.isdigit():
        return DMSBookingDocnoScan(candidate, suffix, 0)

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
        return DMSBookingDocnoScan(candidate, suffix, 0)

    next_number = max(int(suffix), highest + 1)
    return DMSBookingDocnoScan(
        f"{prefix}{str(next_number).zfill(digits)}",
        str(next_number).zfill(digits),
        next_number - int(suffix),
    )


__all__ = [
    "BOOKING_DOCNO_MAX_TRIES",
    "DMSAutonumDetail",
    "DMSBookingAutonumState",
    "DMSBookingDocnoScan",
    "HIDDEN_IDAUTNUMDETAIL",
    "HIDDEN_NEXTAUTONUM",
    "autonum_state",
    "bump_docno",
    "is_duplicate_docno_error",
    "listing_docnos",
    "parse_autonum_detail",
    "scan_unoccupied_docno",
]
