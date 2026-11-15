"""工作流文件闸（``scripts/check-workflows.py``）：GitHub 会拒收的写法被拦，正常写法照过，仓库里现有的工作流全过。

11-15 的实例：步骤名里一个「冒号 + 空格」让 development.yml 整份作废，分支 CI 一步都没跑、本地全闸门没查出。

    python3 tests/test_check_workflows.py
"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_workflows", ROOT / "scripts/check-workflows.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

GOOD = """name: Development checks
on:
  push:
    branches: ["pearnly/**"]
jobs:
  checks:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - name: Guide engine (decided steps, read-back facts)
        run: python3 tests/test_cc_guide_run.py
      - name: "Quoted: a colon is fine inside quotes"
        run: |
          echo "a: b"   # 块里的 shell 不查
          python3 x.py --flag=a:b
      - name: Folded
        run: >
          echo one: two
      - name: Comment after a value # note: here
        run: python3 y.py
"""


class WorkflowGateTests(unittest.TestCase):
    def test_the_good_sample_passes(self):
        self.assertEqual(gate.problems("good.yml", GOOD), [])

    def test_a_colon_and_space_in_an_unquoted_step_name_is_caught_with_its_line(self):
        bad = GOOD.replace("Guide engine (decided steps", "Guide engine (shared engine: decided steps")
        self.assertEqual(gate.problems("bad.yml", bad),
                         ["bad.yml:10: unquoted value of name contains ': ' (quote it or rephrase)"])

    def test_a_value_ending_in_a_colon_a_reserved_first_character_and_a_tab_are_caught(self):
        bad = GOOD.replace("Folded\n", "Folded:\n").replace("python3 y.py", "*not an alias").replace(
            "      - uses: actions/checkout@v4", "\t- uses: actions/checkout@v4")
        found = gate.problems("bad.yml", bad)
        self.assertEqual(len(found), 3, found)
        self.assertTrue(any("tab" in item for item in found))
        self.assertTrue(any("starts with '*'" in item for item in found))

    def test_every_workflow_in_the_repository_passes(self):
        self.assertEqual(gate.main([]), 0)


if __name__ == "__main__":
    unittest.main()
