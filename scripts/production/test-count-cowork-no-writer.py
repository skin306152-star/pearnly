"""`count-cowork-no-writer.py` 的汇总：因没有可写的电脑没推成的单按连接给张数与最早时刻，另数兜底会交给同事的张数；脚本只读。"""
import importlib.util
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parent / "count-cowork-no-writer.py"
spec = importlib.util.spec_from_file_location("count_cowork_no_writer", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Summarize(unittest.TestCase):
    def test_documents_are_counted_per_connection_with_the_earliest_and_the_ones_that_would_move(self):
        rows = [
            {"connection": "CONN-B", "at": "2026-11-10 09:00:00.123", "moves": True},
            {"connection": "CONN-B", "at": "2026-11-02 08:00:00", "moves": False},
            {"connection": "CONN-A", "at": "2026-11-12 10:00:00", "moves": True},
        ]
        self.assertEqual(module.summarize(rows), {
            "total": 3, "would_move": 2,
            "by_connection": {"CONN-A": {"count": 1, "earliest": "2026-11-12 10:00:00"},
                              "CONN-B": {"count": 2, "earliest": "2026-11-02 08:00:00"}}})

    def test_nothing_stopped_is_zero(self):
        self.assertEqual(module.summarize([]), {"total": 0, "by_connection": {}, "would_move": 0})

    def test_queued_before_the_switch_gives_count_oldest_and_the_ones_others_would_write(self):
        rows = [{"at": "2026-11-10 09:00:00.5", "moves": True}, {"at": "2026-10-02 08:00:00", "moves": False},
                {"at": "2026-11-12 10:00:00", "moves": True}]
        self.assertEqual(module.summarize_queued(rows), {"total": 3, "oldest": "2026-10-02 08:00:00", "would_move": 2})
        self.assertEqual(module.summarize_queued([]), {"total": 0, "oldest": "", "would_move": 0})

    def test_it_never_writes(self):
        text = PATH.read_text(encoding="utf-8")
        self.assertIn("frappe.db.rollback()", text)
        for call in ("frappe.db.commit", "set_value(", ".insert(", ".save(", "delete_doc", "db_set("):
            self.assertNotIn(call, text)


if __name__ == "__main__":
    unittest.main()
