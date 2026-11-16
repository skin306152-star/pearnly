"""允许卡问句下面那一行事实（``note``：这一步会动到哪家公司、哪本账套）从服务端一路带到卡片上（总设计
``docs/work/2026-11-14-cc-guided-master-design.md`` 第 14 节；服务端那一半在工具声明的 ``note`` 与 ``tools.approval_view``）。

断言的是应有状态：
- 服务端回了 ``note``：执行接口的返回带上它，卡片上的门记下它，卡片在问句下面画出那一行；
- 服务端没回（没声明、没有事实）：门上没有 ``note``，那一行不出；
- 确认请求没送到、卡片照原样放回时那一行还在；
- 重开对话：那一步重新向服务端要问句与事实（同一条路），拿到后那一行仍在。

    python3 tests/test_cc_allow_card_note.py
"""
import sys
import unittest
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, ROOT + "/tests")

from test_assistant_ui import FAKE_SITE, MA_SETUP, SOCKET_STUB, TOOL_LABELS_STUB, USE_MOBILE_STUB, run_ts  # noqa: E402

STUBS = {"cc-ma-setup": MA_SETUP, "socket.io-client": SOCKET_STUB, "@/lib/tool-labels": TOOL_LABELS_STUB,
         "@/hooks/use-mobile": USE_MOBILE_STUB}
HEAD = r"""
const g = globalThis as any;
""" + FAKE_SITE + r"""
const part = (state: any) => state.messages.flatMap((m: any) => m.content).find((p: any) => p.toolCallId === "run1");
const head = [ev("u1", "user.message", 1, { content: text("run the task now") }), ev("r1", "session.status_running", 2),
  ev("run1", "agent.custom_tool_use", 3, { name: "cowork_run_folder_task", input: { task: "T1" } }),
  ev("p1", "session.status_idle", 4, { stop_reason: { type: "requires_action", event_ids: ["run1"] } })];
const VIEW = { ask: "Run this scheduled task now?", preview: [{ field: "Task", before: "", after: "Month-end" }],
  note: "ABC Co., Ltd. · T.8 2026" };
"""


class AllowCardNoteTests(unittest.TestCase):
    def test_the_phone_sheet_shows_the_same_fact_line_under_its_question(self):
        source = (Path(ROOT) / "frontend/assistant/src/components/tool-uis/approval-sheet.tsx").read_text(encoding="utf-8")
        body = source[source.index("export function ApprovalSheet"):]
        line = '{approval.note ? <p className="cc-ask-note">{approval.note}</p> : null}'
        self.assertIn(line, body)
        self.assertLess(body.index('className="cc-ask-sheet-q"'), body.index(line))
        self.assertLess(body.index(line), body.index("<ChangeList"))

    def run_page(self, imports, body):
        return run_ts('import "cc-test-setup";\nimport "cc-ma-setup";\nimport assert from "node:assert/strict";\n' + imports
                      + HEAD + body + "\ng.done();\n", stubs=STUBS)

    def test_the_fact_line_reaches_the_card_and_stays_when_the_gate_is_restored(self):
        self.run_page(r"""
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.browser";  // 浏览器版：打成 ESM 后不用 require 内置模块
import { gateCustomTool, project, restoreGates } from "@/lib/managed-agents/reducer";
import { ApprovalBar } from "@/components/tool-uis/approval-bar";
""", r"""
// 服务端回了事实：门上记下，卡片在问句下面、清单上面画出那一行
let live = gateCustomTool(project(head as any), "run1", VIEW);
assert.equal(part(live).approval.note, "ABC Co., Ltd. · T.8 2026");
let html = renderToStaticMarkup(createElement(ApprovalBar, { approval: part(live).approval, respondToApproval: () => {} }));
assert.match(html, /<p class="cc-ask-note">ABC Co\., Ltd\. · T\.8 2026<\/p><ul class="cc-ask-changes">/);
// 确认请求没送到：卡片照原样放回，那一行还在，另加一行原因
const clicked = { ...live, messages: live.messages.map((m: any) => ({ ...m, content: m.content.map((p: any) =>
  p.toolCallId === "run1" ? { ...p, approval: { ...p.approval, approved: true, sending: true } } : p) })), pendingApprovals: [] };
const back = restoreGates(clicked as any, ["run1"], "Something went wrong. Try again.");
assert.equal(part(back).approval.note, "ABC Co., Ltd. · T.8 2026");
assert.equal(part(back).approval.failure, "Something went wrong. Try again.");
assert.equal(part(back).approval.approved, undefined);
// 服务端没回事实：门上没有 note，那一行不出
const plain = gateCustomTool(project(head as any), "run1", { ask: VIEW.ask, preview: VIEW.preview });
assert.equal("note" in part(plain).approval, false);
html = renderToStaticMarkup(createElement(ApprovalBar, { approval: part(plain).approval, respondToApproval: () => {} }));
assert.doesNotMatch(html, /cc-ask-note/);
// 重开对话：历史里那一步没有门，重新向服务端要来问句与事实（同一条路 gateCustomTool），那一行仍在
const reopened = gateCustomTool(project(head as any), "run1", VIEW);
assert.equal(part(reopened).approval.note, VIEW.note);
""")

    def test_the_run_call_passes_the_fact_line_through_and_leaves_it_out_when_absent(self):
        self.run_page('import { runTool } from "@/lib/managed-agents/api";\n', r"""
const replies: any[] = [{ status: "awaiting_approval", ask: "Run this scheduled task now?", note: "ABC Co., Ltd. · T.8 2026" },
  { status: "awaiting_approval", ask: "Refresh the account sets?" }, { status: "awaiting_approval", ask: "x", note: "" }];
g.fetch = async () => new Response(JSON.stringify({ message: replies.shift() }));
assert.deepEqual(await runTool("sesn_abc123", "run1"),
  { status: "awaiting_approval", ask: "Run this scheduled task now?", preview: undefined, note: "ABC Co., Ltd. · T.8 2026" });
assert.equal("note" in (await runTool("sesn_abc123", "run1")), false);
assert.equal("note" in (await runTool("sesn_abc123", "run1")), false);      // 空串也当没有
""")


if __name__ == "__main__":
    unittest.main()
