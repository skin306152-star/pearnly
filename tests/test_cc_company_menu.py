"""CC 删左侧「公司」与公司页、功能搬进「···」（用户 11-07，评估 docs/work/2026-11-07-cc-connect-company-picker-评估.md §1.3、
§12.1 定案、§14 的 C 步）：

- 侧栏没有「公司」一行，公司列表页、公司页的文件与视图都删了（不留隐藏路由）；
- 公司页上的改名、刷新账套、在 ERP 中打开、删除公司搬到侧栏公司组头「···」与对话右侧栏公司卡「···」，两处同一份 CompanyMenuItems，
  按产品声明出（服务端按权限给：Cowork 改名、删除只给管理员），直写、代录同一个侧栏；
- 删除公司先确认一次（同删对话的确认弹窗：「删除公司？」「取消 / 删除」，焦点在「取消」）；
- 改名就地改（同对话行的 InlineRename）；公司卡「全部账套」展开；知识库、定时任务卡空的也显示「＋」；「本月」卡的月份胶囊只换卡里看的月份。
"""
import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from test_assistant_ui import ESBUILD, MA_SETUP, NODE, SOCKET_STUB, SRC, block, read, run_ts  # noqa: E402

STUBS = {"socket.io-client": SOCKET_STUB, "cc-ma-setup": MA_SETUP,
         "@/lib/profile": 'export const APP_NAME = "CC"; export const useProfile = () => undefined;'}
NEEDS_NODE = unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")


class CompanyMenuSourceTests(unittest.TestCase):
    def setUp(self):
        self.menu = read("components/company-menu.tsx")

    def test_the_company_pages_and_the_sidebar_entry_are_gone(self):
        for rel in ("components/companies.tsx", "components/company-page.tsx", "components/add-company.tsx"):
            self.assertFalse((SRC / rel).exists(), rel)
        app = read("assistant.tsx")
        self.assertNotIn("cc-nav-companies", app)
        self.assertNotRegex(app, r"\bCompanyNav\b")
        self.assertNotIn("<CompanyPage", app)
        nav = read("lib/nav.ts")
        self.assertIn('export type View = "chat" | "tasks" | "operator";', nav)
        source = "\n".join(p.read_text(encoding="utf-8") for p in SRC.rglob("*.ts*"))
        for gone in ('setView("companies")', 'setView("company")', "useOpenCompany", "usePagePeriod", "keepEmpty", "co.draftNotes"):
            self.assertNotIn(gone, source, gone)

    def test_one_menu_for_the_group_header_and_the_company_card(self):
        projects = read("components/assistant-ui/projects.tsx")
        header = projects.split("export const ProjectHeader = memo(", 1)[1].split("\n});", 1)[0]
        self.assertIn("<CompanyMenuItems company={project.id} onRename={rename.request} separator />", header)
        self.assertIn("onCloseAutoFocus={rename.onCloseAutoFocus}", header)
        self.assertIn("<InlineRename value={project.title} onSave={(next) => void saveCompanyName(project.id, next)}", header)
        group = read("components/assistant-ui/sidebar-group.tsx")
        sort_menu = group.split("export const GroupSortMenu = memo(", 1)[1].split("\n});", 1)[0]
        self.assertIn("onCloseAutoFocus={onCloseAutoFocus}\n        >\n          {children}\n          <SubMenu", sort_menu)
        panel = read("components/session-panel.tsx")
        card = panel.split("const CompanyCard: FC", 1)[1].split("\n};", 1)[0]
        self.assertIn("<CompanyMenuItems company={c.id} onRename={rename.request} onChanged={() => void reload()} />", card)
        self.assertIn("menu.any ? (", card)
        self.assertNotIn("openCompany", card)   # 名称不再点进公司页

    def test_every_item_follows_a_declared_section(self):
        items = self.menu.split("export const CompanyMenuItems: FC", 1)[1]
        for line in ('const canRename = useSection("rename");', 'const canRefresh = useSection("catalog");',
                     'const canDelete = useSection("delete");', 'const withDesk = useSection("desk");',
                     "{canRename && (", "{canRefresh && (", "{desk && (", "{canDelete && ("):
            self.assertIn(line, items)
        self.assertNotIn("product ===", self.menu)
        self.assertIn("void cachedDetail(company).then((d) => alive && setDesk(d?.company.desk_url ?? \"\"));", items)

    def test_delete_asks_once_with_the_chat_delete_dialog(self):
        ask = self.menu.split("export function askDeleteCompany(company: string) {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("askDelete([], async () => {", ask)
        self.assertIn('{ title: co.deleteCompanyAsk, current: () => "" }', ask)
        self.assertIn("forgetProject(company);", ask)
        self.assertIn('onSelect={() => askDeleteCompany(company)}', self.menu)
        dialog = read("components/delete-chats.tsx")
        self.assertIn("cancel.current?.focus();", dialog)   # 打开时焦点在「取消」
        i18n = read("i18n.ts")
        self.assertIn('deleteCompanyAsk: "删除公司？"', i18n)
        self.assertIn('deleteCompanyAsk: "Delete company?"', i18n)

    def test_empty_notes_and_tasks_cards_still_offer_add(self):
        cards = read("components/company-cards.tsx")
        notes = cards.split("export const NotesSection: FC", 1)[1].split("\n};", 1)[0]
        self.assertNotIn("return null", notes)
        tasks = cards.split("export const TasksSection: FC", 1)[1].split("\n};", 1)[0]
        self.assertIn("if (!detail.tasks) return null;", tasks)
        self.assertNotIn("tasks.length &&", tasks)

    def test_all_account_sets_and_the_month_chip(self):
        panel = read("components/session-panel.tsx")
        self.assertIn("{allSets && <SetsList sets={detail.account_sets}", panel)
        self.assertIn("{co.allSets}", panel)
        ledger = read("components/month-ledger.tsx")
        card = ledger.split("export const MonthCard: FC", 1)[1].split("\n};", 1)[0]
        self.assertIn("onChange={(next) => setSeen(next === own ? \"\" : next)}", card)
        self.assertNotIn("fileSession", card)   # 只换卡里看的月份，不移动对话
        self.assertNotIn("export const MonthSection", ledger)


@NEEDS_NODE
class CompanyMenuBehaviourTests(unittest.TestCase):
    def run_page(self, body):
        run_ts('import "cc-test-setup";\nimport "cc-ma-setup";\nimport assert from "node:assert/strict";\n' + body
               + "\n(globalThis as any).done();\n", stubs=STUBS)

    def test_rename_after_the_menu_closes_and_saved_names_reach_the_sidebar(self):
        self.run_page(r"""
import { renameAfterMenu, saveCompanyName } from "@/components/company-menu";
import { refreshProjects, resetProjects, useProjects } from "@/lib/managed-agents/projects";
const g = globalThis as any;
const ok = (message: any) => ({ ok: true, status: 200, json: async () => ({ message }) });
const API = "pearnly_cowork.companies.api.";
let fail = false;
g.respond = async (method: string) => {
  if (method === "projects") return ok({ kind: "company", period: true, scope: true, api: API, sections: ["rename", "delete"],
    projects: [{ id: "C-1", title: "Old", subtitle: "" }] });
  if (method.includes(API + "rename")) return fail ? { ok: false, status: 403, json: async () => ({ exc_type: "PermissionError" }) }
    : ok({ id: "C-1", title: "New Co" });
  return ok(null);
};
resetProjects();
await refreshProjects();
// 菜单项只记下要改名；菜单关完（onCloseAutoFocus）才出改名框，焦点不还给「···」
let started = 0;
const flow = renameAfterMenu(() => started++);
let prevented = 0;
flow.onCloseAutoFocus({ preventDefault: () => prevented++ });
assert.equal(started, 0);
flow.request();
flow.onCloseAutoFocus({ preventDefault: () => prevented++ });
assert.equal(started, 1);
assert.equal(prevented, 1);
// 存下：侧栏这一组跟着改成服务端回的名称；被拒时名称不动、侧栏顶上出原因
assert.equal(await saveCompanyName("C-1", "New"), "New Co");
assert.equal(useProjects.getState().projects.find((p) => p.id === "C-1")?.title, "New Co");
fail = true;
assert.equal(await saveCompanyName("C-1", "Other"), null);
assert.equal(useProjects.getState().projects.find((p) => p.id === "C-1")?.title, "New Co");
assert.ok(useProjects.getState().error);
""")


class IconGlyphTests(unittest.TestCase):
    """用户 11-07：图标一律用 SVG 图标组件（lucide），不拿 emoji 或箭头、三角、勾叉这类字符当图标。"""

    GLYPH = re.compile(r"[\u2190-\u21FF\u25A0-\u25FF\u2700-\u27BF\u2B00-\u2BFF\U0001F000-\U0001FAFF\u2304\u2713-\u2718]")

    def test_no_glyph_stands_in_for_an_icon(self):
        found = []
        for path in sorted(SRC.rglob("*.tsx")):
            text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), path.read_text(encoding="utf-8"), flags=re.S)
            for no, line in enumerate(text.split("\n"), 1):
                code = re.sub(r"(^|[^:])//.*$", r"\1", line)
                # JSX 文字节点（>…<）与模板 / 字符串里拼进界面的箭头
                for piece in re.findall(r">([^<>{}]*)<", code) + re.findall(r"`([^`]*)`", code):
                    if self.GLYPH.search(piece):
                        found.append(f"{path.relative_to(SRC)}:{no}: {code.strip()[:100]}")
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
