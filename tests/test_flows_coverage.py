"""流程用例覆盖闸（scripts/flows/coverage.py）：改到登记过的入口放行，改到没登记的入口拦下，非入口文件不管，登记表自身对得上。"""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts" / "flows" / "coverage.py"
COVERED = "apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/stocktake-entry.js"


def run(*files):
    return subprocess.run([sys.executable, str(GATE), "--files", *files], cwd=ROOT, capture_output=True, text=True)


def uncovered_entry():
    out = subprocess.run([sys.executable, str(GATE), "--list"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    return next(p for p in out if p.endswith(".js"))


class Coverage(unittest.TestCase):
    def test_registered_entry_passes_and_names_its_flow(self):
        result = run(COVERED, "docs/README.md")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("erp.stocktake.scan", result.stdout)

    def test_unregistered_entry_fails(self):
        path = uncovered_entry()
        result = run(COVERED, path)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(path, result.stdout)
        self.assertNotIn(COVERED + "：", result.stdout)

    def test_non_entry_files_are_ignored(self):
        result = run("docs/README.md", "scripts/flows/run.py", "tests/flows/registry.toml")
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_registry_matches_the_tree(self):
        result = run()
        self.assertEqual(result.returncode, 0, result.stdout)


if __name__ == "__main__":
    unittest.main()
