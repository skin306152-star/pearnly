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

    def test_failure_keeps_server_values_and_page_scene_before_cleanup(self):
        """标准 §5.3：不通过时自动留登记过的现场（服务端原值、页面现场），在收尾还原之前；通过、没测到时不取。"""
        order = []

        def run(ctx):
            ctx.on_failure("push-record", lambda: order.append("collect") or {"name": "PUSH-1", "ledger_state": "queued"})
            ctx.on_failure("page.png", lambda: b"\x89PNG")
            ctx.on_failure("console", lambda: 1 / 0)
            with ctx.step("读回"):
                ctx.check("写账状态", "written", "queued")
        result = self.go(run, cleanup=lambda ctx: order.append("cleanup"))
        folder = pathlib.Path(result["evidence"]) / "failure"
        self.assertEqual((result["outcome"], order), (runner.FAIL, ["collect", "cleanup"]))
        self.assertEqual(result["failure_evidence"], ["failure/push-record.json", "failure/page.png", "failure/console.error.txt"])
        self.assertIn('"ledger_state": "queued"', (folder / "push-record.json").read_text(encoding="utf-8"))
        self.assertEqual((folder / "page.png").read_bytes(), b"\x89PNG")
        self.assertIn("ZeroDivisionError", (folder / "console.error.txt").read_text(encoding="utf-8"))

        def crashed(ctx):
            ctx.on_failure("doc", lambda: {"name": "PO-1"})
            raise RuntimeError("页面没出来")
        self.assertEqual(self.go(crashed)["failure_evidence"], ["failure/doc.json"])

        called = []

        def fine(ctx):
            ctx.on_failure("doc", lambda: called.append(1))
            with ctx.step("读回"):
                ctx.check("写账状态", "written", "written")

        def unmet(ctx):
            ctx.on_failure("doc", lambda: called.append(1))
            raise runner.Unmet("没配账号")
        self.assertNotIn("failure_evidence", self.go(fine))
        self.assertNotIn("failure_evidence", self.go(unmet))
        self.assertEqual(called, [])

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


class CoreLanesTest(unittest.TestCase):
    """核心集：换 CC 桌面版那台时锁跟着换；按资源分路（共用任何一项归同一路）；两路真的同时跑、结果都收齐；报告逐条三态。"""

    EXPRESS = {"id": "acc.a", "resources": ["testpc2", "testpc2-desktop", "ledger:69EXP/TEST2"],
               "site_resources": {"lab": ["testpc1"], "maclab": ["lab:erp"]}}
    EXPRESS2 = {"id": "acc.b", "resources": ["testpc2", "ledger:69EXP/TEST2"], "site_resources": {"maclab": ["lab:cowork"]}}
    MRERP = {"id": "acc.m", "resources": ["mrerp:acc"]}

    def test_pc_switch_renames_registered_pc_and_dedupes(self):
        self.assertEqual(runner.resources_of(self.EXPRESS, "lab", "testpc1"), ["testpc1", "testpc1-desktop", "ledger:69EXP/TEST2"])
        self.assertEqual(runner.resources_of(self.EXPRESS, "maclab", "testpc2"),
                         ["testpc2", "testpc2-desktop", "ledger:69EXP/TEST2", "lab:erp"])

    def test_lanes_join_on_any_shared_resource(self):
        groups = runner.lanes([self.MRERP, self.EXPRESS2, self.EXPRESS], "maclab", "testpc1")
        self.assertEqual([[item["id"] for item in group] for group in groups], [["acc.a", "acc.b"], ["acc.m"]])
        isolated = runner.lanes([{"id": "acc.x", "resources": []}, {"id": "acc.y", "resources": []}], "lab", "testpc2")
        self.assertEqual(len(isolated), 2)

    def test_lanes_run_at_the_same_time_and_all_results_come_back(self):
        import threading
        barrier = threading.Barrier(2, timeout=5)  # 两路都走到这里才放行：串行跑就会超时
        def run(ctx):
            barrier.wait()
            ctx.check("ok", 1, 1)
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["PEARNLY_LOCK_DIR"] = str(pathlib.Path(tmp) / "locks")
            try:
                a = dict(SCENARIO, id="acc.a", resources=["testpc2"])
                m = dict(SCENARIO, id="acc.m", resources=["mrerp:acc"])
                fake = module(run)
                results = runner.run_lanes([[a], [m]], pathlib.Path(tmp) / "run", "win-test", False, None, "abc", pathlib.Path(tmp),
                                           modules={"acc.a": fake, "acc.m": fake})
            finally:
                os.environ.pop("PEARNLY_LOCK_DIR", None)
        self.assertEqual([(item["scenario"], item["outcome"]) for item in results], [("acc.a", runner.PASS), ("acc.m", runner.PASS)])

    def test_report_lists_every_scenario_with_reason(self):
        text = runner.report_text([{"scenario": "acc.a", "outcome": runner.NOT_RUN, "detail": "资源被占用：testpc1"},
                                   {"scenario": "acc.b", "outcome": runner.PASS, "seconds": 12.3, "facts": {"docnums": ["RR1"]}}],
                                  "r1", "0123456789abcdef", False, "core; 2 scenarios")
        self.assertIn("| `acc.a` | 没测到 | 0s | 0 | 资源被占用：testpc1 |", text)
        self.assertIn("| `acc.b` | 通过 | 12s | 0 | RR1 |", text)

    def test_core_flag_selects_core_only(self):
        registry = types.SimpleNamespace(scenarios={"acc.a": {"id": "acc.a", "core": True, "tier": "weekly"},
                                                    "acc.b": {"id": "acc.b", "tier": "gate"}})
        args = types.SimpleNamespace(scenario=None, tier=None, since=None, core=True)
        self.assertEqual([item["id"] for item in runner.select(registry, args)], ["acc.a"])


if __name__ == "__main__":
    unittest.main()
