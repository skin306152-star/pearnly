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


class ImportTimeFreeze(unittest.TestCase):
    """`cards.TXT_X` 每次按当前收件人现取；`from cards import TXT_X` 写在模块顶层会在导入时定死成
    当时那个语言 —— 测试机实测：网页进程先处理了一个中文事件，之后给英文用户的菜单卡也是中文。"""

    APP = Path(__file__).resolve().parents[1] / "apps/pearnly_dms/pearnly_dms"

    def test_no_module_level_import_of_lazy_texts(self):
        import ast

        lazy = {}
        for path in self.APP.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            if not any(isinstance(n, ast.FunctionDef) and n.name == "__getattr__" for n in tree.body):
                continue
            defined = set()
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    defined.add(node.name)
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    defined |= {x.id for t in targets for x in ast.walk(t) if isinstance(x, ast.Name)}
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    defined |= {(a.asname or a.name).split(".")[0] for a in node.names}
            lazy["pearnly_dms." + ".".join(path.relative_to(self.APP).with_suffix("").parts)] = defined
        frozen = []
        for path in self.APP.rglob("*.py"):
            for node in ast.parse(path.read_text(encoding="utf-8")).body:  # 只看模块顶层
                if isinstance(node, ast.ImportFrom) and node.module in lazy:
                    frozen += [f"{path.name}:{node.lineno} {a.name}" for a in node.names
                               if a.name not in lazy[node.module]]
        self.assertEqual(frozen, [])

    def test_menu_card_follows_the_recipient(self):
        from pearnly_dms.menu import menu_cards

        with patch.object(menu_cards, "account_action", return_value=None), \
                patch.object(menu_cards, "icon_base", return_value="https://x"):
            with i18n.line_language(language="zh"):
                zh = menu_cards.menu_card()
            with i18n.line_language(language="en"):
                en = menu_cards.menu_card()
        self.assertEqual(zh["altText"], i18n.t("dms.card.TXT_MENU_TITLE", lang="zh"))
        self.assertEqual(en["altText"], i18n.t("dms.card.TXT_MENU_TITLE", lang="en"))
        self.assertNotEqual(zh["altText"], en["altText"])


class EditorAndDiffLabels(unittest.TestCase):
    def test_every_diffable_customer_field_has_a_label(self):
        from pearnly_dms.intake import customer_diff

        with i18n.line_language(language="zh"):
            labels = cards.field_labels()
        self.assertFalse(set(customer_diff._DIFF_FIELDS) - set(labels))
        self.assertEqual(labels["building"], "楼宇")

    def test_editor_save_builds_cards_in_the_recipients_language(self):
        from pearnly_dms.editor import browser_api

        seen = {}

        class Editor:
            def save(self, line_user_id, nonce, form):
                seen["lang"] = i18n.line_lang()
                return "n2"

        with patch.object(browser_api, "_ticket_line_user", return_value={"line_user_id": "U1", "mode": "customer"}), \
             patch.object(browser_api, "_editor", return_value=Editor()), \
             patch("pearnly_core.line.user_language", return_value="zh"):
            self.assertEqual(browser_api.browser_save("t", "n1", {}), {"nonce": "n2"})
        self.assertEqual(seen["lang"], "zh")
        self.assertEqual(i18n.line_lang(), "th", "the language block ends with the request")


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
        self.assertEqual(len(zh["areas"]), 6)
        self.assertEqual(zh["areas"][5]["action"]["label"], "使用手册")
        self.assertEqual(th["areas"][5]["action"]["data"], "action=menu_manual")
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
