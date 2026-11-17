"""第二期场景的跑单（scripts/acceptance/first_run.py）：顺序与分组对得上登记、花钱的格默认跳过、资源被占 / 缺配置就不跑、
不通过即停、一屏结果每格一行。只测它自己的编排，场景都是替身。"""
import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FirstRunTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.old = os.environ.get("PEARNLY_LOCK_DIR")
        os.environ["PEARNLY_LOCK_DIR"] = self.folder.name + "/locks"
        self.addCleanup(lambda: os.environ.pop("PEARNLY_LOCK_DIR", None) if self.old is None else os.environ.__setitem__("PEARNLY_LOCK_DIR", self.old))
        self.first = load("acceptance_first_run", "scripts/acceptance/first_run.py")
        self.run = self.first.load_run()
        self.scenarios = self.run.registry_check.Registry(ROOT).scenarios

    def test_every_planned_scenario_is_registered_and_every_second_phase_scenario_is_planned(self):
        planned = [scenario_id for ids in self.first.GROUPS.values() for scenario_id in ids]
        self.assertEqual(len(planned), len(set(planned)))
        self.assertEqual(sorted(planned), sorted(self.scenarios))   # 登记里的每一条都在某一组里，没有登记外的
        self.assertEqual(self.first.GROUPS["erp"][0], "acc.erp.company.connect.fresh")   # 刚重置的站点只有一次机会：排最前

    def test_paid_scenarios_are_skipped_unless_asked_for(self):
        chosen, skipped, problems = self.first.plan(self.scenarios, ["chat"])
        self.assertEqual((chosen, problems), ([], []))
        self.assertEqual(len(skipped), 7)
        self.assertIn("--paid", skipped[0]["detail"])
        chosen, skipped, _ = self.first.plan(self.scenarios, ["chat"], paid=True)
        self.assertEqual((len(chosen), skipped), (7, []))
        chosen, _, _ = self.first.plan(self.scenarios, ["erp"], start="acc.erp.sales.credit.express")
        self.assertEqual(chosen[0]["id"], "acc.erp.sales.credit.express")
        self.assertIn("不在这一趟里", self.first.plan(self.scenarios, ["erp"], start="acc.erp.mrerp.twice")[2][0])

    def test_nothing_runs_while_a_resource_is_held_or_the_setup_is_incomplete(self):
        chosen, _, _ = self.first.plan(self.scenarios, ["erp"])
        env = {"PEARNLY_ACC_SITE_TARGET": "maclab", "PEARNLY_ACC_ERP_USER": "u", "PEARNLY_ACC_ERP_TAX": "1", "PEARNLY_ACC_BASELINE": "b"}
        resources, blockers = self.first.preflight(self.run, chosen, ["erp"], "win-a", environ=env)
        self.assertEqual(resources, ["lab:pearnly-maclab-erp", "ledger:69EXP/TEST2", "testpc2"])   # 服务端在 Mac 上：不占测试机1
        self.assertEqual([item for item in blockers if "被占用" in item or "环境变量" in item or "--holder" in item], [])
        self.run.locks.acquire("testpc2", "someone-else", "真机", 30)
        _, blockers = self.first.preflight(self.run, chosen, ["erp"], "win-a", environ=env)
        self.assertTrue(any("被占用" in item and "someone-else" in item for item in blockers))
        _, blockers = self.first.preflight(self.run, chosen, ["erp"], "", environ={})
        self.assertTrue(any("PEARNLY_ACC_ERP_USER" in item for item in blockers))
        self.assertTrue(any("--holder" in item for item in blockers))
        lab, _ = self.first.preflight(self.run, chosen, ["erp"], "win-a", environ={**env, "PEARNLY_ACC_SITE_TARGET": "lab"})
        self.assertIn("testpc1", lab)
        night = self.first.preflight(self.run, chosen, ["erp"], "win-a", now=__import__("time").mktime((2026, 11, 16, 22, 0, 0, 0, 0, -1)),
                                     environ=env)[1]
        self.assertTrue(any("08:00–20:40" in item for item in night))

    def test_a_failure_stops_the_run_and_every_cell_gets_a_line(self):
        run, outcomes = self.run, {"a": self.run.PASS, "b": self.run.NOT_RUN, "c": self.run.FAIL, "d": self.run.PASS}
        calls = []

        class Fake:
            PASS, FAIL, NOT_RUN = run.PASS, run.FAIL, run.NOT_RUN

            @staticmethod
            def run_repeated(scenario, run_dir, holder, dry, targets):
                calls.append(scenario["id"])
                return {"scenario": scenario["id"], "outcome": outcomes[scenario["id"]], "seconds": 1.0,
                        "detail": "" if outcomes[scenario["id"]] == run.PASS else "原因", "spent_usd": 0.25 if scenario["id"] == "a" else 0}

        chosen = [{"id": name} for name in "abcd"]
        results = self.first.execute(Fake, chosen, pathlib.Path(self.folder.name), "win-a", None, say=lambda _text: None)
        self.assertEqual(calls, ["a", "b", "c"])   # 没测到不停，不通过才停
        self.assertEqual([item["outcome"] for item in results], [run.PASS, run.NOT_RUN, run.FAIL, run.NOT_RUN])
        self.assertIn("前面不通过", results[3]["detail"])
        text = self.first.screen(Fake, results, [{"scenario": "e", "detail": "会花模型费（上限 1.0 美元），没给 --paid"}], ["testpc2"], 125)
        self.assertIn("1 通过、1 不通过、3 没测到；模型花费 0.25 美元；用时 2.1 分钟", text)
        self.assertEqual(len(text.splitlines()), 2 + 5)
        self.assertIn("没给 --paid", text)
        calls.clear()
        self.first.execute(Fake, chosen, pathlib.Path(self.folder.name), "win-a", None, stop_on_not_run=True, say=lambda _text: None)
        self.assertEqual(calls, ["a", "b"])


if __name__ == "__main__":
    unittest.main()
