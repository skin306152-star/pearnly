"""``scripts/check-conflict-catch.py``（B ①，11-26）：只认 ``QueryDeadlockError`` 的 ``except`` 没标原因就失败；元组里含它也算；
测试、补丁与 ``transaction/conflicts.py`` 本身不查；仓库现状全部已标注。

    python3 tests/test_check_conflict_catch.py
"""
import importlib.util
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_conflict_catch", ROOT / "scripts/check-conflict-catch.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class CheckConflictCatchTest(unittest.TestCase):
    def tree(self, files):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = pathlib.Path(folder.name)
        for rel, text in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return root

    def test_unmarked_marked_tuple_and_skipped(self):
        body = "import frappe\ntry:\n    pass\nexcept frappe.QueryDeadlockError:\n    raise\n"
        root = self.tree({
            "apps/a/a/bad.py": body,
            "apps/a/a/tuple.py": "import frappe\ntry:\n    pass\nexcept (frappe.PermissionError, frappe.QueryDeadlockError):\n    raise\n",
            "apps/a/a/ok.py": body.replace("QueryDeadlockError:", "QueryDeadlockError:  # conflict-ok: 带条件的 UPDATE 抢占"),
            "apps/a/a/empty.py": body.replace("QueryDeadlockError:", "QueryDeadlockError:  # conflict-ok:"),
            "apps/a/a/tests/test_x.py": body,
            "apps/a/a/patches/p.py": body,
            "apps/pearnly_core/pearnly_core/transaction/conflicts.py": body,
            "apps/a/a/fine.py": "import frappe\ntry:\n    pass\nexcept Exception as error:\n    raise\n",
        })
        found = {rel: reason for rel, _line, reason in check.findings(root)}
        self.assertEqual(set(found), {"apps/a/a/bad.py", "apps/a/a/tuple.py", "apps/a/a/ok.py", "apps/a/a/empty.py"})
        self.assertEqual(found["apps/a/a/ok.py"], "带条件的 UPDATE 抢占")
        self.assertIsNone(found["apps/a/a/bad.py"])
        self.assertFalse(found["apps/a/a/empty.py"], "原因不许空")

    def test_registry_marks_by_file_and_function_and_stale_entries_fail(self):
        body = "import frappe\ndef check():\n    try:\n        pass\n    except frappe.QueryDeadlockError:\n        raise\n"
        root = self.tree({"apps/a/a/api.py": body,
                          "scripts/conflict-catch-reviewed.tsv": "# 注释\napps/a/a/api.py\tcheck\t只读\napps/a/a/api.py\tgone\t旧的\n"})
        self.assertEqual([reason for _rel, _line, reason in check.findings(root)], ["只读"])
        self.assertEqual(check.stale_registry(root), [("apps/a/a/api.py", "gone")])

    def test_repository_has_no_unmarked_catch(self):
        self.assertEqual(check.main([]), 0)


if __name__ == "__main__":
    unittest.main()
