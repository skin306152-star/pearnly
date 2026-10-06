# -*- coding: utf-8 -*-
"""客户档字段级 diff（纯函数）。

DMS 里已有的客户 vs 身份证识别出的新值：**只报白名单里真的变了的字段**，
让操作员逐条确认要不要覆盖。

两条铁律：

1. **incoming 缺键或值为空 = 没有这条信息**，绝不拿空值去清 DMS 现值；
2. **`people_id` 是主键身份，永不进 diff**。

归一化吸收无意义差异（首尾/连续空格、泰文数字、电话的连字符）。

照搬老产品 `services/erp/dms_customer_diff.py`。
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Dict, List

# 允许 diff 的字段：身份 + 户籍地址（地址比 id，不比显示标签）。people_id 不在内（主键）。
_DIFF_FIELDS = (
    "prefix_id",
    "name",
    "birthday_be",
    "phone",
    "house_no",
    # 楼/层/房/村 也是地址的一部分（线上 2026-09 补的）：
    # 少这四个字段，用户就看不到这几栏变了，会漏更新。
    "building",
    "floor",
    "room",
    "village",
    "moo",
    "soi",
    "road",
    "province_id",
    "district_id",
    "subdistrict_id",
    "zipcode_id",
)

_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")


def _norm(v: Any) -> str:
    """归一：泰文数字→ASCII、连续空白折成一个、首尾去空。"""
    s = str(v if v is not None else "").translate(_THAI_DIGITS)
    return " ".join(s.split())


def _norm_phone(v: Any) -> str:
    """电话按纯数字比：DMS 手工录入常带连字符/空格，"081-234-5678" ≠ "0812345678"
    是**假差异**，会让每次扫卡都弹一次"要不要更新"。全非数字（如 "-" 占位）退回普通归一。
    """
    s = _norm(v)
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits or s


def diff_customer_fields(current: Dict[str, Any], incoming: Dict[str, Any]) -> List[Dict[str, str]]:
    """比对白名单字段，返回变化项 `[{"field", "old", "new"}]`（归一后的对照值）。

    `incoming` 缺这个键、或值归一后为空 = 无信息，不产生 diff（**绝不覆盖现值**）。
    """
    out: List[Dict[str, str]] = []
    for field in _DIFF_FIELDS:
        if field not in incoming:
            continue
        norm = _norm_phone if field == "phone" else _norm
        new = norm(incoming.get(field))
        if not new:  # 空值 = 无信息
            continue
        old = norm(current.get(field))
        if new != old:
            out.append({"field": field, "old": old, "new": new})
    return out


SAME_NAME_RATIO = 0.8


def master_name_if_close(master: Any, name: str) -> str:
    """证号已命中老客户时：识别的姓名与主档足够像就用主档写法，否则保留识别结果。

    只在按完整证号找到的客户上用（身份已经由证号确定），所以这里只决定"要不要报改名"：
    漏一个符号不该让用户去改 DMS 里本来对的名字；整段不同（例如婚后改姓）照常报差异。
    查重直接命中（`intake.service.recognize_lookup`）和建档时撞上已有客户（`intake.review.build`）
    两条路用同一条规则（用户 2026-10-06 定）。
    """
    master = str(master or "").strip()
    a, b = re.sub(r"\s+", "", master), re.sub(r"\s+", "", str(name or ""))
    if a and b and difflib.SequenceMatcher(None, a, b).ratio() >= SAME_NAME_RATIO:
        return master
    return name
