"""产品四种语言在 Frappe 语言表里启用（离线；替身代替站点，不写库）。"""
import unittest
from unittest.mock import MagicMock, patch

from pearnly_core import hooks
from pearnly_core.account import languages, preferences, ui_language


class FakeLanguageTable:
    """Language 表的替身：code → enabled；保存过的记录记在 saved。"""

    def __init__(self, rows):
        self.rows = dict(rows)
        self.saved = []

    def exists(self, doctype, name):
        assert doctype == "Language"
        return name in self.rows

    def get_value(self, doctype, name, field):
        assert (doctype, field) == ("Language", "enabled")
        return self.rows.get(name)

    def get_doc(self, doctype, name):
        table, doc = self, MagicMock(enabled=self.rows[name])

        def save(ignore_permissions=False):
            table.rows[name] = doc.enabled
            table.saved.append(name)
        doc.save.side_effect = save
        return doc


class TestProductLanguages(unittest.TestCase):
    def run_with(self, rows, synced=None):
        table = FakeLanguageTable(rows)
        native = MagicMock()
        native.db.exists.side_effect = table.exists
        native.db.get_value.side_effect = table.get_value
        native.get_doc.side_effect = table.get_doc

        def sync():
            table.rows.update(synced or {})
        with patch.object(languages, "frappe", native), \
                patch("frappe.core.doctype.language.language.sync_languages", side_effect=sync):
            languages.ensure_product_languages()
        return table

    def test_frappe_ships_japanese_disabled_and_we_enable_it(self):
        # 锁定镜像 languages.csv：en / th / zh 启用，ja 停用
        table = self.run_with({"th": 1, "zh": 1, "en": 1, "ja": 0, "ko": 0})
        self.assertEqual(table.rows["ja"], 1)
        self.assertEqual(table.saved, ["ja"])  # 只动停用的那一种，别的语言不碰
        self.assertEqual(table.rows["ko"], 0)

    def test_all_four_must_end_up_enabled(self):
        table = self.run_with({"th": 0, "zh": 0, "en": 0, "ja": 0})
        self.assertEqual({code: table.rows[code] for code in ("th", "zh", "en", "ja")},
                         {"th": 1, "zh": 1, "en": 1, "ja": 1})

    def test_missing_records_come_from_the_framework_list(self):
        table = self.run_with({"th": 1, "zh": 1, "en": 1}, synced={"ja": 0})
        self.assertEqual(table.rows["ja"], 1)

    def test_a_language_still_missing_fails_the_migration(self):
        with self.assertRaises(RuntimeError):
            self.run_with({"th": 1, "zh": 1, "en": 1})

    def test_the_four_are_the_product_languages_with_frappe_codes(self):
        self.assertEqual(set(languages.PRODUCT_LANGUAGES), {"th", "zh", "en", "ja"})
        self.assertEqual(set(languages.PRODUCT_LANGUAGES), set(ui_language.UI_LANGUAGES))
        # User.language 存的是这四个代码（中文 zh，不是身份服务的 zh-CN）
        self.assertEqual(set(preferences.LANGUAGES), set(languages.PRODUCT_LANGUAGES))

    def test_runs_after_every_migration_and_new_tenant(self):
        self.assertIn("pearnly_core.account.languages.ensure_product_languages", hooks.after_migrate)


if __name__ == "__main__":
    unittest.main()
