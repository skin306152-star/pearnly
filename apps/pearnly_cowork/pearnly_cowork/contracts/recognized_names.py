"""识别出的买卖方名称与品名的统一口径（纯判定，不依赖框架、不做 IO）。

2026-10-02 Big C 27 页样本：同一税号的卖方读出 8 种写法（ไอซ์ 读成 ไลซ์ / ไลจ์、漏 ร），品名被收货章盖住时模型
拿品项编号「4」、单位「ถุง」、章里的邮编「10500」当品名。规则按类，不针对任何公司或商品：

- **名称跟税号走**：税号（13 位且校验位对）是法定唯一身份，同一税号只该有一个名称。这一批里写法不一致时取最多的
  那种；人改过之后按税号记住，以后同一税号直接用。
- **品名不能是编号或单位**：只有数字 / 符号、或者只是一个计量单位的「品名」不是品名，当成看不清（清空，不编）。

只统一值，不加提示、不让人选（用户 2026-10-02）。
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from pearnly_core.tax_documents.amounts import valid_tax_id

PARTIES = ("seller", "buyer")
# 单独出现就不是品名的计量单位（泰文常见单位 + 英文缩写）。只做整串比对：「น้ำแข็งหลอด」里的「หลอด」不算。
UNIT_WORDS = frozenset({
    "ถุง", "ชิ้น", "กล่อง", "ขวด", "แพ็ค", "แพ็ก", "แพค", "ลัง", "อัน", "ตัว", "ห่อ", "ถัง", "กระป๋อง", "ซอง",
    "ใบ", "เล่ม", "ชุด", "คู่", "ม้วน", "แผ่น", "หลอด", "กิโลกรัม", "กก", "กรัม", "ลิตร", "เมตร", "โหล", "แกลลอน",
    "ถาด", "กระสอบ", "หีบ", "ด้าม", "ก้อน", "เส้น", "ลูก", "หน่วย",
    "pcs", "pc", "ea", "each", "unit", "units", "box", "bag", "bottle", "pack", "set", "kg", "g", "l", "m",
})
_SPACES = re.compile(r"\s+")
# 名称比对时不算区别的：空白与常见标点（「บมจ.บิ๊กซี」与「บมจ บิ๊กซี」是同一种写法）。
_NAME_NOISE = re.compile(r"[\s.,()\[\]\-_/\\'\"&*#:;]+")


def tax_key(value) -> str:
    """能当身份用的税号：13 位且校验位对；掉了开头 0 的 12 位补上后校验位对也算。其余回空。"""
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 12 and valid_tax_id("0" + digits):
        return "0" + digits
    return digits if len(digits) == 13 and valid_tax_id(digits) else ""


def clean_name(value) -> str:
    return _SPACES.sub(" ", unicodedata.normalize("NFC", str(value or ""))).strip()[:200]


def name_key(value) -> str:
    """同一种写法的比对键：大小写、空白、标点不算区别；泰文字母与声调原样（ไอซ์ ≠ ไลซ์）。"""
    return _NAME_NOISE.sub("", clean_name(value).lower())


def plan_batch(records) -> dict:
    """一批识别结果里，同一税号的名称写法不一致的那些 → ``{(party, tax): {"name", "variants"}}``。

    ``records``：每张票的主页面字段（按入库顺序）。``name`` 取出现最多的写法（一样多取先出现的），同一写法里
    标点空格不同的取最常见的那种原文；``variants`` 是按出现次数排的各种写法（排查用）。
    """
    tally = {}
    for fields in records or ():
        if not isinstance(fields, dict):
            continue
        for party in PARTIES:
            tax, name = tax_key(fields.get(f"{party}_tax")), clean_name(fields.get(f"{party}_name"))
            key = name_key(name)
            if not tax or not key:
                continue
            entry = tally.setdefault((party, tax), {"spellings": {}})
            spelling = entry["spellings"].setdefault(key, {"count": 0, "order": len(entry["spellings"]), "raw": {}})
            spelling["count"] += 1
            spelling["raw"][name] = spelling["raw"].get(name, 0) + 1
    plan = {}
    for target, entry in tally.items():
        ranked = sorted(entry["spellings"].values(), key=lambda item: (-item["count"], item["order"]))
        shown = [max(item["raw"].items(), key=lambda raw: raw[1])[0] for item in ranked]
        if len(ranked) == 1 and len(ranked[0]["raw"]) == 1:
            continue
        plan[target] = {"name": shown[0], "variants": shown}
    return plan


def placeholder_item_name(value) -> bool:
    """这个「品名」其实不是品名：没有字母（只有数字 / 符号，例如品项编号、章里的邮编）、或者只是一个计量单位。"""
    text = clean_name(value)
    if not text:
        return False
    if not any(char.isalpha() for char in text):
        return True
    return text.lower().rstrip(".") in UNIT_WORDS


def price_key(value) -> str:
    """单价比对键：去掉千分位、定到两位小数；认不出回空。"""
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return ""
    return str(amount.quantize(Decimal("0.01"))) if amount.is_finite() and amount > 0 else ""


__all__ = ["PARTIES", "UNIT_WORDS", "clean_name", "name_key", "placeholder_item_name", "plan_batch", "price_key",
           "tax_key"]
