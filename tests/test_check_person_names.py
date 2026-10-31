"""人名显示闸门（scripts/check-person-names.py）：认得出自己取人名的写法，共用显示名本身与 person-name 标记豁免，基线只许减少。"""
import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_person_names", ROOT / "scripts/check-person-names.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class PersonNames(unittest.TestCase):
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
                "who = frappe.utils.get_fullname(user)\n"
                "name = frappe.db.get_value('User', user, 'full_name')\n"
                "rows = frappe.get_all('User', fields=['name', \"full_name\"])\n"
                "label = row.full_name or row.name\n"
                "doc = {'full_name': username}  # person-name: 建号时写的初始姓名\n"
                "# 注释里提到 full_name 不算\n"
                "who = names.display_name(user)\n"),
            "apps/a/a/public/desk/page.js": (
                "const who = frappe.user_info(user).fullname;\n"
                "const me = frappe.session.user_fullname;\n"
                "const sender = frappe.user.full_name(); // person-name: 框架发邮件\n"
                "const shown = row.cashier_name;\n"),
            "apps/pearnly_core/pearnly_core/account/names.py": "frappe.utils.get_fullname(user)\n",
            "apps/a/a/test_service.py": "frappe.utils.get_fullname(user)\n",
            "apps/a/a/patches/v1.py": "frappe.db.get_value('User', u, 'full_name')\n",
        })
        found = check.scan(root)
        self.assertEqual(found, {"apps/a/a/service.py": [2, 3, 4, 5], "apps/a/a/public/desk/page.js": [1, 2]})

    def test_baseline_only_shrinks(self):
        root = self.repo({"apps/a/a/service.py": "x = frappe.utils.get_fullname(u)\ny = row.full_name\n"})
        baseline = root / "scripts/person-names-baseline.json"
        baseline.write_text(json.dumps({"apps/a/a/service.py": 1}), encoding="utf-8")
        self.assertEqual(check.main(["--repo", str(root)]), 1)
        self.assertEqual(check.main(["--repo", str(root), "--update"]), 1)  # 增加时拒绝改基线
        baseline.write_text(json.dumps({"apps/a/a/service.py": 3}), encoding="utf-8")
        self.assertEqual(check.main(["--repo", str(root)]), 0)
        self.assertEqual(check.main(["--repo", str(root), "--update"]), 0)
        self.assertEqual(json.loads(baseline.read_text(encoding="utf-8")), {"apps/a/a/service.py": 2})

    def test_the_repository_stays_within_its_baseline(self):
        self.assertEqual(check.main(["--repo", str(ROOT)]), 0)


if __name__ == "__main__":
    unittest.main()
