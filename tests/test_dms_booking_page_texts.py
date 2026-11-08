# -*- coding: utf-8 -*-
"""DMS 的 LIFF 交接页（www/liff/dms-booking.html）：页面要显示的每个文案键都先取回来，四种语言都有。

2026-11-08（日语窗口盘点 LINE 时发现）：预取清单里漏了 `dms.edit.expired`，草稿过期时页面直接显示
「dms.edit.expired」这串键名，所有语言都一样。"""
import csv
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGE = ROOT / "apps/pearnly_dms/pearnly_dms/www/liff/dms-booking.html"
PACKS = ROOT / "apps/pearnly_dms/pearnly_dms/translations"


class BookingPageTexts(unittest.TestCase):
    def test_every_key_the_page_shows_is_loaded_first(self):
        html = PAGE.read_text(encoding="utf-8")
        loaded = set()
        for call in re.findall(r"loadTexts\(\[(.*?)\]\)", html, re.S):
            loaded |= set(re.findall(r'"(dms\.edit\.[A-Za-z]+)"', call))
        used = set(re.findall(r'"(dms\.edit\.[A-Za-z]+)"', html))
        self.assertTrue(loaded)
        self.assertEqual(used - loaded, set())

    def test_every_key_has_all_four_languages(self):
        used = set(re.findall(r'"(dms\.edit\.[A-Za-z]+)"', PAGE.read_text(encoding="utf-8")))
        for lang in ("th", "zh", "en", "ja"):
            with open(PACKS / f"{lang}.csv", encoding="utf-8") as handle:
                keys = {row[0] for row in csv.reader(handle) if row and row[1:] and row[1].strip()}
            self.assertEqual(used - keys, set(), lang)


if __name__ == "__main__":
    unittest.main()
