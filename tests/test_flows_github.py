"""流程用例在 GitHub 上分份跑（`.github/workflows/flows.yml`，11-16）：切份、汇总、补跑范围、何时不跑。只用标准库，不连实验室。"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "flows"))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run = load("flows_run_for_github", "scripts/flows/run.py")
github = load("flows_github", "scripts/flows/github.py")


def cells_of(sizes):
    """{(产品, 数据集): 格数} → 格（每格一个假流程）。"""
    out = []
    for (product, dataset), n in sizes.items():
        out += [({"id": f"{product}.{dataset}.{i}", "product": product}, dataset, "desktop") for i in range(n)]
    return out


class Shards(unittest.TestCase):
    def check(self, cells, per=14, most=20):
        parts = run.shards(cells, per, most)
        flat = [run.key(c) for part in parts for c in part]
        self.assertEqual(sorted(flat), sorted(map(run.key, cells)), "每格正好落在一份里")
        self.assertLessEqual(len(parts), most)
        self.assertEqual(parts, run.shards(cells, per, most), "同一参数切出同一结果（plan 与各台机器各算一遍）")
        for part in parts:  # 同一站点在一份里仍按登记表顺序
            for site in {run.cell_site(c) for c in part}:
                mine = [c for c in part if run.cell_site(c) == site]
                self.assertEqual(mine, sorted(mine, key=cells.index))
        return parts

    def test_whole_suite_fills_twenty_machines_and_keeps_big_sites_alone(self):
        cells = cells_of({("erp", "new"): 151, ("erp", "old"): 136, ("cowork", "new"): 74, ("cowork", "old"): 47,
                          ("dms", "new"): 4, ("dms", "old"): 2})
        parts = self.check(cells)
        self.assertEqual(len(parts), 20)
        sizes = sorted(len(p) for p in parts if len({run.cell_site(c) for c in p}) == 1)
        self.assertLessEqual(sizes[-1] - sizes[0], 6, sizes)
        mixed = [p for p in parts if len({run.cell_site(c) for c in p}) > 1]
        self.assertEqual([{run.cell_site(c) for c in p} for p in mixed],
                         [{"dms-new.regression.localhost", "dms-old.regression.localhost"}], "小组装在一起")

    def test_few_runs_stay_on_one_machine(self):
        parts = self.check(cells_of({("erp", "new"): 3, ("erp", "old"): 3, ("cowork", "new"): 2}))
        self.assertEqual(len(parts), 1)

    def test_more_sites_than_machines_packs_whole_sites(self):
        parts = self.check(cells_of({("erp", "new"): 10, ("erp", "old"): 9, ("cowork", "new"): 8}), per=10, most=2)
        self.assertEqual(len(parts), 2)
        for part in parts:
            for site in {run.cell_site(c) for c in part}:
                self.assertEqual(sum(run.cell_site(c) == site for c in cells_of({("erp", "new"): 10, ("erp", "old"): 9,
                                                                                 ("cowork", "new"): 8})),
                                 sum(run.cell_site(c) == site for c in part), "站点不拆到两份")

    def test_plan_lists_the_sites_each_machine_builds(self):
        plan = run.plan_of("all flows", 3, cells_of({("erp", "new"): 30, ("cowork", "old"): 3}))
        self.assertEqual(plan["cells"], 33)
        self.assertEqual([s["shard"] for s in plan["shards"]], ["1/3", "2/3", "3/3"])
        self.assertEqual(plan["shards"][-1]["sites"], "cowork-old.regression.localhost")


class Merge(unittest.TestCase):
    def write(self, folder, name, **data):
        path = Path(folder, name)
        path.write_text(json.dumps(data))
        return str(path)

    def test_missing_shard_counts_as_failed_runs_the_rerun_can_pick_up(self):
        cells = cells_of({("erp", "new"): 4})
        sha = run.head()
        ok = lambda c: {"flow": c[0]["id"], "dataset": c[1], "viewport": c[2], "ok": True, "checks": []}  # noqa: E731
        with tempfile.TemporaryDirectory() as folder:
            one = self.write(folder, "a.json", sha=sha, scope="all flows", shard="1/2", seconds=50,
                             results=[ok(c) for c in cells[:2]])
            merged = run.merge_parts("all flows", 4, cells, [one])
            self.assertFalse(merged["ok"])
            self.assertEqual([r["ok"] for r in merged["results"]], [True, True, False, False])
            self.assertIn("not reported", merged["results"][2]["error"])
            two = self.write(folder, "b.json", sha=sha, scope="all flows", shard="2/2", seconds=70,
                             results=[ok(c) for c in cells[2:]])
            merged = run.merge_parts("all flows", 4, cells, [one, two])
            self.assertTrue(merged["ok"])
            self.assertTrue(merged["summary"].startswith("all flows; 4 flows;"), "部署闸认 all flows 开头")
            self.assertIn("4/4 runs passed; 70s in 2 shards", merged["summary"])
            self.assertEqual([run.key(r) for r in merged["results"]], [run.key(c) for c in cells], "按登记表顺序")

    def test_other_commit_or_scope_is_not_counted(self):
        cells = cells_of({("erp", "new"): 1})
        with tempfile.TemporaryDirectory() as folder:
            stale = self.write(folder, "a.json", sha="0" * 40, scope="all flows", shard="1/1",
                               results=[{"flow": cells[0][0]["id"], "dataset": "new", "viewport": "desktop", "ok": True}])
            merged = run.merge_parts("all flows", 1, cells, [stale])
            self.assertFalse(merged["ok"])
            self.assertIn("ran 000000000000", merged["summary"])

    def test_no_flows_touched_passes_without_shards(self):
        merged = run.merge_parts("base abc", 0, [], [])
        self.assertTrue(merged["ok"])

    def test_shard_option_refuses_a_count_that_does_not_match_the_plan(self):
        with self.assertRaises(SystemExit) as raised:
            run.main(["--all", "--shard", "1/999"])
        self.assertIn("切成", str(raised.exception))


class Decide(unittest.TestCase):
    def test_scope_per_trigger(self):
        self.assertEqual(github.scope_args("push", "", ""), (["--changed", "--base", "origin/main"], True))
        self.assertEqual(github.scope_args("workflow_dispatch", "all", ""), (["--all"], True))
        self.assertEqual(github.scope_args("workflow_dispatch", "flows", "a.b, c.d"),
                         (["--flow", "a.b", "--flow", "c.d"], False), "指定流程只看结果，不回写")
        with self.assertRaises(SystemExit):
            github.scope_args("workflow_dispatch", "flows", " ")

    def test_calibration_marker_picks_flows(self):
        self.assertEqual(github.calibration("ci: x\n\n[flows-on-github: a.b,c.d]"), "a.b,c.d")
        self.assertEqual(github.calibration("ci: x [flows-on-github]"), "")

    def test_existing_whole_suite_or_same_scope_is_not_overwritten(self):
        self.assertTrue(github.covered(("success", "all flows; 134 flows; …"), "base abc"))
        self.assertTrue(github.covered(("success", "base abc; 3 flows; …"), "base abc"))
        self.assertFalse(github.covered(("success", "base abd; 3 flows"), "base abc"))
        self.assertFalse(github.covered(("failure", "all flows; …"), "base abc"))
        self.assertFalse(github.covered(("pending", "running on GitHub"), "base abc"))
        self.assertFalse(github.covered(None, "base abc"))

    def test_failed_sites_for_the_rerun(self):
        flow = run.registry()[0]
        product = flow.get("product", "erp")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "m.json")
            path.write_text(json.dumps({"results": [{"flow": flow["id"], "dataset": "old", "viewport": "mobile", "ok": False},
                                                    {"flow": flow["id"], "dataset": "new", "viewport": "mobile", "ok": True}]}))
            self.assertEqual(github.failed_sites(path), [f"{product}-old.regression.localhost"])

    def test_summary_lists_rerun_passes_and_left_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "f.json")
            path.write_text(json.dumps({"ok": True, "summary": "all flows; x", "results": [
                {"flow": "a", "dataset": "new", "viewport": "desktop", "ok": True, "rerun": True},
                {"flow": "b", "dataset": "old", "viewport": "mobile", "ok": True, "left_data": {"File": 1}}]}))
            text = github.summary(path)
        self.assertIn("Passed only on the rerun (1)", text)
        self.assertIn("Left data behind (1", text)


class Workflow(unittest.TestCase):
    TEXT = (ROOT / ".github/workflows/flows.yml").read_text(encoding="utf-8")

    def test_no_pull_request_and_no_self_hosted_machine(self):
        on = self.TEXT.split("\non:", 1)[1].split("\npermissions:", 1)[0]
        self.assertNotIn("pull_request", on, "外人 PR 不跑、写不了状态")
        self.assertNotIn("self-hosted", self.TEXT)
        self.assertNotIn("vars.", self.TEXT, "公开仓库不经仓库变量挂自有机器")

    def test_private_repository_does_not_run_on_push(self):
        self.assertIn("!github.event.repository.private", self.TEXT)

    def test_leaves_machines_for_other_checks(self):
        self.assertIn("max-parallel: 12", self.TEXT, "一次运行最多占 12 台，账号上限 20")
        self.assertIn("'flows-whole-suite'", self.TEXT, "全套全仓一次一套")

    def test_same_shard_settings_everywhere(self):
        self.assertIn(f"PEARNLY_FLOWS_PER_SHARD: '{run.PER_SHARD}'", self.TEXT)
        self.assertIn(f"PEARNLY_FLOWS_MAX_SHARDS: '{run.MAX_SHARDS}'", self.TEXT)


if __name__ == "__main__":
    unittest.main()
