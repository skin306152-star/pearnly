"""表单引导共用结果步骤（`pearnly_core.assistant.guide.form_result`）：只照确定回包画，不编造、不露内部编号，原因走共用原因表。

    python3 tests/test_guide_form_result.py
"""
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "pearnly_core"))

WORDS = {"intake.delete.done": "Deleted {0}", "guide.reason.failed": "Processing error", "guide.reason.not_permitted": "Not permitted"}
frappe = types.ModuleType("frappe")
frappe._ = lambda text: WORDS.get(text, text)
frappe.ValidationError, frappe.PermissionError = ValueError, PermissionError
sys.modules["frappe"] = frappe

from pearnly_core.assistant.guide import form_result  # noqa: E402


def result(done):
    return form_result.result({"done": done}, "")


class FormResult(unittest.TestCase):
    def test_nothing_confirmed_gives_nothing(self):
        self.assertIsNone(form_result.result({}, ""))

    def test_done_with_a_name_shows_only_what_the_reply_has(self):
        out = result({"status": "entered", "label": "Shelf C", "imported": 3})
        self.assertEqual((out["status"], out["docnum"], out["imported"]), ("entered", "Shelf C", 3))
        self.assertNotIn("reason_text", out)
        self.assertNotIn("files", result({"status": "entered", "files": []}))

    def test_files_and_hand_over_are_carried(self):
        out = result({"status": "entered", "files": [{"file": "F1", "name": "a.xlsx"}], "next": {"run": "R2"}})
        self.assertEqual((out["files"][0]["name"], out["next"]), ("a.xlsx", {"run": "R2"}))

    def test_deleted_says_how_many_and_why_some_were_skipped(self):
        self.assertEqual(result({"status": "entered", "deleted": ["T1", "T2"]})["reason_text"], "Deleted 2")
        out = result({"status": "entered", "deleted": 1, "skipped": 2, "skip_reason_text": "Only drafts"})
        self.assertEqual((out["status"], out["reason_text"]), ("entered", "Deleted 1 · Only drafts"))
        none = result({"status": "entered", "deleted": 0, "skipped": 1, "skip_reason_text": "Only drafts"})
        self.assertEqual((none["status"], none["reason_text"]), ("held", "Only drafts"))

    def test_failure_reason_comes_from_the_reply_or_the_shared_reason_table(self):
        self.assertEqual(result({"status": "failed", "reason_text": "Closed"})["reason_text"], "Closed")
        self.assertEqual(result({"status": "failed", "reason": "not_permitted"})["reason_text"], "Not permitted")
        self.assertEqual(result({"status": "failed"})["reason_text"], "Processing error")   # 不露错误编号

    def test_internal_ids_never_reach_the_card(self):
        for internal in ("S:SD-00122", "I:LI-00110", "CWH-ab12", "a" * 32):
            self.assertNotIn("docnum", result({"status": "entered", "docnum": internal}), internal)
        self.assertEqual(result({"status": "entered", "docnum": "IV-9 · Seller"})["docnum"], "IV-9 · Seller")

    def test_a_fact_state_after_success_is_shown(self):
        out = result({"status": "entered", "label": "ACME", "state_text": "Syncing"})
        self.assertEqual((out["docnum"], out["reason_text"]), ("ACME", "Syncing"))


if __name__ == "__main__":
    unittest.main()
