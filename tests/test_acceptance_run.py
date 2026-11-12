"""验收运行器（scripts/acceptance/run.py）：结果三态；断言失败不重跑、环境类错误只重跑一次并写明；没有断言不算通过；
收尾一定执行、收尾失败算不通过；资源被占记「没测到」；费用到额即停；不在服务器时段不开跑；只登记通过的证据。"""
import importlib.util
import os
import pathlib
import sys
import tempfile
import time
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("acceptance_run", ROOT / "scripts" / "acceptance" / "run.py")
runner = importlib.util.module_from_spec(spec)
sys.modules["acceptance_run"] = runner
spec.loader.exec_module(runner)

SCENARIO = {"id": "acc.demo", "title": "示范", "class": "normal", "env": "B", "resources": ["testpc1", "ledger:69EXP/TEST2"],
            "budget_usd": 0.5, "module": "x.py"}


def module(run, cleanup=None):
    fake = types.SimpleNamespace(run=run)
    if cleanup:
        fake.cleanup = cleanup
    return fake


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name)
        os.environ["PEARNLY_LOCK_DIR"] = str(self.dir / "locks")
        self.addCleanup(os.environ.pop, "PEARNLY_LOCK_DIR", None)

    def go(self, run, cleanup=None, scenario=SCENARIO, **extra):
        return runner.run_one(scenario, self.dir / "run", "win-test", False, module=module(run, cleanup), **extra)

    def test_pass_needs_every_check_to_hold_and_keeps_steps(self):
        def run(ctx):
            with ctx.step("提交", "写进账本"):
                ctx.check("写账状态", "written", "written")
                ctx.check("单号", True, True)
                ctx.facts["docnums"] = ["RR0000100"]
        result = self.go(run)
        self.assertEqual((result["outcome"], result["retried"]), (runner.PASS, False))
        self.assertEqual(result["steps"][0]["checks"][0], {"name": "写账状态", "expected": "written", "actual": "written", "ok": True})
        self.assertEqual(result["facts"], {"docnums": ["RR0000100"]})

    def test_a_scenario_without_assertions_is_not_a_pass(self):
        def run(ctx):
            with ctx.step("打开页面"):
                pass
        self.assertEqual(self.go(run)["outcome"], runner.FAIL)

    def test_failed_assertion_is_never_retried(self):
        calls = []

        def run(ctx):
            calls.append(ctx.attempt)
            with ctx.step("读回"):
                ctx.check("金额", 107.0, 100.0)
                ctx.check("供应商", "S01", "S01")
        result = self.go(run)
        self.assertEqual((result["outcome"], calls, result["retried"]), (runner.FAIL, [1], False))
        self.assertIn("读回 / 金额", result["detail"])

        def hard(ctx):
            calls.append("hard")
            with ctx.step("读回"):
                ctx.require("单号", "RR1", "")
        self.assertEqual(self.go(hard)["outcome"], runner.FAIL)
        self.assertEqual(calls.count("hard"), 1)

    def test_infra_error_is_retried_once_and_marked(self):
        calls = []

        def run(ctx):
            calls.append(ctx.attempt)
            with ctx.step("连测试机"):
                if ctx.attempt == 1:
                    raise runner.Infra("ssh 连不上")
                ctx.check("连上了", True, True)
        result = self.go(run)
        self.assertEqual((result["outcome"], calls, result["retried"]), (runner.PASS, [1, 2], True))
        self.assertEqual(result["retry_reason"], "ssh 连不上")

        def always(ctx):
            calls.append("x")
            raise runner.Infra("超时")
        result = self.go(always)
        self.assertEqual((result["outcome"], calls.count("x")), (runner.FAIL, 2))  # 再失败就是失败

    def test_cleanup_always_runs_and_its_failure_fails_the_scenario(self):
        seen = []

        def run(ctx):
            with ctx.step("写"):
                ctx.check("ok", 1, 2)

        self.go(run, cleanup=lambda ctx: seen.append("cleaned"))
        self.assertEqual(seen, ["cleaned"])

        def fine(ctx):
            with ctx.step("写"):
                ctx.check("ok", 1, 1)

        def broken(ctx):
            raise RuntimeError("账套没能恢复到基线")
        result = self.go(fine, cleanup=broken)
        self.assertEqual(result["outcome"], runner.FAIL)
        self.assertIn("收尾还原失败", result["detail"])

    def test_busy_resource_and_unmet_precondition_are_not_run(self):
        runner.locks.acquire("ledger:69EXP/TEST2", "win-other", "别的实验", 30)
        result = self.go(lambda ctx: self.fail("不该开跑"))
        self.assertEqual(result["outcome"], runner.NOT_RUN)
        self.assertIn("win-other", result["detail"])
        runner.locks.release("ledger:69EXP/TEST2", "win-other")

        def unmet(ctx):
            raise runner.Unmet("基线快照还没建")
        result = self.go(unmet)
        self.assertEqual((result["outcome"], result["detail"]), (runner.NOT_RUN, "基线快照还没建"))
        runner.locks.acquire("testpc1", "win-next", "跑完锁都放了", 5)  # 场景结束后资源是空的

    def test_spend_stops_at_the_registered_cap(self):
        def run(ctx):
            with ctx.step("对话"):
                ctx.spend(0.3, "第一轮")
                ctx.spend(0.3, "第二轮")
                ctx.check("不该到这", True, True)
        result = self.go(run)
        self.assertEqual(result["outcome"], runner.FAIL)
        self.assertIn("OverBudget", result["detail"])
        self.assertEqual(result["spent_usd"], 0.6)

    def test_server_hours_gate_and_dry_run_touch_nothing(self):
        night = time.mktime((2026, 11, 12, 21, 30, 0, 0, 0, -1))
        day = time.mktime((2026, 11, 12, 10, 0, 0, 0, 0, -1))
        self.assertFalse(runner.in_server_hours(night))
        self.assertTrue(runner.in_server_hours(day))
        scenario = dict(SCENARIO, needs_server=True)
        result = self.go(lambda ctx: self.fail("不该开跑"), scenario=scenario, now=night)
        self.assertEqual(result["outcome"], runner.NOT_RUN)
        dry = runner.run_one(SCENARIO, self.dir / "run", "x", True)
        self.assertEqual(dry["outcome"], runner.NOT_RUN)
        self.assertFalse((self.dir / "run").exists())

    def test_only_clean_passes_are_recorded_as_evidence(self):
        path = self.dir / "evidence.toml"
        results = [
            {"scenario": "acc.b", "outcome": runner.PASS, "env": "B", "retried": False, "facts": {"docnums": ["RR1"], "ledger": "TEST2", "x": {"a": 1}}},
            {"scenario": "acc.a", "outcome": runner.FAIL, "env": "B"},
            {"scenario": "acc.c", "outcome": runner.PASS, "env": "B", "unstable": True},
            {"scenario": "acc.d", "outcome": runner.NOT_RUN, "env": "B"},
        ]
        self.assertEqual(runner.record_evidence(results, "abc1234", "run-1", path), ["acc.b"])
        import tomllib
        recorded = tomllib.loads(path.read_text(encoding="utf-8"))["pass"]
        self.assertEqual(len(recorded), 1)
        self.assertEqual({key: recorded[0][key] for key in ("scenario", "sha", "run", "docnums", "ledger")},
                         {"scenario": "acc.b", "sha": "abc1234", "run": "run-1", "docnums": ["RR1"], "ledger": "TEST2"})
        # 再登记别的场景：原有的留着，按 id 排序
        runner.record_evidence([{"scenario": "acc.a", "outcome": runner.PASS, "env": "A", "facts": {}}], "def5678", "run-2", path)
        self.assertEqual([item["scenario"] for item in tomllib.loads(path.read_text(encoding="utf-8"))["pass"]], ["acc.a", "acc.b"])

    def test_unstable_after_two_infra_retries_in_last_ten(self):
        home = self.dir / "home"
        home.mkdir()
        lines = ['{"scenario": "acc.demo", "retried": true}', '{"scenario": "acc.other", "retried": true}',
                 '{"scenario": "acc.demo", "retried": false}']
        (home / "history.jsonl").write_text("\n".join(lines) + "\n")
        self.assertFalse(runner.unstable("acc.demo", home))
        with open(home / "history.jsonl", "a") as out:
            out.write('{"scenario": "acc.demo", "retried": true}\n')
        self.assertTrue(runner.unstable("acc.demo", home))

    def test_soft_checks_are_judged_by_rate_and_hard_ones_every_time(self):
        asked = iter([1, 2, 1])  # 第二次多问了一遍

        def run(ctx):
            with ctx.step("含糊的话"):
                ctx.check("账本里没有新单", 0, 0)
                ctx.check("只问一次", 1, next(asked), soft=True)
        scenario = dict(SCENARIO, repeat=3)
        result = runner.run_repeated(scenario, self.dir / "run", "win-test", False, module=module(run))
        self.assertEqual((result["outcome"], result["soft"], len(result["rounds"])), (runner.PASS, {"含糊的话 / 只问一次": "2/3"}, 3))

        asked = iter([2, 2, 1])
        result = runner.run_repeated(scenario, self.dir / "run-b", "win-test", False, module=module(run))
        self.assertEqual(result["outcome"], runner.FAIL)
        self.assertIn("只过 1/3", result["detail"])

        rounds = []

        def wrote_without_consent(ctx):
            rounds.append(1)
            with ctx.step("含糊的话"):
                ctx.check("没点头不写账", 0, 1 if len(rounds) == 2 else 0)
        result = runner.run_repeated(scenario, self.dir / "run-c", "win-test", False, module=module(wrote_without_consent))
        self.assertEqual((result["outcome"], len(rounds)), (runner.FAIL, 2))  # 硬断言一次不过就是不过，后面不再跑

        def only_soft(ctx):
            with ctx.step("闲聊"):
                ctx.check("语言跟用户", "th", "th", soft=True)
        self.assertEqual(runner.run_repeated(SCENARIO, self.dir / "run-d", "win-test", False, module=module(only_soft))["outcome"], runner.FAIL)

    def test_registered_scenario_module_loads_and_names_its_tables(self):
        scenario = runner.registry_check.Registry(ROOT).scenarios["acc.erp.purchase.express"]
        loaded = runner._load("acc_demo_real", ROOT / scenario["module"])
        tables = loaded.allowed_tables()
        self.assertIn("APTRN", tables)
        self.assertNotIn("ARTRN", tables)
        os.environ.pop("PEARNLY_ACC_ERP_USER", None)
        result = runner.run_one(dict(scenario, needs_server=False), self.dir / "run2", "win-test", False, module=loaded)
        self.assertEqual(result["outcome"], runner.NOT_RUN)  # 没配机器参数：没测到，不碰任何机器
        self.assertIn("PEARNLY_ACC_ERP_USER", result["detail"])


if __name__ == "__main__":
    unittest.main()
