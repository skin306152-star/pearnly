"""产品地图的发现器与行校验（scripts/impact/product_map.py，评估 docs/work/2026-11-04-product-map.md）。纯离线，不起站点。"""
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "impact"))
import product_map as pm  # noqa: E402


def fake_repo(files: dict) -> pm.Repo:
    root = Path(tempfile.mkdtemp())
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(textwrap.dedent(text), encoding="utf-8")
    return pm.Repo(root, sorted(files))


ERP_HOOKS = "apps/pearnly_erp_portal/pearnly_erp_portal/hooks.py"
COWORK_HOOKS = "apps/pearnly_cowork/pearnly_cowork/hooks.py"
EMPTY_HOOKS = "pearnly_navigation = [{'pages': []}]\n"


class Discovery(unittest.TestCase):
    def test_every_kind_on_the_real_repo(self):
        found = pm.discover()
        for anchor, kind in (("nav:erp:pearnly-sales", "nav-page"), ("nav:cowork:pearnly-intake", "nav-page"),
                             ("action:erp:pearnly-sales:Export", "page-action"),
                             ("report:erp:erp_stock_count_variance", "report"),
                             ("line:erp:menu:stocktake", "line-menu"), ("line:erp:postback:mode", "line-postback"),
                             ("line:cowork:postback:cowork_erp_start", "line-postback"),
                             ("line:erp:link:/desk/{root}/line/{x}", "line-link"),
                             ("line:cowork:link:/desk/pearnly-intake/new/{x}", "line-link"),
                             ("line:erp:event:follow", "line-event"), ("line:cowork:message:image", "line-message"),
                             ("liff:erp:/stocktake", "liff-page"),
                             ("tool:erp:erp_push_documents", "cc-tool"), ("tool:cowork:cowork_pending", "cc-tool"),
                             ("tool:erp:pearnly_whoami", "cc-tool"), ("operator:cowork:submit", "cc-operator-hook"),
                             ("ccview:erp:operator", "cc-view"), ("ccaction:erp:switch-account", "cc-action"),
                             ("ccaction:cowork:mrerp-auth", "cc-action"), ("scan:erp.stock-count.entry.camera", "scan"),
                             ("scan:shared.upload.take-photo", "scan")):
            self.assertIn(anchor, found)
            self.assertEqual(found[anchor].kind, kind, anchor)
            self.assertTrue(found[anchor].files, f"{anchor} 推不出文件")
        # 分发事件不单列；富菜单格打开的网页由菜单格代表
        self.assertNotIn("line:erp:event:postback", found)
        self.assertNotIn("line:erp:link:/desk/pearnly-stock-count", found)
        for item in found.values():
            for path in item.files:
                self.assertTrue((ROOT / path).is_file(), f"{item.anchor} 的文件不存在：{path}")

    def test_line_postbacks_by_action_key_links_and_message_types(self):
        repo = fake_repo({
            ERP_HOOKS: EMPTY_HOOKS, COWORK_HOOKS: EMPTY_HOOKS,
            "apps/pearnly_erp_portal/pearnly_erp_portal/integrations/line_flow.py": """
                ROUTE = "/desk/pearnly-x"
                def buttons(name, step, doc):
                    return ["a=mode:" + name, {"a": "erp", "s": step}, entry_url(ROUTE), entry_url(f"/desk/{doc}/edit")]
            """,
            "apps/pearnly_erp_portal/pearnly_erp_portal/integrations/handlers.py": """
                def handle(etype, message):
                    if etype == "follow": pass
                    if etype == "postback": pass
                    if message.get("type") in ("image", "file"): pass
            """,
        })
        found = pm.discover_line(repo)
        anchors = {item.anchor for item in found}
        self.assertEqual(anchors, {"line:erp:postback:mode", "line:erp:postback:erp", "line:erp:link:/desk/pearnly-x",
                                   "line:erp:link:/desk/{doc}/edit", "line:erp:event:follow",
                                   "line:erp:message:image", "line:erp:message:file"})

    def test_cc_actions_from_map_notes_and_unannotated_navigation(self):
        repo = fake_repo({
            "frontend/assistant/src/lib/site.ts": "// map: switch-account\nexport function go(s) { location.assign(s); }\n",
            "frontend/assistant/src/lib/login.tsx": "export const L = () => null;\n// map: mrerp-auth@cowork logout\n",
            "frontend/assistant/src/lib/other.ts": "if (u) window.open(u);\nlocation.href = '/x';\n// location.href = '/commented';\n",
        })
        found = {item.anchor: item for item in pm.discover_cc_actions(repo)}
        self.assertEqual(sorted(found), [
            "ccaction:cowork:logout", "ccaction:cowork:mrerp-auth", "ccaction:cowork:switch-account",
            "ccaction:erp:logout", "ccaction:erp:switch-account",
            "ccnav:lib/other.ts:if (u) window.open(u);", "ccnav:lib/other.ts:location.href = '/x';"])
        self.assertEqual(found["ccaction:erp:switch-account"].files, ["frontend/assistant/src/lib/site.ts"])
        self.assertEqual(found["ccnav:lib/other.ts:if (u) window.open(u);"].kind, "cc-nav")

    def test_config_keys_constants_and_prefix_families(self):
        repo = fake_repo({
            "apps/pearnly_core/pearnly_core/a.py": """
                import frappe
                KEY = "pearnly_menu"
                def read(key):
                    frappe.conf.get("pearnly_engine"); frappe.conf.get(KEY); frappe.conf.pearnly_issuer
                    frappe.get_site_config().get("pearnly_flag"); update_site_config("pearnly_written", 1)
                    frappe.conf.get(f"pearnly_line_{key}"); frappe.conf.get("other_key")
            """,
        })
        keys, families = pm.discover_config(repo)
        self.assertEqual(sorted(keys), ["pearnly_engine", "pearnly_flag", "pearnly_issuer", "pearnly_menu", "pearnly_written"])
        self.assertEqual(list(families), ["pearnly_line_"])

    def test_endpoint_callers_by_full_name_split_prefix_form_script_and_other_roots(self):
        app = "apps/pearnly_erp_portal/pearnly_erp_portal"
        repo = fake_repo({
            f"{app}/sales/api.py": """
                import frappe
                @frappe.whitelist()
                def api_full(): pass
                @frappe.whitelist(methods=["POST"])
                def api_split(): pass
                @frappe.whitelist()
                def api_python(): pass
                @frappe.whitelist()
                def api_nobody(): pass
                @frappe.whitelist()
                def api_only_tests(): pass
            """,
            f"{app}/public/desk/a.js": 'frappe.call("pearnly_erp_portal.sales.api.api_full"); u.api = "pearnly_erp_portal.sales.api.";',
            f"{app}/public/desk/b.js": 'u.call("api_split")',
            f"{app}/cc/tools.py": "from pearnly_erp_portal.sales.api import api_python\n",
            f"{app}/pearnly_erp_portal/doctype/erp_x/erp_x.py": "import frappe\nclass X:\n    @frappe.whitelist()\n    def form_action(self): pass\n",
            f"{app}/pearnly_erp_portal/doctype/erp_x/erp_x.js": 'frm.call("form_action")',
            "infrastructure/keycloak/themes/x/login.ftl": "/api/method/pearnly_core.account.api.from_theme",
            "apps/pearnly_core/pearnly_core/account/api.py": "import frappe\n@frappe.whitelist(allow_guest=True)\ndef from_theme(): pass\n",
            f"{app}/sales/upload.py": "import frappe\n@frappe.whitelist()\ndef api_doc_upload_begin(): pass\n"
                                      "@frappe.whitelist()\ndef api_doc_upload_finish(): pass\n@frappe.whitelist()\ndef api_doc_upload_cancel(): pass\n"
                                      "@frappe.whitelist()\ndef api_sales_export(): pass\n",
            f"{app}/public/desk/upload.js": 'const M = "pearnly_erp_portal.sales.upload.api_doc_upload";\n'
                                            'post(`${M}_begin`); post(M + "_finish");',
            f"{app}/public/desk/list.js": 'p.export_file(`${u.api}api_sales_export`)',
            "apps/pearnly_core/pearnly_core/express_operator/api.py": "import frappe\n@frappe.whitelist()\ndef status(): pass\n"
                                                                     "@frappe.whitelist()\ndef intake(): pass\n",
            # 11-04 误判：别处带引号写了同名短名（导出前缀 + "status"、单据页的 "intake"），不是代录接口的调用方
            "apps/pearnly_core/pearnly_core/public/desk/product.js": 'const EXPORT_API = "pearnly_core.exports."; call(EXPORT_API + "status");',
            f"{app}/public/desk/purchase-form.js": 'u.entry = "intake";',
            # 真调用方：同目录里一个文件写了完整模块路径做前缀，别的文件用它拼名
            "frontend/assistant/src/components/operator/live-source.ts": 'export const API = "pearnly_core.express_operator.api.";\n'
                                                                          'get(API + "intake");',
            "frontend/assistant/src/components/operator/tasks.ts": 'import { API } from "./live-source"; get(API + "status");',
            "tests/test_sales.py": "from pearnly_erp_portal.sales.api import api_only_tests\n",
            "scripts/flows/sales.py": '"pearnly_erp_portal.sales.api.api_nobody"',
        })
        endpoints = pm.discover_endpoints(repo)
        callers = {name.rsplit(".", 1)[1]: e.callers for name, e in endpoints.items()}
        self.assertEqual(callers["api_full"], [f"{app}/public/desk/a.js"])
        self.assertEqual(callers["api_split"], [f"{app}/public/desk/b.js"])
        self.assertEqual(callers["api_python"], [f"{app}/cc/tools.py"])
        self.assertEqual(callers["form_action"], [f"{app}/pearnly_erp_portal/doctype/erp_x/erp_x.js"])
        self.assertEqual(callers["from_theme"], ["infrastructure/keycloak/themes/x/login.ftl"])
        # 前缀常量加后缀拼名（`${M}_begin` / M + "_finish"）、模板串里的 api_ 名字都算；没拼过的后缀不算
        self.assertEqual(callers["api_doc_upload_begin"], [f"{app}/public/desk/upload.js"])
        self.assertEqual(callers["api_doc_upload_finish"], [f"{app}/public/desk/upload.js"])
        self.assertEqual(callers["api_doc_upload_cancel"], [])
        self.assertEqual(callers["api_sales_export"], [f"{app}/public/desk/list.js"])
        # 通用短名：只认同目录写了模块路径的那些文件（CC 代录），别处带引号的同名不算
        self.assertEqual(callers["status"], ["frontend/assistant/src/components/operator/tasks.ts"])
        self.assertEqual(callers["intake"], ["frontend/assistant/src/components/operator/live-source.ts"])
        # 测试、流程用例不算调用方
        self.assertEqual(callers["api_nobody"], [])
        self.assertEqual(callers["api_only_tests"], [])


def row(**overrides):
    base = dict(id="erp.line.card-edit", product="ERP", channel="line", ends=["mobile"], kind="line-link",
                anchor="line:erp:link:/x", where="LINE 卡片「แก้ไข」")
    base.update(overrides)
    return pm.Row(**base)


# 运行时拼出来：本文件里不出现这个连起来的名字
MISSING = "absent" + "_marker"


class Rows(unittest.TestCase):
    flows = {"f.both": {"id": "f.both", "viewports": ["desktop", "mobile"]}, "f.desk": {"id": "f.desk", "viewports": ["desktop"]}}
    impact = {"e.manual": {"id": "e.manual", "check": "manual"}, "e.page": {"id": "e.page", "check": "auto", "smoke": "page"},
              "e.scan": {"id": "e.scan", "check": "auto", "smoke": "scan"}, "e.off": {"id": "e.off", "check": "offline"}}

    def validate(self, *rows):
        product_map = pm.Map({r.id: r for r in rows}, {}, {}, [], [])
        repo = pm.Repo(ROOT, ["tests/test_product_map.py"])
        return pm.validate(product_map, repo, self.flows, self.impact, {"line"})

    def test_status_by_end_and_strength(self):
        both = ["desktop", "mobile"]
        self.assertEqual(pm.status(row(ends=both, tests=["flow:f.desk"]), self.flows, self.impact),
                         {"desktop": pm.GREEN, "mobile": pm.RED})
        self.assertEqual(pm.status(row(ends=both, tests=["flow:f.both@mobile"]), self.flows, self.impact),
                         {"desktop": pm.RED, "mobile": pm.GREEN})
        # 只有打开冒烟 / 人工清单 = 黄，不算有测试（主会话 11-04）
        for ref in ("impact:e.page", "impact:e.manual"):
            levels = pm.status(row(tests=[ref]), self.flows, self.impact)
            self.assertEqual(levels, {"mobile": pm.YELLOW})
            self.assertFalse(pm.tested(levels["mobile"]))
        for ref in ("impact:e.scan", "impact:e.off", "unit:tests/test_product_map.py"):
            self.assertEqual(pm.status(row(tests=[ref]), self.flows, self.impact), {"mobile": pm.GREEN})
        # 人工清单只对标了只能真机的行有效
        device = row(tests=["impact:e.manual"], device_only="要真 LINE App", device="点卡片 → 打开编辑页")
        self.assertEqual(pm.status(device, self.flows, self.impact), {"mobile": pm.DEVICE})
        self.assertTrue(pm.tested(pm.DEVICE))

    def test_validation_rejects_bad_values_and_references(self):
        errors = "\n".join(self.validate(
            row(id="a", product="X", channel="sms", kind="k", ends=["tablet"], where=" "),
            row(id="b", anchor="b", parts=["nope"], config=["pearnly_x"],
                tests=["flow:missing", "flow:f.desk@mobile", "impact:missing", "unit:tests/none.py",
                       "unit:tests/test_product_map.py::" + MISSING, "manual:x"]),
            row(id="c", anchor="c", device="点一下"),
            row(id="d", anchor="d", device_only="要真机"),
            row(id="e", anchor="e", probe={"type": "ping"}),
        ))
        for text in ("product 只能是", "channel 只能是", "kind 只能是", "ends 只能是", "where 要写", "共用件 nope",
                     "配置依赖 'pearnly_x'", "流程不在", "这条流程不跑 mobile", "入口不在 shared-parts", "测试文件不存在",
                     "找不到 " + MISSING, "只能是 flow: / impact: / unit:", "真机清单只对只能真机的行有效",
                     "就要写 device", "probe.type 只能是"):
            self.assertIn(text, errors)
        self.assertEqual(self.validate(row(parts=["line"], config=["site:pearnly_line_liff_id", "liff:endpoint"],
                                           tests=["flow:f.both@mobile", "impact:e.off", "unit:tests/test_product_map.py::Rows"],
                                           probe={"type": "liff"})), [])

    def test_load_reports_duplicates_unknown_fields_and_reasonless_exemptions(self):
        directory = Path(tempfile.mkdtemp())
        (directory / "erp.toml").write_text(textwrap.dedent('''
            [[row]]
            id = "a"
            product = "ERP"
            channel = "desk"
            ends = ["desktop"]
            kind = "nav-page"
            anchor = "nav:erp:x"
            where = "侧栏"
            [[row]]
            id = "a"
            product = "ERP"
            channel = "desk"
            ends = ["desktop"]
            kind = "nav-page"
            anchor = "nav:erp:x"
            where = "侧栏"
            colour = "red"
        '''), encoding="utf-8")
        (directory / "not_entries.toml").write_text('[[anchor]]\nanchor = "tool:erp:x"\nreason = ""\n', encoding="utf-8")
        (directory / "config.toml").write_text('[key.pearnly_x]\nclass = "maybe"\n[key.pearnly_y]\nclass = "shape"\n[key.pearnly_z]\nclass = "shape"\npattern = "("\n'
                                               '[family.pearnly_line_]\nclass = "presence"\n',
                                               encoding="utf-8")
        (directory / "untested_baseline.txt").write_text("# 注释\nerp.a@desktop\n", encoding="utf-8")
        loaded = pm.load(directory)
        errors = "\n".join(loaded.errors)
        self.assertIn("不认识的字段 ['colour']", errors)
        self.assertIn("没写原因", errors)
        self.assertIn("pearnly_x 的 class 只能是", errors)
        self.assertIn("pearnly_y 是 shape，要写 pattern", errors)
        self.assertIn("pearnly_z 的 pattern 不是正则", errors)
        self.assertEqual(list(loaded.rows), ["a"])
        self.assertIn("pearnly_line_*", loaded.config)
        self.assertEqual(loaded.baseline, ["erp.a@desktop"])


if __name__ == "__main__":
    unittest.main()
