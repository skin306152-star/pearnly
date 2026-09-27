"""推送账套：识别后自动挑（LINE 收单）与编辑页改账套（用户 2026-09-27）。数据库与目录都用替身。"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "apps" / "pearnly_cowork"))
sys.path.insert(0, str(root / "apps" / "pearnly_core"))

import frappe  # noqa: E402

from pearnly_cowork.push import account_suggest, account_target  # noqa: E402

OWN = "0105551234567"
ROWS = [
    {"source_id": r"s:\2569\exp69\69acme", "label": "69ACME", "active": True,
     "attributes": {"root_label": "69EXP", "tax_id": OWN, "writable": True,
                    "period_start": "2025-11-01", "period_end": "2026-10-31", "next_end": "2027-10-31"}},
    {"source_id": r"s:\2568\exp68\68acme", "label": "68ACME", "active": True,
     "attributes": {"root_label": "68EXP", "tax_id": OWN, "writable": True,
                    "period_start": "2024-11-01", "period_end": "2025-10-31", "next_end": "2026-10-31"}},
    {"source_id": r"s:\2569\exp69\locked", "label": "LOCKED", "active": True,
     "attributes": {"root_label": "69EXP", "tax_id": OWN, "writable": False}},
]


def _throw(message, exc=None, *args, **kwargs):
    raise (exc or frappe.ValidationError)(message)


def pages(**fields):
    return json.dumps([{"fields": {"invoice_number": "IV1", "total_amount": "10", **fields}}])


class SuggestTest(unittest.TestCase):
    def setUp(self):
        self.written = {}
        self.pages = {"H1": pages(direction="purchase", buyer_tax=OWN, date="2026-03-01"),
                      "H2": pages(direction="purchase", buyer_tax="0105559999999", date="2026-03-01")}
        patches = [
            mock.patch.object(account_suggest.directory, "current", return_value=("express", ROWS)),
            mock.patch.object(frappe, "db", mock.MagicMock()),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        frappe.db.sql.return_value = []
        frappe.db.get_value.side_effect = lambda doctype, name, field: self.pages[name]
        frappe.db.set_value.side_effect = lambda doctype, name, field, value, **k: self.written.__setitem__(name, value)

    def test_fills_only_the_records_it_can_decide(self):
        results = account_suggest.fill("CON-X", ["H1", "H2"], "purchase")
        self.assertEqual(self.written, {"H1": r"s:\2569\exp69\69acme"})
        self.assertEqual(results["H2"]["reason"], "no_tax_match")

    def test_hook_skips_batches_that_already_chose_or_do_not_push(self):
        chosen = frappe._dict(target_connection="CON-X", target_account_set=r"s:\x", intake_target="express",
                              history_records='["H1"]')
        export = frappe._dict(target_connection="", target_account_set="", intake_target="none", history_records='["H1"]')
        for batch in (chosen, export):
            account_suggest.recognized(batch)
        self.assertEqual(self.written, {})
        account_suggest.recognized(frappe._dict(target_connection="CON-X", target_account_set="", intake_target="express",
                                                history_records='["H1"]', direction="purchase"))
        self.assertEqual(self.written, {"H1": r"s:\2569\exp69\69acme"})
        frappe.db.commit.assert_called_once()


class SetAccountTest(unittest.TestCase):
    def setUp(self):
        self.doc = frappe._dict(name="H1", workspace="W1", client="", record_version=3, last_push_status="",
                                push_connection="CON-X", push_account_set="")
        self.doc.check_permission = mock.Mock()
        patches = [
            mock.patch.object(account_target.scope, "require"),
            mock.patch.object(account_target.storage, "read", return_value=self.doc),
            mock.patch.object(account_target.storage, "project", side_effect=lambda doc: {"name": doc.name}),
            mock.patch.object(account_target.directory, "current", return_value=("express", ROWS)),
            mock.patch.object(frappe, "db", mock.MagicMock()),
            mock.patch.object(account_target, "_", side_effect=lambda text, *a, **k: text),
            mock.patch.object(account_target.storage, "_", side_effect=lambda text, *a, **k: text),
            mock.patch.object(frappe, "throw", side_effect=_throw),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        frappe.db.get_value.return_value = frappe._dict(workspace="W1", client="")

    def test_changes_to_a_writable_account_in_the_current_directory(self):
        account_target.set_account("H1", r"S:\2568\EXP68\68ACME", 3)
        frappe.db.set_value.assert_called_once_with("Cowork OCR History", "H1", {
            "push_account_set": r"s:\2568\exp68\68acme", "record_version": 4})

    def test_rejects_stale_version_pushed_record_unknown_or_unwritable_account(self):
        with self.assertRaises(frappe.TimestampMismatchError):
            account_target.set_account("H1", r"S:\2568\EXP68\68ACME", 2)
        for key in (r"S:\NOWHERE", r"S:\2569\EXP69\LOCKED"):
            with self.assertRaises(frappe.ValidationError):
                account_target.set_account("H1", key, 3)
        self.doc.last_push_status = "success"
        with self.assertRaises(frappe.ValidationError):
            account_target.set_account("H1", r"S:\2568\EXP68\68ACME", 3)
        frappe.db.set_value.assert_not_called()

    def test_connection_of_another_workspace_is_refused(self):
        frappe.db.get_value.return_value = frappe._dict(workspace="W2", client="")
        with self.assertRaises(frappe.ValidationError):
            account_target.set_account("H1", r"S:\2568\EXP68\68ACME", 3)


if __name__ == "__main__":
    unittest.main()
