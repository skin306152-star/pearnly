"""CC 选择公司弹窗（用户 11-07，评估 docs/work/2026-11-07-cc-connect-company-picker-评估.md §12.1 定案、§14 的 B 步）：

- 对话里点「公司」不再是下拉，弹出原生对话框：搜索、MR.ERP / Express 标签（有纯 ERP 公司时多一个「ERP」）、连接里读到的全部账套公司；
- 列表来自服务端 choices（已建的与目录里还没建的按身份合并，搜索、按标签筛、分页都在服务端），没建的点了用现有 create 顺手建；
- 第一行「最近」= 不挂公司（新对话里才有），当前选中的打勾；一家都没有时「没有结果」+「连接」（打开设置 → 连接）；
- 新对话下巴、手机问候行、代录新建页（同一个 DraftChin）与定时任务「＋」选公司共用这一个弹窗。

做法同 tests/test_assistant_ui.py：源码断言 + 用 esbuild 打包交给 node 跑的行为检查（要先 npm ci --prefix frontend/assistant）。
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from test_assistant_ui import ESBUILD, MA_SETUP, NODE, SOCKET_STUB, block, read, run_ts  # noqa: E402

STUBS = {"socket.io-client": SOCKET_STUB, "cc-ma-setup": MA_SETUP,
         "@/lib/profile": 'export const APP_NAME = "CC"; export const useProfile = () => undefined;'}
NEEDS_NODE = unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")


class PickerSourceTests(unittest.TestCase):
    def setUp(self):
        self.picker = read("components/company-picker.tsx")

    def test_every_place_that_picks_a_company_opens_the_one_dialog(self):
        projects = read("components/assistant-ui/projects.tsx")
        draft = projects.split('const DraftChin: FC<{ side?: "top" | "bottom" }> = ({ side = "top" }) => {', 1)[1].split("\n};", 1)[0]
        # 下巴（电脑、代录新建页）与手机问候行是同一个 DraftChin：点了弹窗，不再是下拉菜单
        self.assertIn("onClick={() => openCompanyPicker(pick, draft.project)}", draft)
        self.assertNotIn("DropdownMenu", draft)
        self.assertNotIn("CompanyChoices", projects)
        self.assertNotIn("listCompanies", projects)
        self.assertIn('<DraftChin side="bottom" />', block(projects, "export const WelcomeFiling: FC = () => {"))
        # 有公司分组的产品一直出现（一家都没有时弹窗里给「连接」），不再先问「至少有一家」
        self.assertNotIn("hasAnyCompany", projects)
        # 定时任务「＋」：必须选一家，不传当前公司（不出「最近」）
        self.assertIn("openCompanyPicker((id) => void seedFor(id))", read("components/scheduled-tasks.tsx"))
        # 弹窗只挂一次
        app = read("assistant.tsx")
        self.assertEqual(app.count("<CompanyPickerDialog />"), 1)
        self.assertNotIn("add-company", app)

    def test_rows_come_from_the_server_choices_and_unbuilt_ones_are_built_on_pick(self):
        companies = read("lib/companies.ts")
        self.assertIn('await call("choices")', companies)
        self.assertIn("search: search || undefined, adapter: adapter || undefined, start, limit: CHOICES_PAGE,", companies)
        self.assertNotIn('call("candidates")', companies)   # 只看目录、在内存里筛的旧候选不再从页面调
        self.assertIn("void listChoices(text.trim(), tab, start)", self.picker)
        self.assertNotIn("rows.filter(", self.picker)        # 标签、搜索都在服务端筛
        pick = self.picker.split("const pick = async (row: CompanyChoice) => {", 1)[1].split("\n  };", 1)[0]
        self.assertIn("if (row.company) return finish(row.company, row.title);", pick)
        self.assertIn("const made = await createCompany(row, key);", pick)
        self.assertIn("keys.current.set(row.identity, key);", pick)   # 同一行重点不重建
        # 「显示更多」接着服务端下一页
        self.assertIn("onClick={() => load(search, erp, rows.length)}", self.picker)
        self.assertIn("setMore(out.rows.length >= CHOICES_PAGE);", self.picker)

    def test_recents_first_and_the_current_company_is_ticked(self):
        body = self.picker.split('data-slot="cc-candidates">', 1)[1]
        self.assertLess(body.index('data-slot="cc-company-picker-recents"'), body.index("rows.map((row) => ("))
        self.assertIn("const recents = current !== null && !typing;", self.picker)
        # 11-15 真机：转圈时「最近」那一行就在、点得到，加载中误选；列表回来前不可点
        self.assertIn('data-slot="cc-company-picker-recents" disabled={!!busy || rows === null}', self.picker)
        self.assertIn('onClick={() => finish("", "")}', self.picker)
        self.assertIn('{current === "" && <CheckIcon className="shrink-0 text-ink-3" />}', self.picker)
        self.assertIn(") : row.company && row.company === current ? (\n                <CheckIcon", self.picker)

    def test_nothing_connected_offers_the_connections_settings(self):
        self.assertIn("{nothing && hasConnections && (", self.picker)
        self.assertIn('openSettings("connections");', self.picker.split("const connect = () => {", 1)[1].split("};", 1)[0])
        self.assertIn("{st.connect}", self.picker)
        desktop = read("lib/desktop.ts")
        has = block(desktop, "export function useHasConnections(): boolean {")
        # 同设置页：桌面版都有「连接」，浏览器里看产品声明了电脑列表（connect.computers，Cowork 与 ERP 都声明，B50 不按产品名写死）
        self.assertIn("const declared = useProjects((s) => !!s.connect.computers);", has)
        self.assertIn("return app || declared;", has)
        settings = read("components/settings.tsx")
        # hook 无条件调用（11-20 白屏：写成 `!api && !!useProjects(...)` 时 api 有值后少调一个 hook）
        self.assertIn("const declared = useProjects((s) => s.connect.computers);", settings)
        self.assertIn("const web = !api && !!declared;", settings)

    def test_refresh_lives_in_the_header_and_follows_the_catalog_section(self):
        head = self.picker.split('<div className="cc-dialog-head">', 1)[1].split("<DialogPrimitive.Description", 1)[0]
        self.assertIn("{canRefresh && (", head)
        self.assertIn('const canRefresh = useSection("catalog");', self.picker)
        self.assertIn("aria-label={co.refreshSets}", head)


@NEEDS_NODE
class PickerBehaviourTests(unittest.TestCase):
    def run_page(self, body):
        run_ts('import "cc-test-setup";\nimport "cc-ma-setup";\nimport assert from "node:assert/strict";\n' + body
               + "\n(globalThis as any).done();\n", stubs=STUBS)

    def test_tabs_follow_the_server_adapters_and_open_on_the_first_rows_kind(self):
        self.run_page(r"""
import { firstTab, openCompanyPicker, tabsOf, useCompanyPicker } from "@/components/company-picker";
// 一种都没有不摆标签；有就摆，最后是占位的「云端 Express」（11-20）
assert.deepEqual(tabsOf([]), []);
assert.equal(firstTab([], []), "");
assert.deepEqual(tabsOf(["express"]), ["express", "express_cloud"]);
assert.equal(firstTab(["express"], [{ adapter: "express" }]), "express");
// MR.ERP、Express、ERP 的先后，云端 Express 最后；认不出的丢掉
assert.deepEqual(tabsOf(["express", "mrerp", "bogus"]), ["mrerp", "express", "express_cloud"]);
assert.deepEqual(tabsOf(["erp", "express"]), ["express", "erp", "express_cloud"]);
// 停在第一行那种（我最近用的那家），第一行认不出或没有就第一个标签
assert.equal(firstTab(["express", "mrerp"], [{ adapter: "express" }, { adapter: "mrerp" }]), "express");
assert.equal(firstTab(["express", "mrerp"], []), "mrerp");
assert.equal(firstTab(["express", "erp"], [{ adapter: "erp" }]), "erp");
// 打开：新对话带当前草稿的公司（""= 最近）；定时任务不带（null，不出「最近」）
const got: string[] = [];
openCompanyPicker((id) => got.push(id), "");
assert.equal(useCompanyPicker.getState().open, true);
assert.equal(useCompanyPicker.getState().current, "");
openCompanyPicker((id) => got.push(id));
assert.equal(useCompanyPicker.getState().current, null);
""")

    def test_choices_ask_the_declared_product_api_with_the_tab_and_page(self):
        self.run_page(r"""
import { refreshProjects, resetProjects } from "@/lib/managed-agents/projects";
import { CHOICES_PAGE, listChoices } from "@/lib/companies";
const g = globalThis as any;
const ok = (message: any) => ({ ok: true, status: 200, json: async () => ({ message }) });
const API = "pearnly_cowork.companies.api.";
const asked: string[] = [];
const realFetch = g.fetch;
g.fetch = async (url: string, init: any = {}) => {
  asked.push(String(url));
  return realFetch(url, init);
};
g.respond = async (method: string) => {
  if (method === "projects") return ok({ kind: "company", period: true, scope: true, api: API, sections: ["create"], projects: [] });
  if (method.includes(API + "choices")) return ok({ rows: [{ identity: "tax:1:00000", title: "A", company: null, connection: "c1",
    account_set_key: "k1", adapter: "mrerp" }], adapters: ["express", "mrerp"] });
  return ok(null);
};
resetProjects();
await refreshProjects();
const out = await listChoices("abc", "mrerp", 50);
assert.equal(out.rows[0].company, null);
assert.deepEqual(out.adapters, ["express", "mrerp"]);
assert.equal(CHOICES_PAGE, 50);
const url = asked.find((u) => u.includes(API + "choices"))!;
assert.ok(url, asked.join(" "));
const query = new URL(url, "https://site.test").searchParams;
assert.equal(query.get("search"), "abc");
assert.equal(query.get("adapter"), "mrerp");
assert.equal(query.get("start"), "50");
assert.equal(query.get("limit"), "50");
// 不按标签筛时不带 adapter
asked.length = 0;
await listChoices("", "", 0);
const plain = new URL(asked.find((u) => u.includes(API + "choices"))!, "https://site.test").searchParams;
assert.equal(plain.has("adapter"), false);
assert.equal(plain.has("search"), false);
""")


if __name__ == "__main__":
    unittest.main()
