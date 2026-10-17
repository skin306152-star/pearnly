"""CC 对话页手机上的几处内容交互（2026-10-15 验收 F11e、F11f 乙包，三星 A14 / 安卓 13 / Chrome 106 实测）：

- 主档页知识库：新增、修改都有「取消」「保存」；存好的那一条直接用服务端回的放进列表，不等之后整页重取；失败照 shownError 报；
  同页改名、记住的选择、移除文件夹同样先按服务端的回应显示。
- 打开一段长对话停在最新一条（lib/follow-scroll.ts 的 settle），用户一碰就交还、不拉回。

做法同 tests/test_assistant_ui.py：源码断言 + 用 esbuild 打包交给 node 跑的行为检查（要先 npm ci --prefix frontend/assistant）。
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from test_assistant_ui import ESBUILD, FRONTEND, NODE, ROOT, SOCKET_STUB, block, keys, read, run_ts  # noqa: E402

PAGE_STUBS = {"socket.io-client": SOCKET_STUB,
              "@/lib/profile": 'export const APP_NAME = "CC"; export const useProfile = () => undefined;'}


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
        # 第 6 批：按读者编号（「电脑|连接」）记刚移除的那一行，同一条连接上别的电脑那一行不跟着藏
        self.assertIn("setGone((before) => [...before, idOf(f)]);", folders)
        self.assertIn("const idOf = (f: CompanyFolder) => f.reader_id || f.reader;", folders)

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
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { OpenSettler, SETTLE_MAX_MS, SETTLE_QUIET_MS } from "@/lib/follow-scroll";

const g = globalThis as any;
let clock = 1000;
const listeners: Record<string, Array<(e: any) => void>> = {};
let reserve: number | null = null;  // 组件库的占位：只在有在跑的一轮时挂着
const vp: any = {
  scrollTop: 0, scrollHeight: 6000, clientHeight: 800,
  addEventListener(type: string, fn: any) { (listeners[type] ??= []).push(fn); },
  removeEventListener(type: string, fn: any) { listeners[type] = (listeners[type] ?? []).filter((f) => f !== fn); },
  scrollTo({ top }: any) { this.scrollTop = Math.min(top, this.scrollHeight - this.clientHeight); },
  querySelector() { return reserve === null ? null : { offsetHeight: reserve }; },
};
const fire = (type: string, e: any = {}) => (listeners[type] ?? []).forEach((fn) => fn(e));
let grow: () => void = () => {};
const f = new OpenSettler(vp, {}, () => clock, (_t, onResize) => { grow = onResize; return () => {}; });
const bottom = () => vp.scrollHeight - vp.clientHeight;
const more = (px: number, ms = 100) => {
  clock += ms; vp.scrollHeight += px; vp.scrollTop = Math.min(vp.scrollTop, bottom());  // 变矮时浏览器自己把位置收回
  grow();
};

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

// 组件库在把在跑的这一轮推到顶上（占位有高度）时不插手
reserve = 300; vp.scrollTop = 100; f.settle();
assert.equal(vp.scrollTop, 100);
// 第 11 轮审查：有在跑的一轮就交还——回复超出一屏、占位缩成 0 之后内容再长，也不跟到底
reserve = 0; more(900); assert.equal(vp.scrollTop, 100);
more(900); assert.equal(vp.scrollTop, 100);
// 新对话：贴底时还没有占位，随后这一轮开跑（占位先有高度、再缩成 0），回复接着长：不滚
reserve = null; vp.scrollHeight = 800; vp.scrollTop = 0; f.settle();
reserve = 400; more(0);
reserve = 0; more(1500); more(1500);
assert.equal(vp.scrollTop, 0);
// 打开一段正在回复的对话（占位已在、高度 0）：不贴底，停在组件库给的这一轮开头
vp.scrollTop = 500; f.settle();
more(800); assert.equal(vp.scrollTop, 500);
reserve = null;
f.dispose();
assert.equal(Object.values(listeners).every((l) => l.length === 0), true);
g.done();
""")


if __name__ == "__main__":
    unittest.main()
