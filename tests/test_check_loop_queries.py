"""逐行查询闸门（scripts/check-loop-queries.py）：认得出循环里直接查库与调用会查库的本仓函数，标记豁免，基线只许减少。"""
import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_loop_queries", ROOT / "scripts/check-loop-queries.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

SERVICE = '''import frappe
from a.helpers import label as name_of
from a import helpers


def rows(names):
    out = []
    for name in names:
        out.append(frappe.db.get_value("X", name, "title"))
    labels = [name_of(n) for n in names]
    deep = {n: helpers.deep(n) for n in names}
    cached = [frappe.get_cached_value("X", n, "title") for n in names]
    once = [r for r in frappe.get_all("X", filters={"name": ["in", names]}, limit_page_length=50)]
    made = [frappe.get_doc({"doctype": "X", "title": n}) for n in names]
    pure = [helpers.pure(n) for n in names]
    for name in names:
        frappe.db.exists("X", name)  # loop-query: a company has two warehouses
    while names:
        names.pop() and frappe.qb.from_("X").select("name").run()
    return out
'''
HELPERS = '''import frappe


def label(name):
    return frappe.db.get_value("W", name, "title") or name


def deep(name):
    return label(name)


def pure(name):
    return name.upper()
'''


class LoopQueries(unittest.TestCase):
    def repo(self, files):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = pathlib.Path(folder.name)
        for name, text in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (root / "scripts").mkdir(exist_ok=True)
        return root

    def found(self, root):
        check.main(["--repo", str(root), "--list"])
        return [(line, what) for _rel, line, what in check.findings()]

    def test_direct_indirect_and_exempt(self):
        root = self.repo({"apps/a/a/service.py": SERVICE, "apps/a/a/helpers.py": HELPERS,
                          "apps/a/a/tests/test_x.py": "import frappe\nfor n in x:\n    frappe.db.get_value('X', n)\n"})
        self.assertEqual(self.found(root), [(9, "frappe.db.get_value"), (10, "label→frappe.db.get_value"),
                                            (11, "deep→label→frappe.db.get_value"), (19, "frappe.qb….run")])

    def test_the_baseline_may_shrink_but_not_grow(self):
        loop = "import frappe\nfor n in x:\n    frappe.db.get_value('X', n)\n"
        root = self.repo({"apps/a/a/service.py": loop})
        baseline = root / "scripts/loop-queries-baseline.json"
        self.assertEqual(check.main(["--repo", str(root), "--update"]), 0)
        self.assertEqual(json.loads(baseline.read_text()), {"apps/a/a/service.py": 1})
        (root / "apps/a/a/service.py").write_text(loop + "for n in y:\n    frappe.db.exists('X', n)\n")
        self.assertEqual(check.main(["--repo", str(root)]), 1)
        self.assertEqual(check.main(["--repo", str(root), "--update"]), 1)
        self.assertEqual(json.loads(baseline.read_text()), {"apps/a/a/service.py": 1})
        (root / "apps/a/a/service.py").write_text("import frappe\nrows = frappe.get_all('X', limit=50)\n")
        self.assertEqual(check.main(["--repo", str(root)]), 0)
        self.assertEqual(check.main(["--repo", str(root), "--update"]), 0)
        self.assertEqual(json.loads(baseline.read_text()), {})

    def test_the_repository_is_within_its_baseline(self):
        self.assertEqual(check.main(["--repo", str(ROOT)]), 0)


if __name__ == "__main__":
    unittest.main()
