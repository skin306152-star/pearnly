# -*- coding: utf-8 -*-
"""票据字段展示清洗(P1F · 确定性纯函数 · 卡片与详情页共用同一套规则)。

目标:把明显异常值(短数字/日期片段当税号、纯金额/字段标签当卖家)判 invalid,UI 显示为空/
待补而非假值。只清洗展示值,不改 OCR 引擎、不改金额计算。raw 仍可留存供调试。

- clean_tax_id:泰国税号 = 恰好 13 位数字(剥分隔符);否则 ''(短数字/日期/含字母噪声全弃)。
- clean_seller:剥空白;纯金额/字段标签/日期/无内容/过短 → ''。
- clean_invoice_no:允许短号(与税号不同),但纯日期 → ''。
"""

from __future__ import annotations

from pearnly_cowork.contracts.push import clean_tax_id

import re
from decimal import Decimal, InvalidOperation

_AMOUNT_RE = re.compile(r"^[\d,]+(?:\.\d+)?$")
_GARBLE_RE = re.compile(r"[?�]")  # ? 与替换符 · 乱码标志
# 完整日期(DD/MM/YY[YY] 或 ISO)→ 不得当卖家/票号。单段「13」「2026」由金额规则兜住。
_FULL_DATE_RE = re.compile(r"^\d{1,4}[/\-.]\d{1,2}[/\-.]\d{2,4}$|^\d{4}-\d{2}-\d{2}$")
_ALNUM_RE = re.compile(r"[0-9A-Za-z฀-๿]")  # 至少含一个数字/拉丁/泰文字符才算有内容

# 卖家不得是这些字段标签/金额词(票面常见噪声·精确匹配·不误伤「Total Tools」这类真店名)。
_SELLER_STOPWORDS = frozenset(
    {
        "vat",
        "tax",
        "tax id",
        "tax invoice",
        "total",
        "subtotal",
        "sub total",
        "grand total",
        "net",
        "amount",
        "balance",
        "cash",
        "change",
        "qty",
        "discount",
        "ภาษีมูลค่าเพิ่ม",
        "ภาษี",
        "ภาษี 7%",
        "รวม",
        "รวมเงิน",
        "ยอดรวม",
        "ยอดสุทธิ",
        "สุทธิ",
        "เงินสด",
        "เงินทอน",
        "ส่วนลด",
        "จำนวนเงิน",
        "ใบกำกับภาษี",
        "เลขประจำตัวผู้เสียภาษี",
        "สาขา",
    }
)


def to_decimal(v) -> Decimal:
    """票面数字字段(逗号千分位 / 空串 / None)→ Decimal,认不出 → 0(不炸,由调用方判是否为
    有效金额)。intake.py 与 intake_bridge.sales_leg 此前各自维护一份同款实现,单一事实源。"""
    try:
        return Decimal(str(v).replace(",", "").strip()) if v not in (None, "") else Decimal("0")
    except (InvalidOperation, ValueError):
        return Decimal("0")




_HEAD_OFFICE = ("สำนักงานใหญ่", "สนญ", "head office", "headoffice", "hq", "总公司", "總公司", "本社")


def clean_branch_no(raw) -> str:
    """分店号 → 5 位数字串('18'/'สาขาที่ 18'→'00018';印「สำนักงานใหญ่ / Head Office」→ '00000')。
    空/非数字/超 5 位 → ''(=未提供)。

    未提供必须留空而不是归一成总店 00000:连锁同税号多分店档靠它区分,
    没填当总店会把分店票硬配到总店头上。**明确印了总公司**则是 00000(用户 2026-09-27:
    以前把它也当成没提供,总店票就分不出总店档)。
    """
    text = str(raw or "").strip()
    lowered = text.casefold()
    if lowered and not re.search(r"\d", text) and any(word in lowered for word in _HEAD_OFFICE):
        return "00000"
    digits = re.sub(r"\D", "", text)
    if not digits or len(digits) > 5:
        return ""
    return digits.zfill(5)


def clean_seller(raw) -> str:
    """卖家清洗:剥空白;纯金额/字段标签/完整日期/无内容/过短(<2)→ ''(不展示假值)。

    「1780.00」「Total」「VAT」「13」「13/06/26」→ '';「Bangchak」「บางจาก」「7-11」「711」→ 保留。
    票面名称常是泰文名一行、英文名另起一行（识别原样带换行）：只取第一行，第二行英文名不写进 ERP 主档
    （10-09 实测：「…จำกัด\nSIAM HOME APPLIANCE CO., LTD.」整段建进了 Express 供应商档）；
    第一行只是「บริษัท」这类短前缀时接上第二行。
    """
    lines = [" ".join(line.split()) for line in str(raw or "").splitlines() if line.strip()]
    s = lines[0] if lines else ""
    if len(lines) > 1 and len(s) <= 8:
        s = f"{s} {lines[1]}"
    if len(s) < 2 or not _ALNUM_RE.search(s):
        return ""
    if s.lower() in _SELLER_STOPWORDS:
        return ""
    if _AMOUNT_RE.match(s):  # 纯数字:实为店号(711/7-11)当店名保留,否则当金额清空(B-2)
        from pearnly_cowork.push.express import merchant

        return s if merchant.is_known_brand(s) else ""
    if _FULL_DATE_RE.match(s):
        return ""
    return s


def clean_invoice_no(raw) -> str:
    """票号:允许短号(与税号不同);剥空白;纯日期片段/乱码 → ''(日期不当票号·不露乱码)。"""
    s = str(raw or "").strip()
    if not s or _FULL_DATE_RE.match(s) or is_garbled(s):
        return ""
    return s


def is_garbled(raw) -> bool:
    """乱码判定:无真实字符 或 ?/替换符占可见字符 ≥ 一半。

    「?????」→ True;「TW ?????」(部分可读)→ True(过半问号·卖家/地址按整体判);「บางจาก」→ False。
    空串不算乱码(= 缺失·由调用方区分 absent vs dirty)。
    """
    s = str(raw or "").strip()
    if not s:
        return False
    if not _ALNUM_RE.search(s):
        return True
    visible = [c for c in s if not c.isspace()]
    return bool(visible) and len(_GARBLE_RE.findall(s)) >= max(2, len(visible) // 2)


def clean_address(raw) -> str:
    """地址:剥空白;乱码/无内容/过短(<4)→ ''(不污染卡片·详情页可补)。"""
    s = str(raw or "").strip()
    if len(s) < 4 or is_garbled(s) or not _ALNUM_RE.search(s):
        return ""
    return s
