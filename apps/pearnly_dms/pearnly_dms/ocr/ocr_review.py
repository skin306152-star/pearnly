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

from typing import Iterable

from pearnly_dms import i18n
from pearnly_dms.line import cards as cards

CHECKSUM_KEY = "people_id_checksum"

# 键 = 识别归一层 missing_fields 的产出（与源码机械对账，加字段不会静默掉出去）；
# 字段名在语言包 `dms.card.REVIEW_<键>`，按收件人手机的 LINE 语言说（2026-10-04 用户：
# 中文手机上括号里是泰文字段名、校验位那句整句泰文）。
MISSING_FIELDS = ("people_id", "first_name", "last_name", "birthday")

# 列字段名用的分隔符：中文、日文用顿号，其余用逗号（标点，不是文案）。
_SEPARATOR = {"zh": "、", "ja": "、"}


def review_text(missing: Iterable[str]) -> str:
    """打回时对用户说的话：校验位错说号码对不上，其余才是拍不清 + 列缺项。"""
    keys = list(missing)
    head = cards.TXT_ID_CHECKSUM if CHECKSUM_KEY in keys else cards.TXT_BLURRY
    lang = i18n.line_lang()
    labels = [i18n.t(f"dms.card.REVIEW_{key.upper()}", lang=lang) for key in keys if key in MISSING_FIELDS]
    if not labels:
        return head
    return i18n.t("dms.card.TXT_REVIEW_MISSING", lang=lang, text=head,
                  fields=_SEPARATOR.get(lang, ", ").join(labels))
