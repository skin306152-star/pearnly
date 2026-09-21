# -*- coding: utf-8 -*-
"""商品名称的呈现规则（纯逻辑）。

来源：ec2e5c957e6b ``services/products/names.py``（33 行），逐字搬运：三语名称按
泰/英/中顺序取，空的不算，重复（忽略大小写）只保留第一次出现的那一个，展示串用 ``" / "``
连接；一个名称都没有时用调用方给的占位（默认空串）。
"""

from collections.abc import Mapping

FIELDS = ("name_th", "name_en", "name_zh")


def product_names(row: Mapping) -> list:
    """主名称在前，追加其它已填写且不重复的名称。"""
    names: list = []
    seen: set = set()
    for field in FIELDS:
        value = str(row.get(field) or "").strip()
        key = value.casefold()
        if value and key not in seen:
            names.append(value)
            seen.add(key)
    return names


def display_product_name(row: Mapping, *, missing: str = "") -> str:
    return " / ".join(product_names(row)) or missing


def product_name_object(row: Mapping) -> dict:
    return {
        "th": row.get("name_th"),
        "en": row.get("name_en"),
        "zh": row.get("name_zh"),
        "display": display_product_name(row),
    }
