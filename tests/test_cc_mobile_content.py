"""CC 对话页手机上的几处内容交互（2026-10-15 验收 F11e、F11f 乙包，三星 A14 / 安卓 13 / Chrome 106 实测）：

- 主档页知识库：新增、修改都有「取消」「保存」；存好的那一条直接用服务端回的放进列表，不等之后整页重取；失败照 shownError 报；
  同页改名、记住的选择、移除文件夹同样先按服务端的回应显示。
- 打开一段长对话停在最新一条（lib/follow-scroll.ts 的 settle），用户一碰就交还、不拉回；打开正在回复的对话停在这一轮开头
  （提问顶在上面），上面的内容后来变高也对回去，之后不跟随（第 11 轮 C19）。

做法同 tests/test_assistant_ui.py：源码断言 + 用 esbuild 打包交给 node 跑的行为检查（要先 npm ci --prefix frontend/assistant）。
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from test_assistant_ui import (  # noqa: E402
    ESBUILD, FAKE_SITE, FRONTEND, MA_SETUP, NODE, ROOT, SOCKET_STUB, block, keys, read, run_ts,
)

PAGE_STUBS = {"socket.io-client": SOCKET_STUB,
              "@/lib/profile": 'export const APP_NAME = "CC"; export const useProfile = () => undefined;'}


# 打开对话对位置的假视口：questions 按消息编号放提问（offset = 在滚动内容里的上沿），state 是会话状态（这一轮的提问、核对过没有）；
# marker 是组件库留的 data-aui-top-anchor-user（只为证明不看它）。open(turn, question, height) = 用这份状态打开一次。
SETTLE_RIG = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { OpenSettler, runningTurn, SETTLE_MAX_MS, SETTLE_QUIET_MS, turnStart } from "@/lib/follow-scroll";

const g = globalThis as any;
let clock = 1000;
const listeners: Record<string, Array<(e: any) => void>> = {};
type Q = { offset: number; height: number };
const questions: Record<string, Q> = {};
let marker: Q | null = null;
let state: { turn: string | null; synced: boolean } = { turn: null, synced: true };
const box = (q: Q) => ({ offsetHeight: q.height, getBoundingClientRect: () => ({ top: 50 + q.offset - vp.scrollTop }) });
const vp: any = {
  scrollTop: 0, scrollHeight: 6000, clientHeight: 800, selected: [] as string[],
  addEventListener(type: string, fn: any) { (listeners[type] ??= []).push(fn); },
  removeEventListener(type: string, fn: any) { listeners[type] = (listeners[type] ?? []).filter((f) => f !== fn); },
  scrollTo({ top }: any) { this.scrollTop = Math.max(0, Math.min(top, this.scrollHeight - this.clientHeight)); },
  getBoundingClientRect() { return { top: 50 }; },
  querySelector(sel: string) {
    this.selected.push(sel);
    if (sel.includes("top-anchor-user")) return marker && box(marker);
    const id = /^\[data-message-id="(.+)"\]$/.exec(sel)?.[1];
    const q = id ? questions[id] : undefined;
    return q ? box(q) : null;
  },
};
const fire = (type: string, e: any = {}) => (listeners[type] ?? []).forEach((fn) => fn(e));
let grow: () => void = () => {};
const f = new OpenSettler(vp, {}, () => state, () => clock, (_t, onResize) => { grow = onResize; return () => {}; });
const bottom = () => vp.scrollHeight - vp.clientHeight;
const more = (px: number, ms = 100) => {
  clock += ms; vp.scrollHeight += px; vp.scrollTop = Math.min(vp.scrollTop, bottom());  // 变矮时浏览器自己把位置收回
  grow();
};
const open = (turn: string | null, question?: Q | null, height = 6000) => {
  clock += 60_000;
  if (turn) { if (question) questions[turn] = question; else delete questions[turn]; }
  state = { turn, synced: true }; vp.scrollHeight = height; vp.scrollTop = 0; f.settle();
};
"""


class NotesSourceTests(unittest.TestCase):
    def test_note_editor_has_cancel_and_save_and_no_save_on_blur(self):
        page = read("components/company-cards.tsx")  # 知识库卡片（会话栏与公司页共用，CC 外壳 2026-10-16）
        editor = page[page.index("export const NoteEditor"):page.index("// ---------------------------------------------------------------- 单据文件夹")]
        self.assertIn('{t("cancel")}', editor)
        self.assertIn('{t("save")}', editor)
        self.assertIn('data-primary=""', editor)
        # 同一栏其他卡片按钮：.cc-card-btn（电脑 24、手机 44 胶囊，第 11 轮按钮排查 L10；手机点按区不小于 44pt 由它保证）
        self.assertIn('<div className="cc-card-actions">', editor)
        self.assertEqual(editor.count('className="cc-card-btn"'), 2)
        self.assertNotIn("onBlur", editor)  # 点「取消」先失焦：离开就保存会把取消变成保存
        self.assertIn("setError(shownError(e));", editor)
        self.assertIn("noteKey(e, touchOnly())", editor)
        section = page[page.index("const NotesSection"):page.index("export function noteKey")]
        self.assertIn("setList(await commitNote(company, text, id));", section)
        self.assertIn("useEffect(() => setList(notes), [notes]);", section)
        self.assertIn("{list.map((note) =>", section)
        self.assertNotIn("await reload()", section)  # 重取失败不能挡住已存好的那一条
        # 让 CC 起草（第 2 期）：只在公司页的空知识库里（keepEmpty），新对话里预填、不自动发送
        self.assertIn("{keepEmpty && !list.length && !adding && (", section)
        self.assertIn("co.draftNotesText, false);", section)
        pending = read("components/pending.tsx")
        self.assertIn("{open && view && settledView(view) && (", pending)
        self.assertIn("{co.pushNow}", pending)
        i18n = read("i18n.ts")
        messages = block(i18n, "const MESSAGES = {")
        for lang in ("en", "zh", "th"):
            self.assertIn("save", keys(block(messages, f"  {lang}: {{")), lang)

    def test_other_in_place_edits_show_the_saved_value_at_once(self):
        page = read("components/company-page.tsx")
        header = page[page.index("const Header: FC"):page.index("// ---------------------------------------------------------------- 输入框")]
        self.assertIn("setTitle(saved);", header)
        # 公司页标题：点名字就地改名（设计文档 3.15）。第 5 轮：按钮里面放滚动的文字，滚动时的遮罩不再把按钮的焦点环裁掉
        self.assertIn('<button type="button" className="cc-page-title-btn" title={t("rename")} onClick={rename}>\n'
                      '              <Marquee className="cc-page-title-text">{title}</Marquee>\n            </button>', header)
        self.assertNotIn('as="button"', header)
        css = read("globals.css")
        button = css.split("  .cc-page-title-btn {", 1)[1].split("}", 1)[0]
        self.assertNotIn("overflow", button)                                    # 按钮自己不裁切
        self.assertIn(".cc-page-title[data-editable] { display: flex; overflow: visible; }", css)
        # 放不下时末尾渐隐（同所有单行截断的文字，用户 10-16「统一渐隐」），不用省略号；块级，列表页的纯文字标题也用它
        self.assertIn(".cc-page-title-text { display: block; min-width: 0; overflow: hidden; white-space: nowrap; }", css)
        # 第 5 轮测试 3：「··· → 重命名」后菜单关上时不把焦点还给「···」，改名框直接能输入。第 5 轮复测焦点仍在 body：
        # 选中那一下就挂上改名框，自动聚焦被菜单的焦点陷阱拉回菜单项。改为菜单关完（onCloseAutoFocus）才挂改名框
        self.assertIn("const renameFromMenu = useRef(false);", header)
        select = header.split("<DropdownMenu.Item\n              className=\"cc-menu-item\"\n              onSelect={() => {", 1)[1].split("}}", 1)[0]
        self.assertIn("if (isMobile) return rename();", select)
        self.assertIn("renameFromMenu.current = true;", select)
        self.assertNotIn("startRename()", select)                              # 菜单开着时不挂改名框
        close = header.split("onCloseAutoFocus={(e) => {", 1)[1].split("}}", 1)[0]
        self.assertIn("if (!renameFromMenu.current) return;", close)
        self.assertIn("e.preventDefault();", close)
        self.assertIn("startRename();", close)
        title = page[page.index("const TitleRename: FC"):]
        self.assertIn("if (el && document.activeElement !== el) el.focus({ preventScroll: true });", title)
        page = read("components/company-cards.tsx")
        choices = page[page.index("const ChoicesSection"):page.index("export const ChoiceRow")]
        self.assertIn("setChoiceShown(rows, c.key, v)", choices)
        self.assertIn("rows.filter((row) => row.key !== c.key)", choices)
        self.assertNotIn("await reload()", choices)
        folders = page[page.index("export const FolderSection"):page.index("// ---------------------------------------------------------------- 账套")]
        self.assertIn("setGone((before) => [...before, reader]);", folders)

    def test_the_company_page_lists_only_conversations_that_were_sent(self):
        # 第 5 轮测试 4 / 审查：公司页「＋」选了文件就建了会话，不发也在服务端留一条空标题的；公司页对话列表不列它
        # （标题空 = 还没发过消息）。列表自己取数，不经侧栏的多设备同步
        page = read("components/company-page.tsx")
        chats = page[page.index("const load = useCallback("):page.index("useEffect(() => void load(), [load]);")]
        self.assertIn("page.sessions.filter((row) => !seen.has(row.id) && !row.archived_at && row.title)", chats)


class OpenAtBottomSourceTests(unittest.TestCase):
    def test_thread_settles_when_a_conversation_is_opened(self):
        thread = read("components/assistant-ui/thread.tsx")
        self.assertIn("s.thread.messages.length > 0 ? s.threadListItem.id : \"\"", thread)
        self.assertIn("if (opened && !sending.current) settler.current?.settle();", thread)
        self.assertIn("if (openedNow.current && !sending.current) created.settle();", thread)
        # 第 11 轮审查：本页刚发出（发送中的气泡还在）时新对话第一条落进来不算打开，不贴底
        self.assertIn("sending.current = usePendingSend() !== null;", thread)
        # 第 11 轮 C19 审查：在不在跑、这一轮的提问、核对过没有，都从会话控制器同一份快照读（不看组件库的标记）；状态变了重新核一次
        self.assertIn("const { turn, synced } = useOpenState();", thread)
        self.assertIn("new OpenSettler(viewport, content, () => openState.current)", thread)
        self.assertIn("settler.current?.recheck();\n  }, [turn, synced]);", thread)
        provider = read("lib/managed-agents/runtime-provider.tsx")
        hook = provider[provider.index("export function useOpenState"):]
        self.assertIn("runningTurn(isBusy(read()), read().messages)", hook)
        self.assertIn("read().isSynced", hook)
        controller = read("lib/managed-agents/session-controller.ts")
        self.assertIn("this.set({ isTailing: false, isSynced: false });", controller)
        self.assertIn("isSynced: this.source !== null", controller)
        scroll = read("lib/follow-scroll.ts")
        self.assertNotIn("querySelector(TURN_ANCHOR)", scroll)


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class BehaviourTests(unittest.TestCase):
    def test_note_keys_save_on_desktop_enter_and_keep_newlines_on_phones(self):
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { noteKey } from "@/components/company-page";

const g = globalThis as any;
const key = (key: string, extra: any = {}) => ({ key, keyCode: key === "Enter" ? 13 : 0, nativeEvent: { isComposing: false }, ...extra });
// 电脑：回车保存、Shift+回车换行（原有快捷键）
assert.equal(noteKey(key("Enter"), false), "save");
assert.equal(noteKey(key("Enter", { shiftKey: true }), false), null);
// 手机：回车换行，保存靠按钮
assert.equal(noteKey(key("Enter"), true), null);
// Ctrl / ⌘+回车两边都保存；Esc 取消
assert.equal(noteKey(key("Enter", { ctrlKey: true }), true), "save");
assert.equal(noteKey(key("Enter", { metaKey: true }), false), "save");
assert.equal(noteKey(key("Escape"), true), "cancel");
// 选字中的回车不算
assert.equal(noteKey(key("Enter", { nativeEvent: { isComposing: true } }), false), null);
assert.equal(noteKey(key("Enter", { keyCode: 229 }), false), null);
assert.equal(noteKey(key("a"), false), null);
g.done();
""", stubs=PAGE_STUBS)

    def test_note_editor_renders_cancel_and_save(self):
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.browser";
import { NoteEditor } from "@/components/company-page";

const g = globalThis as any;
const html = (initial: string) => renderToStaticMarkup(createElement(NoteEditor, { initial, onSave: async () => {}, onCancel: () => {} }));
const empty = html("");
assert.match(empty, /<textarea[^>]*maxLength="500"/);
assert.match(empty, /<button type="button" class="cc-card-btn">取消<\/button>/);
// 没有字（或没改）时「保存」不可点
assert.match(empty, /<button type="button" class="cc-card-btn" data-primary="" disabled="">保存<\/button>/);
assert.match(html("test"), /data-primary="" disabled="">保存</);
g.done();
""", stubs=PAGE_STUBS, lang="zh")

    def test_saved_note_is_shown_from_the_reply_and_failures_say_why(self):
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { commitNote, putNote, removeNote, setChoiceShown } from "@/components/company-page";
import { shownError } from "@/lib/frappe";

const g = globalThis as any;
const ok = (message: any) => ({ ok: true, status: 200, json: async () => ({ message }) });
const refused = (text: string) => ({ ok: false, status: 417, json: async () => ({ _server_messages: JSON.stringify([JSON.stringify({ message: text })]) }) });
const before = [{ id: "N1", text: "old" }];
let reply: any = ok({ id: "N2", text: "test", origin: "person", updated_by: "a@b", updated_at: "2026-10-15 15:00:12" });
g.respond = async (method: string) => (String(method).includes("note_") ? reply : ok(null));

// 新增：服务端回的那一条接在最后；交出去的是规整过的字与公司
let shown = (await commitNote("C1", "test"))(before);
assert.deepEqual(shown.map((n) => n.id), ["N1", "N2"]);
assert.equal(shown[1]!.text, "test");
const sent = g.calls.at(-1);
assert.match(sent.method, /pearnly_cowork\.companies\.api\.note_save$/);
assert.deepEqual(sent.body, { company: "C1", text: "test" });
// 修改：替换原位，不多出一条
reply = ok({ id: "N1", text: "new" });
shown = (await commitNote("C1", "new", "N1"))(shown);
assert.deepEqual(shown.map((n) => `${n.id}:${n.text}`), ["N1:new", "N2:test"]);
assert.deepEqual(g.calls.at(-1).body, { company: "C1", text: "new", note: "N1" });
// 删除
reply = ok({ id: "N2", text: "test" });
assert.deepEqual((await removeNote("C1", "N2"))(shown).map((n) => n.id), ["N1"]);
// 失败：本站拒收照原文；数据库冲突等只说通用的一句；列表不动
reply = refused("บันทึกเต็มแล้ว");
await assert.rejects(commitNote("C1", "x"), (e: unknown) => shownError(e) === "บันทึกเต็มแล้ว");
reply = { ok: false, status: 500, json: async () => ({ exception: "pymysql.err.OperationalError: (1213, 'Deadlock found')" }) };
await assert.rejects(commitNote("C1", "x"), (e: unknown) => shownError(e) === "出了点问题，请重试。");
assert.deepEqual(before, [{ id: "N1", text: "old" }]);
assert.deepEqual(putNote([], { id: "N9", text: "a" }), [{ id: "N9", text: "a" }]);
// 记住的选择：下拉框换成刚选的值与显示名
const choices: any[] = [{ key: "k1", source: "express", kind: "payment", subject_label: "A", value: "cash", value_label: "现购" },
  { key: "k2", source: "express", kind: "vat", subject_label: "B", value: "claim" }];
const changed = setChoiceShown(choices, "k1", "credit");
assert.equal(changed[0].value, "credit");
assert.equal(changed[0].value_label, "赊购");
assert.equal(changed[1], choices[1]);
g.done();
""", stubs=PAGE_STUBS, lang="zh")

    def test_opening_a_long_conversation_settles_at_the_bottom_without_undoing_scroll_up(self):
        # 已结束的对话停在最新一条；正在回复的停在这一轮开头（第 11 轮 C19，用户 10-17 截图照 Claude 手机端）
        run_ts(SETTLE_RIG + r"""
// 打开：直接到最底下
f.settle();
assert.equal(vp.scrollTop, bottom());
// 之后内容还在变高（content-visibility 量出真实高度、正文排版、文件卡片到了）：一直贴着最底下
more(1300); assert.equal(vp.scrollTop, bottom());
more(400, 1200); assert.equal(vp.scrollTop, bottom());
more(-150); assert.equal(vp.scrollTop, bottom());
// 静下来 SETTLE_QUIET_MS 之后不再管（内容再变也不动）
clock += SETTLE_QUIET_MS;
let top = vp.scrollTop; more(500); assert.equal(vp.scrollTop, top);

// 再打开一次，这回用户马上往上翻：立刻交还，之后内容再长也不拉回
f.settle();
assert.equal(vp.scrollTop, bottom());
fire("touchstart", { touches: [{ clientY: 100 }] });
fire("touchmove", { touches: [{ clientY: 180 }] });
vp.scrollTop = 2000; fire("scroll");
more(800); assert.equal(vp.scrollTop, 2000);
fire("touchend");
more(800); assert.equal(vp.scrollTop, 2000);

// 用户只是碰了一下（滚轮往下、点了一下）：也交还，不再替他滚
f.settle();
fire("wheel", { deltaY: 30 });
top = vp.scrollTop; more(300); assert.equal(vp.scrollTop, top);

// 内容一直在长也有上限
f.settle();
for (let i = 0; i < Math.ceil(SETTLE_MAX_MS / 1000); i++) more(50, 1000);
top = vp.scrollTop; more(500, 100); assert.equal(vp.scrollTop, top);
f.dispose();
assert.equal(Object.values(listeners).every((l) => l.length === 0), true);
g.done();
""")

    def test_opening_a_chat_mid_reply_stops_at_the_start_of_this_turn(self):
        # 第 11 轮 C19：打开一段正在回复的对话——停在这一轮开头（提问顶在上面），不停在最底下、也不停在上一轮回复中间
        run_ts(SETTLE_RIG + r"""
open("q14", { offset: 4200, height: 80 });
assert.equal(vp.scrollTop, 4200);
// 上面的内容后来才变高（content-visibility 量出上一轮回复的真实高度、文件列表取回后多出结果文件卡）：提问被往下推 967，
// 对回去，提问仍顶在上面（A14 实测正是差了这 967：画面停在上一轮回复中间，提问在屏幕下面）
questions.q14.offset += 967; more(967); assert.equal(vp.scrollTop, 4200 + 967);
// 回复接着长（提问的位置不变）：不跟随，停在这一轮开头
for (let i = 0; i < 5; i++) { more(900); assert.equal(vp.scrollTop, 5167); }
// 用户一碰就交还：之后上面再变高也不替他滚
fire("touchstart", { touches: [{ clientY: 300 }] });
questions.q14.offset += 200; more(200); assert.equal(vp.scrollTop, 5167);

// 提问很高（超过 10em）：同组件库，只把最后 6em 留在上面
open("q9", { offset: 1000, height: 400 });
assert.equal(vp.scrollTop, 1000 + 400 - 6 * 16);
assert.equal(turnStart(1000, 400, 16), 1304);
assert.equal(turnStart(1000, 150, 16), 1000);

// 打开时这一轮的提问还没画出来：先到最底下，画出来以后（内容撑高了）对到这一轮开头，回复再长也不跟
open("q20", null, 3000);
assert.equal(vp.scrollTop, 2200);
questions.q20 = { offset: 2500, height: 60 }; more(600); assert.equal(vp.scrollTop, 2500);
more(1500); more(1500); assert.equal(vp.scrollTop, 2500);

// 只有一轮（新对话第一句，C18 / 审查 2）：组件库不给第 0 条提问标 data-aui-top-anchor-user，这里按会话状态认这一轮；
// 回复连续变长（间隔不到 SETTLE_QUIET_MS）也停在 12，不跟到底
open("q0", { offset: 12, height: 60 }, 800);
assert.equal(vp.scrollTop, 0);  // 回复还不到一屏：能滚的范围只到 0
more(400); assert.equal(vp.scrollTop, 12);
for (let i = 0; i < 12; i++) { more(700, 400); assert.equal(vp.scrollTop, 12); }
assert.equal(vp.selected.some((sel: string) => sel.includes("top-anchor")), false);  // 不看组件库的标记
f.dispose();
g.done();
""")

    def test_a_finished_chat_opens_at_the_bottom_even_with_a_stale_turn_marker(self):
        # 审查 1：组件库的 data-aui-top-anchor-user 跑完也留着（换回一段已结束的对话时还在）；在不在跑只按会话状态
        run_ts(SETTLE_RIG + r"""
marker = { offset: 4200, height: 80 };  // 留着的标记
questions.q14 = { offset: 4200, height: 80 };
open(null, undefined, 6000);
assert.equal(vp.scrollTop, bottom());
more(1268); assert.equal(vp.scrollTop, bottom());
more(300, 1000); assert.equal(vp.scrollTop, bottom());

// 打开时是离开前留下的旧状态（离开期间这一轮在别处结束了，这里还显示在跑）：先停在这一轮开头；
// 核对完（synced）状态变成已结束，用户没碰过：重新对一次，停到最底下——晚于 SETTLE_QUIET_MS 才核对完也一样
state = { turn: "q14", synced: false }; vp.scrollHeight = 6000; vp.scrollTop = 0; f.settle();
assert.equal(vp.scrollTop, 4200);
clock += 3000;  // 慢网：核对比安静窗口还晚
vp.scrollHeight += 1268;  // 核对带回这一轮剩下的回复
state = { turn: null, synced: true }; f.recheck();
assert.equal(vp.scrollTop, bottom());
// 之后内容还在长（结果文件卡到了）：接着贴底
more(900); assert.equal(vp.scrollTop, bottom());

// 同样是旧状态，但核对之前用户已经碰过（往上翻了）：核对完也不动
state = { turn: "q14", synced: false }; vp.scrollHeight = 6000; vp.scrollTop = 0; f.settle();
assert.equal(vp.scrollTop, 4200);
fire("wheel", { deltaY: -200 }); vp.scrollTop = 3900;
clock += 3000; state = { turn: null, synced: true }; f.recheck();
assert.equal(vp.scrollTop, 3900);

// 旧状态核对完仍在跑（离开期间还没结束）：仍停在这一轮开头
state = { turn: "q14", synced: false }; vp.scrollHeight = 6000; vp.scrollTop = 0; f.settle();
clock += 3000; state = { turn: "q14", synced: true }; f.recheck();
assert.equal(vp.scrollTop, 4200);

// 按核对过的状态停在这一轮开头以后，这一轮真的结束了（还在对位置的窗口里，用户没碰）：不改去最底下（不跟随）
open("q14", { offset: 4200, height: 80 });
assert.equal(vp.scrollTop, 4200);
more(400);
state = { turn: null, synced: true }; f.recheck();
assert.equal(vp.scrollTop, 4200);
more(300); assert.equal(vp.scrollTop, 4200);

// 打开已结束的对话以后别处发来新的一轮（还在对位置）：停到新这一轮开头，与发出后的落点一致
open(null, undefined, 6000);
assert.equal(vp.scrollTop, 5200);
questions.q15 = { offset: 6050, height: 60 };
state = { turn: "q15", synced: true }; vp.scrollHeight += 900; f.recheck();
assert.equal(vp.scrollTop, 6050);

// 在跑的这一轮的提问：在跑（含等「允许」）、最后一条是回复、前一条是提问；第 0 条也算
const m = (id: string, role: string) => ({ id, role });
assert.equal(runningTurn(true, [m("u0", "user"), m("a0", "assistant")]), "u0");
assert.equal(runningTurn(true, [m("u0", "user"), m("a0", "assistant"), m("u1", "user"), m("a1", "assistant")]), "u1");
assert.equal(runningTurn(false, [m("u0", "user"), m("a0", "assistant")]), null);
assert.equal(runningTurn(true, [m("u0", "user"), m("a0", "assistant"), m("u1", "user")]), "u1");  // 回复还没出字
assert.equal(runningTurn(true, [m("a0", "assistant")]), null);
assert.equal(runningTurn(true, []), null);
f.dispose();
g.done();
""")

    def test_sending_right_after_opening_keeps_the_new_question_at_the_top(self):
        # 审查第 3 条：打开已结束的对话后马上发出（文件列表还没回），上一轮晚到的结果文件卡不把新这一轮的提问推下去；
        # 在输入框里点、打字、发送不算碰，↓ 与消息区里的碰照样交还
        run_ts(SETTLE_RIG + r"""
const inComposer = { closest: (sel: string) => (sel === ".aui-composer-root" ? {} : null) };
const outside = { closest: () => null };
open(null, undefined, 6000);
assert.equal(vp.scrollTop, 5200);
fire("pointerdown", { target: inComposer }); fire("keydown", { target: inComposer });
assert.equal(f.settling, true);
// 发出：新这一轮（提问在 6050），回复还没出字
questions.q15 = { offset: 6050, height: 60 };
vp.scrollHeight += 900; state = { turn: "q15", synced: true }; f.recheck();
assert.equal(vp.scrollTop, 6050);
// 上一轮的结果文件卡这时才到（在提问上面，撑高 934）：对回去，提问仍顶在上面
questions.q15.offset += 934; more(934); assert.equal(vp.scrollTop, 6984);
// 回复接着长：不跟
more(700); more(700); assert.equal(vp.scrollTop, 6984);
// 在消息区里一碰（或点 ↓：在输入框外）就交还
fire("pointerdown", { target: outside });
questions.q15.offset += 300; more(300); assert.equal(vp.scrollTop, 6984);
// 点 ↓ 同样交还
open(null, undefined, 6000);
fire("pointerdown", { target: outside });
assert.equal(f.settling, false);
f.dispose();
g.done();
""")

    def test_the_session_state_says_when_it_was_checked_since_coming_back(self):
        # 会话控制器的 isSynced：接上后整段重放成功才置真，离开（detach）置否；置真时在不在跑已是核对后的
        run_ts(r"""
import "cc-test-setup";
import "cc-ma-setup";
import assert from "node:assert/strict";
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { isBusy } from "@/lib/managed-agents/reducer";
import { runningTurn } from "@/lib/follow-scroll";
const g = globalThis as any;
""" + FAKE_SITE + r"""
const S = "sesn_open1";
site.log.push(ev("u1", "user.message", 1, { content: text("long question") }), ev("r1", "session.status_running", 2),
  ev("m1", "agent.message", 3, { content: text("part 1") }));
site.engine = "running";
const c = getSessionController(S);
const turnOf = () => { const s = c.getSnapshot(); return runningTurn(isBusy(s), s.messages); };
assert.equal(c.getSnapshot().isSynced, false);
c.attach();
await g.sleep(30);
assert.equal(c.getSnapshot().isSynced, true);
const question = turnOf();
assert.ok(question);
assert.equal(c.getSnapshot().messages.at(-2).id, question);
// 离开这段对话：旧状态留着（还显示在跑），但标成没核对过
c.detach();
assert.equal(c.getSnapshot().isSynced, false);
assert.equal(turnOf(), question);
// 离开期间这一轮在别处结束了
site.log.push(ev("m2", "agent.message", 4, { content: text("part 2") }), ev("i1", "session.status_idle", 5, { stop_reason: { type: "end_turn" } }));
site.engine = "idle";
const seen: Array<[boolean, string | null]> = [];
const off = c.subscribe(() => seen.push([c.getSnapshot().isSynced, turnOf()]));
c.attach();
await g.sleep(30);
off();
assert.equal(c.getSnapshot().isSynced, true);
assert.equal(turnOf(), null);
// 每次通知里，「核对过」从没和旧的「在跑」一起出现
assert.equal(seen.some(([synced, turn]) => synced && turn !== null), false);
c.dispose();
g.done();
""", stubs={"cc-ma-setup": MA_SETUP, "socket.io-client": SOCKET_STUB})

if __name__ == "__main__":
    unittest.main()
