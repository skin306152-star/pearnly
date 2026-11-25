"""代录列出被直写抢先的验收场景（scripts/acceptance/scenarios/erp_operator_listed.py）的判定：站点与账套换成替身，只验场景自己——
正确世界通过；清单里凭空消失、单号对不上、写入计划没跳过、交单又交了、多一件写账、账本多两张都判不通过；列出前就被写掉记没测到。"""
import copy
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


HELD = {"ok": True, "company": "CO-1", "doctype": "ERP Sales Document", "doc": "SD-1", "doc_id": "S:SD-1", "number": "SI-1",
        "state": {"ledger_state": "queued"}, "jobs": 0}
GOOD = {"ok": True, "listed": ["S:SD-1"], "listed_gone": [], "state": {"ledger_state": "written", "ledger_docnum": "IV0000030"},
        "job": {"request_key": "erp:sales:SD-1@x", "status": "success"}, "after_listed": [],
        "after_gone": [{"doc_id": "S:SD-1", "state": "saved", "docnum": "IV0000030", "moves": []}],
        "model_waiting": [], "model_entered": [{"doc_id": "S:SD-1", "docnum": "IV0000030"}], "planned": [],
        "skipped": [{"doc_id": "S:SD-1", "reason": "already_entered", "docnum": "IV0000030"}],
        "hand_in_refused": "Invalid request", "jobs": 1}


class World:
    def __init__(self, listed, grew=1):
        self.listed, self.grew, self.calls = listed, grew, []

    def run(self, product, script, name, params, timeout=600):
        self.calls.append(script.name)
        out = HELD if script.name == "erp_push_held.py" else self.listed
        return "ACC " + json.dumps(out, ensure_ascii=False)

    def book(self):
        world = self
        return types.SimpleNamespace(
            probe=lambda: {"ok": True, "busy": []}, verify=lambda snap: {"ok": True}, restore=lambda snap: {"ok": True},
            copy=lambda target: {"ok": True},
            diff=lambda before, after, allow=None, out=None, show=None: {"ok": True, "stray": [], "tables": {"ARTRN.DBF": [0, world.grew], "ARTRN": [0, world.grew]}})


class OperatorListedScenarioTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        keys = {"PEARNLY_LOCK_DIR": self.folder.name + "/locks", "PEARNLY_ACC_ERP_USER": "acc.erp@maclab.test",
                "PEARNLY_ACC_ERP_TAX": "1234567890123", "PEARNLY_ACC_BASELINE": r"C:\PearnlyAcceptance\baselines\TEST2@2026-11-16"}
        self.old = {key: os.environ.get(key) for key in keys}
        os.environ.update(keys)
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in self.old.items()])
        self.runner = load("acceptance_run_for_listed", "scripts/acceptance/run.py")
        sys.modules.pop("acc_erp_chain", None)

    def go(self, listed, grew=1):
        world = World(listed, grew)
        targets = types.SimpleNamespace(site=lambda target: world, ledger=lambda host, path: world.book(),
                                        windows=lambda host: types.SimpleNamespace(work="C:/PearnlyAcceptance"))
        scenario = {"id": "acc.erp.operator.listed.direct", "module": "scripts/acceptance/scenarios/erp_operator_listed.py",
                    "resources": [], "budget_usd": 0}
        return self.runner.run_one(scenario, pathlib.Path(self.folder.name) / "run", "win-test", False, targets=targets), world

    def broken(self, key, value):
        out = copy.deepcopy(GOOD)
        out[key] = value
        return out

    def test_a_correct_world_passes(self):
        result, world = self.go(GOOD)
        self.assertEqual((result["outcome"], result["detail"]), (self.runner.PASS, ""))
        self.assertEqual(world.calls, ["erp_push_held.py", "erp_operator_listed.py"])

    def test_each_broken_world_fails(self):
        cases = [(self.broken("after_gone", []), "成功 · 单号"),
                 (self.broken("after_listed", ["S:SD-1"]), "不再当成待录"),
                 (self.broken("model_entered", []), "给模型"),
                 (self.broken("skipped", []), "跳过它"),
                 (self.broken("planned", ["S:SD-1"]), "写入计划里没有它"),
                 ({**self.broken("hand_in_refused", ""), "hand_in": {"results": [{"doc_id": "S:SD-1"}]}}, "交单不交它"),
                 (self.broken("jobs", 2), "只有一件写账"),
                 (self.broken("job", {"request_key": "erp:operator:x"}), "直写件"),
                 (self.broken("state", {"ledger_state": "queued"}), "直写写成")]
        for out, words in cases:
            result, _ = self.go(out)
            self.assertEqual(result["outcome"], self.runner.FAIL, words)
            self.assertIn(words, result["detail"])
        result, _ = self.go(GOOD, grew=2)
        self.assertEqual(result["outcome"], self.runner.FAIL)
        self.assertIn("ARTRN 只多一行", result["detail"])

    def test_written_before_it_was_listed_is_not_run(self):
        result, _ = self.go(self.broken("listed", []))
        self.assertEqual(result["outcome"], self.runner.NOT_RUN)


if __name__ == "__main__":
    unittest.main()
