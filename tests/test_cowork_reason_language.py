"""失败原因存词条原文、显示时按看的人的语言翻（2026-10-09：泰文界面出现英文原因）。不连库、不依赖 Frappe。"""
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_cowork/pearnly_cowork"

TABLES = {
    "th": {"The document date is outside the fiscal period.": "วันที่เอกสารอยู่นอกงวดบัญชี",
           "push.disabled": "ปิดการส่งอยู่", "intake.error.entry_not_balanced": "รายการไม่สมดุล"},
    "zh": {"The document date is outside the fiscal period.": "单据日期不在会计期间内", "push.disabled": "推送已关闭",
           "intake.error.entry_not_balanced": "借贷不平"},
    "ja": {},
}


def _frappe(lang):
    translate = types.SimpleNamespace(get_all_translations=lambda code: TABLES.get(code, {}))
    frappe = types.SimpleNamespace(local=types.SimpleNamespace(lang=lang), translate=translate,
                                   _=lambda text: TABLES.get(lang, {}).get(text, text))
    return frappe, translate


def _load(name, path, lang, extra=None):
    frappe, translate = _frappe(lang)
    modules = {"frappe": frappe, "frappe.translate": translate, **(extra or {})}
    with mock.patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module, modules


class SourceText(unittest.TestCase):
    def test_a_translated_reason_is_stored_as_its_source(self):
        push_state, modules = _load("push_state_under_test", APP / "history/push_state.py", "th")
        with mock.patch.dict(sys.modules, modules):
            self.assertEqual(push_state.source_text("วันที่เอกสารอยู่นอกงวดบัญชี"), "The document date is outside the fiscal period.")
            self.assertEqual(push_state.source_text("推送已关闭"), "push.disabled")  # 别的语言存的旧记录也认得
            self.assertEqual(push_state.source_text("The document date is outside the fiscal period."),
                             "The document date is outside the fiscal period.")
            self.assertEqual(push_state.source_text("[ENTRY_NOT_BALANCED] dr != cr"), "[ENTRY_NOT_BALANCED] dr != cr")
            self.assertEqual(push_state.source_text(""), "")

    def test_unreadable_translations_store_the_text_as_is(self):
        push_state, modules = _load("push_state_broken", APP / "history/push_state.py", "th")
        broken = types.SimpleNamespace(get_all_translations=lambda code: (_ for _ in ()).throw(AttributeError("no cache")))
        with mock.patch.dict(sys.modules, {**modules, "frappe.translate": broken}):
            self.assertEqual(push_state.source_text("target offline"), "target offline")


class ReasonText(unittest.TestCase):
    def _reason_text(self, lang):
        stub = types.SimpleNamespace
        modules = {"pearnly_cowork": types.ModuleType("pearnly_cowork"),
                   "pearnly_cowork.connections": stub(service=stub()),
                   "pearnly_cowork.push.export_actions": stub(parse_actions=None)}
        export_facts, frappe_modules = _load(f"export_facts_{lang}", APP / "push/export_facts.py", lang, modules)
        return export_facts.reason_text

    def test_stored_source_is_shown_in_the_viewer_language(self):
        source = "The document date is outside the fiscal period."
        self.assertEqual(self._reason_text("th")(source), "วันที่เอกสารอยู่นอกงวดบัญชี")
        self.assertEqual(self._reason_text("zh")(source), "单据日期不在会计期间内")
        self.assertEqual(self._reason_text("zh")("push.disabled"), "推送已关闭")

    def test_helper_codes_match_lower_case_entries(self):
        self.assertEqual(self._reason_text("th")("[ENTRY_NOT_BALANCED] dr != cr"), "รายการไม่สมดุล")


class Sources(unittest.TestCase):
    def test_the_list_retranslates_and_matches_codes_in_either_case(self):
        source = (APP / "public/desk/intake-upload.js").read_text()
        body = source[source.index("p.intake_push_reason = reason =>"):source.index("const RESULT_UNKNOWN")]
        self.assertIn("if (!code) return text ? __(text) : text;", body)
        self.assertIn("`intake.error.${code.toLowerCase()}`", body)

    def test_background_steps_pin_the_language(self):
        for name in ("folder_intake/pump.py", "folder_intake/machine.py"):
            self.assertIn("job.lang_resolved = True", (APP / name).read_text(), name)

    def test_old_records_are_cleaned_once(self):
        self.assertIn("pearnly_cowork.patches.v0_0_15_push_reason_source", (APP / "patches.txt").read_text())


class HelperCodesHaveText(unittest.TestCase):
    """小助手回报的每个错误码都有词条（大小写都认），否则界面原样显示小助手的英文细节（10-09 补了 13 个）。"""

    def test_every_helper_error_code_has_an_entry(self):
        import re

        codes = set()
        for path in (ROOT / "companion/src/companion").glob("*.py"):
            codes.update(re.findall(r'^ERR_[A-Z0-9_]+\s*=\s*"([A-Za-z0-9_]+)"', path.read_text(), re.M))
        keys = set()
        for path in ROOT.glob("apps/*/*/i18n/messages.tsv"):
            for line in path.read_text(encoding="utf-8").splitlines():
                key = line.split("\t")[-1]
                if key.startswith("intake.error."):
                    keys.add(key[len("intake.error."):])
        self.assertGreater(len(codes), 20)
        self.assertEqual(sorted(code for code in codes if code not in keys and code.lower() not in keys), [])

if __name__ == "__main__":
    unittest.main()
