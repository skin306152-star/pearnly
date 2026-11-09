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

    def test_the_stable_code_of_a_reason(self):
        # 月结台账的异常带 detail（`export_facts.reason_code`）：与 `reason_text` 认码同一套写法；整句原文认不出给空串
        stub = types.SimpleNamespace
        modules = {"pearnly_cowork": types.ModuleType("pearnly_cowork"),
                   "pearnly_cowork.connections": stub(service=stub()),
                   "pearnly_cowork.push.export_actions": stub(parse_actions=None)}
        reason_code = _load("export_facts_codes", APP / "push/export_facts.py", "en", modules)[0].reason_code
        for reason, code in (("[ENTRY_NOT_BALANCED] dr != cr", "entry_not_balanced"),
                             ("EXPRESS_MANUAL: party_missing", "party_missing"),
                             ("choice_needed:party,vat", "choice_needed"), ("account_period_mismatch", "account_period_mismatch"),
                             ("push.companion_account_unreported", "push.companion_account_unreported"),
                             ("The document date is outside the fiscal period.", ""), ("", ""), (None, ""),
                             # MR.ERP 导入回报存成「码; 原文」（11-09 前认不出，界面原样露出）
                             ("ERR_MRERP_IMPORT_ERROR; Error : Duplicate entry", "err_mrerp_import_error"),
                             ("ERR_ACCOUNT_NEEDS_REVIEW;ไม่พบรหัสบัญชี", "err_account_needs_review"),
                             ("mrerp_currency_not_thb:usd", "mrerp_currency_not_thb")):
            self.assertEqual(reason_code(reason), code, reason)


class Sources(unittest.TestCase):
    def test_the_list_retranslates_and_matches_codes_in_either_case(self):
        source = (APP / "public/desk/intake-upload.js").read_text()
        body = source[source.index("p.intake_push_reason = reason =>"):source.index("const RESULT_UNKNOWN")]
        self.assertIn("if (!code) return text ? __(text) : text;", body)
        self.assertIn("`intake.error.${code.toLowerCase()}`", body)
        # 「码; 原文」同「码:细节」：网页列表与服务端（export_facts）同一条规则
        self.assertIn("(?=$|[:;])/)?.[1];", body)
        self.assertIn('(?=$|[:;])"))', (APP / "push/export_facts.py").read_text())

    def test_the_review_page_offers_account_sets_for_two_books_of_the_same_dates(self):
        """数字会计员第 3 期（10-17）复核页：自动推判出 book_many（C、没人推过）同定时任务「多于一份」显示同一句、列候选账套；
        仅识别不给按钮；按钮用 btn-sm（手机点击区），请求回来前整行禁用（第五轮后审查）。"""
        source = (APP / "public/desk/intake-upload.js").read_text()
        rule = source[source.index("const book_many = record =>"):source.index("/** 失败原因")]
        for part in ('record?.auto_class === "C"', "!record.last_push_status", "!record.last_push_reason",
                     '.split(",").includes("book_many")'):
            self.assertIn(part, rule)
        self.assertIn("if (reason === ACCOUNT_SET_AMBIGUOUS && !recognize_only) void show_account_sets(record, row);",
                      source)
        self.assertIn("String(record.last_push_reason || ACCOUNT_SET_AMBIGUOUS)", source)
        shown = source[source.index("const show_account_sets = async"):source.index("const resolve_unknown")]
        self.assertIn("btn btn-sm btn-default ml-2' data-account-set", shown)
        self.assertNotIn("btn-xs", shown)
        self.assertIn('buttons.prop("disabled", true)', shown)
        self.assertIn('buttons.prop("disabled", false)', shown)

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


class MrerpCodesHaveText(unittest.TestCase):
    """MR.ERP 写账会落到推送记录 / 待处理 / CC 回包的原因码，四语都有 `intake.error.<码>`（11-09 前 23 个原样露码）。
    清单里每个码都要真在共用层或 Cowork 的 MR.ERP 代码里出现（防清单过期）。"""

    SHOWN = ("ERR_ACCOUNT_NEEDS_REVIEW", "ERR_AMOUNTS_NOT_CLOSED", "ERR_AUTH", "ERR_BUSINESS", "ERR_CUSTOMER_CODE_TOO_LONG",
             "ERR_DATE_FUTURE", "ERR_DIRECTION_UNKNOWN", "ERR_INVOICE_NO_TOO_LONG", "ERR_MRERP_ACCOUNT_IN_USE", "ERR_MRERP_IMPORT_ERROR",
             "ERR_MRERP_MODULE_UNAVAILABLE", "ERR_NEGATIVE_AMOUNT", "ERR_NO_CLIENT", "ERR_NO_CUSTOMER_MAPPING",
             "ERR_NO_INVOICE_DATE", "ERR_NO_INVOICE_NO", "ERR_NO_SUPPLIER", "ERR_NO_TOTAL_AMOUNT", "ERR_SESSION_LOCK",
             "ERR_TAX_RATE_INVALID", "ERR_TECHNICAL", "ERR_UNEXPECTED", "ERR_UNKNOWN_UPLOAD_OUTCOME", "ERR_VAT_RATE_ANOMALY",
             "mrerp_credit_note", "mrerp_currency_not_thb")

    def test_every_shown_mrerp_code_has_four_languages_and_still_exists(self):
        import csv

        source = "".join(path.read_text(encoding="utf-8") for base in (
            ROOT / "apps/pearnly_core/pearnly_core/mrerp", ROOT / "apps/pearnly_core/pearnly_core/express_rules/push",
            APP / "push/mrerp") for path in base.rglob("*.py"))
        for lang in ("zh", "th", "en", "ja"):
            with (APP / f"translations/{lang}.csv").open(encoding="utf-8", newline="") as handle:
                keys = {row[0] for row in csv.reader(handle) if row}
            for code in self.SHOWN:
                with self.subTest(lang=lang, code=code):
                    self.assertIn(f"intake.error.{code}", keys)
        for code in self.SHOWN:
            with self.subTest(code=code):
                self.assertIn(f'"{code}"', source)

