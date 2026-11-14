"""主档按公司可见口径闸门（`scripts/check-master-scope.py`）：新文件自己写 express_company 条件就拦，口径的家与存量基线不拦。"""
from pathlib import Path
import runpy
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
check = runpy.run_path(str(ROOT / "scripts/check-master-scope.py"))


class MasterScope(unittest.TestCase):
    def test_current_code_is_within_the_baseline(self):
        found = check["scan"]()
        baseline = __import__("json").loads((ROOT / "scripts/master-scope-baseline.json").read_text(encoding="utf-8"))
        for path, lines in found.items():
            self.assertLessEqual(len(lines), baseline.get(path, 0), path)

    def test_a_new_entry_writing_its_own_condition_is_found_and_the_home_is_not(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app = root / "apps/pearnly_erp_portal/pearnly_erp_portal"
            (app / "masters").mkdir(parents=True)
            (app / "inventory").mkdir()
            (app / "masters/access.py").write_text('OWNER = "express_company"\n')
            (app / "inventory/new_page.py").write_text('rows = get_all("ERP Product", filters={"express_company": c})\n')
            scan = check["scan"]
            scan.__globals__.update(ROOT=root, APP=app)
            try:
                found = scan()
            finally:
                scan.__globals__.update(ROOT=ROOT, APP=ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal")
        self.assertEqual(found, {"apps/pearnly_erp_portal/pearnly_erp_portal/inventory/new_page.py": [1]})


if __name__ == "__main__":
    unittest.main()
