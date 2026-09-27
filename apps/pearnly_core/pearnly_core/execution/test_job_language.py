"""识别任务的界面语言（`work.job_language`）：续跑由调度器（Administrator）排队时，产品设好的语言不能被
Frappe 后台任务的「首次翻译按排队用户重设语言」冲掉。离线跑，不连站点、不连 Redis。"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.utils import translations

from pearnly_core.execution import work


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


if __name__ == "__main__":
    unittest.main()
