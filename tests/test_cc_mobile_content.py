"""CC 对话页手机上的几处内容交互（2026-10-15 验收 F11e、F11f 乙包，三星 A14 / 安卓 13 / Chrome 106 实测）：

- 主档页知识库：新增、修改都有「取消」「保存」；存好的那一条直接用服务端回的放进列表，不等之后整页重取；失败照 shownError 报；
  同页改名、记住的选择、移除文件夹同样先按服务端的回应显示。
- 打开一段长对话停在最新一条（lib/follow-scroll.ts 的 settle），不破坏「往上翻不拉回」。
- 附件可选类型同服务端 contracts.UPLOAD_EXTENSIONS，不收视频。

做法同 tests/test_assistant_ui.py：源码断言 + 用 esbuild 打包交给 node 跑的行为检查（要先 npm ci --prefix frontend/assistant）。
"""
import ast
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from test_assistant_ui import ESBUILD, FRONTEND, NODE, ROOT, SOCKET_STUB, block, keys, read, run_ts  # noqa: E402

# 组件库判断「收不收这个文件」的那一段（不在公开出口里，按文件取）
ACCEPT_CHECK = FRONTEND / "node_modules/@assistant-ui/core/dist/adapters/attachment.js"
PAGE_STUBS = {"socket.io-client": SOCKET_STUB,
              "@/lib/profile": 'export const APP_NAME = "CC"; export const useProfile = () => undefined;'}


def server_upload_extensions():
    source = (ROOT / "apps/pearnly_core/pearnly_core/assistant/contracts.py").read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "UPLOAD_EXTENSIONS" for t in node.targets):
            return set(ast.literal_eval(node.value.args[0]))
    raise AssertionError("UPLOAD_EXTENSIONS not found")


class NotesSourceTests(unittest.TestCase):
    def test_note_editor_has_cancel_and_save_and_no_save_on_blur(self):
        page = read("components/company-page.tsx")
        editor = page[page.index("export const NoteEditor"):page.index("// ---------------------------------------------------------------- 单据文件夹")]
        self.assertIn('{t("cancel")}', editor)
        self.assertIn('{t("save")}', editor)
        self.assertIn('data-primary=""', editor)
        self.assertIn("cc-tap", editor)  # 手机点按区不小于 44pt（ccs）
        self.assertNotIn("onBlur", editor)  # 点「取消」先失焦：离开就保存会把取消变成保存
        self.assertIn("setError(shownError(e));", editor)
        self.assertIn("noteKey(e, touchOnly())", editor)
        section = page[page.index("const NotesSection"):page.index("export function noteKey")]
        self.assertIn("setList(await commitNote(company, text, id));", section)
        self.assertIn("useEffect(() => setList(notes), [notes]);", section)
        self.assertIn("{list.map((note) =>", section)
        self.assertNotIn("await reload()", section)  # 重取失败不能挡住已存好的那一条
        i18n = read("i18n.ts")
        messages = block(i18n, "const MESSAGES = {")
        for lang in ("en", "zh", "th"):
            self.assertIn("save", keys(block(messages, f"  {lang}: {{")), lang)

    def test_other_in_place_edits_show_the_saved_value_at_once(self):
        page = read("components/company-page.tsx")
        header = page[page.index("const Header: FC"):page.index("// ---------------------------------------------------------------- 输入框")]
        self.assertIn("setTitle(saved);", header)
        self.assertIn("{title}</h1>", header)
        choices = page[page.index("const ChoicesSection"):page.index("export const ChoiceRow")]
        self.assertIn("setChoiceShown(rows, c.key, v)", choices)
        self.assertIn("rows.filter((row) => row.key !== c.key)", choices)
        self.assertNotIn("await reload()", choices)
        folders = page[page.index("export const FolderSection"):page.index("// ---------------------------------------------------------------- 账套")]
        self.assertIn("setGone((before) => [...before, reader]);", folders)


class OpenAtBottomSourceTests(unittest.TestCase):
    def test_thread_settles_when_a_conversation_is_opened(self):
        thread = read("components/assistant-ui/thread.tsx")
        self.assertIn("s.thread.messages.length > 0 ? s.threadListItem.id : \"\"", thread)
        self.assertIn("if (opened) follower.current?.settle();", thread)
        self.assertIn("if (openedNow.current) created.settle();", thread)


class AttachmentTypesTests(unittest.TestCase):
    def test_accept_lists_what_the_server_takes_and_no_video(self):
        attachments = read("lib/managed-agents/attachments.ts")
        listed = attachments.split("export const UPLOAD_EXTENSIONS = [", 1)[1].split("]", 1)[0]
        self.assertEqual({part.strip().strip('"') for part in listed.split(",") if part.strip()}, server_upload_extensions())
        accept = attachments.split("export const ACCEPT", 1)[1].split("\n", 1)[0]
        self.assertNotIn("image/*", attachments.split("export const ACCEPT", 1)[1])
        self.assertNotIn("video", accept)


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
assert.match(empty, /<button type="button" class="cc-btn cc-tap">取消<\/button>/);
// 没有字（或没改）时「保存」不可点
assert.match(empty, /<button type="button" class="cc-btn cc-tap" data-primary="" disabled="">保存<\/button>/);
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
import { SETTLE_MAX_MS, SETTLE_QUIET_MS, StreamFollower } from "@/lib/follow-scroll";

const g = globalThis as any;
let clock = 1000;
const listeners: Record<string, Array<(e: any) => void>> = {};
let reserve = 0;
const vp: any = {
  scrollTop: 0, scrollHeight: 6000, clientHeight: 800,
  addEventListener(type: string, fn: any) { (listeners[type] ??= []).push(fn); },
  removeEventListener(type: string, fn: any) { listeners[type] = (listeners[type] ?? []).filter((f) => f !== fn); },
  scrollTo({ top }: any) { this.scrollTop = Math.min(top, this.scrollHeight - this.clientHeight); },
  querySelector() { return { offsetHeight: reserve }; },
};
const fire = (type: string, e: any = {}) => (listeners[type] ?? []).forEach((fn) => fn(e));
let grow: () => void = () => {};
const live = false;  // 打开的是一段已经结束的对话
const f = new StreamFollower(vp, {}, () => live, () => clock, (_t, onResize) => { grow = onResize; return () => {}; });
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
// 静下来 SETTLE_QUIET_MS 之后不再管（对话没在跑，内容再变也不动）
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
reserve = 0;
f.dispose();
assert.equal(Object.values(listeners).every((l) => l.length === 0), true);
g.done();
""")

    def test_assistant_ui_turns_away_videos_with_the_new_accept(self):
        run_ts(r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { fileMatchesAccept } from "__ACCEPT_CHECK__";
import { ACCEPT } from "@/lib/managed-agents/attachments";

const g = globalThis as any;
const ok = (name: string, type: string) => fileMatchesAccept({ name, type }, ACCEPT);
// 安卓录像、别的视频、服务端不收的图片：在交给 add（建会话、上传）之前就挡掉
assert.equal(ok("VID_20261015_150012.mp4", "video/mp4"), false);
assert.equal(ok("clip.mov", "video/quicktime"), false);
assert.equal(ok("scan.bmp", "image/bmp"), false);
// 拍照、相册、HEIC、单据与表格照收
assert.equal(ok("1697356812345.jpg", "image/jpeg"), true);
assert.equal(ok("IMG_0001.HEIC", "image/heic"), true);
assert.equal(ok("image.png", "image/png"), true);
assert.equal(ok("invoice.pdf", "application/pdf"), true);
assert.equal(ok("ledger.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"), true);
assert.equal(ok("notes.md", ""), true);
g.done();
""".replace("__ACCEPT_CHECK__", ACCEPT_CHECK.as_posix()))


if __name__ == "__main__":
    unittest.main()
