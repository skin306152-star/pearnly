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


def run(rows, discovered, *, baseline=(), not_entries=None, config=None, keys=None, families=None, endpoints=None, ext=None):
    product_map = pm.Map({r.id: r for r in rows}, not_entries or {}, config or {}, list(baseline), [], {}, ext or {})
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

    def test_rule_4_an_endpoint_counts_as_changed_only_when_its_own_function_has_a_diff(self):
        """主会话 11-05：按函数算「改到它调用的接口」；同文件里别的函数删了不算，拿不准（模块级、装饰器、import）照旧命中。"""
        import difflib
        old = "import frappe\n\n\n@frappe.whitelist()\ndef called():\n    return 1\n\n\n@frappe.whitelist()\ndef other():\n    return 2\n"

        def changes(new):
            diff = "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), n=0))
            return pm.function_changes(old, new, diff)

        self.assertEqual(changes(old.replace("\n\n\n@frappe.whitelist()\ndef other():\n    return 2\n", "\n")), {"other"})
        self.assertEqual(changes(old.replace("return 1", "return 3")), {"called"})
        self.assertIsNone(changes(old.replace("import frappe\n", "import frappe\nimport json\n")))
        self.assertIsNone(changes(old.replace("@frappe.whitelist()\ndef called", "@frappe.whitelist(methods=[\"POST\"])\ndef called")))
        self.assertIsNone(changes(old + "\nLIMIT = 5\n"))
        self.assertEqual(changes(old + "\n\n# 说明\n"), set())
        self.assertIsNone(pm.function_changes(None, old, ""))                 # 新文件整个算
        self.assertEqual(pm.deleted_whitelisted(old, old.split("\n\n\n@frappe.whitelist()\ndef other")[0]), {"other"})

        rows = [row("nav:erp:a"), row("nav:erp:b")]
        product_map = pm.Map({r.id: r for r in rows}, {}, {}, [], [])
        endpoints = {"pearnly_x.api.called": pm.Endpoint("pearnly_x.api.called", "apps/x/api.py", ["apps/x/a.js"])}
        hit = lambda functions: gate.impact_rows(product_map, found("nav:erp:a", "nav:erp:b"), {}, ["apps/x/api.py"],
                                                 endpoints=endpoints, functions=functions)[0]
        self.assertEqual(hit({"apps/x/api.py": {"other"}}), {})              # 删同文件里别的函数：不命中
        self.assertEqual(hit({"apps/x/api.py": {"called"}}), {"erp.a": ["改到它调用的接口"]})
        self.assertEqual(hit({"apps/x/api.py": None}), {"erp.a": ["改到它调用的接口"]})   # 模块级改动：整个文件算
        self.assertEqual(hit(None), {"erp.a": ["改到它调用的接口"]})          # 没给函数（--files）：整个文件算

        import tempfile
        root = Path(tempfile.mkdtemp())
        (root / "apps/x").mkdir(parents=True)
        (root / "scripts/impact").mkdir(parents=True)
        (root / "scripts/impact/shared-parts.toml").write_text("part = []\n", encoding="utf-8")
        (root / "apps/x/a.js").write_text('const api = "pearnly_x.api."; p.call(api + "gone");', encoding="utf-8")
        (root / "apps/y").mkdir(parents=True)
        (root / "apps/y/b.js").write_text('p.call("pearnly_y.other.gone");', encoding="utf-8")
        removed = {"apps/pearnly_x/pearnly_x/api.py": {"gone"}}
        hits, _ = gate.impact_rows(product_map, {**found("nav:erp:a"), **{"nav:erp:b": pm.Found("nav:erp:b", "nav-page", "ERP", "desk",
                                   ["desktop"], ["apps/y/b.js"], "页面 b")}}, {}, ["apps/pearnly_x/pearnly_x/api.py"], root=root,
                                   endpoints={}, functions={"apps/pearnly_x/pearnly_x/api.py": {"gone"}}, removed=removed)
        self.assertEqual(hits, {"erp.a": ["改到它调用的接口"]})              # 页面还在调的接口被删：命中；别的模块同名不算

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
        record = root / "docs/work/device.md"

        def judged(text):
            record.write_text(text, encoding="utf-8")
            notes = []
            found = "\n".join(gate.enforce_hits(product_map, hits, FLOWS, IMPACT, ["docs/work/device.md"], root, notes=notes))
            return found, notes

        # 固定格式的一行六栏：算记录（表格行、列表项、全角竖线都认）
        for good in ("真机记录 | erp.d | 2026-11-04 | 4ffe8aea6 | 三星 SM-A146P LINE | 通过 | 点卡片 → 打开编辑页\n",
                     "| 真机记录 | `erp.d` | 2026-11-04 | 4ffe8aea6 | 三星 | 通过 |\n",
                     "- 真机记录｜erp.d｜2026-11-04｜4ffe8aea6｜三星｜通过\n"):
            self.assertNotIn("erp.d", judged(good)[0], good)
        # 反例（11-14：一句待办因为同文件别处有日期和十六进制串被当成了记录）：都不算
        for bad in ("已真机\n",
                    "剩 erp.d 要真机记录\n\n2026-11-14 改了 4ffe8aea6\n",                      # 待办句 + 别处的日期与 SHA
                    "erp.d 这一行这次改到了\n",                                                  # 只有 id 的句子
                    "erp.d 2026-11-04 4ffe8aea6 三星 LINE 点卡片 → 通过\n",                       # 旧写法：没有固定栏目
                    "真机记录 | erp.d | 2026-11-04 | 三星 | 通过\n\n被测提交 4ffe8aea6\n",        # SHA 不在同一条目
                    "真机记录 | erp.x | 2026-11-04 | 4ffe8aea6 | 三星 | 通过\n\nerp.d 也看了\n",   # 条目是别的行的
                    "真机记录 | erp.d | 2026-11-04 | 4ffe8aea6 | 三星 | 待测\n",                    # 结果不是三种之一
                    "真机记录 | erp.d | 2026-11-04 | 4ffe8aea6 | 三星 | 没测到\n",                  # 没测到没写原因
                    "真机记录 | erp.d | 2026-13-40 | 4ffe8aea6 | 三星 | 通过\n",                    # 日期不成立
                    "真机记录 | erp.d | 2026-11-04 | 4ffe8aea6 |  | 通过\n"):                       # 没写设备
            self.assertIn("命中要附真机记录：erp.d", judged(bad)[0], bad)
        found, notes = judged("真机记录 | erp.d | 2026-11-04 | 4ffe8aea6 | 三星 | 不通过：编辑页白屏\n")
        self.assertIn("真机不通过：erp.d", found)                                                  # 不通过的记录不放行
        self.assertIn("编辑页白屏", found)
        found, notes = judged("真机记录 | erp.d | 2026-11-04 | 4ffe8aea6 | 三星 | 没测到：手机不在线\n")
        self.assertNotIn("erp.d", found)                                                           # 没测到：不拦，但要说出来
        self.assertEqual(len(notes), 1)
        self.assertIn("真机没测到：erp.d", notes[0])
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

    def test_cc_coverage_every_web_action_has_a_cc_tool_or_a_reason(self):
        """ADR-027 标准一：ERP / Cowork 的网页动作（页面、页头按钮）对上 CC 工具（cc）或写不适用（cc_na）；两个都没写的要在
        cc_baseline.txt（只许减少）；写的工具要是这个产品地图里真有的 CC 工具；cc 与 cc_na 只写一个；只写在网页动作行上。"""
        tool = row("tool:erp:erp_list_documents", id="erp.cc.tool.erp_list_documents", kind="cc-tool", channel="cc-chat")
        covered = row("nav:erp:purchase", id="erp.purchase", cc=["erp_list_documents"])
        reason = row("nav:erp:cashier", id="erp.cashier", cc_na="当面收款")
        missing = row("nav:erp:sales", id="erp.sales")
        wrong = row("nav:erp:stock", id="erp.stock", cc=["cowork_list_documents"])
        both = row("nav:erp:team", id="erp.team", cc=["erp_list_documents"], cc_na="x")
        misplaced = row("tool:erp:erp_pending", id="erp.cc.tool.erp_pending", kind="cc-tool", channel="cc-chat", cc_na="x")
        dms = row("nav:dms:home", id="dms.home", product="DMS")  # 只管 ERP、Cowork
        rows = [tool, covered, reason, missing, wrong, both, misplaced, dms]
        product_map = pm.Map({r.id: r for r in rows}, {}, {}, [], [])
        errors = gate.check_cc(product_map, [])
        self.assertTrue(any("erp.sales" in e and "cc_na" in e for e in errors))  # 新加的网页动作没配就拦
        self.assertTrue(any("cowork_list_documents" in e and "erp.stock" in e for e in errors))
        self.assertTrue(any("erp.team" in e and "只写一个" in e for e in errors))
        self.assertTrue(any("erp.cc.tool.erp_pending" in e and "只写在网页动作行" in e for e in errors))
        self.assertFalse(any(name in e for e in errors for name in ("erp.purchase", "erp.cashier", "dms.home")))
        product_map = pm.Map({r.id: r for r in (tool, covered, reason, missing)}, {}, {}, [], [])
        self.assertEqual(gate.check_cc(product_map, ["erp.sales"]), [])
        errors = gate.check_cc(product_map, ["erp.sales", "erp.purchase", "erp.purchase"])
        self.assertTrue(any("基线该删" in e and "erp.purchase" in e for e in errors))
        self.assertTrue(any("重复" in e for e in errors))

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


E2E = {"e2e.erp.login": {"id": "e2e.erp.login", "product": "ERP", "runs": ["desktop", "iphone"], "where": "登录",
                         "asserts": ["落回本站"], "since": "2026-11-05 56c1807ed"},
       "e2e.erp.planned": {"id": "e2e.erp.planned", "product": "ERP", "runs": ["desktop", "mobile"], "where": "还没跑通",
                           "asserts": ["x"]}}
EXT = {"kc:pearnly-business:remember-me": {"class": "shape", "where": "production", "check": "记住我 30 天", "reads": "GET realm"}}


class LoginEndToEndAndExternalSettings(unittest.TestCase):
    """11-05：登录后 e2e（部署后对测试租户跑）当测试引用；站外设置引用要在 config.toml 的 [ext] 里（docs/work/2026-11-05-product-map-next.md）。"""

    def setUp(self):
        self.saved = dict(pm._E2E_CACHE)
        pm._E2E_CACHE["default"] = E2E

    def tearDown(self):
        pm._E2E_CACHE.clear()
        pm._E2E_CACHE.update(self.saved)

    def test_a_scenario_that_has_passed_for_real_makes_its_ends_green(self):
        # iphone 跑法算手机那一端
        self.assertEqual(pm.status(row("nav:erp:a", tests=["e2e:e2e.erp.login"]), FLOWS, IMPACT), {"desktop": pm.GREEN, "mobile": pm.GREEN})
        self.assertEqual(pm.status(row("nav:erp:a", tests=["e2e:e2e.erp.login@desktop"]), FLOWS, IMPACT),
                         {"desktop": pm.GREEN, "mobile": pm.RED})
        self.assertEqual(run([row("nav:erp:a", tests=["e2e:e2e.erp.login"])], found("nav:erp:a")), [])

    def test_a_scenario_without_since_or_not_registered_cannot_be_cited(self):
        errors = "\n".join(run([row("nav:erp:a", tests=["e2e:e2e.erp.planned"]), row("nav:erp:b", tests=["e2e:e2e.erp.nope"]),
                                row("nav:erp:c", tests=["e2e:e2e.erp.login@tablet"])], found("nav:erp:a", "nav:erp:b", "nav:erp:c")))
        self.assertIn("还没在测试租户上真实跑通过", errors)
        self.assertIn("引用的场景不在 tests/e2e/registry.toml", errors)
        self.assertIn("这个场景不跑 tablet", errors)
        # 没跑通的场景不让行变绿：两端都还是红
        self.assertEqual(pm.status(row("nav:erp:a", tests=["e2e:e2e.erp.planned"]), FLOWS, IMPACT), {"desktop": pm.RED, "mobile": pm.RED})

    def test_external_setting_refs_must_be_declared_in_ext(self):
        cited = row("nav:erp:a", config=["kc:pearnly-business:remember-me", "line:webhook"])
        errors = "\n".join(run([cited], found("nav:erp:a"), ext=EXT))
        self.assertIn("站外设置依赖 line:webhook 不在 config.toml 的 [ext]", errors)
        self.assertNotIn("remember-me 不在", errors)

    def test_load_checks_ext_sections_and_registry_fields(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "config.toml").write_text('[ext."kc:x"]\nclass = "varies"\nwhere = "moon"\n\n[ext."site:y"]\nclass = "same"\n'
                                                'where = "local"\ncheck = "c"\nreads = "r"\n', encoding="utf-8")
            errors = "\n".join(pm.load(tmp).errors)
        self.assertIn("[ext] kc:x 的 class 只能是", errors)
        self.assertIn("[ext] kc:x 的 where 只能是", errors)
        self.assertIn("[ext] kc:x 要写 check", errors)
        self.assertIn("[ext] 的 'site:y' 要写成", errors)
        bad = {"e2e.x": {"id": "e2e.x", "product": "DMS", "runs": ["tablet"], "where": "", "asserts": [], "since": "昨天"}}
        errors = "\n".join(pm.validate_e2e(bad, path=Path("/nonexistent")))
        for text in ("product 只能是", "runs 只能是", "要写 where", "since 写成"):
            self.assertIn(text, errors)

    def test_impact_list_batch_and_export_mention_e2e_and_ext(self):
        rows = [row("nav:erp:a", tests=["e2e:e2e.erp.login"], must_verify=True, probe={"type": "assistant-gate"})]
        product_map = pm.Map({r.id: r for r in rows}, {}, {}, [], [], {}, EXT)
        hits = {"erp.a": ["改到入口文件"]}
        self.assertIn("部署后跑（测试租户登录后 e2e，合入前没有它兜底）：`e2e.erp.login`", gate.render(product_map, hits, FLOWS, IMPACT, True))
        self.assertIn("登录后 e2e（部署后对测试租户跑全套，其中本批命中）：e2e.erp.login", gate.batch(product_map, hits))
        self.assertEqual(gate.export(product_map)["ext"], EXT)

    def test_the_real_registry_and_ext_sections_are_valid(self):
        self.assertEqual(pm.validate_e2e(pm.e2e_scenarios()), [])
        real = pm.load()
        self.assertEqual(real.errors, [])
        self.assertTrue({"kc:site-client", "line:webhook", "liff:endpoint", "ext:fatcc.com", "dns:tenant"} <= set(real.ext))


if __name__ == "__main__":
    unittest.main()
