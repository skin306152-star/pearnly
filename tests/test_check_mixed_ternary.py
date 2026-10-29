#!/usr/bin/env python3
"""三元混用 || / ?? 检查的规则自测。"""
import importlib.util
import pathlib
import unittest

SPEC = importlib.util.spec_from_file_location("mixed", pathlib.Path(__file__).resolve().parents[1] / "scripts/check-mixed-ternary.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class MixedTernary(unittest.TestCase):
    def test_flags_or_in_the_else_branch(self):
        self.assertTrue(M.problems('String(register ? row.serial_no : row.serial || "").trim()'))
        self.assertTrue(M.problems("const a = x ? y : z ?? w;"))

    def test_parenthesised_and_unrelated_code_pass(self):
        for line in ('String((register ? row.serial_no : row.serial) || "").trim()',
                     "const a = x ? y : (z || w);",
                     "const b = (x || y) ? 1 : 2;",
                     "const c = obj?.a ?? 1;",
                     "call({ a: b || c, d: e ? f : g });",
                     "const s = \"a ? b : c || d\";  // a ? b : c || d"):
            self.assertEqual(M.problems(line), [], line)

    def test_repository_is_clean(self):
        self.assertEqual(M.main([]), 0)


if __name__ == "__main__":
    unittest.main()
