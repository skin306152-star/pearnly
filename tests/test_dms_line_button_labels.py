# -*- coding: utf-8 -*-
"""DMS 的 LINE 按钮标签（`dms.card.*BTN_*`）四种语言都不超过 20 个字符。

LINE 快捷按钮 / 模板按钮的 action.label 上限是 20 个字符，超了整条消息被拒收（2026-10-17 存量清单：
`dms.card.qa.BTN_REMOVE_SLIP` 泰文 22 个字符，「凭证与付款方式矛盾」那一步发不出去）。LINE 没写泰文组合符怎么数，
这里按 UTF-16 码元数（不少于码位数，比 Python len 更严）：过了这条，按码位或按码元数都不超。
术语表与生成的翻译包（运行时读的是翻译包）都查。
"""

from __future__ import annotations

import csv
import io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "apps", "pearnly_dms", "pearnly_dms")
GLOSSARY = os.path.join(APP, "i18n", "messages.tsv")
BUTTON_KEY = re.compile(r"^dms\.card\.(.+\.)?BTN_")
LANGS = ("en", "zh", "th", "ja")
LIMIT = 20


def line_length(text: str) -> int:
    """LINE 计数按严的算：UTF-16 码元数。"""
    return len(text.encode("utf-16-le")) // 2


def glossary_buttons() -> dict:
    with open(GLOSSARY, encoding="utf-8") as handle:
        rows = list(csv.DictReader(io.StringIO(handle.read()), delimiter="\t"))
    return {row["key"]: {lang: (row.get(lang) or "").replace("\\n", "\n") for lang in LANGS}
            for row in rows if BUTTON_KEY.match(row.get("key") or "")}


class DmsLineButtonLabels(unittest.TestCase):
    def test_glossary_labels_fit_line(self):
        buttons = glossary_buttons()
        self.assertGreater(len(buttons), 10)
        too_long = [(key, lang, line_length(text), text) for key, texts in sorted(buttons.items())
                    for lang, text in texts.items() if line_length(text) > LIMIT]
        self.assertEqual(too_long, [])

    def test_translation_packages_fit_line(self):
        keys = set(glossary_buttons())
        too_long = []
        for lang in LANGS:
            with open(os.path.join(APP, "translations", lang + ".csv"), encoding="utf-8", newline="") as handle:
                for row in csv.reader(handle):
                    if len(row) >= 2 and row[0] in keys and line_length(row[1]) > LIMIT:
                        too_long.append((row[0], lang, line_length(row[1]), row[1]))
        self.assertEqual(too_long, [])

    def test_counting_is_strict(self):
        self.assertEqual(line_length("ลบสลิปแล้วดำเนินการต่อ"), 22)  # 原文案：超限
        self.assertEqual(line_length("ลบสลิปแล้วทำต่อ"), 15)
        self.assertEqual(line_length("😀"), 2)  # 补充平面字符按两个码元


if __name__ == "__main__":
    unittest.main()
