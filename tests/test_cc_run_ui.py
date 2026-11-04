"""对话里的上传状态卡片与输入框的「停止」交给调用方（Code 版 CC 施工块 2，docs/work/2026-11-04-cc-code-style.md 第 5 节、11-04 用户更正）。

- 上传状态卡片（components/run/upload-card.tsx、upload-state.ts）：上传中 N%、排队中、识别中 n/m 页、已识别 N 张（· N 张不在某月）、失败；
  样子是附件卡片 + 一行步骤行的字，不新造；同一份再传就是又一张卡片，不加「已传过」之类的提示。
- 执行中、结束、出错、停止复用直写现有的那套；代录经 Composer 的 `stop` 用直写同一个 ■，不另造停止键。
- 给代录对话化（块 3）的三个可选接口，不传时现有行为不变：Composer 的 `stop`、ThreadComponents 的 `Tail`（对话最后，新对话也画）、
  ThreadMessages 的 `before`（每条消息前先画调用方的内容，按时间插上传卡片）。

用假数据渲染（renderToStaticMarkup），要先装好前端依赖（npm ci --prefix frontend/assistant）且有 node。
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from test_assistant_ui import ESBUILD, NODE, run_ts  # noqa: E402
from test_cc_operator import STUBS  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend/assistant/src"

RENDER = r"""
import "cc-test-setup";
import "cc-dom-setup";
import assert from "node:assert/strict";
import { renderToStaticMarkup } from "react-dom/server.browser";
import { createElement as h } from "react";
import { UploadCard } from "@/components/run/upload-card";
import { uploadBusy, uploadState, uploadText } from "@/components/run/upload-state";
import type { TaskUpload, UploadState } from "@/components/run/types";
import { run, steps, t } from "@/i18n";

const g = globalThis as any;
const card = (state: UploadState, extra: Record<string, unknown> = {}) =>
  renderToStaticMarkup(h(UploadCard, { name: "BJC-10.pdf", size: 2_400_000, pages: 12, state, ...extra }));
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const dots = (html: string) => (html.match(/class="typing-dots/g) ?? []).length;

// 服务端一行 → 状态（B2）
const row = (patch: Partial<TaskUpload>): TaskUpload => ({
  original: "o1", filename: "BJC-10.pdf", size: 2_400_000, job: "j1", uploaded_at: "2026-11-04T03:00:00Z",
  status: "queued", pages_done: 0, page_count: 0, documents: [], error_code: null, ...patch,
});
const reason = (code: string) => (code === "unreadable" ? "Unreadable" : "");
assert.deepEqual(uploadState(row({}), reason), { kind: "queued" });
assert.deepEqual(uploadState(row({ status: "reading", pages_done: 3, page_count: 12 }), reason), { kind: "reading", pagesDone: 3, pageCount: 12 });
assert.deepEqual(uploadState(row({ status: "failed", error_code: "unreadable" }), reason), { kind: "failed", stage: "read", reason: "Unreadable" });
const docs = [
  { name: "D1", date: "2026-10-03", in_period: true, in_sources: true },
  { name: "D2", date: "2026-09-28", in_period: false, in_sources: false },
  { name: "D3", date: "2026-09-30", in_period: false, in_sources: false },
];
assert.deepEqual(uploadState(row({ status: "done", documents: docs }), reason, "10"), { kind: "done", documents: 3, outside: 2, month: "10" });
assert.deepEqual(uploadState(row({ status: "done", documents: docs }), reason), { kind: "done", documents: 3, outside: 2 });

// 每种状态那一行字与在动的小点（在路上的才有）
const cases: Array<[UploadState, string, boolean]> = [
  [{ kind: "uploading", percent: 41.6 }, `${t("uploading")} 42%`, true],
  [{ kind: "uploading", percent: 140 }, `${t("uploading")} 100%`, true],
  [{ kind: "queued" }, run.queued, true],
  [{ kind: "reading", pagesDone: 3, pageCount: 12 }, run.readingPages(3, 12), true],
  [{ kind: "reading", pagesDone: 0, pageCount: 0 }, run.reading, true],
  [{ kind: "done", documents: 11, outside: 0 }, run.read(11), false],
  [{ kind: "done", documents: 11, outside: 2, month: "10" }, `${run.read(11)} · ${run.outside(2, "10")}`, false],
  [{ kind: "failed", stage: "read", reason: "Unreadable" }, "Unreadable", false],
];
for (const [state, line, busy] of cases) {
  assert.equal(uploadText(state), line);
  assert.equal(uploadBusy(state), busy);
  const html = card(state);
  assert.match(html, new RegExp(`^<div data-slot="cc-upload-card" data-state="${state.kind}"`));
  // 卡片就是附件卡片（同你发的消息里的那张），不新造
  assert.match(html, /class="cc-attach-card"/);
  assert.ok(text(html).includes(line), `${state.kind}: ${text(html)}`);
  assert.equal(dots(html), busy ? 1 : 0, state.kind);
  // 不加「已传过」之类的提示，也没有说明性的字：卡片里只有文件名、类型说明、状态那一行
  assert.doesNotMatch(html, /cc-alert|cc-notice/);
}

// 失败：同步骤行尾的危险色「失败」；上传失败给了重试才有「重试」（卡片按钮），识别失败不另做重试
const upFail: UploadState = { kind: "failed", stage: "upload", reason: "" };
let calls = 0;
const failed = card(upFail, { onRetry: () => calls++ });
assert.match(failed, new RegExp(`<span class="cc-step-failed">${steps.failed}</span>`));
assert.match(failed, new RegExp(`<button type="button" class="cc-card-btn">${t("retry")}</button>`));
assert.equal((failed.match(new RegExp(steps.failed, "g")) ?? []).length, 1);   // 没有原因时不重复写「失败」
assert.doesNotMatch(card(upFail), /cc-card-btn/);                                 // 没给重试
assert.doesNotMatch(card({ kind: "failed", stage: "read", reason: "Unreadable" }, { onRetry: () => calls++ }), /cc-card-btn/);

// 同一份传两次就是两张卡片，各自的状态，互不影响
const twice = renderToStaticMarkup(h("div", null,
  h(UploadCard, { name: "BJC-10.pdf", state: { kind: "done", documents: 11, outside: 0 } }),
  h(UploadCard, { name: "BJC-10.pdf", state: { kind: "reading", pagesDone: 2, pageCount: 12 } })));
assert.equal((twice.match(/data-slot="cc-upload-card"/g) ?? []).length, 2);
assert.match(twice, /data-state="done"[\s\S]*data-state="reading"/);

// 能预览时整张卡片是按钮（同附件卡片）
assert.match(card({ kind: "queued" }, { onOpen: () => {} }), /<button type="button" class="cc-attach-card"/);
g.done();
"""


MESSAGES = r"""
import "cc-test-setup";
import "cc-dom-setup";
import assert from "node:assert/strict";
import { renderToStaticMarkup } from "react-dom/server.browser";
import { createElement as h } from "react";
import { AssistantRuntimeProvider, useExternalStoreRuntime } from "@assistant-ui/react";
import { ThreadMessages } from "@/components/assistant-ui/thread";

const g = globalThis as any;
const at = new Date(Date.UTC(2026, 10, 4, 3, 0, 0));
const messages = [
  { id: "u1", role: "user", content: [{ type: "text", text: "first" }], createdAt: at },
  { id: "u2", role: "user", content: [{ type: "text", text: "second" }], createdAt: new Date(at.getTime() + 60_000) },
];
function App(props: any) {
  const runtime = useExternalStoreRuntime({ messages, isRunning: false, convertMessage: (m: any) => m, onNew: async () => {} } as any);
  return h(AssistantRuntimeProvider, { runtime }, h(ThreadMessages, props));
}
// 不给 before：只有消息，同以前
const plain = renderToStaticMarkup(h(App, {}));
assert.doesNotMatch(plain, /data-before/);
assert.equal((plain.match(/data-slot="aui_user-message-root"/g) ?? []).length, 2);
// 给了：每条消息前先画调用方的内容，拿到的是这条的编号与时间
const seen: string[] = [];
const html = renderToStaticMarkup(h(App, {
  before: (m: { id: string; createdAt?: Date }) => {
    seen.push(`${m.id}@${m.createdAt?.toISOString()}`);
    return m.id === "u2" ? h("i", { "data-before": m.id }) : null;
  },
}));
assert.deepEqual(seen, ["u1@2026-11-04T03:00:00.000Z", "u2@2026-11-04T03:01:00.000Z"]);
assert.match(html, /data-message-id="u1"[\s\S]*<i data-before="u2"><\/i><div data-slot="aui_user-message-root"[^>]*data-message-id="u2"/);
assert.equal((html.match(/data-before/g) ?? []).length, 1);
g.done();
"""


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class UploadCardTests(unittest.TestCase):
    def test_states_render_with_existing_parts(self):
        for lang in ("zh", "th", "en"):
            run_ts(RENDER, stubs=STUBS, lang=lang)

    def test_thread_messages_before_each_message(self):
        run_ts(MESSAGES, stubs=STUBS, lang="zh")


class ReuseChatStandard(unittest.TestCase):
    def test_composer_stop_reuses_the_chat_stop_key(self):
        thread = (SRC / "components/assistant-ui/thread.tsx").read_text(encoding="utf-8")
        # 调用方给 stop 时：按它的在跑状态换 ■，停止与 Esc 都交给它；不给时照旧按这一轮、走 stopRun
        self.assertIn("stop?: ComposerStop }> = ({ placeholder: fixed, draft = true, chin, stop })", thread)
        self.assertIn("const running = stop ? stop.running : threadRunning;", thread)
        self.assertIn("if (stop) stop.onStop();\n    else void stopRun(thread);", thread)
        self.assertIn("stop.running ? <StopButton onStop={stop.onStop} /> : send", thread)
        self.assertIn("onClick={() => (onStop ? onStop() : void stopRun(thread))}", thread)
        # 只有一个停止键（不另造一套）
        self.assertEqual(thread.count("data-stop=\"\""), 1)

    def test_tail_draws_after_messages_and_on_new_chat(self):
        thread = (SRC / "components/assistant-ui/thread.tsx").read_text(encoding="utf-8")
        self.assertIn("  Tail?: ComponentType | undefined;", thread)
        self.assertIn("const { Welcome, Loading, Tail } = useContext(ThreadComponentsContext);", thread)
        # 在消息组里、发送中的气泡之后；消息组新对话时也在（不在 isEmpty 的分支里）
        self.assertIn("<ThreadPrimitive.Messages>\n              {() => <ThreadMessage />}\n            </ThreadPrimitive.Messages>\n"
                      "            <PendingSendBubble />\n            {Tail && <Tail />}\n          </div>", thread)

    def test_no_second_run_ui(self):
        # 用户 11-04 更正：任务清单、结束框、侧栏「等你处理」新标记、另一套出错显示都不做
        run_dir = SRC / "components/run"
        self.assertEqual(sorted(p.name for p in run_dir.iterdir()), ["types.ts", "upload-card.tsx", "upload-state.ts"])
        types = (run_dir / "types.ts").read_text(encoding="utf-8")
        for gone in ("RunPlan", "NextStep", "RunMark", "RunEnd", "PlanItem"):
            self.assertNotIn(f"export type {gone}", types)
        activity = (SRC / "lib/managed-agents/activity.ts").read_text(encoding="utf-8")
        self.assertIn('export type Activity = "running" | "unread" | "notice";', activity)


if __name__ == "__main__":
    unittest.main()
