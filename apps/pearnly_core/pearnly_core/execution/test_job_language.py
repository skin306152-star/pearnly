"""识别任务的界面语言（`work.job_language`）：续跑由调度器（Administrator）排队时，产品设好的语言不能被
Frappe 后台任务的「首次翻译按排队用户重设语言」冲掉。离线跑，不连站点、不连 Redis。"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.utils import translations

from pearnly_core.execution import language, work


class JobLanguage(unittest.TestCase):
    def setUp(self):
        frappe.local.lang = "th"
        frappe.local.job = SimpleNamespace(user="Administrator", lang_resolved=False)
        self.addCleanup(self.forget)

    @staticmethod
    def forget():
        for key in ("job", "lang"):
            try:
                delattr(frappe.local, key)
            except AttributeError:
                pass

    def test_the_submitter_language_is_set_and_frappe_does_not_reset_it(self):
        with patch("frappe.translate.get_user_lang", side_effect=lambda user: {"u@x": "zh"}.get(user, "th")) as lang, \
                patch("frappe.translate.get_all_translations", return_value={}):
            work.job_language("u@x")
            self.assertEqual(frappe.local.lang, "zh")
            frappe.local.lang = "en"  # 产品处理函数按 LINE 手机语言改
            translations._("Review document")
            self.assertEqual(frappe.local.lang, "en")
            self.assertEqual([call.args for call in lang.call_args_list], [("u@x",)])

    def test_without_this_frappe_would_switch_to_the_queueing_user(self):
        with patch("frappe.translate.get_user_lang", return_value="th"), \
                patch("frappe.translate.get_all_translations", return_value={}):
            frappe.local.lang = "zh"
            translations._("Review document")
            self.assertEqual(frappe.local.lang, "th")  # Frappe 16 自带行为：这就是 09-27 泰文卡的来由


class SharedLanguage(unittest.TestCase):
    """共用的 `execution.language`：LINE webhook 排的任务（Guest）里设的回复语言不被首次 `_()` 换回站点语言。"""

    def setUp(self):
        frappe.local.lang = "th"
        frappe.local.job = SimpleNamespace(user="Guest", lang_resolved=False)
        self.addCleanup(JobLanguage.forget)

    def translate(self):
        with patch("frappe.translate.get_user_lang", return_value="th"), \
                patch("frappe.translate.get_all_translations", return_value={}):
            translations._("Review document")

    def test_settle_keeps_the_language_through_the_first_translation(self):
        language.settle("zh")
        self.translate()
        self.assertEqual(frappe.local.lang, "zh")

    def test_use_restores_the_language_and_the_flag(self):
        with language.use("ja") as lang:
            self.assertEqual(lang, "ja")
            self.translate()
            self.assertEqual(frappe.local.lang, "ja")
        self.assertEqual(frappe.local.lang, "th")
        self.assertFalse(frappe.local.job.lang_resolved)

    def test_empty_language_keeps_the_current_one(self):
        with language.use(""):
            self.assertEqual(frappe.local.lang, "th")
            self.assertTrue(frappe.local.job.lang_resolved)

    def test_outside_a_job_only_sets_the_language(self):
        del frappe.local.job
        language.settle("en")
        self.assertEqual(frappe.local.lang, "en")

    def test_failure_notices_keep_the_language_their_handler_sets(self):
        """识别失败回执（ERP / Cowork LINE 收单、DMS 证件）：调度器续跑的任务里处理函数设的语言要留得住。"""
        frappe.local.job = SimpleNamespace(user="Administrator", lang_resolved=False)
        seen = []

        def handler(arguments, code):
            frappe.local.lang = "zh"
            self.translate()
            seen.append(frappe.local.lang)

        row = SimpleNamespace(kind="k", arguments="{}")
        frappe.local.db = SimpleNamespace(get_value=lambda *a, **k: row)
        self.addCleanup(lambda: delattr(frappe.local, "db"))
        with \
                patch.object(frappe, "get_hooks", lambda name: {"k": ["h"]}), \
                patch.object(frappe, "get_attr", lambda path: handler):
            work._notify("w1", "failed")
        self.assertEqual(seen, ["zh"])


if __name__ == "__main__":
    unittest.main()
