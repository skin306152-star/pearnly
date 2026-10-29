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

    def test_a_file_outside_the_repository_is_reported_by_its_own_path(self):
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
            handle.write("const a = x ? y : z || w;\n")
        self.assertEqual(M.main([handle.name]), 1)
        pathlib.Path(handle.name).unlink()

    def test_repository_is_within_its_baseline(self):
        self.assertEqual(M.main([]), 0)

    def test_baseline_only_shrinks(self):
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory(dir=M.ROOT) as folder:
            page = pathlib.Path(folder) / "page.js"
            page.write_text("const a = x ? y : z || w;\nconst b = x ? y : z || w;\n", encoding="utf-8")
            name = str(page.relative_to(M.ROOT))
            table = pathlib.Path(folder) / "baseline.tsv"
            with mock.patch.object(M, "files", lambda: [page]), mock.patch.object(M, "BASELINE", table):
                table.write_text(f"{name}\t2\n", encoding="utf-8")
                self.assertEqual(M.main([]), 0)
                table.write_text(f"{name}\t1\n", encoding="utf-8")
                self.assertEqual(M.main([]), 1)  # 超过基线：新增的照样拦
                table.write_text(f"{name}\t3\n", encoding="utf-8")
                self.assertEqual(M.main([]), 1)  # 少于基线：要把基线一起调低


if __name__ == "__main__":
    unittest.main()
