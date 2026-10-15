"""CC 外壳第 1 批 B 线（设计文档 docs/work/2026-10-16-cc-shell-design.md 3.9 / 3.13 / 3.14 / 3.15 / 4.3 / 4.4 / 4.5）：

- 标题栏：「公司名 ›」点了打开右侧会话栏的公司卡；对话名点了就地改名；「···」单独是会话菜单；月份分店胶囊不在标题栏；
  右上 26 的「文件 N」（有文件才有）与会话栏开关（aria-pressed）。
- 右侧会话栏：只在 Cowork 挂了公司的对话里；与文件预览共用一个停靠位；宽屏并排、窄屏浮层（Esc、返回键）、手机全屏；
  卡片顺序 / 折叠 / 开关记在本机（键带用户）；拖动与 Alt+↑/↓ 只在看得见的卡片间挪；详情按「公司|月份」缓存。
- 公司页：概览 + 「对话 / 资料 / 定时任务」三个标签，对话按 进行中 / 等你处理 / 已完成 只用真实信号分组。
- 列表页（公司、待处理、定时任务）：标题、搜索、排序（公司）。

做法同 tests/test_assistant_ui.py：源码断言 + 用 esbuild 打包交给 node 跑的行为检查（要先 npm ci --prefix frontend/assistant）。
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from test_assistant_ui import ESBUILD, NODE, SOCKET_STUB, block, keys, read, run_ts  # noqa: E402

STUBS = {"socket.io-client": SOCKET_STUB,
         "@/lib/profile": 'export const APP_NAME = "CC"; export const useProfile = () => undefined;'}
NEEDS_NODE = unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")


class TitleBarSourceTests(unittest.TestCase):
    def test_title_bar_has_company_name_rename_button_and_a_separate_menu(self):
        app = read("assistant.tsx")
        title = block(app, "function ConversationTitle()")
        self.assertIn("<ConversationCompany />", title)
        self.assertIn("<ConversationName />", title)
        self.assertNotIn("ConversationFiling", app)  # 月份、分店胶囊在输入框下巴（C 线），不在标题栏
        name = block(app, "function ConversationName()")
        self.assertIn('className="cc-title-btn" title={t("rename")} onClick={() => setRenaming(true)}', name)
        self.assertIn("<ConversationMenu onRename={() => setRenaming(true)} />", name)
        menu = app.split("function ConversationMenu(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('<DropdownMenu.Trigger className="cc-row-btn" aria-label={t("conversationMenu")}', menu)
        self.assertIn("<MoveToMenu sessionId={remoteId} localId={localId} />", menu)
        self.assertIn('onSelect={onRename}', menu)
        # 「公司名 ›」打开会话栏的公司卡，不再进公司页
        projects = read("components/assistant-ui/projects.tsx")
        company = projects.split("export const ConversationCompany: FC", 1)[1].split("\n};", 1)[0]
        self.assertIn("onClick={openCompanyCard}", company)
        # 会话栏要对话已存到服务端：还没存好时公司名不出现
        self.assertIn("if (!remoteId || !project || !title) return null;", company)
        self.assertNotIn("useOpenCompany", company)
        mobile = projects.split("export const MobileCompanyItem: FC", 1)[1].split("\n};", 1)[0]
        self.assertIn("onSelect={openCompanyCard}", mobile)
        self.assertIn("if (!kind || !remoteId || !project || !title) return null;", mobile)
        self.assertIn("<MobileCompanyItem />", app.split("function MobileChatPill(", 1)[1].split("\nfunction ", 1)[0])

    def test_top_right_files_and_panel_toggle(self):
        app = read("assistant.tsx")
        layout = app.split("function Layout(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('className="cc-bar-btn"', layout)
        self.assertIn("aria-pressed={panelShown}", layout)
        # 开关文案按开着没有：显示 / 隐藏右侧栏（设计文档第 5 节）
        self.assertIn('title={t(panelShown ? "hidePanel" : "showPanel")}', layout)
        self.assertIn('aria-label={t(panelShown ? "hidePanel" : "showPanel")}', layout)
        self.assertIn("view === \"chat\" && results.files.length > 0 && !results.open ? (", layout)  # 文件 N：有文件才有
        # 一个停靠位：预览开着时不显示会话栏；只在挂了公司的对话（ERP / DMS 的 kind 为空，useFiledCompany 回空）
        # 挂不挂看对话有没有服务端编号，不看消息条数（换对话时线程加载期间消息为 0，会话栏不卸下再挂上）
        self.assertIn("const saved = useAuiState((s) => !!s.threadListItem.remoteId);", layout)
        self.assertIn("const hasPanel = view === \"chat\" && saved && !!filing.project;", layout)
        self.assertNotIn("hasMessages", layout)
        # 窄屏：画第一帧前就量好放不放得下
        dock = app.split("function useDockFits(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("useLayoutEffect(() => {", dock)
        # 手机与电脑外壳互换时那一行卸下重建：跟着新节点（回调 ref 存进 state）重新量，不盯着旧节点
        self.assertIn("}, [el]);", dock)
        self.assertIn("const [area, setArea] = useState<HTMLDivElement | null>(null);", layout)
        self.assertIn('<div ref={setArea} className="relative flex min-h-0 flex-1 flex-row">', layout)
        self.assertIn("const panelShown = hasPanel && !results.open && (mode === \"docked\" ? panelOpen : overlay);", layout)
        self.assertIn('const mode: PanelMode = isMobile ? "phone" : fits ? "docked" : "floating";', layout)
        self.assertIn("{panelShown && <SessionPanel filing={filing} mode={mode} onClose={closePanel} />}", layout)
        # 返回键：原有两层不变，会话栏浮层 / 全屏另登记
        self.assertIn('useBackLayer(isMobile && view === "chat" && results.open, () => results.setOpen(false));', layout)
        self.assertIn('useBackLayer(panelShown && mode !== "docked", () => setPanelOverlay(false));', layout)
        panel = read("components/session-panel.tsx")
        self.assertIn("return kind ? filing : NO_FILING;", panel)
        # 输入法选字时的 Esc 不关会话栏
        self.assertIn('if (e.key !== "Escape" || e.isComposing || e.defaultPrevented || document.querySelector(OPEN_LAYERS)) return;', panel)
        # 换到另一家时各卡重新挂上（知识库的新增框不把这条写进另一家）
        self.assertIn("<Fragment key={`${company}:${id}`}>{cards[id]}</Fragment>", panel)
        # 公司页换公司（有缓存不经过加载态）：整页重新挂上，输入框的分店、所在标签不串到另一家
        self.assertIn("<Master key={detail.company.id} detail={detail}", read("components/company-page.tsx"))
        self.assertIn('if (mode === "docked") return;', panel)

    def test_new_words_exist_in_every_language(self):
        i18n = read("i18n.ts")
        messages = block(i18n, "const MESSAGES = {")
        for lang in ("en", "zh", "th"):
            self.assertTrue({"showPanel", "hidePanel", "reorder"} <= keys(block(messages, f"  {lang}: {{")), lang)
            company = block(block(i18n, "const COMPANY = {"), f"  {lang}: {{")
            self.assertTrue({"company", "thisMonth", "library", "sortBy", "sortRecent", "sortName", "chatRunning", "chatWaiting",
                             "chatDone", "noChats"} <= keys(company), lang)
        self.assertNotIn("sessionPanel", i18n)
        self.assertIn('showPanel: "显示右侧栏",', i18n)
        self.assertIn('hidePanel: "隐藏右侧栏",', i18n)
        self.assertIn('reorder: "调整顺序",', i18n)
        self.assertIn('noChats: "还没有对话。"', i18n)
        self.assertIn('chatWaiting: "等你处理"', i18n)


class PanelStylesSourceTests(unittest.TestCase):
    def test_panel_and_list_pages_read_the_measured_values(self):
        css = read("globals.css")
        for name, target in {
            "--cc-panel-w": "--code-panel-w", "--cc-panel-gap": "--code-panel-gap", "--cc-panel-pad": "--code-panel-pad",
            "--cc-panel-head": "--code-panel-head", "--cc-panel-row": "--code-panel-row", "--cc-panel-btn": "--code-panel-btn",
            "--cc-panel-font": "--code-text-footnote", "--cc-panel-lh": "--code-text-panel-lh", "--cc-radius-panel": "--code-radius-10",
            "--cc-topbar-h": "--code-titlebar", "--cc-bar-btn": "--code-titlebar-btn", "--cc-list-w": "--code-list-w",
            "--cc-list-search-h": "--code-list-search-h", "--cc-radius-list-search": "--code-radius-7", "--cc-list-btn": "--code-list-btn",
            "--cc-text-page": "--code-text-page", "--cc-text-page-weight": "--code-text-page-weight",
        }.items():
            self.assertIn(f"{name}: var({target});", css, name)
        self.assertIn("--cc-panel-ring: inset 0 0 0 1px var(--cc-line);", css)
        # 卡片：透明底 + 1px 内环、圆角 10、上下内边距 8；卡头 26、12px；卡内行 26、13/19
        card = css.split(".cc-panel-card {", 1)[1].split("}", 1)[0]
        self.assertIn("padding-block: var(--cc-panel-pad); border-radius: var(--cc-radius-panel);", card)
        self.assertIn("box-shadow: var(--cc-panel-ring);", card)
        self.assertNotIn("background", card)
        self.assertIn("height: var(--cc-panel-head);", css.split(".cc-panel-card-head {", 1)[1].split("}", 1)[0])
        self.assertIn("font-size: var(--cc-text-caption);", css.split(".cc-panel-card-toggle {", 1)[1].split("}", 1)[0])
        self.assertIn("min-height: var(--cc-panel-row);", css.split(".cc-panel-row {", 1)[1].split("}", 1)[0])
        # 列表页：标题 24/500、搜索 36 圆角 7
        self.assertIn("font-size: var(--cc-text-page); line-height: var(--cc-text-page-lh); font-weight: var(--cc-text-page-weight);", css)
        search = css.split(".cc-page-search {", 1)[1].split("}", 1)[0]
        self.assertIn("height: var(--cc-list-search-h);", search)
        self.assertIn("border-radius: var(--cc-radius-list-search);", search)
        for page in ("components/companies.tsx", "components/pending.tsx", "components/scheduled-tasks.tsx"):
            text = read(page)
            self.assertIn('<h1 className="cc-page-title">', text, page)
            self.assertIn('className="cc-page-search"', text, page)

    def test_review_fixes_for_tools_title_bar_and_grid(self):
        css = read("globals.css")
        # 卡头操作：公司页一直显示（空标签的「＋」看得见）；会话栏里悬停才出、不能悬停的触屏常显
        tools = css.split(".cc-panel-card-tools {", 1)[1].split("}", 1)[0]
        self.assertNotIn("opacity: var(--cc-reveal-rest)", tools)
        self.assertIn(".cc-panel .cc-panel-card-tools { opacity: var(--cc-reveal-rest); }", css)
        self.assertIn("@media (hover: none) { .cc-panel .cc-panel-card-tools { opacity: 1; } }", css)
        # 网页标题栏实高 32（设计文档 3.9）：不再加上下外边距
        topbar = css.split(".cc-topbar {", 1)[1].split("}", 1)[0]
        self.assertIn("height: var(--cc-topbar-h);", topbar)
        self.assertNotIn("margin", topbar)
        # 公司列表的卡片网格在「公司主档（ADR-020」区块里
        company = css.split("公司主档（ADR-020", 1)[1]
        self.assertIn(".cc-card-grid {", company)
        self.assertIn(".cc-grid-card {", company)
        self.assertNotIn(".cc-grid-card {", css.split("公司主档（ADR-020", 1)[0])
        # 标题栏改名时 Esc 只取消改名，浮着的会话栏（看 defaultPrevented）不跟着关
        rename = read("components/assistant-ui/thread-list.tsx")
        self.assertIn('if (e.key === "Escape") {\n          e.preventDefault();\n          onDone();', rename)
        self.assertIn("e.defaultPrevented", read("components/session-panel.tsx"))
        # 「名称」排序：剩下的页一次取完，出错停下
        companies = read("components/companies.tsx")
        self.assertIn('if (sort !== "name" || done || loading || error || !rows) return;', companies)

    def test_the_f18_rule_and_the_task_buttons_stay(self):
        pending = read("components/pending.tsx")
        self.assertIn("initialChoice(next, need)", pending)
        self.assertIn("view.needed.some((need) => !values[choiceKey(need)])", pending)
        self.assertIn("if (embedded && (!items || !items.length) && !error) return null;", pending)
        tasks = read("components/scheduled-tasks.tsx")
        self.assertEqual(tasks.count("onClick={() => newTask(taskCompany)}"), 2)


@NEEDS_NODE
class PanelLogicTests(unittest.TestCase):
    def run_page(self, body, lang="en"):
        run_ts('import "cc-test-setup";\nimport assert from "node:assert/strict";\n' + body + "\n(globalThis as any).done();\n",
               stubs=STUBS, lang=lang)

    def test_card_order_moves_only_between_visible_cards(self):
        self.run_page(r"""
import { moveCard, orderCards, PANEL_CARDS } from "@/lib/company-panel";
// 默认顺序（设计文档 4.4）
assert.deepEqual([...PANEL_CARDS], ["company", "month", "pending", "notes", "folders", "tasks", "choices", "drafts", "outputs"]);
// 记下的顺序：认不出的丢掉、重复的只留一个；没记过的卡片（以后新加的）插在它默认的前一张卡片后面
assert.deepEqual(orderCards(["notes", "company", "bogus", "notes"]),
  ["notes", "folders", "tasks", "choices", "drafts", "outputs", "company", "month", "pending"]);
assert.deepEqual(orderCards(["outputs", "drafts", "choices", "tasks", "folders", "notes", "pending", "company"]),
  ["outputs", "drafts", "choices", "tasks", "folders", "notes", "pending", "company", "month"]);
assert.deepEqual(orderCards(null), [...PANEL_CARDS]);
const order = orderCards(null);
// 只在看得见的之间挪：本月卡（不显示）不占位
const shown = ["company", "pending", "notes", "outputs"];
assert.deepEqual(moveCard(order, "pending", -1, shown).filter((c) => shown.includes(c)), ["pending", "company", "notes", "outputs"]);
assert.deepEqual(moveCard(order, "notes", 1, shown).filter((c) => shown.includes(c)), ["company", "pending", "outputs", "notes"]);
// 拖动：目标位置 - 原位置
assert.deepEqual(moveCard(order, "outputs", -3, shown).filter((c) => shown.includes(c)), ["outputs", "company", "pending", "notes"]);
// 挪不动（到头、看不见的卡、0 格）原样
assert.deepEqual(moveCard(order, "company", -1, shown), order);
assert.deepEqual(moveCard(order, "outputs", 1, shown), order);
assert.deepEqual(moveCard(order, "month", 1, shown), order);
assert.deepEqual(moveCard(order, "notes", 0, shown), order);
// 挪完仍是全部卡片各一张
assert.deepEqual([...moveCard(order, "notes", 1, shown)].sort(), [...PANEL_CARDS].sort());
""")

    def test_prefs_are_kept_per_user_on_this_device(self):
        self.run_page(r"""
import { loadPanelPrefs, panelKey, readPrefs, setCardOrder, setPanelDocked, setPanelOpen, showCard, toggleCard, usePanel } from "@/lib/company-panel";
const g = globalThis as any;
// 没有存储（无痕、被禁）：照样能用，用默认值
assert.deepEqual(readPrefs(null), { open: true, order: usePanel.getState().order, collapsed: [] });
assert.deepEqual(readPrefs("{not json"), readPrefs(null));
assert.deepEqual(readPrefs(JSON.stringify({ open: false, collapsed: ["notes", "x", "notes"], order: ["outputs"] })).collapsed, ["notes"]);
loadPanelPrefs("a@b");   // 没有 localStorage：不抛错
const store = new Map<string, string>();
g.localStorage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
// 还没读到登录资料：用默认值，改了不写进不带人的公共键
store.set(panelKey(""), JSON.stringify({ open: false }));
loadPanelPrefs("");
assert.equal(usePanel.getState().open, true);
toggleCard("notes");
assert.deepEqual(usePanel.getState().collapsed, ["notes"]);
assert.equal(store.get(panelKey("")), JSON.stringify({ open: false }));
store.clear();
loadPanelPrefs("a@b");
assert.equal(panelKey("a@b"), "cc.panel.a@b");
toggleCard("notes");
setPanelOpen(false);
setCardOrder(["tasks", "company"]);
const saved = JSON.parse(store.get("cc.panel.a@b")!);
assert.equal(saved.open, false);
assert.deepEqual(saved.collapsed, ["notes"]);
assert.equal(saved.order[0], "tasks");
assert.ok(saved.order.indexOf("company") > 0);
assert.equal(saved.order.length, 9);   // 没给到的卡片按默认补齐
assert.equal(usePanel.getState().overlay, false);
// 别人登录这台设备：读他自己的
loadPanelPrefs("c@d");
assert.equal(usePanel.getState().open, true);
assert.deepEqual(usePanel.getState().collapsed, []);
// 「公司名 ›」：打开、展开公司卡、记下要滚到它
loadPanelPrefs("a@b");
toggleCard("company");
showCard("company");
const state = usePanel.getState();
assert.equal(state.open, true);
assert.equal(state.overlay, true);
assert.ok(!state.collapsed.includes("company"));
assert.equal(state.focus?.card, "company");
// 浮层、手机全屏里点公司名：只开这一次，不把宽屏记下的「关着」改成「开着」
setPanelOpen(false);
setPanelDocked(false);
showCard("company");
assert.equal(usePanel.getState().overlay, true);
assert.equal(usePanel.getState().open, false);
assert.equal(JSON.parse(store.get("cc.panel.a@b")!).open, false);
setPanelDocked(true);
showCard("company");
assert.equal(JSON.parse(store.get("cc.panel.a@b")!).open, true);
""")

    def test_dock_or_float_and_css_lengths(self):
        self.run_page(r"""
import { cssPx, dockFits } from "@/lib/company-panel";
assert.equal(cssPx("280px"), 280);
assert.equal(cssPx(" 24rem", 16), 384);
assert.equal(cssPx("var(--x)"), 0);
assert.equal(dockFits(664, 384, 280), true);
assert.equal(dockFits(663, 384, 280), false);
""")

    def test_chat_states_use_real_signals_only(self):
        self.run_page(r"""
import { chatState, groupChats, unreadRuns } from "@/lib/company-panel";
const idle = { status: "idle", awaiting_approval: [] } as any;
// 还在跑、读回里有等你点「允许」的 → 等你处理
assert.equal(chatState("running", { status: "requires_action", awaiting_approval: ["t1"] } as any), "waiting");
// 在跑只看实时状态（读回的先交给 noteRun 校正过）
assert.equal(chatState("running"), "running");
assert.equal(chatState("running", idle), "running");
// 读回的快照过期了不算数：实时说跑完了（unread），快照还写着在跑 / 等你点「允许」→ 已完成
assert.equal(chatState("unread", { status: "running", awaiting_approval: [] } as any), "done");
assert.equal(chatState(undefined, { status: "requires_action", awaiting_approval: ["t1"] } as any), "done");
// 没看的 CC 提醒 → 等你处理
assert.equal(chatState("notice"), "waiting");
assert.equal(chatState(undefined, null, true), "waiting");
// 其余（含「跑完没看」的回复圆点）→ 已完成
assert.equal(chatState("unread"), "done");
assert.equal(chatState(undefined), "done");
const rows = [{ id: "a" }, { id: "b" }, { id: "c" }, { id: "d" }];
const states: Record<string, any> = { a: "done", b: "running", c: "done", d: "waiting" };
assert.deepEqual(groupChats(rows, (r) => states[r.id]).map(([s, list]) => [s, list.map((r) => r.id)]),
  [["running", ["b"]], ["waiting", ["d"]], ["done", ["a", "c"]]]);
assert.deepEqual(groupChats([], () => "done"), []);
// 页面开着时才开始跑的：没读过运行状态、也没请服务端读过的才补读
assert.deepEqual(unreadRuns(["a", "b", "c"], { a: idle }, new Set(["b"])), ["c"]);
assert.deepEqual(unreadRuns([], {}, new Set()), []);
""")

    def test_list_page_search_and_sort(self):
        self.run_page(r"""
import { matchesPending } from "@/components/pending";
import { sortCompanies } from "@/components/companies";
const item: any = { document: "h1", invoice_no: "INV-001", counterparty: "ACME Co", reason_label: "Pick account", amount: "1200.00", date: "2026-09-30" };
assert.equal(matchesPending(item, ""), true);
assert.equal(matchesPending(item, "acme"), true);
assert.equal(matchesPending(item, " inv-0 "), true);
assert.equal(matchesPending(item, "2026-09"), true);
assert.equal(matchesPending(item, "zzz"), false);
const rows = [{ title: "beta" }, { title: "Alpha" }, { title: "gamma 10" }, { title: "gamma 9" }];
assert.deepEqual(sortCompanies(rows, "recent").map((r) => r.title), ["beta", "Alpha", "gamma 10", "gamma 9"]);  // 服务端的顺序
assert.deepEqual(sortCompanies(rows, "name").map((r) => r.title), ["Alpha", "beta", "gamma 9", "gamma 10"]);
""")

    def test_cards_toggle_and_reorder_only_inside_the_panel(self):
        self.run_page(r"""
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.browser";
import { PanelCard, PanelControlContext, PendingCard } from "@/components/company-cards";
import { usePendingCounts } from "@/lib/companies";
const card = (props: any = {}) => createElement(PanelCard, { id: "notes", title: "Notes", summary: 2, ...props }, createElement("p", null, "body"));
// 公司页（没有会话栏控制）：默认展开、没有拖动把手；整行开关带 aria-expanded；摘要在开关里
const page = renderToStaticMarkup(card());
assert.match(page, /<button type="button" class="cc-panel-card-toggle" aria-expanded="true">/);
assert.ok(page.includes(">body<"));
assert.ok(!page.includes("cc-panel-grip"));
assert.ok(page.includes('<span class="cc-panel-card-summary">2</span>'));
// 摘要为 0 不显示
assert.ok(!renderToStaticMarkup(card({ summary: 0 })).includes("cc-panel-card-summary"));
// 会话栏里：折叠记在会话栏（折叠了不画内容），有拖动把手（Drag to reorder）
const control = { collapsed: ["notes"], dragging: null, toggle() {}, move() {}, drag() {} };
const panel = renderToStaticMarkup(createElement(PanelControlContext.Provider, { value: control }, card()));
assert.match(panel, /aria-expanded="false"/);
assert.ok(!panel.includes(">body<"));
assert.match(panel, /class="cc-panel-btn cc-panel-grip" aria-label="Reorder"/);
// 正在里面新增 / 编辑（forceOpen）：折叠了也展开
assert.ok(renderToStaticMarkup(createElement(PanelControlContext.Provider, { value: control }, card({ forceOpen: true }))).includes(">body<"));
// 待处理：这家计数为 0 不显示
usePendingCounts.setState({ loaded: true, total: 0, companies: {} });
assert.equal(renderToStaticMarkup(createElement(PendingCard, { company: "comp_abc" })), "");
""")


if __name__ == "__main__":
    unittest.main()
