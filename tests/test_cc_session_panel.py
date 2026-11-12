"""CC 外壳第 1 批 B 线（设计文档 docs/work/2026-10-16-cc-shell-design.md 3.9 / 3.13 / 3.14 / 3.15 / 4.3 / 4.4 / 4.5）：

- 标题栏：「公司名 ›」点了打开右侧会话栏的公司卡；对话名点了就地改名；「···」单独是会话菜单；月份分店胶囊不在标题栏；
  右上 26 的「文件 N」（有文件才有）与会话栏开关（aria-pressed）。
- 右侧会话栏：只在 Cowork 挂了公司的对话里；与文件预览共用一个停靠位；宽屏并排、窄屏浮层（Esc、返回键）、手机全屏；
  卡片顺序 / 折叠 / 开关记在本机（键带用户）；拖动与 Alt+↑/↓ 只在看得见的卡片间挪；详情按「公司|月份」缓存。
- 公司页（10-16 改）：版式同对话页——中间标题 + 输入框（同对话的输入框与下巴）+ 这家的对话列表（行同侧栏行、相对时间、
  最新在前），公司的资料全在右侧会话栏（默认开着），没有标签。
- 卡头摘要一直显示（「2 条」「需处理 2」），把手的槽固定留宽、摘要一列对齐；拖动走共用拖动 components/ui/drag.tsx
  （自写的指针排序 + 浮层，第 12 轮起不再用 dnd-kit：键盘、触屏长按、读屏播报，松手先落位，浮层不出这一列）。
- 列表页（公司、待处理、定时任务）：标题、搜索、排序（公司）。

做法同 tests/test_assistant_ui.py：源码断言 + 用 esbuild 打包交给 node 跑的行为检查（要先 npm ci --prefix frontend/assistant）。
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from test_assistant_ui import ESBUILD, MA_SETUP, NODE, SOCKET_STUB, SRC as SRC_ROOT, block, keys, read, run_ts  # noqa: E402
import re  # noqa: E402

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
        self.assertNotIn("MoveToMenu", app)   # 用户 10-17：对话不能换公司，「移到」已删
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
        # 只在对话里（用户 11-07：公司页连同它的会话栏删了；定时任务页没有会话栏）
        self.assertIn("const hasPanel = view === \"chat\" && saved && !!filing.project;", layout)
        self.assertNotIn("companyPage", layout)
        self.assertNotIn("<SessionPanel page", layout)
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
        self.assertIn("{panelShown && <SessionPanel session={session} filing={filing} mode={mode} onClose={closePanel} />}", layout)
        self.assertIn('const session = useAuiState((s) => s.threadListItem.remoteId) ?? "";', layout)
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
        self.assertIn('if (mode === "docked") return;', panel)
        # 拖动中按 Esc 只取消拖动，不关浮着的会话栏（鼠标、键盘拖都一样；取消拖动的那一下 Esc 也认得出，lib/layers.ts）
        self.assertIn("if (isDragEscape(e)) return;", panel)

    def test_new_words_exist_in_every_language(self):
        i18n = read("i18n.ts")
        messages = block(i18n, "const MESSAGES = {")
        for lang in ("en", "zh", "th"):
            self.assertTrue({"showPanel", "hidePanel", "reorder"} <= keys(block(messages, f"  {lang}: {{")), lang)
            company = block(block(i18n, "const COMPANY = {"), f"  {lang}: {{")
            self.assertTrue({"company", "library", "chatRunning", "chatWaiting", "chatDone", "card", "allSets",
                             "deleteCompanyAsk"} <= keys(company), lang)
            # 公司列表页的排序、公司页的对话列表 11-07 随页删了
            self.assertFalse({"sortBy", "sortRecent", "sortName", "noChats", "conversations", "openCompany"} & keys(company), lang)
            card = block(company, "    card: {")
            self.assertEqual({"sets", "notes", "tasks", "choices", "docs", "drafts", "files", "folders", "toDo", "toCheck", "pickedUp",
                              "movedTo", "droppedAt", "cancelled", "instructions"}, keys(card), lang)
        self.assertNotIn("sessionPanel", i18n)
        self.assertIn('showPanel: "显示右侧栏",', i18n)
        self.assertIn('hidePanel: "隐藏右侧栏",', i18n)
        self.assertIn('reorder: "调整顺序",', i18n)
        self.assertIn('allSets: "全部账套"', i18n)
        self.assertIn('deleteCompanyAsk: "删除公司？"', i18n)
        for words in ('notes: (n: number) => `${n} 条`', 'tasks: (n: number) => `${n} 个任务`', 'choices: (n: number) => `${n} 项`',
                      'docs: (n: number) => `${n} 张`', 'drafts: (n: number) => `${n} 份`', 'files: (n: number) => `${n} 个文件`',
                      'sets: (n: number) => `${n} 本`', 'toDo: (n: number) => `需处理 ${n}`',
                      'notes: (n: number) => (n === 1 ? "1 note" : `${n} notes`)'):
            self.assertIn(words, i18n)
        self.assertIn('chatWaiting: "需要您的输入"', i18n)


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
        # 待处理页 11-04 删了（并进对话末尾的框），公司列表 11-07 删了，列表页只剩定时任务
        self.assertNotIn("cc-page-title", read("components/pending.tsx"))
        for page in ("components/scheduled-tasks.tsx",):
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
        self.assertIn(':root[data-cc-variant="phone"] .cc-panel-card-end { display: flex; gap: var(--cc-space-4); }', css)
        self.assertIn("@media (hover: none) {\n    .cc-panel-card-end { display: flex; gap: var(--cc-space-4); }", css)
        # 网页标题栏实高 32（设计文档 3.9）：不再加上下外边距
        topbar = css.split(".cc-topbar {", 1)[1].split("}", 1)[0]
        self.assertIn("height: var(--cc-topbar-h);", topbar)
        self.assertNotIn("margin", topbar)
        # 标题栏改名时 Esc 只取消改名，浮着的会话栏（看 defaultPrevented）不跟着关
        rename = read("components/assistant-ui/thread-list.tsx")
        self.assertIn('if (e.key === "Escape" && !e.nativeEvent.isComposing) {\n          e.preventDefault();\n          finish(false);', rename)
        # 公司改名（11-07 公司页标题删了，组头与公司卡就地改）用的就是上面这个对话行的 InlineRename，不另写一份
        for rel in ("components/session-panel.tsx", "components/assistant-ui/projects.tsx"):
            self.assertIn('import { InlineRename } from "@/components/assistant-ui/thread-list";', read(rel), rel)
            self.assertNotIn("TitleRename", read(rel), rel)
        self.assertIn("e.defaultPrevented", read("components/session-panel.tsx"))

    def test_the_f18_rule_and_the_task_buttons_stay(self):
        pending = read("components/pending.tsx")
        # F18（不拿列表第一项充数）在提问卡里：只有预选值排第一带「(推荐)」才默认选中，没选不能提交
        self.assertIn("<AskCard", pending)
        self.assertIn("isRecommended(q.options[0]) ? [q.options[0]!.label] : []", read("components/tool-uis/ask-shared.ts"))
        self.assertIn('disabled={busy || !answered(state)} onClick={primary}>', read("components/tool-uis/ask-card.tsx"))
        self.assertIn("if (embedded && (!items || !items.length) && !error) return null;", pending)
        tasks = read("components/scheduled-tasks.tsx")
        self.assertEqual(tasks.count("onClick={() => newTask(taskCompany)}"), 2)

    def test_company_card_shows_the_conversations_account_set(self):
        # A 线 11-12（R2-5）：右栏原先按日期现算（setsForScope：覆盖本月、都不覆盖退最新可写、同期两本静默取第一本），与服务端
        # 写入的不是同一本；现在「ชุดบัญชี」一行是本对话账套（服务端 api.ledgers），点开可改（move 带 ledger），不再现算
        panel = read("components/session-panel.tsx")
        card = panel.split("const CompanyCard: FC", 1)[1].split("\n};", 1)[0]
        self.assertIn("<LedgerRow session={session} filing={filing} />", card)
        self.assertNotIn("setsForScope", panel)
        self.assertNotIn("label={co.accountSets}", card)
        row = panel.split("export const LedgerRow: FC", 1)[1].split("\n};", 1)[0]
        self.assertIn("const entry = useConversationLedger(session, filing);", row)
        self.assertIn("void chooseLedger(session, filing, id)", row)
        row = panel.split("export const LedgerMenu: FC", 1)[1].split("\n};", 1)[0]
        self.assertIn("if (!entry?.options.length) return null;", row)
        self.assertIn('<button type="button" className="cc-panel-row" data-slot="cc-company-ledger" title={label}>', row)
        self.assertIn("<Marquee className=\"min-w-0 flex-1 truncate\">{co.accountSets}</Marquee>", row)   # 沿用已有词条，不加新文案
        self.assertIn("onSelect={() => item.id !== entry.current && onChoose(item.id)}", row)
        # 前端不再有按日期现算账套的函数
        companies = read("lib/companies.ts")
        for gone in ("setsForScope", "setForPeriod", "coveringSet", "coveringOf", "coveringOrNewest", "const covers ="):
            self.assertNotIn(gone, companies, gone)
        for rel in ("components/company-cards.tsx", "components/scheduled-tasks.tsx", "components/month-ledger.tsx",
                    "components/pending.tsx"):
            text = read(rel)
            for gone in ("setsForScope", "setForPeriod", "coveringSet", "setFor("):
                self.assertNotIn(gone, text, (rel, gone))

    def test_the_panel_pending_card_lists_only_other_months(self):
        # 开发窗口 10-16 定：会话栏「待处理」卡只列对话月份以外的，对话那个月的只在「本月」卡（列全部月份的公司页 11-07 删了）
        panel = read("components/session-panel.tsx")
        self.assertIn("pending: <PendingCard company={company} period={period} />,", panel)
        self.assertNotIn("pending: <PendingCard company={company} />,", panel)
        # 对话那个月的落在对话末尾的「需要处理」框（11-04 待处理并进对话），两处正好不重复
        self.assertIn("pendingMonth(item) === month", read("components/pending.tsx").split("const ChatPendingBox: FC", 1)[1])
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

    def test_the_panel_shows_and_changes_the_conversations_account_set(self):
        # A 线 11-12（R2-5 回放）：选中 T.8、同期还有 T.9（按日期现算会取第一本 T.9）——右栏显示服务端给的 T.8；选 T.9 调 move 带
        # ledger；未定显示空值；推来的 move 带 ledger 就地换，认不出重取；服务端没收下撤回
        self.run_page(r"""
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.browser";
import { LedgerMenu } from "@/components/session-panel";
import { applyLedgerChange, chooseLedger, ledgerOf, loadLedgers, resetLedgers, useLedgers } from "@/lib/conversation-ledger";
const g = globalThis as any;
const ok = (message: any) => ({ ok: true, status: 200, json: async () => ({ message }) });
const T9 = { id: "c1|T9", label: "T.9", covers: true }, T8 = { id: "c1|T8", label: "T.8", covers: true };
let served: any = { current: "c1|T8", options: [T9, T8] };
let refuse = false;
g.respond = async (method: string, body: any) => {
  if (method === "ledgers") return ok(served);
  if (method === "move") return refuse ? { ok: false, status: 417, json: async () => ({ exc_type: "ValidationError", _server_messages: "[]" }) }
    : ok({ id: body.session_id, project: body.project, period: body.period, scope: body.scope, ledger: body.ledger });
  return ok(null);
};
const filing = { project: "comp_t", period: "2026-06", scope: "" };
// 右栏那一行（LedgerRow 读的就是 ledgerOf，再交给 LedgerMenu 画）
const entryFor = (f: any) => ledgerOf(useLedgers.getState().views, "sesn_1", f);
const row = () => renderToStaticMarkup(createElement(LedgerMenu, { entry: entryFor(filing), onChoose() {} }));
const shown = (markup: string) => markup.replace(/<[^>]+>/g, "\n").split("\n").map((s) => s.trim()).filter(Boolean);
resetLedgers();
// 还没读到：不显示（不先显示一本按日期猜的）
assert.equal(row(), "");
await loadLedgers("sesn_1", filing);
assert.deepEqual(g.calls.at(-1), { method: "ledgers", body: null });
// 显示服务端的当前账套 T.8，不是排第一 / 按日期覆盖本月的 T.9；只显示当前那一本（点开才列可选的）
assert.deepEqual(shown(row()), ["Account sets", "T.8"]);
assert.ok(row().includes('data-slot="cc-company-ledger"'));
// 选 T.9：move 带 ledger（公司、月份、分店照对话的），先按本页显示
const picked = chooseLedger("sesn_1", filing, "c1|T9");
assert.equal(useLedgers.getState().views.sesn_1.current, "c1|T9");
await picked;
assert.deepEqual(g.calls.at(-1), { method: "move", body: { session_id: "sesn_1", project: "comp_t", period: "2026-06", scope: "", ledger: "c1|T9" } });
assert.deepEqual(shown(row()), ["Account sets", "T.9"]);
// 推来的 move 带 ledger（CC 工具按用户的提问卡设的）：认得的就地换；认不出的标记重取，不显示猜的
applyLedgerChange({ type: "pearnly.project", op: "move", session_id: "sesn_1", project: "comp_t", period: "2026-06", scope: "", ledger: "c1|T8" });
assert.deepEqual(shown(row()), ["Account sets", "T.8"]);
applyLedgerChange({ type: "pearnly.project", op: "move", session_id: "sesn_1", project: "comp_t", period: "2026-06", scope: "", ledger: "c1|T7" });
assert.equal(useLedgers.getState().views.sesn_1.stale, true);
// 这家公司改了（刷新账套等，part 为空）：可选的可能变了，标记重取；待处理变了（part pending）不管
resetLedgers();
await loadLedgers("sesn_1", filing);
applyLedgerChange({ type: "pearnly.project", op: "upsert", project: { id: "comp_t", title: "T" }, part: "pending" }, "sesn_1", "comp_t");
assert.equal(useLedgers.getState().views.sesn_1.stale, undefined);
applyLedgerChange({ type: "pearnly.project", op: "upsert", project: { id: "comp_x", title: "X" } }, "sesn_1", "comp_t");
assert.equal(useLedgers.getState().views.sesn_1.stale, undefined);
applyLedgerChange({ type: "pearnly.project", op: "upsert", project: { id: "comp_t", title: "T" } }, "sesn_1", "comp_t");
assert.equal(useLedgers.getState().views.sesn_1.stale, true);
// 不带 ledger 的 move（产品没有本对话账套）不动它
applyLedgerChange({ type: "pearnly.project", op: "move", session_id: "sesn_2", project: "comp_t", period: "2026-06", scope: "" });
assert.equal(useLedgers.getState().views.sesn_2, undefined);
// 月份改了：读的是别的月份的那份不拿来显示（等重取）
assert.equal(entryFor({ ...filing, period: "2026-07" }), null);
// 服务端没收下：撤回原来那本
served = { current: "c1|T8", options: [T9, T8] };
resetLedgers();
await loadLedgers("sesn_1", filing);
refuse = true;
await assert.rejects(chooseLedger("sesn_1", filing, "c1|T9"));
assert.equal(useLedgers.getState().views.sesn_1.current, "c1|T8");
refuse = false;
// 未定（current 为空）：这一行照样在、值空着，不加说明文字；只有一本可选也照样显示
served = { current: "", options: [T9] };
resetLedgers();
await loadLedgers("sesn_1", filing);
assert.deepEqual(shown(row()), ["Account sets"]);
assert.ok(row().includes('data-slot="cc-company-ledger"'));
// 这家没有本对话账套（ERP 不接 Express 的公司等）：不出这一行
served = { current: "", options: [] };
resetLedgers();
await loadLedgers("sesn_1", filing);
assert.equal(row(), "");
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
import { cssPx } from "@/lib/css-var";
import { dockFits } from "@/lib/company-panel";
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
        # 待处理页（连同它的搜索）11-04 删了：待处理并进对话末尾的框，按这段对话的公司、月份列，不再搜
        self.assertNotIn("matchesPending", read("components/pending.tsx"))
        # 公司列表页（连同它的「最近活动 / 名称」排序）11-07 删了：选公司在对话里的选择公司窗口（服务端分页搜索）
        self.assertFalse((SRC_ROOT / "components/companies.tsx").exists())
        self.assertNotIn("sortCompanies", "\n".join(p.read_text(encoding="utf-8") for p in SRC_ROOT.rglob("*.ts*")))

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
// 摘要也是单行截断的文字（放不下时末尾渐隐，同侧栏；用户 10-16「统一渐隐」）
assert.ok(page.includes(`<span class="cc-panel-card-end" data-slot="cc-card-end"><span class="cc-marquee cc-panel-card-summary" id="${described}"><span class="cc-marquee-text">2 notes</span></span></span>`));
assert.ok(page.includes(">body<"));
assert.ok(!page.includes("cc-panel-grip") && !page.includes("cc-card-grip"));
// 摘要为 0、空不显示（开关也不带说明）
assert.ok(!renderToStaticMarkup(card({ summary: 0 })).includes("cc-panel-card-summary"));
assert.ok(!renderToStaticMarkup(card({ summary: "" })).includes("aria-describedby"));
// 卡头操作与摘要叠在同一格（悬停才出，盖住摘要）；一直显示的控件在标题后面
const tools = renderToStaticMarkup(card({ action: createElement("button", null, "+"), extra: createElement("b", null, "Sep") }));
assert.match(tools, /<span class="cc-panel-card-extra"><b>Sep<\/b><\/span><span class="cc-panel-card-end" data-slot="cc-card-end"><span class="cc-marquee cc-panel-card-summary" id="[^"]+"><span class="cc-marquee-text">2 notes<\/span><\/span><span class="cc-panel-card-tools"><button>\+<\/button><\/span><\/span>/);
// 会话栏里（排序列表里）：折叠记在会话栏（折叠了不画内容）；每张卡最右边都有把手槽，把手接上排序（共用拖动给的读屏属性）
const control = { collapsed: ["notes"], toggle() {}, move() {}, register: () => () => {} };
const texts = { instructions: "", pickedUp: String, movedTo: String, droppedAt: String, cancelled: String };
const inPanel = (child: any) => renderToStaticMarkup(createElement(PanelControlContext.Provider, { value: control },
  createElement(DragArea, { accepts: () => true, onDrop() {}, texts }, createElement(DragList, { id: "cards", items: ["notes"] }, child))));
const panel = inPanel(card());
assert.match(panel, /aria-expanded="false"/);
assert.ok(!panel.includes(">body<"));
assert.match(panel, /<span class="cc-panel-card-grip" data-slot="cc-card-grip"><button type="button"[^>]*role="button"[^>]*tabindex="0"[^>]*aria-roledescription="sortable"[^>]*class="cc-panel-btn cc-panel-grip" aria-label="Reorder" title="Reorder">/);
assert.match(panel, /<section data-drag-id="notes" data-drag-list="cards" class="cc-panel-card" data-card="notes">/);   // 没在拖：没有位移、不留空
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
        # 公司卡只剩对话里这一张（公司页的公司卡 CompanyPageCard 11-07 删了）；本对话账套只有一本，卡头不再写「N 本」
        self.assertEqual(panel.count('slot="cc-panel-company"'), 1)
        self.assertNotIn("co.card.sets(sets.length)", panel)
        self.assertNotIn("CompanyPageCard", panel)

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

    def test_company_page_month_is_gone(self):
        # 公司页自己的月份（输入框与「月结」卡共用）11-07 随公司页删了；「本月」卡的月份胶囊只在卡里（见 test_cc_month_ledger）
        self.run_page(r"""
import * as panel from "@/lib/company-panel";
for (const gone of ["pagePeriod", "setPagePeriod", "usePagePeriod", "usePageMonth"]) assert.equal(gone in panel, false, gone);
""")


class SortableSourceTests(unittest.TestCase):
    def test_the_panel_uses_the_shared_drag(self):
        panel = read("components/session-panel.tsx")
        # 会话栏卡片走共用拖动：一个拖动区 + 一列（只认同一列的卡片），放下记顺序
        self.assertIn('import { DragArea, type DragData, DragList, type DragTexts, spotIn } from "@/components/ui/drag";', panel)
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
        # 拖动是自写的（第 12 轮，components/ui/drag.tsx）：依赖与锁文件里都不再有 dnd-kit，不会被别处悄悄引回来
        for manifest in ("../package.json", "../package-lock.json"):
            self.assertNotIn("@dnd-kit", read(manifest), manifest)

    def test_shared_drag_rules(self):
        drag = read("components/ui/drag.tsx")
        # 手感：鼠标按住动 3px、手机长按 250ms（5px 内）、键盘空格拿起 / 空格回车放下 / Esc 取消
        self.assertIn("export const MOUSE_START = { distance: 3 };", drag)
        self.assertIn("export const TOUCH_START = { delay: 250, tolerance: 5 };", drag)
        # 三种传感器放下时都先交给 beforeEnd（第 6 轮：先播落位，播完才结束拖动）；手机侧栏的行、组头与卡片把手同一规则
        self.assertIn("mouse: { activationConstraint: MOUSE_START, beforeEnd, beforeCancel },", drag)
        self.assertIn("touch: { ...TOUCH_START, beforeEnd, beforeCancel },", drag)
        # 键盘另带 owns：只认按在把手上的键（第 12 轮 D15），放下、取消同样先交 beforeEnd / beforeCancel
        self.assertIn("keyboard: { keyboardCodes: KEYS, scrollBehavior: KEYBOARD_SCROLL, step, owns, beforeEnd, beforeCancel },", drag)
        self.assertIn("export const LandingMouseSensor = landFirst<MouseSensorOptions>(MouseSensor, \"mouse\");", drag)
        self.assertIn("export const LandingKeyboardSensor = landFirst<KeyboardSensorOptions>(KeyboardSensor);", drag)
        self.assertIn("start: [KeyboardCode.Space],", drag)
        self.assertNotIn("PointerSensor", drag)   # 鼠标与触屏分开：触屏要长按，鼠标不用
        # 浮层（克隆原来那一项）跟手，放下先播落位（动效变量），落点那一项在动画播完前不显示
        overlay = drag.split("function liftOverlay(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("copy.appendChild(cloneOf(node));", overlay)
        # 浮层挂在 body 上：会话栏、侧栏是滚动区（又可能成了 fixed 的定位基准），挂在里面会跟着滚走。挂在 body 下一直留着的那一层
        # （拖动中 body 下不增删节点）
        self.assertIn("dragLayer()?.appendChild(overlay);", overlay)
        self.assertIn("document.body.appendChild(layer);", drag.split("export function dragLayer()", 1)[1].split("\n}\n", 1)[0])
        # 落点那一项按拖动编号在此刻的页面里找（拖进另一列以后取消，拖动库给的是那一列里已经卸下的那一份）
        self.assertIn("const node = landingNode(active.id, active.node);", drag)
        self.assertIn('node.dataset.dragLanding = "";', drag)
        self.assertIn('return { duration: cssMs(css.getPropertyValue("--cc-dur"), 200), easing: css.getPropertyValue("--cc-ease").trim() || "ease" };', drag)
        self.assertIn('if (root.dataset.ccMotion === "reduce" || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return null;', drag)
        # 只竖着拖、浮层不出这一列（按版面位置，不含让位的位移），浮层 fixed 不撑大滚动区
        position = drag.split("  private position() {", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("const y = list ? clampToList(d.rawY, d.start, list) : d.rawY;", position)
        self.assertIn("d.overlay.style.transform = transformOf({ x: 0, y, scaleX: 1, scaleY: 1 });", position)
        self.assertIn("rects.push(this.rectNow(d, id));", position)
        self.assertIn("const rect = untransformedRect(node);", drag.split("export function layoutRect(", 1)[1].split("\n}\n", 1)[0])
        # 碰撞只认能放的地方：按 narrowPool 收窄（键盘只在本列；指针进了哪一列的整块就认那一列）后取最近的中心
        self.assertIn("closestCenter({ collisionRect: box, droppableRects: rects, droppableContainers: pool })[0]", drag)
        self.assertNotIn("pointerWithin", drag)
        # 拖动标记：Esc 只取消拖动
        self.assertIn("setDragging(true, byMouse(activatorEvent));", drag)
        self.assertIn("this.letGo.flush(); // 卸下时还在等落位的放下当场结束\n      if (this.drag) this.finish(this.drag, () => undefined);\n      setDragging(false);", drag)
        # 克隆去掉会被别处按属性找到的标记，整块 inert
        self.assertIn('float.className = "cc-drag-float";\n  float.dataset.kind = data.kind;\n  float.setAttribute("inert", "");\n  float.setAttribute("aria-hidden", "true");', drag)
        for name in ('"data-card"', '"data-row-id"', '"data-drag-id"', '"data-roving"', '"data-selected"'):
            self.assertIn(name, drag.split("const STRIP = [", 1)[1].split("];", 1)[0])
        layers = read("lib/layers.ts")
        self.assertIn('window.addEventListener("keydown", markEscape, true);', layers)
        self.assertIn("return escapes.has(event) || (isDragging() && !released);", layers)   # 已经放手、只等落位的不算（审查）

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
        self.assertIn(".cc-drag-shield { position: fixed; inset: 0; z-index: var(--cc-z-drag-shield); cursor: grabbing; }", css)
        # 抓手光标归共用拖动的把手标记（会话栏卡片把手、侧栏行、置顶行、公司组头同一条，第 5 轮审查）；把手里的输入框是文字光标
        self.assertIn("[data-drag-handle] { cursor: grab; }", css)
        self.assertIn("[data-drag-handle] :is(input, textarea) { cursor: text; }", css)
        # 侧栏里悬停是手指、不是抓手（用户 10-16，只改光标不改行为）：排在抓手那条后面、选择器更具体
        self.assertIn(':is([data-slot="sidebar"], .cc-drawer) [data-drag-handle] { cursor: pointer; }', css)
        self.assertLess(css.index("[data-drag-handle] { cursor: grab; }"), css.index(':is([data-slot="sidebar"], .cc-drawer) [data-drag-handle] { cursor: pointer; }'))
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


class CompanyPageGoneTests(unittest.TestCase):
    """用户 11-07：公司页（对话骨架的那一页与它的会话栏）删了；资料都在对话的会话栏里，会话栏只有一套卡片。"""

    def test_the_company_page_is_gone(self):
        self.assertFalse((SRC_ROOT / "components/company-page.tsx").exists())
        self.assertNotIn("<CompanyPage", read("assistant.tsx"))

    def test_the_panel_has_one_set_of_cards(self):
        panel = read("components/session-panel.tsx")
        self.assertIn("export const SessionPanel: FC<{ session: string; filing: Filing; mode: PanelMode; onClose: () => void }> = ({ session, filing, mode, onClose }) => {", panel)
        self.assertNotIn("data-page", panel)
        self.assertNotIn("= page", panel)
        cards = panel.split("const all: Record<CardId, ReactNode> = {", 1)[1].split("\n  };", 1)[0]
        for line in ("company: detail ? <CompanyCard detail={detail} session={session} filing={filing} reload={reload} /> : null,",
                     "month: <MonthCard company={company} period={period} />,",
                     "pending: <PendingCard company={company} period={period} />,",
                     # 知识库、定时任务空的也显示（「＋」加第一条），不再要公司页的 keepEmpty
                     "notes: detail ? <NotesSection company={company} notes={detail.notes} reload={reload} /> : null,",
                     "folders: detail ? <FolderSection company={company} folders={detail.folders} reload={reload} hideEmpty /> : null,",
                     "tasks: detail ? <TasksSection company={company} detail={detail} /> : null,",
                     "outputs: <OutputsCard />,"):
            self.assertIn(line, cards)
        self.assertIn("<ChoicesSection", cards)
        self.assertIn("<DraftsSection", cards)
        self.assertNotIn("keepEmpty", cards)
        # 公司页公司卡上的全部账套搬进对话的公司卡：「全部账套」一行展开（多分店按分店分组，覆盖这个月的高亮）
        card = panel.split("const CompanyCard: FC", 1)[1].split("\n};", 1)[0]
        self.assertIn('aria-expanded={allSets} data-slot="cc-company-all-sets" onClick={() => setAllSets((v) => !v)}>', card)
        self.assertIn("{allSets && <SetsList sets={detail.account_sets} branches={branches.length > 1 ? branches : []} />}", card)
        # 名称一行不再是「打开公司」的按钮
        self.assertIn('<div className="cc-panel-row" data-strong="" title={c.title}>', card)
        self.assertNotIn("openCompany", card)
        tasks = read("components/company-cards.tsx").split("export const TasksSection: FC", 1)[1].split("\n};", 1)[0]
        self.assertIn("if (!detail.tasks) return null;", tasks)
        self.assertNotIn("!tasks.length", tasks.split("return (", 1)[0])


class ErpSectionsSourceTests(unittest.TestCase):
    """ERP 版 CC 按公司（2026-11-02，评估 §15）：页面只出产品声明的分区，不按产品名分支；Cowork 写死的接口只在 Cowork 的分区里。"""

    def test_the_companies_api_comes_from_the_declaration(self):
        companies = read("lib/companies.ts")
        self.assertNotIn("const API =", companies)
        self.assertIn("const api = useProjects.getState().api;", companies)
        self.assertNotIn("pearnly_cowork.companies.api", companies.split("\n\n", 1)[1])   # 只在开头注释里提到
        projects = read("lib/managed-agents/projects.ts")
        self.assertIn('api: kind && typeof out?.api === "string" ? out.api : "",', projects)
        self.assertIn("sections: kind ? sectionsOf(out?.sections) : [],", projects)
        # 代录不再自己取公司（11-04 统一对话框架）：新建页的公司就是直写下巴那一个（声明的接口）；代录的卡片、桥不取公司、不按产品名分支
        for rel in ("components/tool-uis/operator-shared.ts", "components/tool-uis/operator-start.tsx", "components/operator/bridge.ts"):
            text = read(rel)
            self.assertNotIn("search_link", text)
            self.assertNotIn('"companies"', text)
            self.assertNotIn("product ===", text)
        self.assertIn("<Thread components={{ Welcome, Loading: ThreadLoading, ToolFallback: ManagedAgentsToolFallback, Chin: ComposerChin, Tail: PendingInChat }} />",
                      read("assistant.tsx"))

    def test_no_branch_on_the_product_name_for_company_parts(self):
        for path in ("lib/companies.ts", "lib/company-panel.ts", "components/company-menu.tsx", "lib/nav.ts",
                     "components/session-panel.tsx", "components/pending.tsx", "components/company-picker.tsx",
                     "components/company-cards.tsx", "components/month-ledger.tsx", "components/assistant-ui/projects.tsx"):
            text = read(path)
            self.assertNotRegex(text, r'product\s*===\s*"(erp|cowork)"', path)
        app = read("assistant.tsx")
        # 待处理并进对话（11-04）：对话末尾的框、侧栏占位行同样只看产品声明的 pending，不按产品名分支
        self.assertIn('const kind = useSection("pending");', read("components/pending.tsx").split("function useChatPending(", 1)[1].split("\n}\n", 1)[0])
        self.assertIn('useEffect(() => (pending ? watchPendingCounts() : undefined), [pending]);', app)
        nav = read("lib/nav.ts")
        self.assertIn('if (!kind || (target.view === "pending" && !hasSection("pending"))) return newChat();', nav)

    def test_every_card_and_menu_item_follows_a_declared_section(self):
        panel = read("components/session-panel.tsx")
        self.assertIn('month: "month", pending: "pending", notes: "notes", folders: "folders", tasks: "tasks", choices: "choices", drafts: "drafts",',
                      panel)
        self.assertIn("const cards = declaredCards(all, sections);", panel)
        # 公司的「···」（11-07 从公司页标题搬到组头与公司卡，components/company-menu.tsx）：每项按声明出，一项都没有时不出
        menu = read("components/company-menu.tsx")
        items = menu.split("export const CompanyMenuItems: FC", 1)[1]
        for line in ('const canRename = useSection("rename");', 'const canRefresh = useSection("catalog");',
                     'const canDelete = useSection("delete");', 'const withDesk = useSection("desk");', "if (!withDesk) return;",
                     "if (!(canRename || canRefresh || canDelete || desk)) return null;",
                     "{canRename && (", "{canRefresh && (", "{desk && (", "{canDelete && ("):
            self.assertIn(line, items)
        self.assertIn("menu.any ? (", read("components/session-panel.tsx").split("const CompanyCard: FC", 1)[1].split("\n};", 1)[0])
        picker = read("components/company-picker.tsx")
        # 选择公司只有账套目录这一种（2026-11-04：ERP 以前在新窗口开 ERP 新建公司页，CC 桌面版里交给系统浏览器、要重新登录）
        self.assertNotIn("openSitePage", picker)
        self.assertNotIn("newCompanyUrl", read("lib/companies.ts"))
        # 「刷新账套」只在声明了 catalog 的产品（ERP 的站点上没有 Cowork 的连接接口），在弹窗头部一直在
        self.assertIn('const canRefresh = useSection("catalog");', picker)
        self.assertIn("{canRefresh && (", picker.split('<div className="cc-dialog-head">', 1)[1].split("</div>", 1)[0])
        # 没建的行服务端只给能建的人：页面不另判 create，点了直接建
        self.assertNotIn('useSection("create")', picker)
        self.assertIn("onClick={() => openCompanyPicker(pick, draft.project)}", read("components/assistant-ui/projects.tsx"))
        pending = read("components/pending.tsx")
        self.assertIn("const hand = items.filter((item) => !item.url && ", pending)
        self.assertIn("useEffect(() => onPendingChange(() => void reload()), [reload]);", pending)

    def test_every_hard_coded_cowork_call_is_only_reached_from_cowork_parts(self):
        """前端里所有 pearnly_cowork. 字面量：在哪个文件、为什么 ERP 调不到（评估 §15.4 的检查）。"""
        found = {}
        for path in sorted(SRC_ROOT.rglob("*.ts*")):
            for line in path.read_text(encoding="utf-8").splitlines():
                for method in re.findall(r"pearnly_cowork\.[\w.]+", line):
                    found.setdefault(path.relative_to(SRC_ROOT).as_posix(), set()).add(method.rstrip("."))
        reasons = {
            # 待处理里 Cowork 服务端才给的 action（choose / push / account_set / mrerp），ERP 的待处理只给 open；记住的选择卡（choices）
            # 里改 MR.ERP 菜单；选择公司窗口与公司「···」的「刷新账套」（catalog）；文件头注释
            "lib/companies.ts": {"pearnly_cowork.companies.api", "pearnly_cowork.push.application.api.choices",
                                 "pearnly_cowork.assistant.pending_api.answer", "pearnly_cowork.assistant.pending_api.proposal_detail",
                                 "pearnly_cowork.assistant.pending_api.account_sets",
                                 "pearnly_cowork.assistant.pending_api.choose_account_set",
                                 "pearnly_cowork.assistant.pending_api.mrerp_needs",
                                 "pearnly_cowork.assistant.pending_api.mrerp_answer", "pearnly_cowork.connections.api.mrerp_menu",
                                 "pearnly_cowork.connections.api.records", "pearnly_cowork.connections.api.request_refresh"},
            # 设置页的「电脑与连接」只在 Cowork（web = !api && product === "cowork"；桌面窗口里按通道）
            "components/settings.tsx": {"pearnly_cowork.folder_intake.api.computers", "pearnly_cowork.connections.api.member_choices",
                                        "pearnly_cowork.companion.api.remove_device", "pearnly_cowork.companion.api.installer",
                                        "pearnly_cowork.connections.api.records", "pearnly_cowork.connections.api.set_members"},
            # 定时任务页只在 Cowork（hasScheduledTasks）
            "components/scheduled-tasks.tsx": {"pearnly_cowork.folder_intake.api", "pearnly_cowork.connections.api",
                                               "pearnly_cowork.companion.api"},
            # 桌面端用登录的账号连 Cowork 通道（只在 Cowork 站点上做）；装桌面版取安装包；注释
            "lib/desktop.ts": {"pearnly_cowork.companion.api.register"},
            "lib/install.ts": {"pearnly_cowork.companion.api.installer"},
            # 月结台账只在声明了 month 的产品（Cowork）的会话栏「本月」卡里读
            "lib/month.ts": {"pearnly_cowork.companies.api.month"},
            "lib/frappe.ts": {"pearnly_cowork.folder_intake.api"},
            # MR.ERP 登录授权的共用组件按产品的通道要票：Cowork 走 Cowork 通道，ERP 走共用通道（channelOf(product)），ERP 调不到这一条
            "components/mrerp-login.tsx": {"pearnly_cowork.companion.api.mrerp_login_ticket"},
        }
        self.assertLessEqual(set(found), set(reasons))
        for path, methods in found.items():
            for method in methods:
                self.assertTrue(any(method == r or method.startswith(r + ".") for r in reasons[path]), f"{path}: {method}")
        # lib/companies.ts 里这几个函数只从 Cowork 的分区或 Cowork 才有的 action 调到
        # 当场选、待选账套、MR.ERP 要选的 11-05 起走共用写账前置判定（pearnly_core.write_precheck，两产品都有）出提问卡，不再调 Cowork 专有接口
        callers = {"pushProposal": "components/pending.tsx", "loadProposal": "components/pending.tsx",
                   "loadMrErpOptions": "components/company-cards.tsx", "refreshCatalogs": "components/company-picker.tsx"}
        # 「刷新账套」还在公司的「···」里（11-07 从公司页搬来；只在声明了 catalog 的产品）
        also = {"refreshCatalogs": {"components/company-menu.tsx"}}
        for gone in ("loadChoices", "answerChoices", "loadAccountSets", "chooseAccountSet", "loadMrErpNeeds", "answerMrErp"):
            self.assertNotIn(f"export function {gone}(", read("lib/companies.ts"))
        for name, home in callers.items():
            users = [p.relative_to(SRC_ROOT).as_posix() for p in SRC_ROOT.rglob("*.ts*")
                     if p.name != "companies.ts" and re.search(rf"\b{name}\(", p.read_text(encoding="utf-8"))]
            self.assertIn(home, users, name)
            self.assertLessEqual(set(users), {home, "components/month-ledger.tsx", *also.get(name, ())}, name)
        pending = read("components/pending.tsx")
        for kind in ("choose", "push", "mrerp", "account_set"):
            self.assertIn(f'item.action === "{kind}"', pending)
        menu = read("components/company-menu.tsx")
        self.assertIn("await refreshCatalogs();", menu.split("const refresh = async", 1)[1].split("};", 1)[0])
        self.assertIn("{canRefresh && (", menu)


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class ErpSectionsBehaviourTests(unittest.TestCase):
    STUBS = {**STUBS, "cc-ma-setup": MA_SETUP}

    def run_page(self, body):
        run_ts('import "cc-test-setup";\nimport "cc-ma-setup";\nimport assert from "node:assert/strict";\n' + body
               + "\n(globalThis as any).done();\n", stubs=self.STUBS)

    def test_erp_declaration_calls_only_erp_methods_and_keeps_only_its_cards(self):
        self.run_page(r"""
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.browser";
import { refreshProjects, resetProjects, useProjects } from "@/lib/managed-agents/projects";
import { createCompany, deleteNote, listChoices, listCompanies, loadDetail, loadPending, refreshPendingCounts, saveNote, usePendingCounts } from "@/lib/companies";
import { declaredCards } from "@/components/session-panel";
import { PendingItems } from "@/components/pending";
const g = globalThis as any;
const ok = (message: any) => ({ ok: true, status: 200, json: async () => ({ message }) });
const ERP = "pearnly_erp_portal.masters.cc_companies.";
g.respond = async (method: string) => {
  if (method === "projects") return ok({ kind: "company", period: true, scope: false, api: ERP,
    sections: ["pending", "notes", "create", "desk", "bogus", 3], projects: [{ id: "CO-00001", title: "Siam", subtitle: "0105536082808" }] });
  if (method.includes(ERP + "companies")) return ok([{ id: "CO-00001", title: "Siam", tax_id: "0105536082808" }]);
  if (method.includes(ERP + "choices")) return ok({ rows: [{ identity: "express:0105536082808:00000", title: "Siam", company: "CO-00001",
    connection: "", account_set_key: "0105536082808:00000", adapter: "express" }], adapters: ["express"] });
  if (method.includes(ERP + "create")) return ok({ id: "CO-00009", title: "Korat", created: true });
  if (method.includes(ERP + "pending_counts")) return ok({ total: 2, companies: { "CO-00001": 2 }, unassigned: 0 });
  if (method.includes(ERP + "pending")) return ok({ items: [], count: 0 });
  if (method.includes(ERP + "detail")) return ok({ company: { id: "CO-00001", title: "Siam", desk_url: "/desk/pearnly-company/CO-00001" },
    account_sets: [], branches: [], folders: [], notes: [], choices: [], tasks: null, drafts: null });
  if (method.includes(ERP + "note_save")) return ok({ id: "n1", text: "x" });
  return ok(null);
};
resetProjects();
assert.equal(await refreshProjects(), "company");
const state = useProjects.getState();
assert.equal(state.api, ERP);
assert.deepEqual(state.sections, ["pending", "notes", "create", "desk"]);   // 认不出的丢掉
assert.equal(state.scope, false);
assert.deepEqual((await listCompanies("si")).map((row) => row.id), ["CO-00001"]);
assert.equal((await loadDetail("CO-00001", "2026-10")).company.desk_url, "/desk/pearnly-company/CO-00001");
assert.deepEqual(await loadPending("CO-00001"), []);
await refreshPendingCounts();
assert.equal(usePendingCounts.getState().total, 2);
// 选择公司：同 Cowork 的弹窗，选择列表与建公司走 ERP 自己的接口
const picked = await listChoices("si", "express");
assert.equal(picked.rows[0].company, "CO-00001");
assert.deepEqual(picked.adapters, ["express"]);
assert.ok(g.calls.some((call: any) => String(call.method).endsWith(ERP + "choices")));
assert.equal((await createCompany({ connection: "", account_set_key: "0105536082808:00001" })).id, "CO-00009");
await saveNote("CO-00001", "x");
await deleteNote("CO-00001", "n1");
const methods = g.calls.map((call: any) => call.method).filter((m: string) => m !== "projects");
assert.ok(methods.length >= 7);
assert.ok(methods.every((m: string) => m.includes(ERP)), methods.join(", "));
assert.ok(!g.calls.some((call: any) => String(call.method).includes("pearnly_cowork")));
// 会话栏：ERP 只出公司、待处理、知识库与输出文件；Cowork 全部照旧
const all: any = { company: "c", month: "m", pending: "p", notes: "n", folders: "f", tasks: "t", choices: "ch", drafts: "d", outputs: "o" };
const shown = (sections: any) => Object.entries(declaredCards(all, sections)).filter(([, card]) => card).map(([id]) => id);
assert.deepEqual(shown(state.sections), ["company", "pending", "notes", "outputs"]);
assert.deepEqual(shown(["pending", "month", "notes", "folders", "tasks", "choices", "drafts", "create", "rename", "delete", "catalog"]),
  Object.keys(all));
// 待处理：ERP 的单带单据页地址，点开去那里；不进「交给 CC」
const noop = () => {};
const erpItem = { document: "PUR-1", company: "CO-00001", reason_code: "failed", reason_label: "Failed", invoice_no: "PUR-1-NO",
  counterparty: "Thai Steel", amount: "1070.00", date: "2026-10-20", action: "open" as const, url: "/desk/pearnly-purchase/PUR-1" };
const page = renderToStaticMarkup(createElement(PendingItems, { items: [erpItem], error: "", done: noop, changed: noop, company: "CO-00001" }));
assert.match(page, /<button[^>]*data-slot="cc-pending-row"/);
assert.ok(!page.includes('data-slot="cc-hand-to-cc"'));
// Cowork 的「交给 CC」那一类照旧：没有 url 的不是按钮，组下有「交给 CC」
const cowork = { ...erpItem, url: undefined, document: "CWH-1" };
const old = renderToStaticMarkup(createElement(PendingItems, { items: [cowork], error: "", done: noop, changed: noop, company: "C1" }));
assert.match(old, /<div[^>]*data-slot="cc-pending-row"/);
assert.ok(old.includes('data-slot="cc-hand-to-cc"'));
""")

    def test_cowork_declaration_keeps_todays_methods(self):
        self.run_page(r"""
import { refreshProjects, resetProjects, useProjects } from "@/lib/managed-agents/projects";
import { listCompanies, loadDetail, renameCompany } from "@/lib/companies";
const g = globalThis as any;
const ok = (message: any) => ({ ok: true, status: 200, json: async () => ({ message }) });
g.respond = async (method: string) => method === "projects"
  ? ok({ kind: "company", period: true, scope: true, api: "pearnly_cowork.companies.api.",
         sections: ["pending", "month", "notes", "folders", "tasks", "choices", "drafts", "create", "rename", "delete", "catalog"], projects: [] })
  : ok([]);
resetProjects();
await refreshProjects();
assert.equal(useProjects.getState().sections.length, 11);
await listCompanies();
await loadDetail("C1");
await renameCompany("C1", "New");
const methods = g.calls.map((call: any) => String(call.method)).filter((m: string) => m !== "projects");
assert.ok(methods[0].includes("/api/method/pearnly_cowork.companies.api.companies"), methods[0]);
assert.ok(methods[1].includes("/api/method/pearnly_cowork.companies.api.detail"), methods[1]);
assert.ok(methods[2].includes("/api/method/pearnly_cowork.companies.api.rename"), methods[2]);
// 没有公司分组的产品（DMS）：不声明，公司接口不调
g.respond = async (method: string) => ok(method === "projects" ? { kind: null, period: false, scope: false, projects: [] } : null);
resetProjects();
await refreshProjects();
assert.deepEqual([useProjects.getState().api, useProjects.getState().sections], ["", []]);
await assert.rejects(listCompanies(), /companies not declared/);
""")


if __name__ == "__main__":
    unittest.main()
