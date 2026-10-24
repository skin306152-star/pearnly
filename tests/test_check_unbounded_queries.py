"""大数据量闸门（scripts/check-unbounded-queries.py）：认得出无上限查询与前端整批取回，bounded 标记豁免，基线只许减少。"""
import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_unbounded", ROOT / "scripts/check-unbounded-queries.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class UnboundedQueries(unittest.TestCase):
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

    def test_python_and_page_patterns(self):
        root = self.repo({
            "apps/a/a/service.py": (
                "import frappe\n"
                "rows = frappe.get_all('X', fields=['name'])\n"
                "rows = frappe.get_list('X', limit_page_length=0)\n"
                "rows = frappe.db.get_all('X', limit=None)\n"
                "rows = frappe.get_list('X', limit_page_length=20)\n"
                "rows = frappe.get_all('X', filters={'parent': p})  # bounded: one document's rows\n"
                "rows = frappe.get_list('X', **kwargs)\n"),
            "apps/a/a/public/desk/page.js": (
                "p.call(api, {limit: 0});\n"
                "const sorted = p.sort_rows(rows, {});\n"
                "p.call(api, {limit: page_length});\n"
                "p.call(api, {limit:0}); // bounded: a company has a few warehouses\n"
                "// p.sort_rows( in a comment\n"),
            "apps/a/a/tests/test_x.py": "import frappe\nfrappe.get_all('X')\n",
        })
        self.assertEqual(check.scan(root), {"apps/a/a/public/desk/page.js": [1, 2],
                                            "apps/a/a/service.py": [2, 3, 4]})

    def test_the_baseline_may_shrink_but_not_grow(self):
        root = self.repo({"apps/a/a/service.py": "import frappe\nfrappe.get_all('X')\nfrappe.get_all('Y')\n"})
        baseline = root / "scripts/unbounded-queries-baseline.json"
        self.assertEqual(check.main(["--repo", str(root), "--update"]), 0)
        self.assertEqual(json.loads(baseline.read_text()), {"apps/a/a/service.py": 2})
        (root / "apps/a/a/service.py").write_text("import frappe\nfrappe.get_all('X')\nfrappe.get_all('Y')\nfrappe.get_all('Z')\n")
        self.assertEqual(check.main(["--repo", str(root)]), 1)
        self.assertEqual(check.main(["--repo", str(root), "--update"]), 1)
        self.assertEqual(json.loads(baseline.read_text()), {"apps/a/a/service.py": 2})
        (root / "apps/a/a/service.py").write_text("import frappe\nfrappe.get_all('X', limit=50)\n")
        self.assertEqual(check.main(["--repo", str(root)]), 0)
        self.assertEqual(check.main(["--repo", str(root), "--update"]), 0)
        self.assertEqual(json.loads(baseline.read_text()), {})

    def test_the_repository_is_within_its_baseline(self):
        self.assertEqual(check.main([]), 0)


if __name__ == "__main__":
    unittest.main()
