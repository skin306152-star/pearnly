# -*- coding: utf-8 -*-
"""身份证识别被打回时，**按真实原因**对用户说的话。

老产品踩过两个坑，都在注释里留着：

1. **键表对错了**：打回原因取自识别归一层产出的 `missing_fields`，而文案曾经拿 DMS 建档
   表单的字段名去查它 —— 两个词表交集为空，括号里的缺项**从来没印出来过**；
2. **校验位不过被说成"拍不清"**：13 位已经读到了，只是校验位对不上。归因成"拍不清"
   会让人**反复重拍，而每拍一次都真扣一次 OCR 费**。所以要明说是号码本身对不上，
   让人先核对卡面数字。
"""

from __future__ import annotations

from typing import Dict, Iterable

from pearnly_dms import dms_cards as cards

CHECKSUM_KEY = "people_id_checksum"

# 键 = 识别归一层 missing_fields 的产出（与源码机械对账，加字段不会静默掉出去）
MISSING_LABELS_TH: Dict[str, str] = {
    "people_id": "เลขบัตรประชาชน",
    "first_name": "ชื่อ",
    "last_name": "นามสกุล",
    "birthday": "วันเกิด",
}

TXT_ID_CHECKSUM = (
    "อ่านเลขบัตรประชาชนได้ครบ 13 หลัก แต่หลักตรวจสอบไม่ผ่าน "
    "กรุณาตรวจสอบเลขบนบัตรอีกครั้ง แล้วถ่ายใหม่ให้เห็นเลขบัตรชัดเจน"
)


def review_text(missing: Iterable[str]) -> str:
    """打回时对用户说的话：校验位错说号码对不上，其余才是拍不清 + 列缺项。"""
    keys = list(missing)
    head = TXT_ID_CHECKSUM if CHECKSUM_KEY in keys else cards.TXT_BLURRY
    labels = [MISSING_LABELS_TH[k] for k in keys if k in MISSING_LABELS_TH]
    return head + (" (" + ", ".join(labels) + ")" if labels else "")
