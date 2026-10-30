"""流程用例覆盖闸（scripts/flows/coverage.py）：改到登记过的入口放行，改到没登记的入口拦下，非入口文件不管，登记表自身对得上。"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
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


sys.path.insert(0, str(ROOT / "scripts" / "flows"))
import coverage as gate  # noqa: E402


class Behind(unittest.TestCase):
    """入口背后的模块（主会话 10-29 B 方案）：不在存量清单里又没有流程的拦；清单里的只提醒；清单只许减少。"""

    @classmethod
    def setUpClass(cls):
        covers, exempt = gate.registered()
        cls.reach = gate.behind_entries(gate.import_graph()[0])
        cls.bare = sorted(m for m, e in cls.reach.items() if m not in exempt and not gate.behind_covered(m, e, covers))
        cls.covered = sorted(m for m, e in cls.reach.items() if gate.behind_covered(m, e, covers))

    def run_with(self, stock, *files):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "behind_baseline.txt"
            path.write_text("\n".join(stock) + "\n", encoding="utf-8")
            saved, gate.BASELINE = gate.BASELINE, path
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    code = gate.main(["--files", *files])
            finally:
                gate.BASELINE = saved
        return code, out.getvalue()

    def test_the_committed_baseline_matches_the_tree(self):
        self.assertEqual(gate.baseline(), self.bare)

    def test_a_module_without_flow_outside_the_baseline_fails(self):
        target = self.bare[0]
        code, out = self.run_with([m for m in self.bare if m != target], target)
        self.assertEqual(code, 1, out)
        self.assertIn(target, out)

    def test_a_baseline_module_is_only_reminded(self):
        target = self.bare[0]
        code, out = self.run_with(self.bare, target)
        self.assertEqual(code, 0, out)
        self.assertIn("提醒", out)

    def test_a_covered_module_left_in_the_baseline_fails(self):
        target = self.covered[0]
        code, out = self.run_with(self.bare + [target])
        self.assertEqual(code, 1, out)
        self.assertIn("behind_baseline.txt 删掉", out)

    def test_the_deploy_note_never_fails(self):
        result = subprocess.run([sys.executable, str(GATE), "--deploy-note", "--base", "HEAD", "--head", "HEAD"], cwd=ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
