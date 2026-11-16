"""`count-erp-queued-no-computer.py` 的汇总：只数写账件归的人没有电脑的件，按单据类型给张数与最早的排队时刻，另数兜底会改归的件。"""
import importlib.util
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parent / "count-erp-queued-no-computer.py"
spec = importlib.util.spec_from_file_location("count_erp_queued_no_computer", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Summarize(unittest.TestCase):
    def test_only_jobs_whose_owner_has_no_computer_are_counted(self):
        rows = [
            {"doctype": "ERP Sales Document", "created": "2026-11-10 09:00:00.123", "missing": True, "moves": True},
            {"doctype": "ERP Sales Document", "created": "2026-11-02 08:00:00", "missing": True, "moves": False},
            {"doctype": "Purchase Document", "created": "2026-11-12 10:00:00", "missing": True, "moves": True},
            {"doctype": "Purchase Document", "created": "2026-10-01 10:00:00", "missing": False, "moves": False},
        ]
        self.assertEqual(module.summarize(rows), {
            "total": 3, "would_move": 2,
            "by_doctype": {"ERP Sales Document": {"count": 2, "earliest": "2026-11-02 08:00:00"},
                           "Purchase Document": {"count": 1, "earliest": "2026-11-12 10:00:00"}}})

    def test_nothing_waiting_is_zero(self):
        self.assertEqual(module.summarize([]), {"total": 0, "by_doctype": {}, "would_move": 0})

    def test_it_never_writes(self):
        text = PATH.read_text(encoding="utf-8")
        self.assertIn("frappe.db.rollback()", text)
        for call in ("frappe.db.commit", "set_value(", ".insert(", ".save(", "delete_doc", "db_set("):
            self.assertNotIn(call, text)


if __name__ == "__main__":
    unittest.main()
