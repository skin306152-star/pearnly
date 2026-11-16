"""等接线的流程用例（`tests/flows/waiting.toml`，11-16）：每条的模块在、有 prepare / steps / judge / verify / cleanup，步骤能按参数拼出来，
id 不与 `registry.toml` 重复（搬过去时整条搬，不是两边各一份）。不连实验栈。

    python3 tests/test_flows_waiting.py
"""
import importlib.util
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/flows"))
WAITING = tomllib.loads((ROOT / "tests/flows/waiting.toml").read_text(encoding="utf-8"))["flow"]
REGISTERED = {flow["id"] for flow in tomllib.loads((ROOT / "tests/flows/registry.toml").read_text(encoding="utf-8"))["flow"]}
PARAMS = {"tag": "t1", "company": "CO-1", "t8": "CONN|T8", "connection": "CONN", "customer": "C1"}


class WaitingFlows(unittest.TestCase):
    def test_each_waiting_flow_is_complete_and_not_also_registered(self):
        self.assertTrue(WAITING)
        for flow in WAITING:
            with self.subTest(flow=flow["id"]):
                self.assertNotIn(flow["id"], REGISTERED)
                self.assertTrue(flow.get("waits"))
                path = ROOT / flow["module"]
                spec = importlib.util.spec_from_file_location(path.stem, path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                for name in ("prepare", "steps", "judge", "verify", "cleanup"):
                    self.assertTrue(callable(getattr(module, name, None)), name)
                built = module.steps(PARAMS)
                self.assertTrue(all(step.get("name") and step.get("js") for step in built))
                self.assertTrue(module.judge(PARAMS, []))
                for cover in flow["covers"]:
                    self.assertTrue((ROOT / cover).exists(), cover)


if __name__ == "__main__":
    unittest.main()
