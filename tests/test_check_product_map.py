"""产品地图闸门 scripts/check-product-map.py 的四条规则（评估与审定 docs/work/2026-11-04-product-map.md）。纯离线。
真实地图与代码是否一致不在这里核：同一 CI 步骤紧接着跑闸门本身。"""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "impact"))
import product_map as pm  # noqa: E402

spec = importlib.util.spec_from_file_location("check_product_map", ROOT / "scripts" / "check-product-map.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

FLOWS = {"f.both": {"id": "f.both", "viewports": ["desktop", "mobile"]}, "f.desk": {"id": "f.desk", "viewports": ["desktop"]}}
IMPACT = {"e.manual": {"id": "e.manual", "check": "manual"}}
REPO = pm.Repo(ROOT, ["tests/test_check_product_map.py"])


def found(*anchors):
    return {a: pm.Found(a, "nav-page", "ERP", "desk", ["desktop", "mobile"], [f"apps/x/{a[-1]}.js"], f"页面 {a}") for a in anchors}


def row(anchor, **overrides):
    base = dict(id="erp." + anchor.split(":")[-1], product="ERP", channel="desk", ends=["desktop", "mobile"], kind="nav-page",
                anchor=anchor, where="侧栏", tests=["flow:f.both"])
    base.update(overrides)
    return pm.Row(**base)


def run(rows, discovered, *, baseline=(), not_entries=None, config=None, keys=None, families=None, endpoints=None):
    product_map = pm.Map({r.id: r for r in rows}, not_entries or {}, config or {}, list(baseline), [])
    return gate.check(product_map, REPO, discovered, keys or {}, families or {}, FLOWS, IMPACT, set(), endpoints or {})


class Rules(unittest.TestCase):
    def test_a_map_that_matches_the_code_passes(self):
        self.assertEqual(run([row("nav:erp:a")], found("nav:erp:a"), config={"pearnly_x": {"class": "same"}},
                             keys={"pearnly_x": ["a.py"]}), [])

    def test_rule_1_new_entries_and_config_keys_must_be_registered(self):
        errors = "\n".join(run([], found("nav:erp:b"), keys={"pearnly_new": ["a.py"]}, families={"pearnly_line_": ["l.py"]}))
        self.assertIn("未登记：ERP nav-page nav:erp:b", errors)
        self.assertIn("site_config 键 pearnly_new", errors)
        self.assertIn("前缀族 pearnly_line_*", errors)
        self.assertEqual(run([], found("nav:erp:b"), not_entries={"nav:erp:b": "只给框架加载"}), [])

    def test_rule_2_rows_and_exemptions_whose_code_is_gone_are_stale(self):
        errors = "\n".join(run([row("nav:erp:gone", config=["site:pearnly_gone"])], {}, not_entries={"tool:erp:old": "x"},
                               config={"pearnly_unread": {"class": "same"}}))
        self.assertIn("已过期：行 erp.gone 的锚点 nav:erp:gone", errors)
        self.assertIn("配置依赖 site:pearnly_gone 不在 config.toml", errors)
        self.assertIn("not_entries.toml 里的 tool:erp:old", errors)
        self.assertIn("config.toml 里的 pearnly_unread", errors)
        # CC 外壳动作从 F 块起也按 `// map:` 注释核锚点：注释删了，行就过期
        self.assertIn("已过期：行 erp.switch 的锚点 ccaction:erp:switch",
                      "\n".join(run([row("ccaction:erp:switch", kind="cc-action", channel="cc-chat")], {})))
        # 前缀族覆盖的键不算过期
        self.assertEqual(run([row("nav:erp:a", config=["site:pearnly_line_channel_secret"])], found("nav:erp:a"),
                             config={"pearnly_line_*": {"class": "presence"}}, families={"pearnly_line_": ["l.py"]}), [])

    def test_cc_front_end_errors_say_how_to_fix_them(self):
        action = {"ccaction:erp:new": pm.Found("ccaction:erp:new", "cc-action", "ERP", "cc-chat", ["desktop"], ["frontend/assistant/src/a.ts"]),
                  "ccnav:a.ts:location.href = x": pm.Found("ccnav:a.ts:location.href = x", "cc-nav", "共用", "cc-chat", ["desktop"],
                                                           ["frontend/assistant/src/a.ts"])}
        errors = "\n".join(run([row("ccaction:erp:gone", kind="cc-action", channel="cc-chat")], action))
        self.assertIn("erp.toml / cowork.toml 各加一行", errors)
        self.assertIn('not_entries.toml 按原文登记 anchor，kind = "internal"', errors)
        self.assertIn("两个产品各一行", errors)
        self.assertIn("sibling_baseline.txt", errors)

    def test_rule_3_every_end_needs_a_test_and_the_baseline_only_shrinks(self):
        desk_only = row("nav:erp:a", tests=["flow:f.desk"])
        errors = "\n".join(run([desk_only], found("nav:erp:a")))
        self.assertIn("没有测试：ERP 侧栏（行 erp.a，手机）", errors)
        self.assertNotIn("电脑）", errors)
        self.assertEqual(run([desk_only], found("nav:erp:a"), baseline=["erp.a@mobile"]), [])
        # 人工清单（黄）不算有测试
        manual = row("nav:erp:a", tests=["impact:e.manual"])
        self.assertEqual(len(run([manual], found("nav:erp:a"))), 2)
        # 有了测试，基线里的条目就要删；行不在了也要删
        errors = "\n".join(run([row("nav:erp:a")], found("nav:erp:a"), baseline=["erp.a@mobile", "erp.gone@desktop"]))
        self.assertIn("基线该删：untested_baseline.txt 里的 erp.a@mobile", errors)
        self.assertIn("基线该删：untested_baseline.txt 里的 erp.gone@desktop", errors)
        self.assertIn("重复", "\n".join(run([desk_only], found("nav:erp:a"), baseline=["erp.a@mobile", "erp.a@mobile"])))
        # 只能真机：写了理由与清单才算
        device = row("nav:erp:a", tests=[], device_only="要真 LINE App", device="点卡片 → 打开编辑页")
        self.assertEqual(run([device], found("nav:erp:a")), [])

    def test_rule_3_must_verify_rows_need_tests_on_every_end_or_a_probe(self):
        must = row("nav:erp:a", tests=["flow:f.desk"], must_verify=True)
        errors = "\n".join(run([must], found("nav:erp:a"), baseline=["erp.a@mobile"]))
        self.assertIn("标了上线必验", errors)
        must.probe = {"type": "liff"}
        self.assertEqual(run([must], found("nav:erp:a"), baseline=["erp.a@mobile"]), [])

    def test_rule_4_impact_lists_rows_by_file_shared_part_and_config_key(self):
        rows = [row("nav:erp:a"), row("nav:erp:b", parts=["line"]), row("nav:erp:c", config=["site:pearnly_liff"], must_verify=True,
                                                                         probe={"type": "liff"}),
                row("nav:erp:d")]
        product_map = pm.Map({r.id: r for r in rows}, {}, {}, [], [])
        discovered = found("nav:erp:a", "nav:erp:b", "nav:erp:c", "nav:erp:d")
        changes = ["apps/x/a.js", "apps/pearnly_core/pearnly_core/line.py", "apps/y/reads.py"]
        hits, parts = gate.impact_rows(product_map, discovered, {"pearnly_liff": ["apps/y/reads.py"]}, changes)
        self.assertEqual(hits, {"erp.a": ["改到入口文件"], "erp.b": ["改到共用件 line"], "erp.c": ["改到读这项配置的代码"]})
        report = gate.render(product_map, hits, FLOWS, IMPACT, markdown=True)
        self.assertIn("--flow f.both", report)
        self.assertIn("部署后逐个正式租户核（上线必验）：`erp.c`", report)
        self.assertEqual(gate.render(product_map, {}, FLOWS, IMPACT, markdown=True), "没有改到地图里的入口。")


    def test_rule_1_and_2_endpoints_without_callers(self):
        endpoints = {"pearnly_x.api.dead": pm.Endpoint("pearnly_x.api.dead", "apps/x/api.py", []),
                     "pearnly_x.api.live": pm.Endpoint("pearnly_x.api.live", "apps/x/api.py", ["apps/x/a.js"])}
        errors = "\n".join(run([], {}, endpoints=endpoints))
        self.assertIn("未登记：白名单接口 pearnly_x.api.dead", errors)
        self.assertNotIn("live", errors)
        self.assertEqual(run([], {}, endpoints=endpoints, not_entries={"endpoint:pearnly_x.api.dead": "拼名调用"}), [])
        errors = "\n".join(run([], {}, endpoints=endpoints, not_entries={"endpoint:pearnly_x.api.live": "x",
                                                                         "endpoint:pearnly_x.api.gone": "x",
                                                                         "endpoint:pearnly_x.api.dead": "x"}))
        self.assertIn("endpoint:pearnly_x.api.live 现在有调用方", errors)
        self.assertIn("endpoint:pearnly_x.api.gone 接口已经不在了", errors)

    def test_rule_4_changing_an_endpoint_hits_the_rows_that_call_it(self):
        rows = [row("nav:erp:a"), row("nav:erp:b")]
        product_map = pm.Map({r.id: r for r in rows}, {}, {}, [], [])
        endpoints = {"pearnly_x.api.f": pm.Endpoint("pearnly_x.api.f", "apps/x/api.py", ["apps/x/a.js"])}
        hits, _ = gate.impact_rows(product_map, found("nav:erp:a", "nav:erp:b"), {}, ["apps/x/api.py"], endpoints=endpoints)
        self.assertEqual(hits, {"erp.a": ["改到它调用的接口"]})

    def test_rule_6_changed_must_verify_or_shared_rows_need_green_tests_or_a_device_record(self):
        import tempfile
        rows = [row("nav:erp:a", must_verify=True, probe={"type": "liff"}, tests=["flow:f.desk"]),
                row("nav:erp:b", parts=["line"], tests=["impact:e.manual"]),
                row("nav:erp:c", tests=[]),                                   # 不是上线必验、没有共用件、没出过事：不管
                row("nav:erp:e", tests=[], incidents=["2026-11-03 事故"]),       # 出过事的一律管
                row("nav:erp:d", parts=["line"], tests=[], device_only="要真 LINE App", device="点卡片")]
        product_map = pm.Map({r.id: r for r in rows}, {}, {}, [], [])
        hits = {r.id: ["改到入口文件"] for r in rows}
        root = Path(tempfile.mkdtemp())
        (root / "docs/work").mkdir(parents=True)
        errors = "\n".join(gate.enforce_hits(product_map, hits, FLOWS, IMPACT, [], root))
        self.assertIn("命中要补测试：erp.a（上线必验", errors)
        self.assertIn("手机红", errors)
        self.assertIn("命中要补测试：erp.b（共用件 line", errors)
        self.assertIn("电脑黄", errors)
        self.assertNotIn("erp.c", errors)
        self.assertIn("命中要补测试：erp.e（出过事", errors)
        self.assertIn("命中要附真机记录：erp.d", errors)
        (root / "docs/work/device.md").write_text("erp.d 2026-11-04 4ffe8aea6 三星 LINE 点卡片 → 通过\n", encoding="utf-8")
        errors = "\n".join(gate.enforce_hits(product_map, hits, FLOWS, IMPACT, ["docs/work/device.md"], root))
        self.assertNotIn("erp.d", errors)
        (root / "docs/work/device.md").write_text("已真机\n", encoding="utf-8")
        self.assertIn("erp.d", "\n".join(gate.enforce_hits(product_map, hits, FLOWS, IMPACT, ["docs/work/device.md"], root)))
        self.assertEqual(gate.enforce_hits(product_map, {}, FLOWS, IMPACT, [], root), [])

    def test_rule_5_siblings_missing_or_weaker_and_their_baseline(self):
        rows = [row("nav:erp:a", group="g"), row("nav:cowork:a", id="cowork.a", product="Cowork", tests=["flow:f.desk"], group="g"),
                row("nav:erp:x", id="erp.x", group="lonely")]
        product_map = pm.Map({r.id: r for r in rows}, {}, {}, [], [])
        gaps = gate.sibling_gaps(product_map, FLOWS, IMPACT)
        self.assertEqual(sorted(gaps), ["cowork.a@mobile", "lonely@Cowork"])
        self.assertIn("比兄弟 erp.a 弱", gaps["cowork.a@mobile"])
        rows[2].na = {"Cowork": "Cowork 没有这一项"}
        self.assertEqual(sorted(gate.sibling_gaps(product_map, FLOWS, IMPACT)), ["cowork.a@mobile"])
        errors = gate.check_siblings(gaps, ["cowork.a@mobile", "gone@ERP", "gone@ERP"])
        self.assertTrue(any("lonely@Cowork" in e and "na" in e for e in errors))
        self.assertTrue(any("基线该删" in e and "gone@ERP" in e for e in errors))
        self.assertTrue(any("重复" in e for e in errors))
        self.assertEqual(gate.check_siblings(gaps, sorted(gaps)), [])

    def test_rule_4_lists_the_siblings_of_a_changed_row(self):
        rows = [row("nav:erp:a", group="g"), row("nav:cowork:a", id="cowork.a", product="Cowork", tests=["flow:f.desk"], group="g")]
        product_map = pm.Map({r.id: r for r in rows}, {}, {}, [], [])
        report = gate.render(product_map, {"erp.a": ["改到入口文件"]}, FLOWS, IMPACT, markdown=True)
        self.assertIn("同组兄弟入口", report)
        self.assertIn("`erp.a` 的兄弟 `cowork.a`（Cowork，侧栏）：电脑绿 手机红", report)

    def test_export_carries_config_classes_and_only_must_verify_or_device_rows(self):
        rows = [row("nav:erp:a"), row("nav:erp:b", must_verify=True, probe={"type": "liff", "path": "/desk/x"}),
                row("nav:erp:c", tests=[], device_only="要真 LINE App", device="点卡片 → 打开编辑页")]
        product_map = pm.Map({r.id: r for r in rows}, {}, {"pearnly_x": {"class": "same"}}, [], [])
        exported = gate.export(product_map)
        self.assertEqual(exported["version"], gate.EXPORT_VERSION)
        self.assertEqual(exported["config"], {"pearnly_x": {"class": "same"}})
        self.assertEqual([r["id"] for r in exported["rows"]], ["erp.b", "erp.c"])
        self.assertEqual(exported["rows"][0]["probe"], {"type": "liff", "path": "/desk/x"})
        report = gate.batch(product_map, {"erp.b": ["改到入口文件"], "erp.c": ["改到入口文件"]})
        self.assertIn("上线必验 1 个", report)
        self.assertIn("真机 erp.c：点卡片 → 打开编辑页（只能真机：要真 LINE App）", report)
        self.assertIn("真机单：本批没有只能真机的入口", gate.batch(product_map, {"erp.b": ["x"]}))

    def test_the_real_export_has_a_probe_on_every_must_verify_row(self):
        exported = gate.export(pm.load())
        must = [r for r in exported["rows"] if r["must_verify"]]
        self.assertTrue(must)
        self.assertTrue(all(r["probe"].get("type") in pm.PROBES for r in must))
        shape = [rule for rule in exported["config"].values() if rule.get("class") == "shape"]
        self.assertTrue(shape and all(rule.get("pattern") for rule in shape))


if __name__ == "__main__":
    unittest.main()
