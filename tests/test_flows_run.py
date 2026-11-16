"""流程用例跑法（scripts/flows/run.py）里不碰实验室的部分：前端产物核对、只补跑失败格的合并、计费站点表。"""
import contextlib
import importlib.util
import io
import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run = load("scripts/flows/run.py", "flows_run")
FLOWS = [{"id": "a.one", "module": "x.py", "start": "/", "datasets": ["new"], "viewports": ["desktop", "mobile"]},
         {"id": "b.two", "module": "y.py", "start": "/", "datasets": ["new", "old"], "viewports": ["desktop"]}]


def cell(flow, dataset, viewport, ok=True):
    return {"flow": flow, "dataset": dataset, "viewport": viewport, "ok": ok, "seconds": 1.0, "checks": []}


class FakeLab:
    fallbacks = []

    def __init__(self, persistent=False):
        self.persistent = persistent

    def cell_started(self):
        pass

    def cell_mode(self):
        return "persistent" if self.persistent else "per-call"

    def close_workers(self):
        pass

    def unbilled(self):
        return []

    def dataset(self, which, product="erp"):
        return {}


class SpaFresh(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "frontend/src").mkdir(parents=True)
        (self.tmp / "frontend/assistant/src").mkdir(parents=True)
        self.saved = run.ROOT, run.SPA
        run.ROOT = self.tmp
        run.SPA = [(self.tmp / "frontend", self.tmp / "out/pearnly-app/index.html"),
                   (self.tmp / "frontend/assistant", self.tmp / "out/assistant/index.html")]

    def tearDown(self):
        run.ROOT, run.SPA = self.saved

    def touch(self, path, age=0):
        target = self.tmp / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")
        stamp = time.time() - age
        os.utime(target, (stamp, stamp))

    def test_missing_frontend_build_stops_with_the_build_command(self):
        self.touch("frontend/src/App.vue", age=100)
        self.touch("out/assistant/index.html")
        with self.assertRaises(SystemExit) as stop:
            run.spa_fresh()
        self.assertIn("out/pearnly-app/index.html", str(stop.exception))
        self.assertIn("cd frontend && npm ci && npm run build", str(stop.exception))

    def test_vue_source_newer_than_build_is_stale(self):
        self.touch("out/pearnly-app/index.html", age=100)
        self.touch("out/assistant/index.html")
        self.touch("frontend/src/App.vue")
        with self.assertRaises(SystemExit) as stop:
            run.spa_fresh()
        self.assertIn("比 frontend 的源码旧", str(stop.exception))

    def test_assistant_source_does_not_make_the_outer_build_stale(self):
        self.touch("frontend/src/App.vue", age=200)
        self.touch("out/pearnly-app/index.html", age=100)
        self.touch("frontend/assistant/src/main.tsx", age=10)
        self.touch("out/assistant/index.html")
        run.spa_fresh()


class RerunFailed(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # main() 会拿实验室锁：测试用自己的临时锁目录，别的窗口正在跑真流程（拿着真锁）时本测试照样能跑
        self.lock_env = os.environ.get("PEARNLY_LOCK_DIR")
        os.environ["PEARNLY_LOCK_DIR"] = str(self.tmp / "locks")
        self.saved = {name: getattr(run, name) for name in ("registry", "head", "product_dirty", "Lab", "run_flow", "spa_fresh",
                                                            "product_map_flows")}
        run.registry = lambda: FLOWS
        run.product_map_flows = lambda base: set()  # 地图连带另有测试（test_product_map_flows_follow_the_real_map）
        run.head = lambda: "f" * 40
        run.product_dirty = lambda: ""
        run.Lab = FakeLab
        run.spa_fresh = lambda: None
        self.ran = []

        def run_flow(lab, flow, dataset, viewport):
            self.ran.append((flow["id"], dataset, viewport))
            return cell(flow["id"], dataset, viewport)
        run.run_flow = run_flow

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(run, name, value)
        if self.lock_env is None:
            os.environ.pop("PEARNLY_LOCK_DIR", None)
        else:
            os.environ["PEARNLY_LOCK_DIR"] = self.lock_env

    def test_runs_while_another_process_holds_the_real_lab_lock(self):
        # 别的窗口正在跑同一套栈的真流程（拿着用户目录那把锁）：测试用临时锁目录，不受影响（11-14 本机闸门被挡 4 个 error）
        real = Path(tempfile.mkdtemp())
        held = run.lab_lock(real)
        try:
            out = self.tmp / "merged.json"
            code, _ = self.main("--rerun-failed", str(self.previous()), "--out", str(out))
            self.assertEqual(code, 0)
            self.assertTrue((self.tmp / "locks" / f"{run.LAB}.lock").exists())
        finally:
            held.close()

    def test_locks_are_per_lab_name(self):
        folder = Path(tempfile.mkdtemp())
        saved = run.LAB
        try:
            run.LAB = "lab-one"
            first = run.lab_lock(folder)
            run.LAB = "lab-two"
            run.lab_lock(folder).close()  # 另一套栈名不用等
            run.LAB = "lab-one"
            with self.assertRaises(SystemExit):
                run.lab_lock(folder)
            first.close()
        finally:
            run.LAB = saved

    def previous(self, **extra):
        data = {"ok": False, "summary": "…", "sha": "f" * 40, "dirty": False, "scope": "all flows", "flow_count": 2, "seconds": 100,
                "results": [cell("a.one", "new", "desktop"), cell("a.one", "new", "mobile", ok=False),
                            cell("b.two", "new", "desktop"), cell("b.two", "old", "desktop")]}
        data.update(extra)
        path = self.tmp / "prev.json"
        path.write_text(json.dumps(data))
        return path

    def main(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = run.main(list(argv))
        return code, out.getvalue()

    def test_reruns_only_failed_cells_and_merges_with_the_full_suite(self):
        out = self.tmp / "merged.json"
        code, printed = self.main("--rerun-failed", str(self.previous()), "--out", str(out))
        self.assertEqual(code, 0, printed)
        self.assertEqual(self.ran, [("a.one", "new", "mobile")])
        merged = json.loads(out.read_text())
        self.assertTrue(merged["ok"])
        self.assertTrue(merged["summary"].startswith("all flows; 2 flows; datasets new,old; 4/4 runs passed; "))
        self.assertTrue(merged["summary"].endswith("; reran 1: a.one new/mobile"))
        self.assertEqual(merged["reran"], ["a.one new/mobile"])
        self.assertEqual([r.get("rerun", False) for r in merged["results"]], [False, True, False, False])
        self.assertGreaterEqual(merged["seconds"], 100)
        self.assertEqual((merged["sha"], merged["scope"]), ("f" * 40, "all flows"))

    def test_earlier_reruns_stay_listed(self):
        out = self.tmp / "merged.json"
        self.main("--rerun-failed", str(self.previous(reran=["b.two old/desktop"])), "--out", str(out))
        self.assertEqual(json.loads(out.read_text())["reran"], ["b.two old/desktop", "a.one new/mobile"])

    def test_other_commit_is_refused(self):
        with self.assertRaises(SystemExit) as stop:
            self.main("--rerun-failed", str(self.previous(sha="e" * 40)))
        self.assertIn("同一提交", str(stop.exception))
        self.assertEqual(self.ran, [])

    def test_result_without_commit_is_refused(self):
        path = self.previous()
        data = json.loads(path.read_text())
        del data["sha"]
        path.write_text(json.dumps(data))
        with self.assertRaises(SystemExit):
            self.main("--rerun-failed", str(path))

    def test_dirty_result_is_refused(self):
        with self.assertRaises(SystemExit) as stop:
            self.main("--rerun-failed", str(self.previous(dirty=True)))
        self.assertIn("未提交改动", str(stop.exception))

    def test_cells_must_match_the_registry(self):
        path = self.previous()
        data = json.loads(path.read_text())
        data["results"].pop()
        path.write_text(json.dumps(data))
        with self.assertRaises(SystemExit) as stop:
            self.main("--rerun-failed", str(path))
        self.assertIn("对不上", str(stop.exception))

    def test_a_full_run_without_post_status_says_twice_that_the_deploy_gate_will_not_count_it(self):
        # 11-15 发布日：全套 210 格没带 --post-status，部署闸不认，头冻结后整轮重跑
        code, text = self.main("--all")
        self.assertEqual(code, 0)
        self.assertEqual(text.count(run.UNPOSTED), 2)                       # 开跑前一次、结束汇总一次
        self.assertLess(text.index(run.UNPOSTED), text.index("通过 "))       # 第一次在任何一格之前
        self.assertIn("--all --post-status", run.UNPOSTED)
        self.assertIn("--changed --base", run.UNPOSTED)

    def test_scopes_that_are_not_the_full_suite_are_not_nagged(self):
        flow_id = run.registry()[0]["id"]
        for argv in (["--flow", flow_id], ["--all", "--dataset", "new"], ["--changed", "--base", "HEAD"]):
            self.assertNotIn(run.UNPOSTED, self.main(*argv)[1], argv)
        self.assertEqual(run.unposted_note("all flows", True), "")           # 带了 --post-status 不提醒
        self.assertEqual(run.unposted_note("base 4ffe8aea6abc", False), "")
        self.assertEqual(run.unposted_note("all flows", False), run.UNPOSTED)

    def test_partial_scope_cannot_post_status(self):
        with self.assertRaises(SystemExit) as stop:
            self.main("--rerun-failed", str(self.previous(scope="flows a.one")), "--post-status")
        self.assertIn("部分范围", str(stop.exception))

    def test_scope_flags_do_not_combine(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.main("--rerun-failed", str(self.previous()), "--all")

    def test_nothing_failed_runs_nothing(self):
        path = self.previous()
        data = json.loads(path.read_text())
        for r in data["results"]:
            r["ok"] = True
        path.write_text(json.dumps(data))
        code, printed = self.main("--rerun-failed", str(path))
        self.assertEqual((code, self.ran), (0, []))
        self.assertIn("没有不通过的格", printed)

    def test_full_run_records_commit_and_scope_for_a_later_merge(self):
        out = self.tmp / "full.json"
        self.main("--all", "--out", str(out))
        data = json.loads(out.read_text())
        self.assertEqual((data["sha"], data["dirty"], data["scope"], data["flow_count"]), ("f" * 40, False, "all flows", 2))
        self.assertEqual(len(data["results"]), 4)
        self.assertNotIn("reran", data)

    def test_reverse_order_runs_each_site_backwards_but_records_registry_order(self):
        out = self.tmp / "reverse.json"
        self.main("--all", "--order", "reverse", "--out", str(out))
        # 站点内倒过来；站点之间仍是大组先跑（new 三格、old 一格）
        self.assertEqual(self.ran, [("b.two", "new", "desktop"), ("a.one", "new", "mobile"), ("a.one", "new", "desktop"),
                                    ("b.two", "old", "desktop")])
        data = json.loads(out.read_text())
        self.assertEqual([(r["flow"], r["dataset"], r["viewport"]) for r in data["results"]],
                         [("a.one", "new", "desktop"), ("a.one", "new", "mobile"), ("b.two", "new", "desktop"), ("b.two", "old", "desktop")])
        self.assertTrue(data["summary"].startswith("all flows; "))
        self.assertEqual(data["order"], "reverse")

    def test_bigger_sites_start_first_and_cells_say_how_lab_python_ran(self):
        # 墙钟由最大的一组决定：大组先排，小组跟在后面（11-15）；登记表顺序、结果顺序不变
        run.registry = lambda: [{"id": "c.small", "module": "z.py", "start": "/", "datasets": ["old"], "viewports": ["desktop"]},
                                {"id": "d.big", "module": "w.py", "start": "/", "datasets": ["new"], "viewports": ["desktop", "mobile"]}]
        out = self.tmp / "order.json"
        self.main("--all", "--out", str(out))
        self.assertEqual(self.ran, [("d.big", "new", "desktop"), ("d.big", "new", "mobile"), ("c.small", "old", "desktop")])
        data = json.loads(out.read_text())
        self.assertEqual([r["flow"] for r in data["results"]], ["c.small", "d.big", "d.big"])
        self.assertEqual({r["lab_python"] for r in data["results"]}, {"persistent"})
        self.assertEqual((data["jobs"], data["lab_python_fallbacks"]), (1, []))

    def test_parallel_sites_each_get_their_own_web_process(self):
        # 三个网页口各是一个进程；并行的几路各占一个，超过 3 路才轮着共用（11-15 前都挤在 8000）
        out = self.tmp / "ports.json"
        self.main("--all", "--jobs", "2", "--out", str(out))
        ports = {r["dataset"]: r["port"] for r in json.loads(out.read_text())["results"]}
        self.assertEqual(sorted(ports.values()), sorted(run.PORTS[:2]))
        five = run.port_slots(5)
        self.assertEqual([five.get() for _ in range(5)], run.PORTS + run.PORTS[:2])

    def test_cells_that_leave_data_behind_are_named_with_the_table(self):
        # 跑完和跑前不一样的格记 left_data（哪张表多几行、哪份文件新增 / 改了）；只记不判，判过与否仍看检查项（11-16 收款留资料）
        state = {"rows: ERP Customer": 3, "file: erp_express_facts/k.json.gz": None}

        class Lab(FakeLab):
            def dataset(self, which, product="erp"):
                return {"site": f"{product}-{which}.site"}

            def footprint(self, site):
                return {key: value for key, value in state.items() if value is not None}
        run.Lab = Lab

        def run_flow(lab, flow, dataset, viewport):
            if flow["id"] == "a.one":
                state.update({"rows: ERP Customer": 4, "file: erp_express_facts/k.json.gz": "abc"})
            return {"flow": flow["id"], "dataset": dataset, "viewport": viewport, "ok": True, "checks": []}
        run.run_flow = run_flow
        out = self.tmp / "left.json"
        self.main("--all", "--out", str(out))
        data = json.loads(out.read_text())
        left = [r.get("left_data") for r in data["results"]]
        # 第一格 a.one 留下了；之后的格跑前就已是这个样子，自己没留就不记
        self.assertEqual([value for value in left if value], [{"file: erp_express_facts/k.json.gz": "新增", "rows: ERP Customer": "+1"}])
        self.assertTrue(data["ok"])

    def test_changed_takes_the_product_map_flows_too(self):
        # --changed = covers / 流程自己的脚本 ∪ 产品地图按连带算的「要跑的流程」（11-16：只算前一套漏了 22 条 / 13 条）
        flows = [{"id": "a.covered", "module": "scripts/flows/none_a.py", "covers": ["apps/x/page.js"]},
                 {"id": "b.mapped", "module": "scripts/flows/none_b.py", "covers": []},
                 {"id": "c.neither", "module": "scripts/flows/none_c.py", "covers": []}]
        saved = run.product_map_flows
        asked = []
        run.product_map_flows = lambda base: asked.append(base) or {"b.mapped"}
        try:
            self.assertEqual([f["id"] for f in run.changed_flows(flows, ["apps/x/page.js"], "abc123")], ["a.covered", "b.mapped"])
            self.assertEqual(asked, ["abc123"])
            self.assertEqual([f["id"] for f in run.changed_flows(flows, ["apps/x/page.js"])], ["a.covered"])  # 没给基线：只看文件
        finally:
            run.product_map_flows = saved

    def test_product_map_flows_follow_the_real_map(self):
        # 真地图：改到某一行入口的文件，这一行登记的流程都在地图清单里（与 check-product-map 的「要跑的流程」同一函数）
        spec = importlib.util.spec_from_file_location("check_product_map_t", run.ROOT / "scripts" / "check-product-map.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        product_map, found, *_ = module.hits_for(files=[])
        row = next(r for r in product_map.rows.values() if any(t.startswith("flow:") for t in r.tests) and r.anchor in found)
        wanted = {t.partition(":")[2].partition("@")[0] for t in row.tests if t.startswith("flow:")}
        self.assertTrue(wanted <= set(module.map_flows(files=list(found[row.anchor].files))))
        self.assertEqual(module.map_flows(files=[]), [])

    def test_footprint_diff(self):
        self.assertEqual(run.footprint_diff({"rows: A": 2, "file: x": "1", "settings: S": "a"},
                                            {"rows: A": 1, "file: y": "2", "settings: S": "b"}),
                         {"file: x": "删了", "file: y": "新增", "rows: A": "-1", "settings: S": "改了"})

    def test_long_description_is_cut_for_github(self):
        posted = []
        saved = run.subprocess.run
        run.subprocess.run = lambda cmd, **kw: posted.append(cmd)
        try:
            run.post_status(True, "all flows; " + "x" * 200)
        finally:
            run.subprocess.run = saved
        description = next(arg for arg in posted[0] if arg.startswith("description="))
        self.assertEqual(len(description), len("description=") + 140)
        self.assertTrue(description.endswith("…"))


class SiteConfig(unittest.TestCase):
    """流程临时改站点配置：Frappe 16 网页进程缓存站点配置 60 秒，写完要等它过期才往下（11-09 --jobs 3 偶发「请先配置」）。"""

    def test_waits_until_web_workers_see_the_new_value_and_returns_the_old_one(self):
        lab, ran, slept = run.Lab(), [], []
        lab.python = lambda site, body: ran.append((site, body)) or {"k": None}
        saved = run.time.sleep
        run.time.sleep = slept.append
        try:
            before = lab.site_config("cowork-new.regression.localhost", {"k": {"mode": "economy"}})
        finally:
            run.time.sleep = saved
        self.assertEqual(before, {"k": None})
        self.assertIn("update_site_config", ran[0][1])
        self.assertIn("'k': {'mode': 'economy'}", ran[0][1])
        self.assertEqual(slept, [run.SITE_CONFIG_SETTLE])
        self.assertGreater(run.SITE_CONFIG_SETTLE, 60)

    def test_flows_change_site_config_only_through_the_lab_and_restore_it_in_cleanup(self):
        for path in sorted((ROOT / "scripts/flows").glob("*.py")):
            if path.name == "run.py":
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("update_site_config", text, f"{path.name}：改站点配置走 lab.site_config（等网页进程缓存过期）")
            self.assertNotIn("site_config.json", text, path.name)
            if "lab.site_config(" in text:
                cleanup = text.split("\ndef cleanup(", 1)
                self.assertEqual(len(cleanup), 2, f"{path.name}：改了站点配置要在 cleanup 里还原（成功、失败、超时都跑）")
                self.assertIn("lab.site_config(", cleanup[1].split("\ndef ", 1)[0], path.name)


class CompanyFields(unittest.TestCase):
    """流程临时改公司等设置：经 `Lab.set_fields` / `keep_fields` 记原值，格结束时 run_flow 统一还原。
    11-09 全套：erp.purchase.create 页面步骤 900 秒超时，写在 verify 里的「接 Express」还原没跑到，同一站点后面 6 个流程跟着失败。"""

    SETTINGS = ("ERP Company", "Cowork Company", "Cowork Workspace")

    def lab(self, befores):
        lab, ran = run.Lab(), []
        answers = iter(befores)
        lab.python = lambda site, body: ran.append((site, body)) or next(answers, {})
        return lab, ran

    def test_set_fields_writes_and_returns_the_old_values_keep_fields_only_reads(self):
        lab, ran = self.lab([{"express_enabled": 1}, {"note": "n"}])
        self.assertEqual(lab.set_fields("erp-new", "ERP Company", "CO-1", {"express_enabled": 0}), {"express_enabled": 1})
        self.assertEqual(lab.keep_fields("erp-new", "ERP Company", "CO-1", ["note"]), {"note": "n"})
        self.assertIn("{'express_enabled': 0}", ran[0][1])
        self.assertIn("fields, values = ['note'], None", ran[1][1])
        self.assertEqual(len(lab.changed()), 2)

    def test_restore_writes_back_the_earliest_value_once_per_record(self):
        lab, ran = self.lab([{"express_enabled": 1}, {"express_enabled": 0, "company_type": "general"}, {"pos_enabled": 0}])
        lab.set_fields("erp-new", "ERP Company", "CO-1", {"express_enabled": 0})
        lab.set_fields("erp-new", "ERP Company", "CO-1", {"express_enabled": 1, "company_type": "vehicle"})
        lab.set_fields("erp-old", "ERP Company", "CO-9", {"pos_enabled": 1})
        ran.clear()
        lab.restore_fields()
        self.assertEqual([site for site, _ in ran], ["erp-new", "erp-old"])
        self.assertIn("{'express_enabled': 1, 'company_type': 'general'}", ran[0][1])
        self.assertIn("{'pos_enabled': 0}", ran[1][1])
        self.assertEqual(lab.changed(), [])

    def test_one_failed_restore_does_not_skip_the_others(self):
        lab, ran = self.lab([{"a": 1}, {"b": 2}])
        lab.set_fields("s1", "ERP Company", "CO-1", {"a": 0})
        lab.set_fields("s2", "ERP Company", "CO-2", {"b": 0})

        def python(site, body):
            ran.append(site)
            if site == "s1":
                raise RuntimeError("lab python failed")
            return {}
        lab.python, ran[:] = python, []
        with self.assertRaises(RuntimeError):
            lab.restore_fields()
        self.assertEqual(ran, ["s1", "s2"])

    def run_cell(self, prepare_fails=False):
        lab, ran = self.lab([{"express_enabled": 1}, {}])
        lab.dataset = lambda which, product="erp": {"site": "erp-new"}
        lab.client_errors = lambda site: set()
        order = []

        class Module:
            @staticmethod
            def prepare(lab_, manifest):
                lab_.set_fields("erp-new", "ERP Company", "CO-1", {"express_enabled": 0})
                if prepare_fails:
                    raise RuntimeError("prepare failed")
                return {"n": len(order)}

            @staticmethod
            def cleanup(lab_, manifest, params):
                order.append(("cleanup", params["n"]))

        saved = run.load_module, run._run_prepared
        run.load_module = lambda path: Module

        def timed_out(*args):
            args[-1].append({"n": 1})  # 相机那一遍的准备
            raise RuntimeError("page steps timed out after 900s")
        run._run_prepared = timed_out
        try:
            with self.assertRaises(RuntimeError):
                run.run_flow(lab, {"id": "x", "module": "x.py"}, "new", "mobile")
        finally:
            run.load_module, run._run_prepared = saved
        return ran, order

    def test_steps_timing_out_still_restores_and_cleans_up_the_later_preparation_first(self):
        ran, order = self.run_cell()
        self.assertIn("{'express_enabled': 1}", ran[-1][1])
        self.assertEqual(order, [("cleanup", 1), ("cleanup", 0)])

    def test_preparation_failing_halfway_still_restores(self):
        ran, order = self.run_cell(prepare_fails=True)
        self.assertIn("{'express_enabled': 1}", ran[-1][1])
        self.assertEqual(order, [])

    def test_flows_write_company_settings_only_after_the_lab_recorded_the_original(self):
        names = "|".join(map(re.escape, self.SETTINGS))
        write = re.compile(r'set_value\(\s*"(?P<dt>%s)"\s*,\s*[^,]+,\s*(?:\{+(?P<many>[^}]*)\}+|"(?P<field>\w+)")' % names)
        record = re.compile(r'lab\.(?:set_fields|keep_fields)\([^,]+,\s*"(?P<dt>[^"]+)"\s*,\s*[^,]+,\s*(?P<what>\{[^}]*\}|\[[^\]]*\])')
        for path in sorted((ROOT / "scripts/flows").glob("*.py")):
            if path.name == "run.py":
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("set_single_value", text, f"{path.name}：改单例设置要先经 lab 记原值（加 Lab 方法），格结束才能还原")
            self.assertNotRegex(text, r"(?i)update\s+`tab(%s)`" % names, f"{path.name}：改公司设置走 lab.set_fields，不写 SQL")
            self.assertNotRegex(text, r'get_doc\(\s*"(%s)"' % names, f"{path.name}：改公司设置走 lab.set_fields，不 get_doc 再 save")
            kept = set()
            for m in record.finditer(text):
                keys = re.findall(r'"(\w+)"\s*:', m["what"]) if m["what"].startswith("{") else re.findall(r'"(\w+)"', m["what"])
                kept |= {(m["dt"], key) for key in keys}
            for m in write.finditer(text):
                fields = [m["field"]] if m["field"] else re.findall(r'"(\w+)"\s*:', m["many"])
                for field in fields:
                    self.assertIn((m["dt"], field), kept,
                                  f"{path.name}：直接改了 {m['dt']}.{field}，先用 lab.set_fields / keep_fields 记原值（格结束时 run_flow 还原，"
                                  "成功、失败、超时都还原），不在 verify / cleanup 里自己还原")

    def test_the_check_catches_a_restore_left_in_verify(self):
        names = "|".join(map(re.escape, self.SETTINGS))
        write = re.compile(r'set_value\(\s*"(?P<dt>%s)"\s*,\s*[^,]+,\s*(?:\{+(?P<many>[^}]*)\}+|"(?P<field>\w+)")' % names)
        old = '''frappe.db.set_value("ERP Company", {company!r}, "express_enabled", 0, update_modified=False)'''
        many = '''frappe.db.set_value("ERP Company", {t!r}, {{"express_sync_state": None, "express_sync_note": None}}, update_modified=False)'''
        self.assertEqual(write.search(old)["field"], "express_enabled")
        self.assertEqual(re.findall(r'"(\w+)"\s*:', write.search(many)["many"]), ["express_sync_state", "express_sync_note"])


class FlowDocuments(unittest.TestCase):
    """流程每遍新开的采购 / 销售单会一直算作那个月「等着录」的单，每遍都留就越积越多（11-10 全套：erp.document.express 每遍在
    上个月留两张，积到 51 张越过 cc.operator 开始前卡片一页 50 张，那格跟着失败）。这类流程在 cleanup 里作废
    （`erp_lib.void_documents`）；请求号固定、重跑认回同一张的（`erp_lib.ensure_documents`、cc.operator.replay）不积，不用收。"""

    POSTS = re.compile(r"\b(?:entry|purchase|sales)\.post\(|line_items\.prepare\(")
    # 开单用固定请求号、重跑认回同几张的（不积）
    REUSED = {"cc_operator_replay.py": "自备两张已提交的服务采购单，请求号按公司固定（flow-replay-po-<公司>-1/2）"}

    def test_flows_that_open_documents_each_run_void_them_in_cleanup(self):
        for path in sorted((ROOT / "scripts/flows").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            if path.name == "erp_lib.py" or path.name in self.REUSED or "uuid.uuid4" not in text or not self.POSTS.search(text):
                continue
            cleanup = text.split("\ndef cleanup(", 1)
            self.assertEqual(len(cleanup), 2, f"{path.name}：每遍新开单据，要在 cleanup 里作废（成功、失败、超时都跑）")
            self.assertIn("void_documents(", cleanup[1].split("\ndef ", 1)[0], path.name)


class BorrowedPreparation(unittest.TestCase):
    """借另一个流程的准备（`from x import prepare` / `import x as base`）就要连它的 cleanup 一起借：准备里改的、造的，收尾在那边
    （11-10：erp.trade.line-stock-sales 借了采购那份的准备和步骤，没借 cleanup，每遍开的两张销售单一直留着）。"""

    def test_flows_that_borrow_a_preparation_also_borrow_its_cleanup(self):
        flows = ROOT / "scripts/flows"
        for path in sorted(flows.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for other in re.findall(r"^(?:import (\w+) as base|from (\w+) import [^\n]*\bprepare\b)", text, re.M):
                name = other[0] or other[1]
                source = flows / f"{name}.py"
                if source.exists() and "\ndef cleanup(" in source.read_text(encoding="utf-8"):
                    self.assertRegex(text, r"\bcleanup\b", f"{path.name} 借了 {name} 的准备，要连它的 cleanup 一起借")


class BilledSites(unittest.TestCase):
    def test_billed_sites_are_lab_sites(self):
        init = load("scripts/regression/lab_init.py", "lab_init")
        self.assertTrue({site for _, _, site in init.BILLED} <= set(init.SITES))
        self.assertIn("lab_billing.py", (ROOT / "scripts/regression/lab_init.py").read_text())


class ChangedFlows(unittest.TestCase):
    """`--changed` 与补跑范围按改动选流程（11-11 第 9 车：只改了 `cc_send_while_waiting.py` 的等待，--changed 没选中那条流程，
    靠手工 --flow 补跑）：除了 registry 的 covers，流程自己的脚本、它引用到的流程辅助脚本（同目录的 cc_lib、erp_lib…，
    也包括引用的别的流程脚本，层层引用都算）改了，这条流程也算被改到。所有流程一律按这条规则，不逐条补 covers。"""

    def setUp(self):
        self.flows = run.registry()
        self.saved = run.changed_files, run.product_map_flows
        run.product_map_flows = lambda base: set()  # 这里只看第一套规则；并集见 RerunFailed.test_changed_takes_the_product_map_flows_too

    def tearDown(self):
        run.changed_files, run.product_map_flows = self.saved

    def picked(self, *files):
        run.changed_files = lambda base: list(files)
        return {flow["id"] for flow in run.scope_flows(self.flows, "base 0123456789ab")}

    def test_every_flow_is_picked_when_only_its_own_script_changed(self):
        missed = [flow["id"] for flow in self.flows if flow["id"] not in self.picked(flow["module"])]
        self.assertEqual(missed, [])

    def test_a_helper_change_picks_the_flows_that_use_it_directly_or_through_another_script(self):
        # cc_send_while_waiting 用 cc_lib；cc_send_while_waiting_operator 引用 cc_send_while_waiting（两层）
        picked = self.picked("scripts/flows/cc_lib.py")
        self.assertTrue({"cowork.cc.send-while-waiting", "cowork.operator.send-while-waiting"} <= picked, picked)
        self.assertNotIn("erp.stocktake.scan", picked)
        self.assertEqual({"cowork.cc.send-while-waiting", "cowork.operator.send-while-waiting"},
                         self.picked("scripts/flows/cc_send_while_waiting.py"))

    def test_the_runner_itself_is_not_a_helper_of_any_flow(self):
        self.assertEqual(self.picked("scripts/flows/run.py", "scripts/flows/coverage.py"), set())

    def test_changed_and_rerun_pick_with_the_same_rule(self):
        source = (ROOT / "scripts/flows/run.py").read_text(encoding="utf-8")
        self.assertNotIn("any(covers(f, path) for path in files)", source, "两处选流程都走同一个函数")



class DriveTimeout(unittest.TestCase):
    """整格卡满 900 秒时说出卡在哪一步（驱动每步开始前打一行 progress），已跑完的步骤输出不再整个丢掉（11-12）。"""

    def drive(self, out):
        import subprocess as sp
        from unittest import mock

        class Proc:
            pid = 0
            calls = 0

            def communicate(self, timeout=None):
                Proc.calls += 1
                if Proc.calls == 1:
                    raise sp.TimeoutExpired("node", timeout)
                return out, ""

        with mock.patch.object(run.subprocess, "Popen", return_value=Proc()), mock.patch.object(run.os, "killpg"):
            run.drive("erp.regression.localhost", "sid", "/desk", [], False, None)

    def test_the_stuck_step_is_named(self):
        out = '{"progress": "one"}\n{"step": "one", "value": 1}\n{"progress": "two"}\n'
        with self.assertRaises(RuntimeError) as stopped:
            self.drive(out)
        self.assertIn("in step 'two'", str(stopped.exception))
        self.assertEqual(stopped.exception.steps, [{"step": "one", "value": 1}], "已跑完的步骤结果随失败格记下")

    def test_before_the_first_step(self):
        with self.assertRaises(RuntimeError) as stopped:
            self.drive("")
        self.assertIn("before the first step", str(stopped.exception))

class ChromeDebugPort(unittest.TestCase):
    """无头 Chrome 一律用系统分配的调试端口（`scripts/smoke/devtools.mjs`）：随机挑端口时并行的两格会连到同一个 Chrome，
    一格跑完关掉它，另一格报 debug connection closed（11-16 列车 A、D 并行反复挂、串行补跑就过）。"""

    def test_no_driver_picks_its_own_debug_port(self):
        import subprocess

        listed = subprocess.run(["git", "ls-files", "*.mjs", "*.js", "*.py", "*.ts"], cwd=ROOT, capture_output=True, text=True,
                                check=True).stdout.split()
        bad = []
        for name in listed:
            if name.startswith(("docs/", "tests/")) or "node_modules" in name:
                continue
            for number, line in enumerate((ROOT / name).read_text(errors="ignore").splitlines(), 1):
                for flag in re.findall(r"remote-debugging-port=(\S*)", line):
                    if not flag.startswith("0"):
                        bad.append(f"{name}:{number}: {line.strip()[:120]}")
        self.assertEqual(bad, [], "改用 scripts/smoke/devtools.mjs 的 DEVTOOLS_PORT_FLAG / devtoolsVersion")

    def test_the_shared_flag_is_port_zero(self):
        text = (ROOT / "scripts/smoke/devtools.mjs").read_text()
        self.assertIn('DEVTOOLS_PORT_FLAG = "--remote-debugging-port=0"', text)
        self.assertIn("DevToolsActivePort", text)


if __name__ == "__main__":
    unittest.main()
