"""测试覆盖闸门（`scripts/check-test-coverage.py`）自己的测试：在临时仓库里摆出各种情况，看它拦不拦。"""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_test_coverage", ROOT / "scripts" / "check-test-coverage.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

WORKFLOW = """name: Development checks
jobs:
  checks:
    steps:
      - name: one
        run: python3 tests/test_direct.py
      - name: block
        run: |
          python3 scripts/check-lane.py
          node tests/other.cjs
"""


class Coverage(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        for folder in (".github/workflows", "tests", "scripts/tests"):
            (self.root / folder).mkdir(parents=True)
        (self.root / ".github/workflows/development.yml").write_text(WORKFLOW)
        (self.root / "scripts/ci-candidate-checks.sh").write_text('offline_py "$IMAGE" /candidate/tests/test_candidate.py\n')
        (self.root / "scripts/check-lane.py").write_text('TESTS = ["tests/test_via_script.py"]\n')
        for name in ("test_direct.py", "test_candidate.py", "test_via_script.py"):
            self.touch(name)

    def touch(self, name, text="import unittest\n"):
        (self.root / "tests" / name).write_text(text)

    def tables(self, exempt="", baseline=""):
        (self.root / "scripts/tests/not-in-ci.tsv").write_text(exempt)
        (self.root / "scripts/tests/orphan-baseline.txt").write_text(baseline)

    def check(self, base="origin/main"):
        return gate.check(self.root, base=base)

    def test_workflow_candidate_and_listed_script_count_as_run(self):
        self.tables()
        problems, state = self.check()
        self.assertEqual(problems, [])
        self.assertEqual(state["ran"], {"test_direct.py": "development.yml", "test_candidate.py": "ci-candidate-checks.sh",
                                        "test_via_script.py": "scripts/check-lane.py"})

    def test_a_new_test_nobody_runs_is_blocked(self):
        self.tables()
        self.touch("test_forgotten.py")
        problems, _ = self.check()
        self.assertEqual(len(problems), 1)
        self.assertIn("test_forgotten.py", problems[0])

    def test_imported_only_does_not_count(self):
        """被别的测试 import 借样例不算执行：它自己的用例没有跑。"""
        self.tables()
        self.touch("test_helper.py")
        self.touch("test_direct.py", "from test_helper import SAMPLE\n")
        problems, _ = self.check()
        self.assertTrue(any("test_helper.py" in line for line in problems))

    def test_exemption_needs_a_reason_and_must_stay_true(self):
        self.touch("test_tool.py")
        self.tables(exempt="test_tool.py\t\n")
        self.assertTrue(any("没写原因" in line for line in self.check()[0]))
        self.tables(exempt="test_tool.py\t要旧仓源码\ntest_direct.py\t多余\ntest_gone.py\t已删\n")
        problems = self.check()[0]
        self.assertTrue(any("test_direct.py" in line and "删掉这一行" in line for line in problems))
        self.assertTrue(any("test_gone.py" in line and "已不存在" in line for line in problems))
        self.assertFalse(any("test_tool.py" in line for line in problems))

    def test_baseline_only_shrinks(self):
        self.touch("test_old.py")
        self.touch("test_other.py")
        self.tables(baseline="# comment\ntest_old.py\n")
        git = lambda *args: subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=self.root, check=True, capture_output=True)  # noqa: E731
        git("init", "-q")
        git("-c", "user.email=t@example.com", "-c", "user.name=t", "add", "-A")
        git("-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "base")
        git("tag", "base")
        self.assertEqual([line for line in self.check("base")[0] if "test_old.py" in line], [])
        self.tables(baseline="test_old.py\ntest_other.py\n")
        self.assertTrue(any("test_other.py" in line and "新加" in line for line in self.check("base")[0]))
        # 已经被 CI 跑到的还留在基线：要删掉
        self.tables(baseline="test_old.py\ntest_direct.py\n")
        self.assertTrue(any("test_direct.py" in line and "基线只许减少" in line for line in self.check("base")[0]))

    def test_this_repository_passes(self):
        problems, state = gate.check(ROOT)
        self.assertEqual(problems, [])
        self.assertIn("test_check_test_coverage.py", state["ran"])


if __name__ == "__main__":
    unittest.main()
