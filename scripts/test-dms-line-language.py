"""DMS LINE language follows the recipient's phone (user 2026-09-27); pinned Frappe runtime, no IO.

Run: docker run ... <image> /candidate/scripts/test-dms-line-language.py
"""
import csv
from pathlib import Path
import re
import unittest
from unittest.mock import patch

import frappe

from pearnly_dms import i18n
from pearnly_dms.booking.conversation import booking_qa, qa_cards, qa_payment_cards
from pearnly_dms.line import cards, commands
from pearnly_dms.menu import rich_menu

PACKS = Path(__file__).resolve().parents[1] / "apps/pearnly_dms/pearnly_dms/translations"


class LanguageContext(unittest.TestCase):
    def test_block_sets_and_restores_the_language(self):
        frappe.local.lang = "en"
        self.assertEqual(i18n.line_lang(), "th")
        with i18n.line_language(language="zh"):
            self.assertEqual((i18n.line_lang(), frappe.local.lang), ("zh", "zh"))
            with i18n.line_language(language="ja"):
                self.assertEqual(i18n.line_lang(), "ja")
            self.assertEqual(i18n.line_lang(), "zh")
        self.assertEqual((i18n.line_lang(), frappe.local.lang), ("th", "en"))

    def test_user_language_comes_from_line_and_unknown_falls_back_to_thai(self):
        with patch("pearnly_core.line.user_language", return_value="ja") as asked:
            with i18n.line_language("U1") as language:
                self.assertEqual(language, "ja")
        asked.assert_called_once_with("U1", default="th")
        with patch("pearnly_core.line.user_language", return_value="ko"):
            with i18n.line_language("U1") as language:
                self.assertEqual(language, "th")
        with patch("pearnly_core.line.user_language", side_effect=RuntimeError("no LINE")):
            with i18n.line_language("U1") as language:
                self.assertEqual(language, "th")


class Texts(unittest.TestCase):
    def test_cards_follow_the_block_and_stay_thai_outside(self):
        thai = cards.TXT_DONE_SAVED
        with i18n.line_language(language="zh"):
            self.assertIn("菜单", cards.TXT_DONE_SAVED)
            self.assertEqual(cards.FIELD_LABELS_TH["phone"], "电话")
        self.assertEqual(cards.TXT_DONE_SAVED, thai)
        self.assertRegex(thai, "[฀-๿]")

    def test_composed_booking_texts_are_built_at_use_time(self):
        with i18n.line_language(language="zh"):
            self.assertEqual(qa_cards.PAY_LABELS["cash"], "现金")
            self.assertIn("「{username}」", qa_cards.TXT_ADVISOR_BLOCK)
            self.assertIn("分隔", qa_payment_cards.TXT_ASK_CHEQUE_REF_MANUAL)
            self.assertEqual(qa_cards.need_slip()["text"], i18n.t("dms.card.qa.TXT_NEED_SLIP", lang="zh"))
        with i18n.line_language(language="en"):
            self.assertEqual(qa_cards.PAY_LABELS["transfer"], "Bank transfer")

    def test_every_thai_card_key_exists_in_all_languages_with_the_same_placeholders(self):
        packs = {}
        for lang in i18n.LANGUAGES:
            with open(PACKS / f"{lang}.csv", encoding="utf-8", newline="") as handle:
                packs[lang] = {row[0]: row[1] for row in csv.reader(handle) if len(row) >= 2}
        holes = lambda text: sorted(re.findall(r"\{[a-z_0-9]*\}", text))
        for key, thai in packs["th"].items():
            if not key.startswith("dms."):
                continue
            for lang in ("zh", "en", "ja"):
                self.assertIn(key, packs[lang], f"{key} missing in {lang}")
                self.assertEqual(holes(packs[lang][key]), holes(thai), f"{key} placeholders in {lang}")


class Commands(unittest.TestCase):
    def test_localised_command_words(self):
        for word in ("เมนู", "menu", "MENU", "菜单", "メニュー"):
            self.assertEqual(commands.classify(word), commands.CMD_MENU, word)
        for word in ("เริ่มใหม่", "重新开始", "やり直し", "restart"):
            self.assertEqual(commands.classify(word), commands.CMD_RESET, word)
        for word in ("สวัสดีครับ", "你好", "こんにちは", "Hello", "hi"):
            self.assertEqual(commands.classify(word), commands.CMD_GREETING, word)
        for word in ("Hi-Lux Revo", "菜单栏", "张三", "0812345678"):
            self.assertIsNone(commands.classify(word), word)

    def test_cash_words(self):
        self.assertEqual(booking_qa._CASH_WORDS, {"เงินสด", "现金", "現金", "cash"})


class RichMenu(unittest.TestCase):
    def test_payload_is_labelled_in_its_language(self):
        with patch.object(rich_menu, "account_action",
                          side_effect=lambda kind, label: {"type": "uri", "label": str(label), "uri": "https://x"}):
            zh = rich_menu.payload(True, "zh")
            th = rich_menu.payload(True, "th")
        self.assertEqual(zh["chatBarText"], "菜单")
        self.assertEqual(th["chatBarText"], "เมนู")
        self.assertEqual(zh["areas"][0]["action"]["label"], "客户资料")
        self.assertEqual(len(zh["areas"]), 5)
        self.assertTrue(all(len(area["action"]["label"]) <= 20 for area in zh["areas"]))

    def test_old_thai_only_publish_is_still_used(self):
        old = {"basic": "B", "query": "Q"}
        self.assertEqual(rich_menu.wanted_menu(old, "query", "zh"), "Q")
        new = {**old, "zh:query": "ZQ"}
        self.assertEqual(rich_menu.wanted_menu(new, "query", "zh"), "ZQ")

    def test_localised_images_exist(self):
        for variant in rich_menu.VARIANTS:
            for language in i18n.LANGUAGES:
                self.assertTrue(rich_menu._image(variant, language).startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
