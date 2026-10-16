"""CC 外壳第 1 批 B 线（设计文档 docs/work/2026-10-16-cc-shell-design.md 3.9 / 3.13 / 3.14 / 3.15 / 4.3 / 4.4 / 4.5）：

- 标题栏：「公司名 ›」点了打开右侧会话栏的公司卡；对话名点了就地改名；「···」单独是会话菜单；月份分店胶囊不在标题栏；
  右上 26 的「文件 N」（有文件才有）与会话栏开关（aria-pressed）。
- 右侧会话栏：只在 Cowork 挂了公司的对话里；与文件预览共用一个停靠位；宽屏并排、窄屏浮层（Esc、返回键）、手机全屏；
  卡片顺序 / 折叠 / 开关记在本机（键带用户）；拖动与 Alt+↑/↓ 只在看得见的卡片间挪；详情按「公司|月份」缓存。
- 公司页（10-16 改）：版式同对话页——中间标题 + 输入框（同对话的输入框与下巴）+ 这家的对话列表（行同侧栏行、相对时间、
  最新在前），公司的资料全在右侧会话栏（默认开着），没有标签。
- 卡头摘要一直显示（「2 条」「需处理 2」），把手的槽固定留宽、摘要一列对齐；拖动走共用拖动 components/ui/drag.tsx
  （dnd-kit 排序列表 + 浮层：键盘、触屏长按、读屏播报，松手当帧落位，浮层不出这一列）。
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
        self.assertIn("view === \"chat\" && results.outputs.length > 0 && !results.open ? (", layout)  # 文件 N：有结果文件才有（点开的附件不算）
        # 一个停靠位：预览开着时不显示会话栏；只在挂了公司的对话（ERP / DMS 的 kind 为空，useFiledCompany 回空）
        # 挂不挂看对话有没有服务端编号，不看消息条数（换对话时线程加载期间消息为 0，会话栏不卸下再挂上）
        self.assertIn("const saved = useAuiState((s) => !!s.threadListItem.remoteId);", layout)
        self.assertIn("const hasPanel = (view === \"chat\" && saved && !!filing.project) || companyPage;", layout)
        # 公司页也有会话栏（公司的资料全在这里），月份是公司页自己的
        self.assertIn('const companyPage = view === "company" && !!kind && !!pageCompany;', layout)
        self.assertIn('<SessionPanel page filing={{ project: pageCompany, period: pagePeriod, scope: "" }} mode={mode} onClose={closePanel} />', layout)
        self.assertNotIn("hasMessages", layout)
        # 窄屏：画第一帧前就量好放不放得下
        dock = app.split("function useDockFits(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("useLayoutEffect(() => {", dock)
        # 手机与电脑外壳互换时那一行卸下重建：跟着新节点（回调 ref 存进 state）重新量，不盯着旧节点
        self.assertIn("}, [el]);", dock)
        self.assertIn("const [area, setArea] = useState<HTMLDivElement | null>(null);", layout)
        self.assertIn('<div ref={setArea} className="relative flex min-h-0 flex-1 flex-row">', layout)
        self.assertIn("const panelShown = hasPanel && !(view === \"chat\" && results.open) && (mode === \"docked\" ? panelOpen : overlay);", layout)
        self.assertIn('const mode: PanelMode = isMobile ? "phone" : fits ? "docked" : "floating";', layout)
        self.assertIn("<SessionPanel filing={filing} mode={mode} onClose={closePanel} />", layout)
        # 返回键：原有两层不变，会话栏浮层 / 全屏另登记
        self.assertIn('useBackLayer(isMobile && view === "chat" && results.open, () => results.setOpen(false));', layout)
        self.assertIn('useBackLayer(panelShown && mode !== "docked", () => setPanelOverlay(false));', layout)
        panel = read("components/session-panel.tsx")
        self.assertIn("return kind ? filing : NO_FILING;", panel)
        # 输入法选字时的 Esc 不关会话栏
        self.assertIn('if (e.key !== "Escape" || e.isComposing || e.defaultPrevented || document.querySelector(OPEN_LAYERS)) return;', panel)
        # 换到另一家时各卡重新挂上（知识库的新增框不把这条写进另一家）
        self.assertIn("<Fragment key={`${scope}:${id}`}>{cards[id] ?? null}</Fragment>", panel)
        self.assertIn("<SortableCards cards={cards} scope={company} />", panel)
        # 公司页换公司（有缓存不经过加载态）：整页重新挂上，输入框的字与分店不串到另一家
        self.assertIn("<Master key={detail.company.id} detail={detail}", read("components/company-page.tsx"))
        self.assertIn('if (mode === "docked") return;', panel)
        # 拖动中按 Esc 只取消拖动，不关浮着的会话栏（鼠标、键盘拖都一样；取消拖动的那一下 Esc 也认得出，lib/layers.ts）
        self.assertIn("if (isDragEscape(e)) return;", panel)

    def test_new_words_exist_in_every_language(self):
        i18n = read("i18n.ts")
        messages = block(i18n, "const MESSAGES = {")
        for lang in ("en", "zh", "th"):
            self.assertTrue({"showPanel", "hidePanel", "reorder"} <= keys(block(messages, f"  {lang}: {{")), lang)
            company = block(block(i18n, "const COMPANY = {"), f"  {lang}: {{")
            self.assertTrue({"company", "thisMonth", "library", "sortBy", "sortRecent", "sortName", "chatRunning", "chatWaiting",
                             "chatDone", "noChats", "card"} <= keys(company), lang)
            card = block(company, "    card: {")
            self.assertEqual({"sets", "notes", "tasks", "choices", "docs", "drafts", "files", "folders", "toDo", "toCheck", "pickedUp",
                              "movedTo", "droppedAt", "cancelled", "instructions"}, keys(card), lang)
        self.assertNotIn("sessionPanel", i18n)
        self.assertIn('showPanel: "显示右侧栏",', i18n)
        self.assertIn('hidePanel: "隐藏右侧栏",', i18n)
        self.assertIn('reorder: "调整顺序",', i18n)
        self.assertIn('noChats: "还没有对话。"', i18n)
        for words in ('notes: (n: number) => `${n} 条`', 'tasks: (n: number) => `${n} 个任务`', 'choices: (n: number) => `${n} 项`',
                      'docs: (n: number) => `${n} 张`', 'drafts: (n: number) => `${n} 份`', 'files: (n: number) => `${n} 个文件`',
                      'sets: (n: number) => `${n} 本`', 'toDo: (n: number) => `需处理 ${n}`',
                      'notes: (n: number) => (n === 1 ? "1 note" : `${n} notes`)'):
            self.assertIn(words, i18n)
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
        self.assertIn("padding-block: var(--cc-panel-pad);", card)
        self.assertIn("border-radius: var(--cc-radius-panel); box-shadow: var(--cc-panel-ring);", card)
        self.assertIn("margin: 0;", card)   # 卡片间距只用会话栏的一个变量
        self.assertNotIn("background", card)
        self.assertIn("gap: var(--cc-panel-gap);", css.split(".cc-panel {", 1)[1].split("}", 1)[0])
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
        # 卡头操作：悬停 / 聚焦卡头才出，盖在摘要那一格上（摘要一列不挪动）；手机变体与不能悬停的触屏接在摘要后面常显
        tools = css.split(".cc-panel-card-tools {", 1)[1].split("}", 1)[0]
        self.assertIn("opacity: var(--cc-reveal-rest); pointer-events: none;", tools)
        self.assertIn(".cc-panel-card-end > * { grid-area: 1 / 1; }", css)
        self.assertIn(".cc-panel-card-head:is(:hover, :focus-within) .cc-panel-card-tools { opacity: 1; pointer-events: auto; }", css)
        self.assertIn(".cc-panel-card-head:is(:hover, :focus-within) .cc-panel-card-end:has(.cc-panel-card-tools) .cc-panel-card-summary { opacity: 0; }", css)
        self.assertIn(':root[data-cc-variant="phone"] .cc-panel-card-end { display: flex; gap: 0.25rem; }', css)
        self.assertIn("@media (hover: none) {\n    .cc-panel-card-end { display: flex; gap: 0.25rem; }", css)
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
        self.assertIn('if (e.key === "Escape" && !e.nativeEvent.isComposing) {\n          e.preventDefault();\n          finish(false);', rename)
        # 公司页标题同一套（审查第 4 轮）：只结束一次、输入法回车 / Esc 不算、cc-inline-edit 样式
        title = read("components/company-page.tsx").split("const TitleRename: FC", 1)[1].split("\n};", 1)[0]
        for line in ("if (finished.current) return;", 'if (e.key === "Enter" && !isImeEnter(e)) {',
                     'if (e.key === "Escape" && !e.nativeEvent.isComposing) {', 'className="cc-inline-edit cc-page-title-edit"'):
            self.assertIn(line, title)
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

    def test_company_card_lists_every_account_set_of_the_month(self):
        # 测试窗口第 2 轮：没指定分店的对话公司卡只显示「T.9 นิภา (สาขา 00012)」，推送却进了总店 T9NIEXCP：
        # 没指定分店时各分店覆盖这个月的都列（一份一行，总店在前），指定了分店只列那个分店的
        panel = read("components/session-panel.tsx")
        card = panel.split("const CompanyCard: FC", 1)[1].split("\n};", 1)[0]
        self.assertIn("const sets = setsForScope(detail, period, scope);", card)
        self.assertIn("{sets.length === 1 && <InfoRow icon={<BookOpenIcon />} label={co.accountSets} value={sets[0]!.label} />}", card)
        self.assertIn("{sets.map((set) => (", card)
        self.assertIn('className="cc-panel-row cc-panel-indent" title={set.label}', card)
        self.assertNotIn("setFor(", card)

    def test_the_panel_pending_card_lists_only_other_months(self):
        # 开发窗口 10-16 定：会话栏「待处理」卡只列对话月份以外的，对话那个月的只在「本月」卡；公司页（会话栏 page）列全部月份
        panel = read("components/session-panel.tsx")
        self.assertIn("pending: <PendingCard company={company} period={period} />,", panel)
        self.assertIn("pending: <PendingCard company={company} />,", panel)
        self.assertIn('<PendingList company={company} search={search} />', read("components/pending.tsx"))
        cards = read("components/company-cards.tsx")
        card = cards.split("const OtherMonthsPending: FC", 1)[1].split("\n};", 1)[0]
        self.assertIn("otherMonths(state.items, period)", card)
        self.assertIn("if (!items?.length && !state.error) return null;", card)
        self.assertIn("summary={items?.length ? co.card.docs(items.length) : undefined}", card)

@NEEDS_NODE
class PanelLogicTests(unittest.TestCase):
    def run_page(self, body, lang="en"):
        run_ts('import "cc-test-setup";\nimport assert from "node:assert/strict";\n' + body + "\n(globalThis as any).done();\n",
               stubs=STUBS, lang=lang)

    def test_account_sets_shown_for_the_conversation_scope(self):
        self.run_page(r"""
import { setsForScope } from "@/lib/companies";
const set = (key: string, branch: string, start: string, end: string, writable = true) =>
  ({ connection: "c1", adapter: "express", key, label: key, branch, period_start: start, period_end: end, writable });
// 照测试机 T.9：分店 00012 在前（服务端顺序），总店 T9NIEXCP 覆盖同一个月
const b12 = [set("T9N12EXP", "00012", "2026-01-01", "2026-12-31"), set("T9N12-68", "00012", "2025-01-01", "2025-12-31")];
const hq = [set("T9NIEXCP", "00000", "2026-01-01", "2026-12-31"), set("T9NI-68", "00000", "2025-01-01", "2025-12-31")];
const detail: any = { account_sets: [...b12, ...hq],
  branches: [{ branch: "00012", account_sets: b12 }, { branch: "00000", account_sets: hq }] };
const keys = (sets: any[]) => sets.map((s) => s.key);
// 没指定分店：各分店覆盖这个月的都列，总店在前
assert.deepEqual(keys(setsForScope(detail, "2026-09", "")), ["T9NIEXCP", "T9N12EXP"]);
assert.deepEqual(keys(setsForScope(detail, "2025-03", "")), ["T9NI-68", "T9N12-68"]);
// 指定了分店：只列那个分店的
assert.deepEqual(keys(setsForScope(detail, "2026-09", "00012")), ["T9N12EXP"]);
assert.deepEqual(keys(setsForScope(detail, "2026-09", "00000")), ["T9NIEXCP"]);
// 对话的分店不在名单里：照旧取整家公司覆盖这个月的一份
assert.deepEqual(keys(setsForScope(detail, "2026-09", "00099")), ["T9N12EXP"]);
// 有覆盖这个月的就只列覆盖的：分店 00012 只有往年的账套时不拿它最新的一份冒充这个月的
const b12old = [set("T9N12-68", "00012", "2025-01-01", "2025-12-31")];
const partial: any = { account_sets: [...b12old, ...hq],
  branches: [{ branch: "00012", account_sets: b12old }, { branch: "00000", account_sets: hq }] };
assert.deepEqual(keys(setsForScope(partial, "2026-09", "")), ["T9NIEXCP"]);
// 一份都不覆盖这个月：每个分店各取最新可写的一份；没有月份时同样每个分店各一份
assert.deepEqual(keys(setsForScope(detail, "2024-05", "")), ["T9NIEXCP", "T9N12EXP"]);
assert.deepEqual(keys(setsForScope(detail, "", "")), ["T9NIEXCP", "T9N12EXP"]);
// 只有一家（没有分店信息）：照旧覆盖这个月的那份
const single: any = { account_sets: [set("69EXP", "", "2026-01-01", "2026-12-31"), set("68EXP", "", "2025-01-01", "2025-12-31")] };
assert.deepEqual(keys(setsForScope(single, "2026-09", "")), ["69EXP"]);
assert.deepEqual(keys(setsForScope({ account_sets: [] } as any, "2026-09", "")), []);
// 旧服务端（没有 branches，按账套上的分店号现分）：同样总店在前；没有分店号的账套当总店那一组
const old: any = { account_sets: [...b12, ...hq, set("LOOSE", "", "2026-01-01", "2026-12-31")] };
assert.deepEqual(keys(setsForScope(old, "2026-09", "")), ["LOOSE", "T9NIEXCP", "T9N12EXP"]);
""")

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
import { chatMark, chatState, newestFirst, relativeWhen, unreadRuns } from "@/lib/company-panel";
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
// 行尾装饰（同侧栏行）：在跑脉冲点、等你处理提醒点、跑完没看圆点，其余不标
assert.equal(chatMark("running", "running"), "running");
assert.equal(chatMark("waiting", "running"), "notice");
assert.equal(chatMark("waiting", undefined), "notice");
assert.equal(chatMark("done", "unread"), "unread");
assert.equal(chatMark("done", undefined), null);
// 最新的在前；没有时间的放最后，同时间保持原顺序
const rows = [{ id: "a", updated_at: "2026-10-01T00:00:00Z" }, { id: "b", updated_at: "2026-10-03T00:00:00Z" }, { id: "c" },
  { id: "d", updated_at: "2026-10-03T00:00:00Z" }];
assert.deepEqual(newestFirst(rows).map((r) => r.id), ["b", "d", "a", "c"]);
// 相对时间（同 Claude 项目页）
const now = Date.parse("2026-10-16T12:00:00Z");
assert.equal(relativeWhen("2026-10-16T11:59:30Z", now, "en-US"), "now");
assert.equal(relativeWhen("2026-10-16T11:55:00Z", now, "en-US"), "5 minutes ago");
assert.equal(relativeWhen("2026-10-16T09:00:00Z", now, "en-US"), "3 hours ago");
assert.equal(relativeWhen("2026-10-15T12:00:00Z", now, "en-US"), "yesterday");
assert.equal(relativeWhen("2026-09-25T12:00:00Z", now, "en-US"), "3 weeks ago");
assert.equal(relativeWhen("2026-10-16T11:55:00Z", now, "zh-CN"), "5分钟前");
assert.equal(relativeWhen("", now), "");
assert.equal(relativeWhen("not a date", now), "");
// 页面开着时才开始跑的：没读过运行状态、也没请服务端读过的才补读
assert.deepEqual(unreadRuns(["a", "b", "c"], { a: idle }, new Set(["b"])), ["c"]);
assert.deepEqual(unreadRuns([], {}, new Set()), []);
""")

    def test_pending_months_follow_the_ledger(self):
        self.run_page(r"""
import { otherMonths, pendingMonth } from "@/components/pending";
const row = (document: string, extra: any) => ({ document, company: "C", reason_code: "choice_needed", reason_label: "", action: "choose", ...extra });
// 服务端给的 month 优先（没有单据日期的按上传月份，同月结台账）；旧服务端没给时取单据日期
assert.equal(pendingMonth({ month: "2026-10", date: "" }), "2026-10");
assert.equal(pendingMonth({ date: "2026-09-30" }), "2026-09");
assert.equal(pendingMonth({ date: "" }), "");
const items: any[] = [row("a", { date: "2026-09-20", month: "2026-09" }), row("b", { date: "2026-08-02", month: "2026-08" }),
  row("c", { date: "", month: "2026-09" }), row("d", { date: "2026-07-01" }), row("e", { date: "", month: "" })];
// 对话是 2026-09：那个月的（含按上传月份算进来的）只在「本月」卡
assert.deepEqual(otherMonths(items, "2026-09").map((i) => i.document), ["b", "d", "e"]);
// 全是对话那个月的：一张不剩（卡不显示）
assert.deepEqual(otherMonths(items.slice(0, 1), "2026-09"), []);
// 对话没有月份：全列
assert.deepEqual(otherMonths(items, "").map((i) => i.document), ["a", "b", "c", "d", "e"]);
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

    def test_cards_toggle_summaries_and_grip_slot(self):
        self.run_page(r"""
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.browser";
import { DragArea, DragList } from "@/components/ui/drag";
import { folderSummary, PanelCard, PanelControlContext, PendingCard } from "@/components/company-cards";
import { usePendingCounts } from "@/lib/companies";
const card = (props: any = {}) => createElement(PanelCard, { id: "notes", title: "Notes", summary: "2 notes", ...props }, createElement("p", null, "body"));
// 不在会话栏里：默认展开、没有把手（不留把手槽）；标题 + 箭头是开关，带 aria-expanded；摘要在右侧一列，读屏念作开关的说明
const page = renderToStaticMarkup(card());
assert.match(page, /<button type="button" class="cc-panel-card-toggle" aria-expanded="true" aria-describedby="([^"]+)">/);
const described = page.match(/aria-describedby="([^"]+)"/)![1];
assert.ok(page.includes(`<span class="cc-panel-card-end" data-slot="cc-card-end"><span id="${described}" class="cc-panel-card-summary">2 notes</span></span>`));
assert.ok(page.includes(">body<"));
assert.ok(!page.includes("cc-panel-grip") && !page.includes("cc-card-grip"));
// 摘要为 0、空不显示（开关也不带说明）
assert.ok(!renderToStaticMarkup(card({ summary: 0 })).includes("cc-panel-card-summary"));
assert.ok(!renderToStaticMarkup(card({ summary: "" })).includes("aria-describedby"));
// 卡头操作与摘要叠在同一格（悬停才出，盖住摘要）；一直显示的控件在标题后面
const tools = renderToStaticMarkup(card({ action: createElement("button", null, "+"), extra: createElement("b", null, "Sep") }));
assert.match(tools, /<span class="cc-panel-card-extra"><b>Sep<\/b><\/span><span class="cc-panel-card-end" data-slot="cc-card-end"><span id="[^"]+" class="cc-panel-card-summary">2 notes<\/span><span class="cc-panel-card-tools"><button>\+<\/button><\/span><\/span>/);
// 会话栏里（排序列表里）：折叠记在会话栏（折叠了不画内容）；每张卡最右边都有把手槽，把手接上排序（dnd-kit 的属性）
const control = { collapsed: ["notes"], toggle() {}, move() {}, register: () => () => {} };
const texts = { instructions: "", pickedUp: String, movedTo: String, droppedAt: String, cancelled: String };
const inPanel = (child: any) => renderToStaticMarkup(createElement(PanelControlContext.Provider, { value: control },
  createElement(DragArea, { accepts: () => true, onDrop() {}, texts }, createElement(DragList, { id: "cards", items: ["notes"] }, child))));
const panel = inPanel(card());
assert.match(panel, /aria-expanded="false"/);
assert.ok(!panel.includes(">body<"));
assert.match(panel, /<span class="cc-panel-card-grip" data-slot="cc-card-grip"><button type="button"[^>]*role="button"[^>]*tabindex="0"[^>]*aria-roledescription="sortable"[^>]*class="cc-panel-btn cc-panel-grip" aria-label="Reorder" title="Reorder">/);
assert.match(panel, /<section data-drag-list="cards" class="cc-panel-card" data-card="notes">/);   // 没在拖：没有位移、不留空
assert.ok(!panel.includes("data-drag-source") && !panel.includes("style="));
// 正在里面新增 / 编辑（forceOpen）：折叠了也展开
assert.ok(inPanel(card({ forceOpen: true })).includes(">body<"));
// 待处理：这家计数为 0 不显示（有的写「N 张」，见下面的源码断言；服务端渲染读不到改过的计数）
usePendingCounts.setState({ loaded: true, total: 0, companies: {} });
assert.equal(renderToStaticMarkup(createElement(PendingCard, { company: "comp_abc" })), "");
// 单据文件夹：一个写状态，几个写个数，没有不写
assert.equal(folderSummary([{ state: "ok" } as any]), "Ready");
assert.equal(folderSummary([{ state: "ok" }, { state: "offline" }] as any), "2 folders");
assert.equal(folderSummary([]), "");
""")
        cards = read("components/company-cards.tsx")
        for line in ('<PanelCard id="pending" title={co.pending} summary={co.card.docs(count)} slot="cc-company-pending">',
                     "summary={list.length ? co.card.notes(list.length) : undefined}",
                     "summary={tasks.length ? co.card.tasks(tasks.length) : undefined}",
                     '<PanelCard id="drafts" title={co.taxDrafts} summary={co.card.drafts(drafts.length)} slot="cc-company-drafts">',
                     '<PanelCard id="choices" title={co.remembered} summary={co.card.choices(list.length)} slot="cc-company-choices">',
                     '<PanelCard id="folders" title={co.docFolder} summary={folderSummary(folders)} slot="cc-company-folders">'):
            self.assertIn(line, cards)
        panel = read("components/session-panel.tsx")
        self.assertIn('<PanelCard id="outputs" title={t("files")} summary={co.card.files(files.length)} slot="cc-panel-outputs">', panel)
        self.assertEqual(panel.count("summary={sets.length ? co.card.sets(sets.length) : undefined}"), 2)

    def test_summaries_in_every_language_and_the_month_head(self):
        self.run_page(r"""
import { co } from "@/i18n";
import { monthSummary } from "@/components/month-ledger";
assert.equal(co.card.notes(2), "2 条");
assert.equal(co.card.tasks(1), "1 个任务");
assert.equal(co.card.choices(10), "10 项");
assert.equal(co.card.docs(2), "2 张");
assert.equal(co.card.drafts(1), "1 份");
assert.equal(co.card.files(3), "3 个文件");
assert.equal(co.card.sets(3), "3 本");
const stage = (state: string) => ({ state });
const month = (items: any[], states: string[]) => ({ exceptions: { items, total: items.length, counts: {} },
  stages: { intake: stage(states[0]), entry: stage(states[1]), verify: stage(states[2]), tax: stage(states[3]) } }) as any;
// 有需处理写「需处理 N」，再是需留意；都没有写走到哪一段
assert.equal(monthSummary(month([{ severity: "action" }, { severity: "action" }, { severity: "warn" }], ["clear", "open", "empty", "empty"])), "需处理 2");
assert.equal(monthSummary(month([{ severity: "warn" }, { severity: "info" }], ["clear", "open", "empty", "empty"])), "需留意 1");
assert.equal(monthSummary(month([{ severity: "info" }], ["clear", "open", "empty", "empty"])), "录账 · 进行中");
assert.equal(monthSummary(month([], ["clear", "clear", "clear", "clear"])), "已完成");
assert.equal(monthSummary(month([], ["empty", "empty", "empty", "empty"])), "暂无");
""", lang="zh")
        self.run_page(r"""
import { co } from "@/i18n";
assert.equal(co.card.notes(1), "1 note");
assert.equal(co.card.notes(2), "2 notes");
assert.equal(co.card.tasks(1), "1 task");
assert.equal(co.card.files(3), "3 files");
assert.equal(co.card.sets(1), "1 account set");
assert.equal(co.card.toDo(2), "2 to do");
assert.equal(co.card.pickedUp("Notes"), "Picked up Notes.");
""")
        self.run_page(r"""
import { co } from "@/i18n";
assert.equal(co.card.notes(2), "2 รายการ");
assert.equal(co.card.tasks(1), "1 งาน");
assert.equal(co.card.toDo(2), "ต้องทำ 2");
assert.ok(co.card.instructions.includes("Space") && co.card.instructions.includes("Esc"));
""", lang="th")

    def test_drag_drop_order_and_announcements(self):
        self.run_page(r"""
import { cardTexts, dropOrder } from "@/components/session-panel";
import { dragAnnouncements } from "@/components/ui/drag";
import { orderCards } from "@/lib/company-panel";
const order = orderCards(null);
const shown = ["company", "pending", "notes", "outputs"];
// 放下：在看得见的卡片里挪到落点（看不见的本月卡不占位）
assert.deepEqual(dropOrder(order, shown, "outputs", "company")!.filter((c) => shown.includes(c)), ["outputs", "company", "pending", "notes"]);
assert.deepEqual(dropOrder(order, shown, "company", "notes")!.filter((c) => shown.includes(c)), ["pending", "notes", "company", "outputs"]);
// 没动、认不出：不改
assert.equal(dropOrder(order, shown, "notes", "notes"), null);
assert.equal(dropOrder(order, shown, "month", "notes"), null);
// 读屏播报：名字用卡片标题（label），位置按这一列看得见的卡片
const entry = (id: string, label: string, index?: number) =>
  ({ id, data: { current: { drag: { id, kind: "card", label, list: "cards" }, ...(index === undefined ? {} : { sortable: { index, items: shown } }) } } });
const say = dragAnnouncements(cardTexts());
assert.equal(say.onDragStart({ active: entry("notes", "Notes") } as any), "Picked up Notes.");
assert.equal(say.onDragOver({ active: entry("notes", "Notes"), over: entry("company", "Company", 0) } as any), "Notes moved to position 1 of 4.");
assert.equal(say.onDragEnd({ active: entry("notes", "Notes"), over: entry("outputs", "Files", 3) } as any), "Notes dropped at position 4 of 4.");
assert.equal(say.onDragOver({ active: entry("notes", "Notes"), over: null } as any), undefined);
assert.equal(say.onDragEnd({ active: entry("notes", "Notes"), over: null } as any), "Moving Notes was cancelled.");
assert.equal(say.onDragCancel({ active: entry("notes", "Notes") } as any), "Moving Notes was cancelled.");
""")

    def test_lifted_card_stays_in_the_column_not_the_visible_box(self):
        # 审查第 4 轮：按会话栏看得见的框限制时，第 4 张卡（顶 279、高 300、会话栏高 400）往下移 20px 被拽到 91。
        # 现在按整列卡片的位置限制：同样的卡往下 20 照样跟手（299），只在第一张的顶边与最后一张的底边之间。
        # 第 5 轮：浮层是 fixed（视口坐标），这一列的上下边按此刻的位置量，会话栏滚动后照样对；浮层不撑大滚动区
        self.run_page(r"""
import { clampToList } from "@/components/ui/drag";
const list = { top: 0, bottom: 1000 };
const tall = { top: 279, height: 300 };
assert.equal(279 + clampToList(20, tall, list), 299);
assert.equal(clampToList(-500, tall, list), -279);          // 不过第一张的顶边
assert.equal(clampToList(900, tall, list), 1000 - 579);      // 不过最后一张的底边
assert.equal(clampToList(5, { top: 0, height: 1200 }, list), 0);  // 比整列还高（不会发生）：不动
// 会话栏往下滚了 400（这一列上移到 -400 ~ 600）：拿起时在 279 的卡最多下到 600 - 300
assert.equal(clampToList(900, tall, { top: -400, bottom: 600 }), 600 - 579);
""")

    def test_company_page_month_is_shared_with_the_panel(self):
        self.run_page(r"""
import { pagePeriod, setPagePeriod, usePagePeriod } from "@/lib/company-panel";
// 还没选过：按设置取上个月（产品不按月份时为空）；选了以后输入框与「月结」卡都用它
assert.equal(pagePeriod(null, true, "2026-09"), "2026-09");
assert.equal(pagePeriod(null, false, "2026-09"), "");
setPagePeriod("2026-07");
assert.equal(usePagePeriod.getState().period, "2026-07");
assert.equal(pagePeriod(usePagePeriod.getState().period, true, "2026-09"), "2026-07");
assert.equal(pagePeriod("", true, "2026-09"), "");   // 选了「不指定」
""")


class SortableSourceTests(unittest.TestCase):
    def test_the_panel_uses_the_shared_drag(self):
        panel = read("components/session-panel.tsx")
        # 会话栏卡片走共用拖动：一个拖动区 + 一列（只认同一列的卡片），放下记顺序
        self.assertIn('import { DragArea, type DragData, DragList, type DragTexts } from "@/components/ui/drag";', panel)
        self.assertIn("<DragArea accepts={sameColumn} onDrop={onDrop} texts={texts}>", panel)
        self.assertIn('<DragList id="cards" items={items}>', panel)
        self.assertIn("const items = useMemo(() => order.filter((id) => id in titles), [order, titles]);", panel)
        self.assertIn("if (next) setCardOrder(next);", panel)
        self.assertIn("instructions: co.card.instructions,", panel)
        # 旧的自写拖动、放下过渡都删掉了（第 4 轮的 data-landing / data-moving 与 dnd-kit 放下帧打架）
        for old in ("pointermove", "onPointerDown", "landing", "withinList", "restrictToParentElement", "DndContext", "useSensor"):
            self.assertNotIn(old, panel, old)
        cards = read("components/company-cards.tsx")
        self.assertNotIn("onPointerDown", cards)
        self.assertNotIn("@dnd-kit", cards)
        self.assertNotIn("moving(", cards)
        self.assertIn('const sortable = useSortableItem({ id: props.id, kind: "card", label: props.title, list: "cards" });', cards)
        self.assertIn('<section {...sortable?.node} className="cc-panel-card" data-card={id} data-slot={slot}>', cards)
        self.assertIn("{...sortable.handle}", cards)
        self.assertIn("if (!keys(e)) sortable.handle.onKeyDown?.(e);", cards)   # Alt+↑/↓ 先，其余交给拖动（空格拿起）
        self.assertIn("useLayoutEffect(() => register(props.id, props.title), [register, props.id, props.title]);", cards)
        package = read("../package.json")
        for dep in ('"@dnd-kit/core": "6.3.1"', '"@dnd-kit/sortable": "10.0.0"', '"@dnd-kit/modifiers": "9.0.0"', '"@dnd-kit/utilities": "3.2.2"'):
            self.assertIn(dep, package)

    def test_shared_drag_rules(self):
        drag = read("components/ui/drag.tsx")
        # 手感：鼠标按住动 3px、手机长按 250ms（5px 内）、键盘空格拿起 / 空格回车放下 / Esc 取消
        self.assertIn("export const MOUSE_START = { distance: 3 };", drag)
        self.assertIn("export const TOUCH_START = { delay: 250, tolerance: 5 };", drag)
        self.assertIn("useSensor(MouseSensor, { activationConstraint: MOUSE_START }),", drag)
        self.assertIn('useSensor(HoldTouchSensor, { ...TOUCH_START, moveToLift: touch === "move" }),', drag)
        self.assertIn("useSensor(KeyboardSensor, { coordinateGetter: coordinates, keyboardCodes: KEYS }),", drag)
        self.assertIn("start: [KeyboardCode.Space],", drag)
        self.assertNotIn("PointerSensor", drag)   # 鼠标与触屏分开：触屏要长按，鼠标不用
        # 浮层（克隆原来那一项）跟手，放下用拖动库的落位动画（动效变量），落点那一项在动画播完前不显示
        self.assertIn('<DragOverlay dropAnimation={dropAnimation} transition={keyboardMove} className="cc-drag-overlay">', drag)
        # 浮层挂在 body 上：会话栏、侧栏是滚动区（又可能成了 fixed 的定位基准），挂在里面会跟着滚走
        self.assertIn("createPortal(overlay, document.body)", drag)
        self.assertIn('landing.node.dataset.dragLanding = "";', drag)
        self.assertIn('return { duration: cssMs(css.getPropertyValue("--cc-dur"), 200), easing: css.getPropertyValue("--cc-ease").trim() || "ease" };', drag)
        self.assertIn('if (root.dataset.ccMotion === "reduce" || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return null;', drag)
        # 只竖着拖、浮层不出这一列（按版面位置，不含让位的位移），浮层 fixed 不撑大滚动区
        self.assertIn("return { ...transform, x: 0, y: clampToList(transform.y, { top: start.top, height: start.height }, list) };", drag)
        self.assertIn("const rect = getClientRect(node, { ignoreTransform: true });", drag)
        # 碰撞只认能放的地方：排序最近的中心，拖到别的组看指针（键盘拖时是浮层中心）落在哪一组
        self.assertIn("if (active.list) return closestCenter({ ...args, droppableContainers });", drag)
        self.assertIn("return pointerWithin({ ...args, droppableContainers, pointerCoordinates });", drag)
        # 拖动标记：Esc 只取消拖动
        self.assertIn("setDragging(true);", drag)
        self.assertIn("useEffect(() => () => setDragging(false), []);", drag)
        # 克隆去掉会被别处按属性找到的标记，整块 inert
        self.assertIn('<div className="cc-drag-float" data-kind={data?.kind} inert aria-hidden>', drag)
        for name in ('"data-card"', '"data-row-id"', '"data-roving"', '"data-selected"'):
            self.assertIn(name, drag.split("const STRIP = [", 1)[1].split("];", 1)[0])
        layers = read("lib/layers.ts")
        self.assertIn('window.addEventListener("keydown", markEscape, true);', layers)
        self.assertIn("return escapes.has(event) || isDragging();", layers)

    def test_drag_lift_and_grip_slot_styles(self):
        css = read("globals.css")
        self.assertIn("--cc-drag-scale: 1.02;", css)
        self.assertIn("--cc-drag-opacity: 0.9;", css)
        self.assertIn("--cc-drag-ghost: 0.4;", css)
        lift = css.split("\n.cc-drag-lift {", 1)[1].split("}", 1)[0]
        self.assertIn("background: var(--cc-bg-popover); box-shadow: var(--cc-shadow-pop);", lift)
        self.assertIn("opacity: var(--cc-drag-opacity); scale: var(--cc-drag-scale);", lift)
        self.assertIn("transition: scale var(--cc-dur) var(--cc-ease)", lift)
        self.assertIn('.cc-drag-overlay[data-dropping] .cc-drag-lift { scale: 1; opacity: 1; box-shadow: none; }', css)
        self.assertIn('[data-drag-source="gap"] { opacity: 0; }', css)
        self.assertIn("[data-drag-landing] { opacity: 0; }", css)
        self.assertIn("[data-drop-target] { background: var(--cc-row-hover); }", css)
        self.assertIn(":root[data-cc-drag] * { cursor: grabbing; }", css)
        # 抓手光标归共用拖动的把手标记（会话栏卡片把手、侧栏行、置顶行、公司组头同一条，第 5 轮审查）；把手里的输入框是文字光标
        self.assertIn("[data-drag-handle] { cursor: grab; }", css)
        self.assertIn("[data-drag-handle] :is(input, textarea) { cursor: text; }", css)
        self.assertIn('"data-drag-handle": "" as const,', read("components/ui/drag.tsx"))
        # 第 4 轮的放下过渡（data-moving / data-landing）删掉了
        for old in ("data-moving", "data-landing", "--cc-land-y", "data-cc-card-drag"):
            self.assertNotIn(old, css)
        # 把手槽固定宽、平时把手透明，摘要一列右对齐
        self.assertIn(".cc-panel-card-grip { position: relative; display: inline-flex; flex-shrink: 0; width: var(--cc-panel-btn); }", css)
        self.assertIn(".cc-panel-card-grip .cc-panel-grip { opacity: var(--cc-reveal-rest); }", css)
        end = css.split(".cc-panel-card-end {", 1)[1].split("}", 1)[0]
        self.assertIn("justify-items: end;", end)
        self.assertIn("margin-inline-start: auto;", end)


class CompanyPageSkeletonTests(unittest.TestCase):
    def test_the_company_page_is_the_conversation_skeleton(self):
        page = read("components/company-page.tsx")
        master = page.split("const Master: FC", 1)[1].split("\n};", 1)[0]
        lines = [line.strip() for line in master.split('<div className="cc-company-page" data-slot="cc-company-page">', 1)[1].strip().splitlines()]
        self.assertEqual(lines[0], "<Header detail={detail} reload={reload} />")
        self.assertTrue(lines[2].startswith("<CompanyComposer company={id} title={detail.company.title} period={period}"), lines)
        self.assertEqual(lines[3], "<Conversations company={id} />")
        # 不再有标签与左栏的卡片：资料全在会话栏
        for gone in ("Tabs", "cc-tabs", "CompanyTabs", "cc-company-cols", "MonthSection", "SetsSection company", "<PendingCard"):
            self.assertNotIn(gone, master, gone)
        self.assertNotIn('from "radix-ui"', page)
        # 输入框同对话：输入框 + 下巴（公司固定，月份胶囊 = 本页的月份，分店胶囊）
        composer = page.split("const CompanyComposer: FC", 1)[1].split("\n};", 1)[0]
        self.assertIn('<div className="cc-composer cc-composer-inline">', composer)
        self.assertIn('<div className="cc-composer-chin" data-slot="cc-composer-chin">', composer)
        self.assertIn('<span className="cc-chin-btn cc-chin-fixed" data-slot="cc-chin-company" data-chosen="">', composer)
        self.assertIn('{withPeriod && <PeriodChip look="cc-chin-btn" side="top" value={period} onChange={setPagePeriod} />}', composer)
        self.assertIn("<BranchChip look=\"cc-chin-btn\" side=\"top\" branches={branches} value={scope} onChange={setScope} />", composer)
        # 对话列表：行同侧栏行（标题、月份 · 分店、相对时间），最新在前，显示更多
        chats = page.split("export const Conversations: FC", 1)[1]
        self.assertIn("{newestFirst(rows).map((row) => {", chats)
        self.assertIn('const meta = [monthShort(row.period ?? ""), branchShort(row.scope ?? "")].filter(Boolean).join(" · ");', chats)
        self.assertIn('<span className="cc-chat-row-when">{relativeWhen(row.updated_at, now)}</span>', chats)
        self.assertIn("const now = useMinuteClock();", chats)
        self.assertIn('<Marquee className="cc-row-title">{row.title || t("untitled")}</Marquee>', chats)
        self.assertIn('{meta && <Marquee className="cc-row-meta">{meta}</Marquee>}', chats)
        self.assertIn("onClick={() => void load(next)}", chats)
        # 手机：标题旁打开盖满屏的会话栏
        self.assertIn("onClick={() => setPanelOverlay(true)}", page)
        # 页宽同对话
        css = read("globals.css")
        self.assertIn("max-width: calc(var(--cc-thread-w) + 2rem);", css.split(".cc-company-page {", 1)[1].split("}", 1)[0])
        for gone in (".cc-tabs", ".cc-tab {", ".cc-company-cols", ".cc-company-pane"):
            self.assertNotIn(gone, css, gone)

    def test_the_company_page_panel_holds_the_company_cards(self):
        panel = read("components/session-panel.tsx")
        page = panel.split("const cards: Record<CardId, ReactNode> = page", 1)[1].split("    : {", 1)[0]
        for line in ("company: detail ? <CompanyPageCard detail={detail} period={period} /> : null,",
                     "month: <MonthSection company={company} period={period} onPeriod={setPagePeriod} />,",
                     "pending: <PendingCard company={company} />,",
                     "notes: detail ? <NotesSection company={company} notes={detail.notes} reload={reload} keepEmpty /> : null,",
                     "folders: detail ? <FolderSection company={company} folders={detail.folders} reload={reload} /> : null,",
                     "tasks: detail ? <TasksSection company={company} detail={detail} period={period} keepEmpty /> : null,",
                     "outputs: null,"):
            self.assertIn(line, page)
        self.assertIn("<ChoicesSection", page)
        self.assertIn("<DraftsSection", page)
        card = panel.split("const CompanyPageCard: FC", 1)[1].split("\n};", 1)[0]
        self.assertIn("<SetsList sets={sets} branches={branches.length > 1 ? branches : []} period={period} />", card)
        self.assertIn("summary={sets.length ? co.card.sets(sets.length) : undefined}", card)
        # 公司页的「月结」卡：月份胶囊一直显示（不是悬停才出的操作）
        self.assertIn('extra={<PeriodChip value={period} onChange={onPeriod} allowNone={false} look="cc-chin-btn" />}', read("components/month-ledger.tsx"))

if __name__ == "__main__":
    unittest.main()
