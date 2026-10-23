"""分店号（用户 2026-09-27：旧项目分店票记到总店名下）：票面分店号怎么整理后交给小助手。"""
import sys
import unittest
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "apps" / "pearnly_cowork"))
sys.path.insert(0, str(root / "apps" / "pearnly_core"))

from pearnly_core.express_rules.push.field_clean import clean_branch_no  # noqa: E402


class BranchNumberTest(unittest.TestCase):
    def test_printed_branch_numbers_become_five_digits(self):
        for raw, expected in (("18", "00018"), ("สาขาที่ 18", "00018"), ("สาขา 00018", "00018"),
                              ("Branch 18", "00018"), ("00000", "00000"), ("สำนักงานใหญ่ 00000", "00000")):
            self.assertEqual(clean_branch_no(raw), expected, raw)

    def test_head_office_printed_in_words_is_00000(self):
        for raw in ("สำนักงานใหญ่", "(สำนักงานใหญ่)", "Head Office", "HEAD OFFICE", "HQ", "สนญ."):
            self.assertEqual(clean_branch_no(raw), "00000", raw)

    def test_not_printed_stays_empty_so_it_is_never_forced_to_head_office(self):
        for raw in ("", None, "-", "สาขา", "123456"):
            self.assertEqual(clean_branch_no(raw), "", raw)


if __name__ == "__main__":
    unittest.main()
