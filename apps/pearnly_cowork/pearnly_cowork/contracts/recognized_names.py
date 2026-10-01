"""识别出的买卖方名称与品名的统一口径（纯判定，不依赖框架、不做 IO）。

2026-10-02 Big C 27 页样本：同一税号的卖方读出 9 种写法（ไอซ์ 读成 ไลซ์ / ไลจ์、漏 ร、ซ/ช 混读），品名被收货章盖住时
模型拿品项编号「4」、单位「ถุง」、章里的邮编「10500」当品名。规则按类，不针对任何公司或商品：

- **人改正过的名称跟税号走**：税号（13 位且校验位对）是法定唯一身份。人在复核页改正过某个税号的名称，以后同一税号
  读出的**相近写法**（读错几个字母）换成人定的那个；写法差得远的（海关与报关行、泰文名与英文名）不动——
  那多半是税号读错或套用了别家的，换掉反而把错藏起来。一批里不按多数自动统一：金标 234 份里多数写法有一半是错的。
- **品名不能是编号或单位**：只有数字、或者只是一个泰文计量单位的「品名」不是品名，当成看不清（清空，不编）。
  带分隔符的货号（740462-0010）、日期、英文缩写（PCS 在运费单上是一项收费）不动。

只改值，不加提示、不让人选（用户 2026-10-02）。
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from decimal import Decimal, InvalidOperation

from pearnly_core.tax_documents.amounts import valid_tax_id

PARTIES = ("seller", "buyer")
# 人定的名称只替换相近写法：Big C 各种读错写法与正确写法相似度 0.83–0.96；差得远的（海关与报关行 0.05、
# 泰文与英文名 0、同一机构加了英文全称 0.49）不换。
SIMILAR = 0.8
# 单独出现就不是品名的泰文计量单位。只做整串比对：「น้ำแข็งหลอด」里的「หลอด」不算。英文缩写不放（PCS 可能是一项收费）。
UNIT_WORDS = frozenset({
    "ถุง", "ชิ้น", "กล่อง", "ขวด", "แพ็ค", "แพ็ก", "แพค", "ลัง", "อัน", "ตัว", "ห่อ", "ถัง", "กระป๋อง", "ซอง",
    "ใบ", "เล่ม", "ชุด", "คู่", "ม้วน", "แผ่น", "หลอด", "กิโลกรัม", "กก", "กรัม", "ลิตร", "เมตร", "โหล", "แกลลอน",
    "ถาด", "กระสอบ", "หีบ", "ด้าม", "ก้อน", "เส้น", "ลูก", "หน่วย",
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


def similar(first, second) -> bool:
    """两个名称是不是同一个名称读错了几个字（相似度够高）；完全相同也算。"""
    a, b = name_key(first), name_key(second)
    return bool(a and b) and (a == b or SequenceMatcher(None, a, b).ratio() >= SIMILAR)


def placeholder_item_name(value) -> bool:
    """这个「品名」其实不是品名：只有数字（品项编号、章里的邮编）、没有字母也没有数字（符号）、或者只是一个泰文计量单位。"""
    text = clean_name(value)
    if not text:
        return False
    compact = text.replace(" ", "")
    if compact.isdigit() or not any(char.isalnum() for char in compact):
        return True
    return compact.rstrip(".") in UNIT_WORDS


def price_key(value) -> str:
    """单价比对键：去掉千分位、定到两位小数；认不出回空。"""
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return ""
    return str(amount.quantize(Decimal("0.01"))) if amount.is_finite() and amount > 0 else ""


__all__ = ["PARTIES", "SIMILAR", "UNIT_WORDS", "clean_name", "name_key", "placeholder_item_name", "price_key",
           "similar", "tax_key"]
