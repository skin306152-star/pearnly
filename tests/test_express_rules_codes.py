"""模型给的编码换回主档原样编码（11-15 真机：收款客户 ``นาย\\xa0ทดสอบ`` 被模型复述成普通空格，写进 Express 被拒）。

不连库、不依赖 Frappe：带不换行空格、首尾空格、连续空格、全角的编码都换回主档原样；只差空白的几件档交人选；不像的不猜。
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_core.express_rules.contracts import codes  # noqa: E402

NBSP = "นาย ทดสอบ"


class ResolveTest(unittest.TestCase):
    def test_exact_keeps_master_code(self):
        self.assertEqual(codes.resolve(NBSP, [NBSP]), {"status": codes.EXACT, "code": NBSP, "matches": [NBSP]})

    def test_model_plain_space_maps_back_to_nbsp(self):
        found = codes.resolve("นาย ทดสอบ", [NBSP])
        self.assertEqual((found["status"], found["code"]), (codes.LOOSE, NBSP))
        self.assertIn(" ", found["code"])

    def test_edge_double_and_fullwidth_spaces(self):
        for given in (" นาย ทดสอบ ", "นาย  ทดสอบ", "นาย　ทดสอบ", "นาย\tทดสอบ", "นาย​ ทดสอบ"):
            with self.subTest(given=repr(given)):
                self.assertEqual(codes.resolve(given, [NBSP])["code"], NBSP)

    def test_fullwidth_letters_and_digits(self):
        self.assertEqual(codes.resolve("ＡＲ－００１", ["AR-001"])["code"], "AR-001")
        self.assertEqual(codes.resolve("AR-001", ["ＡＲ－００１"])["code"], "ＡＲ－００１")

    def test_master_code_with_double_space_kept_verbatim(self):
        master = "C  01"
        self.assertEqual(codes.resolve("C 01", [master])["code"], master)

    def test_exact_wins_over_loose_sibling(self):
        plain = "นาย ทดสอบ"
        self.assertEqual(codes.resolve(plain, [NBSP, plain])["status"], codes.EXACT)
        self.assertEqual(codes.resolve(plain, [NBSP, plain])["code"], plain)

    def test_several_loose_siblings_go_to_the_user(self):
        found = codes.resolve("นาย ทดสอบ", [NBSP, "นาย　ทดสอบ"])
        self.assertEqual(found["status"], codes.SEVERAL)
        self.assertEqual(found["code"], "")
        self.assertEqual(found["matches"], [NBSP, "นาย　ทดสอบ"])

    def test_case_is_not_folded(self):
        self.assertEqual(codes.resolve("ar001", ["AR001"])["status"], codes.NONE)
        self.assertEqual(codes.resolve("ar001", ["AR001", "ar001"])["code"], "ar001")

    def test_none_and_empty(self):
        self.assertEqual(codes.resolve("X", ["Y"])["status"], codes.NONE)
        self.assertEqual(codes.resolve("", [""])["status"], codes.NONE)
        self.assertEqual(codes.resolve("", [])["status"], codes.NONE)

    def test_duplicates_in_candidates_count_once(self):
        self.assertEqual(codes.resolve("นาย ทดสอบ", [NBSP, NBSP])["code"], NBSP)


class PatternTest(unittest.TestCase):
    def test_whitespace_becomes_wildcard(self):
        self.assertEqual(codes.like_pattern("นาย  ทดสอบ"), "นาย%ทดสอบ")

    def test_wildcards_escaped(self):
        self.assertEqual(codes.like_pattern("A_1%2"), "A\\_1\\%2")
        self.assertEqual(codes.like_pattern("   "), "")

    def test_key(self):
        self.assertEqual(codes.key("﻿ ＡＢ  c "), "AB c")


if __name__ == "__main__":
    unittest.main()
