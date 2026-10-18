"""CC 对话页的工作状态行（教程第 6、8 条，第 12 条里的滚动与输入法两项）的行为检查。

做法同 tests/test_assistant_ui.py 的 BehaviourTests：把被测源码连同组件库用 esbuild 打成一个文件交给 node 跑，浏览器接口、
站点接口、实时通道换成桩。事件按录屏里的真实顺序推：发送 → 思考 → 工具 1（服务端执行，页面兜底的提醒迟迟不回）→
结果到了、引擎还没重新开跑的交接空档 → 工具 2 → 正文 → 结束。要先装好前端依赖（npm ci --prefix frontend/assistant）且有 node。
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from test_assistant_ui import ESBUILD, FAKE_SITE, MA_SETUP, NODE, SRC, run_ts  # noqa: E402

# 实时通道的桩：推事件直接用 realtime.dispatch；心跳（socket.io 的 ping）由 g.socket.ping() 发
SOCKET_STUB = r"""
export function io() {
  const handlers: Record<string, Array<(data?: any) => void>> = {};
  const manager: Record<string, Array<() => void>> = {};
  const socket: any = {
    connected: true,
    on(name: string, fn: any) { (handlers[name] ??= []).push(fn); return socket; },
    off() { return socket; },
    io: {
      on(name: string, fn: any) { (manager[name] ??= []).push(fn); },
      off(name: string, fn: any) { manager[name] = (manager[name] ?? []).filter((f) => f !== fn); },
    },
  };
  (globalThis as any).socket = {
    drop() { socket.connected = false; (handlers.disconnect ?? []).forEach((fn) => fn()); },
    back() { socket.connected = true; (handlers.connect ?? []).forEach((fn) => fn()); },
    // 通道其实已断、还没报断开（socket.io 发现断开前的那几秒）
    lose() { socket.connected = false; },
    ping() { (manager.ping ?? []).forEach((fn) => fn()); },
    pingListeners: () => (manager.ping ?? []).length,
  };
  return socket;
}
"""

# 推一条事件：同时记进事件记录（补拉读到的与推来的一致），再经实时通道推给页面
PUSH = r"""
import { dispatch } from "@/lib/managed-agents/realtime";
const push = (sid: string, e: any) => { site.log.push(e); dispatch({ session_id: sid, event: e }); };
const preview = (sid: string, e: any) => dispatch({ session_id: sid, event: e });
"""


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class RunIndicatorTests(unittest.TestCase):
    STUBS = {"cc-ma-setup": MA_SETUP, "socket.io-client": SOCKET_STUB}

    def run_page(self, body, lang="zh"):
        imports, script = body.split("\n---\n", 1)
        run_ts('import "cc-test-setup";\nimport "cc-ma-setup";\nimport assert from "node:assert/strict";\n'
               + imports + "\nconst g = globalThis as any;\n" + FAKE_SITE + PUSH + script + "\ng.done();\n",
               stubs=self.STUBS, lang=lang)

    def test_recorded_turn_never_reads_as_finished_until_it_ends(self):
        # 2026-10-13 iPhone 录屏：两段工具之间约 10 秒显示「2 ขั้นตอน」+ 复制按钮、发送键变回空闲，之后又变回运行中。
        # 原因：工具 1 的结果已到（服务端执行完）、引擎还没重新开跑，这段空档里页面按「已结束」画。
        self.run_page(r"""
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { isBusy } from "@/lib/managed-agents/reducer";
import { statusText, ThinkingClock, turnActivity } from "@/lib/managed-agents/turn";
---
const S = "sesn_rec1";
site.echo = false;
site.toolReply = () => new Promise(() => {});  // 页面兜底的「执行一次」迟迟不回（引擎列出事件慢）
const c = getSessionController(S);
c.attach();
await g.sleep(30);
const lastAssistant = (s: any) => [...s.messages].reverse().find((m: any) => m.role === "assistant");
const steps: string[] = [];
function look(label: string, expect: string) {
  const s: any = c.getSnapshot();
  const activity = turnActivity(s);
  steps.push(`${label}:${activity.kind}`);
  assert.equal(activity.kind, expect, label);
  assert.equal(isBusy(s), true, `${label}: composer stays busy (no send key, no copy button)`);
  const status = lastAssistant(s)?.status?.type;
  assert.notEqual(status, "complete", `${label}: the reply is not finished`);
  assert.ok(s.messages.some((m: any) => m.role === "user" && m.content[0]?.text === "บิลนี้"), `${label}: own bubble stays`);
}
await c.sendMessage("บิลนี้", []);
look("sent", "thinking");
push(S, ev("u1", "user.message", 1, { content: text("บิลนี้") }));
look("echo", "thinking");
push(S, ev("r1", "session.status_running", 2));
preview(S, { type: "event_start", event: { id: "th1", type: "agent.thinking" } });
push(S, ev("th1", "agent.thinking", 3));
look("thinking", "thinking");
push(S, ev("t1", "agent.custom_tool_use", 4, { name: "cowork_recognize_attachments", input: { files: ["a.jpg"] } }));
look("tool 1", "tool");
assert.equal((turnActivity(c.getSnapshot()) as any).toolName, "cowork_recognize_attachments");
push(S, ev("p1", "session.status_idle", 5, { stop_reason: { type: "requires_action", event_ids: ["t1"] } }));
await g.sleep(5);
look("tool 1 parked, executing on the server", "tool");
// 服务端执行完，结果到了；引擎还没重新开跑（交接空档）
push(S, ev("res1", "user.custom_tool_result", 6, { custom_tool_use_id: "t1", content: text("1 document") }));
look("handoff gap", "thinking");
// 空档里回到前台：读回的状态是停在 t1、结果已交 → 仍在跑
site.engine = "idle";
g.document.fire("visibilitychange");
await g.sleep(30);
look("handoff gap after a status read", "thinking");
push(S, ev("r2", "session.status_running", 7));
look("resumed", "thinking");
push(S, ev("t2", "agent.custom_tool_use", 8, { name: "cowork_push_documents", input: {} }));
look("tool 2", "tool");
push(S, ev("p2", "session.status_idle", 9, { stop_reason: { type: "requires_action", event_ids: ["t2"] } }));
push(S, ev("res2", "user.custom_tool_result", 10, { custom_tool_use_id: "t2", content: text("HP691013-001") }));
look("second handoff", "thinking");
push(S, ev("r3", "session.status_running", 11));
// 正文：逐字预览先开了个空段，字还没到 → 仍是「思考中」，不出一个孤零零的点
preview(S, { type: "event_start", event: { id: "m1", type: "agent.message" } });
look("empty preview", "thinking");
preview(S, { type: "event_delta", event_id: "m1", delta: { type: "content_delta", content: { type: "text", text: "บันทึก" } } });
look("text streaming", "writing");
push(S, ev("m1", "agent.message", 12, { content: text("บันทึกแล้ว HP691013-001") }));
look("text done, turn not ended", "writing");
// 真正结束：状态行与「在跑」同一刻收起，回复同一刻标完成
push(S, ev("i1", "session.status_idle", 13, { stop_reason: { type: "end_turn" } }));
const end: any = c.getSnapshot();
assert.equal(turnActivity(end).kind, "hidden");
assert.equal(isBusy(end), false);
assert.equal(lastAssistant(end).status.type, "complete");
assert.deepEqual(steps.map((s) => s.split(":")[1]), ["thinking", "thinking", "thinking", "tool", "tool", "thinking", "thinking",
  "thinking", "tool", "thinking", "thinking", "writing", "writing"]);
// 文字（中文页）：计时从发出算；想完出正文时说「思考了 M秒」；执行中说这一步的进行时
assert.equal(statusText({ kind: "thinking" }, 3, 0, ""), "3秒 · 思考中…");
assert.equal(statusText({ kind: "writing" }, 8, 4, ""), "8秒 · 思考了 4秒");
assert.equal(statusText({ kind: "writing" }, 8, 0, ""), "8秒");
assert.equal(statusText({ kind: "tool", toolName: "x", args: {} }, 75, 0, "正在识别单据"), "1分15秒 · 正在识别单据");
assert.equal(statusText({ kind: "reconnecting" }, 40, 0, ""), "连接中断，正在重连…");
// 「思考了几秒」：从发出想到出正文；中间一步工具做完再想的，从交回结果算；换位置重画（同一份状态）不改
const clock = new ThinkingClock();
const t0 = 1_000_000;
assert.equal(clock.note("thinking", t0, t0 + 1000), 0);
assert.equal(clock.note("tool", t0, t0 + 4200), 4);
assert.equal(clock.note("tool", t0, t0 + 9000), 4);
assert.equal(clock.note("thinking", t0, t0 + 12000), 4);
assert.equal(clock.note("hidden", t0, t0 + 13000), 4);  // 等「允许」时收起，不打断
assert.equal(clock.note("thinking", t0, t0 + 14000), 4);
assert.equal(clock.note("writing", t0, t0 + 18400), 6);
assert.equal(clock.note("writing", t0, t0 + 30000), 6);
assert.equal(clock.note("thinking", t0 + 60000, t0 + 61000), 0);  // 新的一轮从头记
// 刷新后正文已在出：没看到它想，只走计时
const late = new ThinkingClock();
assert.equal(late.note("writing", t0, t0 + 20000), 0);
""")

    def test_the_line_says_needs_input_while_the_allow_card_waits_and_comes_back_after_the_click(self):
        self.run_page(r"""
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { isBusy } from "@/lib/managed-agents/reducer";
import { turnActivity } from "@/lib/managed-agents/turn";
---
const S = "sesn_gate2";
site.echo = false;
site.runs = { tz: "awaiting_approval" };
site.toolReply = async () => ({ status: "awaiting_approval", ask: "Turn it on?", preview: [] });
const c = getSessionController(S);
c.attach();
await g.sleep(30);
await c.sendMessage("turn the task on", []);
push(S, ev("u1", "user.message", 1, { content: text("turn the task on") }));
push(S, ev("r1", "session.status_running", 2));
push(S, ev("tz", "agent.custom_tool_use", 3, { name: "cowork_set_folder_task_enabled", input: { enabled: true } }));
push(S, ev("p1", "session.status_idle", 4, { stop_reason: { type: "requires_action", event_ids: ["tz"] } }));
await g.sleep(20);
let s: any = c.getSnapshot();
assert.deepEqual(s.pendingApprovals, ["tz"]);  // 卡片照常显示
assert.equal(turnActivity(s).kind, "input");  // 轮到用户：状态行换成「需要您的输入」（照 Claude，用户 10-17）
assert.equal(isBusy(s), true);
// 点了允许：服务端开始执行，状态行回来，说这一步在做什么
c.respondToApproval("tz", true);
s = c.getSnapshot();
assert.equal(turnActivity(s).kind, "tool");
assert.equal(isBusy(s), true);
""")

    def test_a_confirm_that_never_arrived_keeps_the_card_and_says_why(self):
        # 第 9 轮测试：拦住 assistant.api.confirm 后卡片折成做完的说法「记下了公司选择」、按钮还挂着。确认没送到不能写成功：
        # 卡片照原样回来（问话、改动都在），按钮可再点，上方一行原因（通用句）；再点送到了才定下
        self.run_page(r"""
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { approvalCard } from "@/lib/approval-card";
import { turnActivity } from "@/lib/managed-agents/turn";
import { t } from "@/i18n";
---
const S = "sesn_gate_fail";
site.echo = false;
site.runs = { tz: "awaiting_approval" };
const changes = [{ field: "公司", before: "", after: "A" }];
site.toolReply = async () => ({ status: "awaiting_approval", ask: "保存为公司选择？", preview: changes });
let confirmFails = true;
const base = g.respond;
g.respond = async (method: string, args: any, body: any) => {
  if (method === "confirm" && confirmFails) throw new TypeError("Failed to fetch");
  return base(method, args, body);
};
const c = getSessionController(S);
c.attach();
await g.sleep(30);
push(S, ev("u1", "user.message", 1, { content: text("记到公司") }));
push(S, ev("r1", "session.status_running", 2));
push(S, ev("tz", "agent.custom_tool_use", 3, { name: "cowork_company_choice_save", input: { choice: "A" } }));
push(S, ev("p1", "session.status_idle", 4, { stop_reason: { type: "requires_action", event_ids: ["tz"] } }));
await g.sleep(20);
const tz = () => c.getSnapshot().messages.flatMap((m: any) => m.content).find((p: any) => p.toolCallId === "tz");
assert.equal(tz().approval.ask, "保存为公司选择？");
c.respondToApproval("tz", true);
assert.equal(tz().approval.approved, true);      // 点下去先按允许画（乐观）
assert.equal(tz().approval.sending, true);       // 确认还没送到：点完那一行先淡一档
assert.deepEqual(approvalCard(tz(), { ask: {} }), { phase: "answered", approved: true, failed: false, sending: true });
await g.sleep(320);
const confirms = () => g.calls.filter((call: any) => call.method === "confirm").length;
assert.equal(confirms(), 1);
let part = tz();
assert.equal(part.approval.approved, undefined);  // 没送到：不写成功，按钮回来
assert.equal(part.approval.sending, undefined);
// 只写卡片上的原因：确认没送到不设连接出错，顶上不再同时出现「正在重新连接…」（第 10 轮审查）
assert.equal(c.getSnapshot().transportError, null);
assert.equal(part.approval.ask, "保存为公司选择？");
assert.deepEqual(part.approval.preview, changes);
assert.equal(part.approval.failure, t("somethingWrong"));
assert.deepEqual(approvalCard(part, { ask: {} }), { phase: "ask" });
assert.deepEqual(c.getSnapshot().pendingApprovals, ["tz"]);
assert.equal(turnActivity(c.getSnapshot()).kind, "input");
// 再点：送到了，定下「允许」，原因清掉
confirmFails = false;
c.respondToApproval("tz", true);
await g.sleep(320);
assert.equal(confirms(), 2);
part = tz();
assert.equal(part.approval.approved, true);
assert.equal(part.approval.failure, undefined);
assert.equal(part.approval.sending, undefined);   // 送到了：不再淡着
assert.equal(part.approval.ask, "保存为公司选择？");
""")

    def test_a_card_answered_elsewhere_settles_here_at_once(self):
        # 别的设备上答过：实时事件里这一步已有结论，这边按钮立即消失、变成点完的样子（不用刷新）
        self.run_page(r"""
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { approvalCard } from "@/lib/approval-card";
---
const S = "sesn_gate_else";
site.echo = false;
site.runs = { ta: "awaiting_approval", td: "awaiting_approval" };
site.toolReply = async (id: string) => ({ status: "awaiting_approval", ask: id === "ta" ? "记到公司？" : "删掉？", preview: [] });
const c = getSessionController(S);
c.attach();
await g.sleep(30);
push(S, ev("u1", "user.message", 1, { content: text("go") }));
push(S, ev("r1", "session.status_running", 2));
push(S, ev("ta", "agent.custom_tool_use", 3, { name: "cowork_company_choice_save", input: {} }));
push(S, ev("td", "agent.custom_tool_use", 4, { name: "cowork_company_choice_delete", input: {} }));
push(S, ev("tb", "agent.tool_use", 5, { name: "bash", input: { command: "ls" } }));
push(S, ev("p1", "session.status_idle", 6, { stop_reason: { type: "requires_action", event_ids: ["ta", "td", "tb"] } }));
await g.sleep(20);
const find = (id: string) => c.getSnapshot().messages.flatMap((m: any) => m.content).find((p: any) => p.toolCallId === id);
assert.deepEqual([...c.getSnapshot().pendingApprovals].sort(), ["ta", "tb", "td"]);
// 本站工具：别处点了允许 / 拒绝，服务端执行完交回结果
push(S, ev("ra", "user.custom_tool_result", 7, { custom_tool_use_id: "ta", content: text(JSON.stringify({ status: "saved" })) }));
push(S, ev("rd", "user.custom_tool_result", 8, { custom_tool_use_id: "td", content: text(JSON.stringify({ status: "denied", message: "x" })) }));
// 内置工具：别处的确认（服务端记下的）推过来
push(S, ev("cb", "user.tool_confirmation", 9, { tool_use_id: "tb", result: "allow" }));
assert.deepEqual(approvalCard(find("ta"), { ask: {} }), { phase: "answered", approved: true, failed: false });
assert.deepEqual(approvalCard(find("td"), { ask: {} }), { phase: "answered", approved: false, failed: false });
assert.deepEqual(approvalCard(find("tb"), { ask: {} }), { phase: "answered", approved: true, failed: false });
assert.equal(find("ta").approval.ask, "记到公司？");   // 点完的卡片照写问话
assert.deepEqual(c.getSnapshot().pendingApprovals, []);
// 两台设备同时点（第 10 轮审查）：本页点了「允许」，别处先点了「拒绝」，服务端结果写明拒绝——卡片以结果为准写「拒绝」，
// 不一直写本页点的那一项；反过来本页点「拒绝」、结果是做过了的，写「允许」
site.runs = { ...site.runs, tx: "awaiting_approval", ty: "awaiting_approval" };
push(S, ev("tx", "agent.custom_tool_use", 10, { name: "cowork_company_choice_save", input: {} }));
push(S, ev("ty", "agent.custom_tool_use", 11, { name: "cowork_company_choice_save", input: {} }));
push(S, ev("p2", "session.status_idle", 12, { stop_reason: { type: "requires_action", event_ids: ["tx", "ty"] } }));
await g.sleep(20);
c.respondToApproval("tx", true);
c.respondToApproval("ty", false);
assert.equal(find("tx").approval.approved, true);
push(S, ev("rx", "user.custom_tool_result", 13, { custom_tool_use_id: "tx", content: text(JSON.stringify({ status: "denied", message: "x" })) }));
push(S, ev("ry", "user.custom_tool_result", 14, { custom_tool_use_id: "ty", content: text(JSON.stringify({ status: "saved" })) }));
assert.equal(find("tx").approval.approved, false);
assert.deepEqual(approvalCard(find("tx"), { ask: {} }), { phase: "answered", approved: false, failed: false });
assert.equal(find("ty").approval.approved, true);
assert.deepEqual(approvalCard(find("ty"), { ask: {} }), { phase: "answered", approved: true, failed: false });
// 结果先到、本页那次点击的回声后到：不再改回本页点的
push(S, ev("cx", "user.tool_confirmation", 15, { tool_use_id: "tx", result: "allow" }));
assert.equal(find("tx").approval.approved, false);
await g.sleep(320);
""")

    def test_silence_while_running_reads_as_reconnecting_and_recovers(self):
        self.run_page(r"""
import { SessionController, getSessionController } from "@/lib/managed-agents/session-controller";
import { turnActivity } from "@/lib/managed-agents/turn";
---
SessionController.stallMs = 80;
const S = "sesn_stall3";
site.engine = "running";  // 服务端那边这一轮一直在跑
site.echo = false;
const c = getSessionController(S);
c.attach();
await g.sleep(30);
await c.sendMessage("hello", []);
push(S, ev("u1", "user.message", 1, { content: text("hello") }));
push(S, ev("r1", "session.status_running", 2));
// 实时通道的心跳一直到：这段时间没有任何事件、补拉也还没轮到，仍算连着（不误报）
for (let i = 0; i < 8; i++) {
  g.socket.ping();
  await g.sleep(25);
}
assert.equal(c.getSnapshot().stalled, false);
assert.equal(turnActivity(c.getSnapshot()).kind, "thinking");
// 断了：心跳停、补拉一直不回
const respond = g.respond;
g.respond = (method: string, args: any, body: any) => method === "events" ? new Promise(() => {}) : respond(method, args, body);
const before = eventsCalls(S).length;
await g.sleep(150);
let s: any = c.getSnapshot();
assert.equal(s.stalled, true);
assert.equal(turnActivity(s).kind, "reconnecting");
assert.ok(eventsCalls(S).length > before, "the tail was reopened (a fresh fetch), no new polling loop");
assert.ok(g.socket.pingListeners() <= 1, "the replaced tail stopped listening");
// 网络回来了（卡住的那几次请求永远不回）：下一次看门重开的尾巴连上就补齐断线期间的事件、回到正常
g.respond = respond;
site.log.push(ev("th0", "agent.thinking", 3));
await g.sleep(200);
s = c.getSnapshot();
assert.equal(s.stalled, false);
assert.ok(s.messages.some((m: any) => m.role === "assistant" && m.content.some((p: any) => p.type === "reasoning")),
  "events from the gap were backfilled by the reopened tail");
assert.equal(turnActivity(s).kind, "thinking");
// 推送照常并进来
push(S, ev("t1", "agent.custom_tool_use", 4, { name: "cowork_push_documents", input: {} }));
assert.equal(turnActivity(c.getSnapshot()).kind, "tool");
// 结束后不再看门：再安静多久也不会冒出「连接中断」
push(S, ev("i1", "session.status_idle", 5, { stop_reason: { type: "end_turn" } }));
await g.sleep(150);
assert.equal(c.getSnapshot().stalled, false);
assert.equal(turnActivity(c.getSnapshot()).kind, "hidden");
SessionController.stallMs = 30000;
""")

    def test_a_status_read_sent_before_the_server_took_the_message_does_not_end_the_turn(self):
        # 发出后马上回到前台 / 重连：那次读状态在服务端收下消息之前，读到的是上一轮结束时的「空闲」
        self.run_page(r"""
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { isBusy } from "@/lib/managed-agents/reducer";
---
const S = "sesn_race4";
site.log.push(ev("u0", "user.message", 1, { content: text("hi") }), ev("r0", "session.status_running", 2),
  ev("i0", "session.status_idle", 3, { stop_reason: { type: "end_turn" } }));
const c = getSessionController(S);
c.attach();
await g.sleep(30);
let release: () => void = () => {};
const respond = g.respond;
g.respond = async (method: string, args: any, body: any) => {
  if (method === "send") await new Promise<void>((resolve) => { release = resolve; });
  return respond(method, args, body);
};
const sending = c.sendMessage("again", []);
await g.sleep(5);
g.document.fire("visibilitychange");  // 服务端还没收下：读到「空闲」
await g.sleep(30);
assert.equal(isBusy(c.getSnapshot()), true);
release();
await sending;
assert.equal(isBusy(c.getSnapshot()), true);
g.respond = respond;
""")

    def test_own_bubble_hands_over_without_a_gap(self):
        self.run_page(r"""
import { beginPendingText, clearPending, lastSendAt, pendingLanded, pendingShownAs, takeHandoff } from "@/lib/managed-agents/pending-send";
import { turnStartedAt } from "@/lib/managed-agents/turn";
---
// 按下发送：临时气泡；正式气泡放进会话后，在对话真画出它之前临时气泡一直在
beginPendingText("บิลนี้");
const sent = lastSendAt();
pendingShownAs("optimistic-1");
// 渲染时判断：组件库的消息里还没有这一条 → 临时气泡继续显示
const pending = { text: "บิลนี้", files: [], shownAs: "optimistic-1" };
assert.equal(pendingLanded(pending, ["u0", "a0"]), false);
assert.equal(pendingLanded(pending, ["u0", "a0", "optimistic-1"]), true);
assert.equal(pendingLanded({ text: "x", files: [] }, ["optimistic-1"]), false);  // 还没交给会话的不算
// 接替它的正式气泡不再弹一次；别的消息照常
assert.equal(takeHandoff("optimistic-2"), false);
assert.equal(takeHandoff("optimistic-1"), true);
assert.equal(takeHandoff("optimistic-1"), false);
clearPending();
// 计时：本页发出的从按下发送算，回显换成服务端时间也不跳；刷新后接着看的用服务端时间
const now = sent + 5000;
const bubble = (id: string, at: number) => ({ id, role: "user", content: [], createdAt: new Date(at) }) as any;
assert.equal(turnStartedAt([bubble("optimistic-1", sent + 900)], sent, false, now), sent);
assert.equal(turnStartedAt([bubble("optimistic-1", sent - 3000)], sent, false, now), sent);
assert.equal(turnStartedAt([bubble("sevt_1", sent - 3000)], sent, false, now), sent - 3000);
assert.equal(turnStartedAt([bubble("sevt_1", now + 9000)], sent, false, now), now);  // 服务端时钟快了也不倒着走
assert.equal(turnStartedAt([], sent, true, now), sent);
""")

    def test_a_streaming_reply_is_not_followed_and_the_arrow_brings_the_reader_down(self):
        # 照 Claude（用户 10-17 截图，三端一样）：回复在出时画面停在这一轮开头，不跟到底；下面有内容时输入框上方出 ↓，点了才到最新
        self.run_page(r"""
import { OpenSettler } from "@/lib/follow-scroll";
---
let clock = 1000;
const listeners: Record<string, Array<(e: any) => void>> = {};
const vp: any = {
  scrollTop: 0, scrollHeight: 2000, clientHeight: 800, scrolls: 0,
  addEventListener(type: string, fn: any) { (listeners[type] ??= []).push(fn); },
  removeEventListener(type: string, fn: any) { listeners[type] = (listeners[type] ?? []).filter((f) => f !== fn); },
  scrollTo({ top }: any) { this.scrolls++; this.scrollTop = Math.min(top, this.scrollHeight - this.clientHeight); },
  querySelector() { return { offsetHeight: 0 }; },
};
let grow: () => void = () => {};
const f = new OpenSettler(vp, {}, () => ({ turn: null, synced: true }), () => clock, (_t, onResize) => { grow = onResize; return () => {}; });
vp.scrollTop = 1200;  // 在底部（这一轮刚顶到上面）
const more = (px: number) => { clock += 200; vp.scrollHeight += px; grow(); };
// 回复一直在长：位置不动，也不去滚
for (let i = 0; i < 20; i++) more(120);
assert.equal(vp.scrollTop, 1200);
assert.equal(vp.scrolls, 0);
f.dispose();
assert.equal(Object.values(listeners).every((l) => l.length === 0), true);
""")
        thread = (SRC / "components/assistant-ui/thread.tsx").read_text(encoding="utf-8")
        self.assertNotIn("resume()", thread)
        self.assertIn('turnAnchor="top"', thread)
        arrow = thread[thread.index("const ThreadScrollToBottom"):thread.index("const Composer: FC")]
        # ↓ 照 13.webp：40 的圆、箭头 20、在输入框上方居中；点了平滑滚到最新；在底部时藏起来
        self.assertIn('<ThreadPrimitive.ScrollToBottom asChild behavior="smooth">', arrow)
        # 第 11 轮 C21：读屏名写在 aria-label 上（复用 scrollToBottom 词条）；到底时 visibility: hidden，读屏读不到、Tab 走不到
        self.assertIn('aria-label={t("scrollToBottom")}', arrow)
        self.assertIn('tooltip={t("scrollToBottom")}', arrow)
        # aria-label 原样落到按钮上（TooltipIconButton 把其余属性展开到 Button，不另给 aria-label 盖掉）
        button = (SRC / "components/assistant-ui/tooltip-icon-button.tsx").read_text(encoding="utf-8")
        self.assertIn("{...rest}", button)
        self.assertNotIn("aria-label", button)
        for cls in ("size-10", "p-0", "-top-9", "self-center", "rounded-full", "disabled:invisible"):
            self.assertIn(cls, arrow)
        self.assertNotIn("p-4", arrow)
        self.assertIn('<ArrowDownIcon className="size-5" />', arrow)

    def test_sending_on_a_phone_drops_the_keyboard(self):
        # 手机（触屏或手机外壳）发出去就收起键盘（同 Claude 手机 app，11.webp）；电脑保持焦点；回车、发送键两条路都管
        thread = (SRC / "components/assistant-ui/thread.tsx").read_text(encoding="utf-8")
        composer = thread[thread.index("const Composer: FC"):thread.index("// 对话里出错那一行")]
        self.assertIn("const dropKeyboard = TOUCH || phone;", composer)
        self.assertIn("if (dropKeyboard && !emptyNow.current) input.current?.blur();", composer)
        self.assertIn('onSubmit={sent}', composer)
        self.assertIn("<ComposerAction onSend={sent} />", composer)
        self.assertIn("onClick={onSend}", composer)
        self.assertIn("ref={input}", composer)
        self.assertIn("unstable_focusOnRunStart={!dropKeyboard", composer)
        self.assertIn("unstable_focusOnScrollToBottom={!dropKeyboard}", composer)

    def test_ime_enter_does_not_send(self):
        self.run_page(r"""
import { holdImeEnter, isImeEnter } from "@/lib/ime";
---
const key = (key: string, keyCode: number, isComposing: boolean) => {
  const e: any = { key, keyCode, nativeEvent: { isComposing, keyCode }, prevented: false };
  e.preventDefault = () => { e.prevented = true; };
  return e;
};
// Safari：先结束选字再来这次回车，isComposing 已是 false，只剩 229 → 不发送（组件库那边被标成已处理）
const safari = key("Enter", 229, false);
holdImeEnter(safari);
assert.equal(isImeEnter(safari), true);
assert.equal(safari.prevented, true);
// Chrome 选字中：组件库本来就不发，这里不动（不打断输入法上屏）
const chrome = key("Enter", 229, true);
holdImeEnter(chrome);
assert.equal(isImeEnter(chrome), true);
assert.equal(chrome.prevented, false);
// 普通回车照常发送；Shift+回车、别的键不管
const plain = key("Enter", 13, false);
holdImeEnter(plain);
assert.equal(plain.prevented, false);
assert.equal(isImeEnter(key("a", 229, false)), false);
""")

    def test_a_new_chat_is_running_while_its_session_is_created(self):
        # 2026-10-15 生产（F4）：新对话发第一句，状态行已在「思考中」，发送键却还是「发送」约 4 秒（建会话期间没有会话控制器，
        # 组件库以为没在跑），这几秒还能再发一条。现在：建会话并发出期间就是在跑；期间点停止，发出后打断；失败恢复可发送。
        self.run_page(r"""
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { NewChatSend } from "@/lib/managed-agents/new-chat-send";
---
const posted = (method: string) => g.calls.filter((c: any) => c.method === method);
const send = (text: string) => (id: string) => getSessionController(id).sendMessage(text, []);
const interrupt = (id: string) => getSessionController(id).interrupt();
const chat = new NewChatSend();
const seen: boolean[] = [];
chat.subscribe(() => seen.push(chat.isSending()));
assert.equal(chat.cancel(), false);                 // 没在建会话：停止键照旧由会话控制器打断
// 建会话（服务端约 1–4 秒）期间：在跑（组件库 isRunning = isBusy(空状态) || 这一项 → 停止键，不能再发）
let made: (id: string) => void = () => {};
const running = chat.run(() => new Promise<string>((resolve) => { made = resolve; }), send("first"), interrupt);
assert.equal(chat.isSending(), true);
await g.sleep(20);
assert.equal(chat.isSending(), true);
// 期间点停止：先记下，不发打断（会话还没有），也不丢消息
assert.equal(chat.cancel(), true);
assert.equal(posted("interrupt").length, 0);
made("sesn_new4a");
await running;
// 用户那条照常发出、留在对话里；发出之后才打断这段新会话
assert.deepEqual(posted("send").map((c: any) => [c.body.session_id, c.body.text]), [["sesn_new4a", "first"]]);
assert.deepEqual(posted("interrupt").map((c: any) => c.body.session_id), ["sesn_new4a"]);
assert.ok(g.calls.findIndex((c: any) => c.method === "send") < g.calls.findIndex((c: any) => c.method === "interrupt"));
assert.ok(getSessionController("sesn_new4a").getSnapshot().messages.some((m: any) => m.role === "user"));
assert.equal(chat.isSending(), false);
assert.deepEqual(seen, [true, false]);
// 没点停止：照常发出，不打断
await chat.run(async () => "sesn_new4b", send("second"), interrupt);
assert.equal(posted("interrupt").length, 1);
assert.equal(chat.isSending(), false);
// 建会话失败：照常报错，恢复可发送；记下的停止不留到下一次
const failing = chat.run(async () => { await g.sleep(10); throw new Error("session was not created"); }, send("x"), interrupt);
assert.equal(chat.cancel(), true);
await assert.rejects(failing, /session was not created/);
assert.equal(chat.isSending(), false);
assert.equal(chat.cancel(), false);
await chat.run(async () => "sesn_new4c", send("third"), interrupt);
assert.equal(posted("interrupt").length, 1);
""")

    def test_a_failed_send_says_why_and_keeps_what_was_typed(self):
        # 2026-10-16 测试窗口（lab testerp，站点没配 CC）：新对话发第一条，create 回 417，气泡和「思考中」闪约 100ms 就没了，
        # 打的字也没了，界面不报错，控制台只有一条未捕获的「本站点尚未设置 CC。」。组件库按下发送先清空输入框、调发送不等也不接错。
        # 现在（用组件库真实的输入框与对话运行时走一遍）：原因按服务端原文写在输入框上方、字与附件放回、没有未捕获的错误、重试时清掉；
        # 没有原文（断网等）用通用的一句；已有对话发出被拒同样处理；已发出、只是之后的打断没成功不放回。
        self.run_page(r"""
import { ExternalStoreThreadRuntimeCore } from "@assistant-ui/core/internal";
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { NewChatSend } from "@/lib/managed-agents/new-chat-send";
import { createSession } from "@/lib/managed-agents/api";
import { attemptSend, sendFailureOf } from "@/lib/managed-agents/send-failure";
import { attachedFilesOf } from "@/lib/managed-agents/attachments";
import { t } from "@/i18n";
---
const unhandled: unknown[] = [];
process.on("unhandledRejection", (reason) => unhandled.push(reason));
const respond = g.respond;
const rejected = (status: number, message: string) => ({
  ok: false, status,
  json: async () => ({ exception: `frappe.exceptions.ValidationError: ${message}`, _server_messages: JSON.stringify([JSON.stringify({ message })]) }),
});
let createReply: () => any = () => rejected(417, "本站点尚未设置 CC。");
let sendReply: (() => any) | null = null;
g.respond = async (method: string, args: any, body: any) => {
  if (method === "create") return createReply();
  if (method === "send" && sendReply) return sendReply();
  return respond(method, args, body);
};
const posted = (method: string) => g.calls.filter((c: any) => c.method === method);
const textOf = (m: any) => m.content.filter((p: any) => p.type === "text").map((p: any) => p.text).join("\n").trim();

// 同 runtime-provider.tsx 的 onNew：新对话先建会话再发出，已有对话直接发出；都经 attemptSend
function chat(thread: string, session: string | null) {
  const newChat = new NewChatSend();
  let core: any;
  const composer = {
    getState: () => ({ text: core.composer.text }),
    setText: (text: string) => core.composer.setText(text),
    addAttachment: (a: any) => core.composer.addAttachment(a),
  };
  const store: any = {
    messages: [], isRunning: false, convertMessage: (m: any) => m,
    onNew: async (message: any) => {
      const text = textOf(message);
      await attemptSend(thread, { composer, text, attachments: message.attachments }, async (sent) => {
        const send = async (id: string) => {
          await getSessionController(id).sendMessage(text, attachedFilesOf(message.attachments));
          sent();
        };
        if (session) await send(session);
        else await newChat.run(async () => (session = (await createSession()).id), send, (id) => getSessionController(id).interrupt());
      });
    },
  };
  core = new ExternalStoreThreadRuntimeCore({ getModelContext: () => ({}), subscribe: () => () => {} } as any, store);
  return { core, newChat };
}

// ① 新对话、站点没配 CC：create 417
const a = chat("thread-new", null);
a.core.composer.setText("ช่วยดูบิลนี้");
await a.core.composer.send();
assert.equal(a.core.composer.text, "");                    // 组件库按下发送就清空了输入框
await g.sleep(30);
assert.equal(sendFailureOf("thread-new"), "本站点尚未设置 CC。");  // 服务端原文
assert.equal(a.core.composer.text, "ช่วยดูบิลนี้");          // 字放回输入框
assert.equal(posted("send").length, 0);
assert.equal(a.newChat.isSending(), false);                 // 发送键恢复可发送
assert.equal(sendFailureOf("thread-other"), "");            // 只在这段对话
// 重试：一按发送就清掉原因；这次建好会话、发出
createReply = async () => { await g.sleep(40); return ok({ id: "sesn_retry1", title: "" }); };
await a.core.composer.send();
assert.equal(a.newChat.isSending(), true);                  // 还在建会话
assert.equal(sendFailureOf("thread-new"), "");               // 原因已清掉
await g.sleep(80);
assert.equal(sendFailureOf("thread-new"), "");
assert.deepEqual(posted("send").map((c: any) => [c.body.session_id, c.body.text]), [["sesn_retry1", "ช่วยดูบิลนี้"]]);
assert.equal(a.core.composer.text, "");

// ② 没有服务端原文（断网）：通用的一句；等的时候又打了字，两段都留着
createReply = () => { throw new TypeError("Failed to fetch"); };
const b = chat("thread-offline", null);
b.core.composer.setText("first");
await b.core.composer.send();
b.core.composer.setText("more");
await g.sleep(30);
assert.equal(sendFailureOf("thread-offline"), t("somethingWrong"));
assert.equal(sendFailureOf("thread-offline"), "出了点问题，请重试。");
assert.equal(b.core.composer.text, "first\nmore");

// ③ 已有对话发出被拒（附件已传进会话）：乐观气泡撤回，原因原文，字和附件（编号、位置不变）放回，再发照样带上附件
site.log.length = 0;                                         // 假站点的事件记录各对话共用：这段从空的开始
const c = chat("thread-old", "sesn_old3");
getSessionController("sesn_old3").attach();
await g.sleep(30);
sendReply = () => rejected(417, "ไม่มีสิทธิ์ในบริษัทนี้");
const file = { type: "file", filename: "r.pdf", data: "r.pdf", mimeType: "application/pdf" };
await c.core.composer.addAttachment({ id: "att-r", type: "document", name: "r.pdf", contentType: "application/pdf", content: [file] });
c.core.composer.setText("ดูไฟล์นี้");
await c.core.composer.send();
assert.equal(c.core.composer.attachments.length, 0);
await g.sleep(30);
assert.equal(sendFailureOf("thread-old"), "ไม่มีสิทธิ์ในบริษัทนี้");
assert.equal(c.core.composer.text, "ดูไฟล์นี้");
assert.deepEqual(c.core.composer.attachments.map((x: any) => [x.id, x.name, x.status.type, x.content[0].data]), [["att-r", "r.pdf", "complete", "r.pdf"]]);
assert.ok(!getSessionController("sesn_old3").getSnapshot().messages.some((m: any) => m.role === "user"));  // 没留下像是发出去了的气泡
assert.equal(getSessionController("sesn_old3").getSnapshot().isRunning, false);
sendReply = null;
let delivered: any = null;
c.core.__internal_setAdapter({ ...c.core._store, onNew: async (m: any) => { delivered = m; } });
await c.core.composer.send();
assert.deepEqual(delivered.attachments.map((x: any) => x.id), ["att-r"]);
assert.equal(textOf(delivered), "ดูไฟล์นี้");

// ④ 已发出、只是之后的打断没成功：说原因，不放回（消息已在对话里）
const d = { text: "", setText(v: string) { d.text = v; }, getState: () => ({ text: d.text }), addAttachment: async () => { throw new Error("no"); } };
const outcome = await attemptSend("thread-int", { composer: d, text: "sent already" }, async (sent) => { sent(); throw new Error("interrupt failed"); });
assert.equal(outcome, false);
assert.equal(d.text, "");
assert.equal(sendFailureOf("thread-int"), "出了点问题，请重试。");

// ⑤ 没传上的附件（新对话没设置 CC 时选的文件：上传前建会话就被拒，那一项标着出错）：放回原文件重新传，不当成传好了的
const added: any[] = [];
const e = { text: "", setText(v: string) { e.text = v; }, getState: () => ({ text: e.text }), addAttachment: async (a: any) => { added.push(a); } };
const photo = new File(["x"], "p.jpg", { type: "image/jpeg" });
const never = { id: "att-never", type: "image", name: "p.jpg", contentType: "image/jpeg", file: photo, status: { type: "complete" },
  content: [{ type: "file", filename: "p.jpg", data: "p.jpg", mimeType: "image/jpeg" }] };
await attemptSend("thread-photo", { composer: e, text: "ดูรูป", attachments: [never as any] }, async () => { throw new Error("x"); });
assert.equal(e.text, "ดูรูป");
assert.equal(added.length, 1);
assert.ok(added[0] instanceof File);
assert.equal(added[0], photo);

await g.sleep(30);
assert.deepEqual(unhandled, []);                             // 一个未捕获的错误都没有
g.respond = respond;
""")

    def test_a_failed_attachment_is_uploaded_again_on_send_and_never_dropped(self):
        # 2026-10-17 第 9 轮第 15 条（手机为主）：附件上传失败（红色感叹号）后点发送，页面不重传、不提示，消息不带附件就发出去了，
        # CC 答的是上一张旧图。现在（组件库真实的输入框与对话运行时 + 本站附件适配器）：发送时先重传；传上了带着发；仍没传上就不发，
        # 原因写在输入框上方、字和附件留在输入框，没有未捕获的错误。上传中断网（点了发送还在传）走同一条路。
        self.run_page(r"""
import { ExternalStoreThreadRuntimeCore } from "@assistant-ui/core/internal";
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { attemptSend, sendFailureOf } from "@/lib/managed-agents/send-failure";
import { createAttachmentAdapter, filesToSend } from "@/lib/managed-agents/attachments";
---
const unhandled: unknown[] = [];
process.on("unhandledRejection", (reason) => unhandled.push(reason));
g.URL.createObjectURL = () => "blob:local";
// 上传：表单体不经假站点的 JSON 记录；每次调用按 uploadReply 回（断网就抛 TypeError，同浏览器）
const siteFetch = g.fetch;
const uploads: string[] = [];
let uploadReply: (name: string) => Promise<any> = async () => { throw new TypeError("Failed to fetch"); };
let n = 0;
const stored = (name: string) => ok({ fileId: `file_${++n}`, filename: name, mountPath: `/mnt/session/uploads/file_${n}/${name}`, size_bytes: 1 });
g.fetch = async (url: string, init: any = {}) => {
  if (String(url).endsWith(".upload")) {
    const name = (init.body.get("file") as File).name;
    uploads.push(name);
    return uploadReply(name);
  }
  return siteFetch(url, init);
};
const posted = () => g.calls.filter((c: any) => c.method === "send").map((c: any) => c.body.text);
const textOf = (m: any) => m.content.filter((p: any) => p.type === "text").map((p: any) => p.text).join("\n").trim();
const S = "sesn_att1";
site.log.length = 0;
getSessionController(S).attach();
await g.sleep(30);

// 同 runtime-provider.tsx 的 onNew：附件先过 filesToSend，再发出；都经 attemptSend
let core: any;
const composer = {
  getState: () => ({ text: core.composer.text }),
  setText: (text: string) => core.composer.setText(text),
  addAttachment: (a: any) => core.composer.addAttachment(a),
};
const store: any = {
  messages: [], isRunning: false, convertMessage: (m: any) => m,
  adapters: { attachments: createAttachmentAdapter(async () => S) },
  onNew: async (message: any) => {
    const text = textOf(message);
    await attemptSend("thread-att", { composer, text, attachments: message.attachments }, async (sent) => {
      const files = filesToSend(message.attachments);
      await getSessionController(S).sendMessage(text, files);
      sent();
    });
  },
};
core = new ExternalStoreThreadRuntimeCore({ getModelContext: () => ({}), subscribe: () => () => {} } as any, store);
const shown = () => core.composer.attachments.map((a: any) => [a.name, a.status.type]);

// ① 断网时选的图：上传失败，带红色感叹号
const photo = new File(["x"], "bill.jpg", { type: "image/jpeg" });
await core.composer.addAttachment(photo);
assert.deepEqual(shown(), [["bill.jpg", "incomplete"]]);
// 仍断网时点发送：先重传，仍没传上 → 不发；原因一行，字和附件都回到输入框（原文件放回去再传，仍断网就又标出错）
core.composer.setText("ดูบิลนี้");
await core.composer.send();
await g.sleep(30);
assert.deepEqual(uploads, ["bill.jpg", "bill.jpg", "bill.jpg"]);  // 选中、发送时重传、放回输入框再传
assert.deepEqual(posted(), []);                                     // 没有不带附件就发出去
assert.equal(sendFailureOf("thread-att"), "出了点问题，请重试。");
assert.equal(core.composer.text, "ดูบิลนี้");
assert.deepEqual(shown(), [["bill.jpg", "incomplete"]]);
assert.ok(!getSessionController(S).getSnapshot().messages.some((m: any) => m.role === "user"));

// ② 联网后再点发送：重传成功，带着它发出；原因清掉
uploadReply = async (name) => stored(name);
await core.composer.send();
await g.sleep(30);
assert.equal(uploads.length, 4);
assert.equal(posted().length, 1);
assert.match(posted()[0], /ดูบิลนี้/);
assert.match(posted()[0], /\/mnt\/session\/uploads\/file_1\/bill\.jpg/);
assert.equal(sendFailureOf("thread-att"), "");
assert.equal(core.composer.text, "");
assert.deepEqual(shown(), []);

// ③ 上传中（限速）点了发送，这时断网：重传一次传上了就带着发，输入框里不冒出一个出错的旧项
let drop: () => void = () => {};
uploadReply = () => new Promise((_resolve, reject) => { drop = () => reject(new TypeError("Failed to fetch")); });
const slow = core.composer.addAttachment(new File(["y"], "slip.pdf", { type: "application/pdf" }));
await g.sleep(5);
assert.deepEqual(shown(), [["slip.pdf", "running"]]);
core.composer.setText("ดูสลิป");
const sending = core.composer.send();
await g.sleep(5);
uploadReply = async (name) => stored(name);
drop();
await sending;
await slow;
await g.sleep(30);
assert.equal(posted().length, 2);
assert.match(posted()[1], /\/mnt\/session\/uploads\/file_2\/slip\.pdf/);
assert.deepEqual(shown(), []);
assert.equal(sendFailureOf("thread-att"), "");

// ④ 同上，但重传也没传上：同 ①，不发、说原因、字和附件回到输入框
uploadReply = () => new Promise((_resolve, reject) => { drop = () => reject(new TypeError("Failed to fetch")); });
const slow2 = core.composer.addAttachment(new File(["z"], "receipt.png", { type: "image/png" }));
await g.sleep(5);
core.composer.setText("ดูใบเสร็จ");
const sending2 = core.composer.send();
await g.sleep(5);
uploadReply = async () => { throw new TypeError("Failed to fetch"); };
drop();
await sending2;
await slow2;
await g.sleep(30);
assert.equal(posted().length, 2);
assert.equal(sendFailureOf("thread-att"), "出了点问题，请重试。");
assert.equal(core.composer.text, "ดูใบเสร็จ");
assert.deepEqual(shown(), [["receipt.png", "incomplete"]]);        // 只有一项，不重复

assert.deepEqual(unhandled, []);                                    // 一个未捕获的错误都没有
g.fetch = siteFetch;
""")

    def test_a_failed_stop_says_why_and_can_be_pressed_again(self):
        # 2026-10-17 第 9 轮第 16 条：回复中途断网后按停止，停止请求失败，控制台未捕获的「TypeError: Failed to fetch」，页面一直
        # 「思考中」+ 停止钮、没有提示。现在：接住，输入框上方同一行原因（通用句），停止钮还在、再点再试；字不放回输入框（已发出）；
        # 这一轮自己结束了（联网后回复给完）原因就清掉，发送没成功的原因不跟着清。
        self.run_page(r"""
import { ExternalStoreThreadRuntimeCore } from "@assistant-ui/core/internal";
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { attemptStop, clearStopFailure, sendFailed, sendFailureOf } from "@/lib/managed-agents/send-failure";
import { registerStop, stopRun } from "@/lib/managed-agents/stop-run";
import { isBusy } from "@/lib/managed-agents/reducer";
---
const unhandled: unknown[] = [];
process.on("unhandledRejection", (reason) => unhandled.push(reason));
const respond = g.respond;
let offline = true;
g.respond = async (method: string, args: any, body: any) => {
  if (offline) throw new TypeError("Failed to fetch");
  if (method === "interrupt") { site.engine = "idle"; return ok(); }
  return respond(method, args, body);
};
const S = "sesn_stop1";
site.log.length = 0;
site.engine = "running";
site.log.push(ev("u1", "user.message", 1, { content: text("summarise the bank statement") }), ev("r1", "session.status_running", 2),
  ev("m1", "agent.message", 3, { content: text("Reading page 1") }));
offline = false;
const c = getSessionController(S);
c.attach();
await g.sleep(30);
assert.equal(isBusy(c.getSnapshot()), true);

// 同 runtime-provider.tsx 登记的停止：经 attemptStop；停止键、Esc 调 stopRun（不经组件库的 cancelRun，组件库没有取消能力）
registerStop("thread-stop", () => attemptStop("thread-stop", () => c.interrupt()));
const store: any = { messages: [], isRunning: true, convertMessage: (m: any) => m, onNew: async () => {} };
const core = new ExternalStoreThreadRuntimeCore({ getModelContext: () => ({}), subscribe: () => () => {} } as any, store);
assert.equal(core.capabilities.cancel, false);

// ① 断网：停止请求失败 → 原因一行（通用句），还在跑（停止钮还在），字不放回
offline = true;
await stopRun("thread-stop");
await g.sleep(30);
assert.equal(sendFailureOf("thread-stop"), "出了点问题，请重试。");
assert.equal(isBusy(c.getSnapshot()), true);
assert.equal(core.composer.text, "");
// ② 联网后再点停止：打断成功，原因清掉
offline = false;
void stopRun("thread-stop");
await g.sleep(5);
assert.equal(sendFailureOf("thread-stop"), "");
assert.deepEqual(g.calls.filter((x: any) => x.method === "interrupt").map((x: any) => x.body.session_id), [S, S]);  // 断网那次、再点这次
// ③ 这一轮不在跑了：停止没成功的原因清掉；发送没成功的原因留到再发送
offline = true;
await attemptStop("thread-stop", () => c.interrupt());
assert.equal(sendFailureOf("thread-stop"), "出了点问题，请重试。");
clearStopFailure("thread-stop");
assert.equal(sendFailureOf("thread-stop"), "");
sendFailed("thread-stop", new TypeError("Failed to fetch"));
clearStopFailure("thread-stop");
assert.equal(sendFailureOf("thread-stop"), "出了点问题，请重试。");
// ④ 原因按对话分开记（第 10 轮审查）：别的对话停止没成功不盖掉这段的发送失败；本段点停止也不先清掉发送失败的原因
sendFailed("thread-b", new TypeError("Failed to fetch"));
clearStopFailure("thread-stop");
await attemptStop("thread-other", () => c.interrupt());
assert.equal(sendFailureOf("thread-b"), "出了点问题，请重试。");
assert.equal(sendFailureOf("thread-other"), "出了点问题，请重试。");
clearStopFailure("thread-other");
assert.equal(sendFailureOf("thread-other"), "");
assert.equal(sendFailureOf("thread-b"), "出了点问题，请重试。");
await attemptStop("thread-stop", () => c.interrupt());   // 停止又没成功、随后这一轮结束：发送失败的原因还在
clearStopFailure("thread-stop");
assert.equal(sendFailureOf("thread-stop"), "出了点问题，请重试。");
offline = false;

await g.sleep(30);
assert.deepEqual(unhandled, []);                                    // 一个未捕获的错误都没有
g.respond = respond;
""")

    # 第 11 轮 F39（lab 9798f54e，电脑内置浏览器）：发出 1 秒后断网（回声还没到）、点停止，字被放回输入框；联网后回复照常给完，
    # 对话最后多出一条同样的用户气泡（服务端只有一条，刷新后只剩一条）；再点停止控制台未捕获「Entry not available in the store」。
    # 原因（复刻页按真实顺序复现：断网期间回声没推到，联网后实时通道先推来回复，回声随补拉晚到）：
    #   1) 停止经组件库的 cancelRun：回复还没出时它把最后那条用户消息从自己的消息仓库里删掉、字放回输入框。我们的对话是外部数据，
    #      那条消息我们这边还在、服务端也收下了 —— 字放回再按发送就重复；组件库仓库与我们的数据从此对不上，之后按编号取那条就抛错。
    #   2) 补拉失败（断网）到补齐之间推来的事件直接并：回复先并，晚到的回声排在回复后面；回声又只认「最后一条」乐观气泡，对不上就
    #      另加一条。
    # 现在：停止不经组件库（不给 onCancel，停止键与 Esc 走 stopRun），消息留在对话里、字不放回；刚发出还在路上的，等它有了结果
    # 再打断；补拉失败、实时通道重连之后推来的先压着，等补齐再按先后并；回声认本页还没对上的那个乐观气泡，不论它后面已有什么；
    # 发出请求断在路上而服务端其实收下了（回声晚到）：放回输入框的字与附件撤掉、原因清掉，不再发第二遍。
    def test_f39_offline_stop_keeps_the_message_and_one_bubble_after_reconnect(self):
        self.run_page(r"""
import { ExternalStoreThreadRuntimeCore } from "@assistant-ui/core/internal";
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { attemptSend, attemptStop, clearStopFailure, sendFailureOf, sendLanded } from "@/lib/managed-agents/send-failure";
import { registerStop, stopRun } from "@/lib/managed-agents/stop-run";
import { isBusy } from "@/lib/managed-agents/reducer";
---
const unhandled: unknown[] = [];
process.on("unhandledRejection", (reason) => unhandled.push(reason));
const S = "sesn_f39";
const T = "thread-f39";
site.log.length = 0;
site.log.push(ev("u0", "user.message", 1, { content: text("hi") }), ev("r0", "session.status_running", 2),
  ev("m0", "agent.message", 3, { content: text("hello") }), ev("i0", "session.status_idle", 4, { stop_reason: { type: "end_turn" } }));
const c = getSessionController(S);
c.attach();
await g.sleep(30);

// 同 runtime-provider.tsx：发送经 attemptSend，停止登记给 stopRun，晚到的回声交给 sendLanded；组件库不给 onCancel
let core: any;
const composer = { getState: () => ({ text: core.composer.text, attachments: core.composer.attachments }), setText: (t: string) => core.composer.setText(t),
  addAttachment: (a: any) => core.composer.addAttachment(a), clearAttachments: () => core.composer.clearAttachments() };
const textOf = (m: any) => m.content.filter((p: any) => p.type === "text").map((p: any) => p.text).join("\n").trim();
const adapter = () => ({
  messages: c.getSnapshot().messages, isRunning: isBusy(c.getSnapshot()), convertMessage: (m: any) => m,
  onNew: async (message: any) => {
    const typed = textOf(message);
    await attemptSend(T, { composer, text: typed, attachments: message.attachments }, async (sent) => { await c.sendMessage(typed, []); sent(); });
  },
});
core = new ExternalStoreThreadRuntimeCore({ getModelContext: () => ({}), subscribe: () => () => {} } as any, adapter() as any);
c.subscribe(() => core.__internal_setAdapter(adapter() as any));
registerStop(T, () => attemptStop(T, () => c.interrupt()));
c.onDelivered((typed) => sendLanded(T, typed));
assert.equal(core.capabilities.cancel, false);   // 组件库自己的停止（cancelRun）用不上
const roles = () => c.getSnapshot().messages.map((m: any) => `${m.role}:${textOf(m)}`);
// 组件库眼里的对话（去掉它自己补的「思考中」占位）与我们的数据一致，每一条都取得到（取不到就是 Entry not available）
const libraryAgrees = () => {
  const mine = c.getSnapshot().messages.map((m: any) => m.id);
  const lib = core.messages.filter((m: any) => !m.metadata?.isOptimistic).map((m: any) => m.id);
  assert.deepEqual(lib, mine);
  for (const id of mine) assert.ok(core.getMessageById(id), id);
};

// 发出：服务端几秒后才收下，期间断网
let offline = false;
let releaseSend: () => void = () => {};
const respond = g.respond;
g.respond = async (method: string, args: any, body: any) => {
  if (method === "send") {
    await new Promise<void>((resolve) => { releaseSend = resolve; });
    // 服务端收下了（回声只在事件记录里：断网期间没推到；正文按服务端 contracts.message_text 去掉首尾空白），回应却断在路上
    site.log.push(ev("echoF", "user.message", 10, { content: text(String(body.text).trim()) }), ev("r1", "session.status_running", 11));
    site.engine = "running";
    throw new TypeError("Failed to fetch");
  }
  if (offline) throw new TypeError("Failed to fetch");
  if (method === "interrupt") {
    site.engine = "idle";
    site.log.push(ev("int1", "user.interrupt", 30), ev("i1", "session.status_idle", 31, { stop_reason: { type: "end_turn" } }));
    return ok();
  }
  return respond(method, args, body);
};
core.composer.setText("F39 list ten points");
void core.composer.send();
await g.sleep(10);
assert.deepEqual(roles(), ["user:hi", "assistant:hello", "user:F39 list ten points"]);
assert.equal(core.composer.text, "");
offline = true;
g.socket.drop();                         // 断网：实时通道随之断开（再连上时开缺口）
c.refreshStatus();                       // 补拉也失败
await g.sleep(10);

// ① 断网点停止：消息留在对话里，字不放回输入框；打断等发出的那条有了结果（服务端还没收下就打断会落空）
const stopping = stopRun(T);
await g.sleep(10);
assert.equal(g.calls.filter((x: any) => x.method === "interrupt").length, 0);
assert.equal(core.composer.text, "");
assert.deepEqual(roles(), ["user:hi", "assistant:hello", "user:F39 list ten points"]);
libraryAgrees();
// 发出请求断在路上（看不出服务端收没收下）：先当没发出，字放回、写原因；打断随后也因断网没成功
releaseSend();
await stopping;
await g.sleep(10);
assert.equal(core.composer.text, "F39 list ten points");
assert.equal(sendFailureOf(T), "出了点问题，请重试。");
assert.deepEqual(roles(), ["user:hi", "assistant:hello"]);
libraryAgrees();

// ② 联网：实时通道先推来回复（回声已在事件记录里、没推到），补拉晚一步；推来的压到补齐以后，顺序同服务端
offline = false;
let slowEvents = true;
const online = g.respond;
g.respond = async (method: string, args: any, body: any) => {
  if (method === "events" && slowEvents) await g.sleep(30);
  return online(method, args, body);
};
g.socket.back();
g.fire("online");
await g.sleep(5);
push(S, ev("m1", "agent.message", 12, { content: text("1. File by the 15th") }));
push(S, ev("m2", "agent.message", 13, { content: text("2. Keep the invoices") }));
await g.sleep(120);
slowEvents = false;
assert.deepEqual(roles(), ["user:hi", "assistant:hello", "user:F39 list ten points", "assistant:1. File by the 15th\n2. Keep the invoices"]);
assert.equal(c.getSnapshot().messages.filter((m: any) => m.role === "user" && textOf(m) === "F39 list ten points").length, 1);
// 服务端其实收下了：放回的字撤掉、发送没成功的原因清掉，不会再发第二遍（还显示的是断网时停止没成功那一句：这一轮还在跑）
assert.equal(core.composer.text, "");
clearStopFailure(T);
assert.equal(sendFailureOf(T), "");
libraryAgrees();

// ③ 联网后再点停止：打断照常生效，没有未捕获的错误，组件库与我们的数据仍一致
await stopRun(T);
await g.sleep(30);
assert.equal(isBusy(c.getSnapshot()), false);
assert.equal(g.calls.filter((x: any) => x.method === "interrupt").length, 2);
libraryAgrees();
assert.equal(core.composer.text, "");

// ④ 在线时发出后马上点停止：等服务端收下这条再打断（不落空），消息留着、字不放回
let release2: () => void = () => {};
g.respond = async (method: string, args: any, body: any) => {
  if (method === "send") {
    await new Promise<void>((resolve) => { release2 = resolve; });
    site.log.push(ev("echo2", "user.message", 40, { content: text(body.text) }), ev("r2", "session.status_running", 41));
    site.engine = "running";
    return ok();
  }
  return online(method, args, body);
};
core.composer.setText("again");
void core.composer.send();
await g.sleep(10);
const before = g.calls.length;
const stop2 = stopRun(T);
await g.sleep(10);
assert.ok(!g.calls.slice(before).some((x: any) => x.method === "interrupt"));
release2();
await stop2;
const order = g.calls.slice(before).map((x: any) => x.method).filter((m: string) => m === "send" || m === "interrupt");
assert.deepEqual(order, ["interrupt"]);       // send 在停止之前已发出；打断在它回来之后才发
assert.equal(core.composer.text, "");
await g.sleep(30);
assert.equal(c.getSnapshot().messages.filter((m: any) => m.role === "user" && textOf(m) === "again").length, 1);
libraryAgrees();

await g.sleep(30);
assert.deepEqual(unhandled, []);
g.respond = respond;
""")

    def test_a_failed_backfill_does_not_hold_back_live_pushes(self):
        # 审查（第 11 轮 F39 修复）：实时通道一直连着、只是一次低频补拉失败（网关 502、列事件接口一时出错），推来的回复与逐字预览
        # 不压：走的是同一条活着的连接，不会漏推。以前补拉失败就开缺口，回复要压到下一次补拉成功，接口一直坏就一直不出
        self.run_page(r"""
import { getSessionController } from "@/lib/managed-agents/session-controller";
---
const S = "sesn_502";
site.log.length = 0;
site.log.push(ev("u0", "user.message", 1, { content: text("hi") }), ev("r0", "session.status_running", 2));
site.engine = "running";
const c = getSessionController(S);
c.attach();
await g.sleep(30);
const respond = g.respond;
let failing = true;
g.respond = async (method: string, args: any, body: any) => {
  if (method === "events" && failing) return { ok: false, status: 502, json: async () => ({}) };
  return respond(method, args, body);
};
const before = eventsCalls(S).length;
c.refreshStatus();
await g.sleep(20);
assert.ok(eventsCalls(S).length > before);                                   // 这次补拉发出了、失败了
const parts = () => c.getSnapshot().messages.flatMap((m: any) => m.content).filter((p: any) => p.type === "text").map((p: any) => p.text);
preview(S, { type: "event_start", event: { id: "m1", type: "agent.message" } });
preview(S, { type: "event_delta", event_id: "m1", delta: { type: "content_delta", content: { type: "text", text: "Fil" } } });
await g.sleep(5);
assert.deepEqual(parts(), ["hi", "Fil"]);                                    // 逐字预览当场出
push(S, ev("m1", "agent.message", 3, { content: text("File by the 15th") }));
await g.sleep(5);
assert.deepEqual(parts(), ["hi", "File by the 15th"]);                       // 回复当场出，不等下一次补拉
failing = false;
g.respond = respond;
""")

    def test_an_attachment_only_send_cut_off_in_transit_is_taken_back_when_its_echo_lands(self):
        # 审查（第 11 轮 F39 修复）：只带附件、不打字发出，请求断在路上而服务端其实收下了。拼出的字以空行开头（附件说明），服务端存的
        # 去掉了首尾空白，晚到的回声与撤回时记下的对不上，放回的附件与原因一直挂着，再按发送同一份文件发第二条。撤回记下的与回声都按
        # 服务端口径去掉首尾空白再比
        self.run_page(r"""
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { attemptSend, sendFailureOf, sendLanded } from "@/lib/managed-agents/send-failure";
---
const S = "sesn_att_late";
const T = "thread-att-late";
site.log.length = 0;
site.log.push(ev("u0", "user.message", 1, { content: text("hi") }), ev("i0", "session.status_idle", 2, { stop_reason: { type: "end_turn" } }));
const c = getSessionController(S);
c.attach();
await g.sleep(30);
const landed: string[] = [];
c.onDelivered((typed) => { landed.push(typed); sendLanded(T, typed); });
const state: any = { text: "", attachments: [] as any[] };
const box = { getState: () => state, setText: (t: string) => { state.text = t; }, addAttachment: async (a: any) => { state.attachments.push({ id: a.id }); },
  clearAttachments: () => { state.attachments = []; } };
const respond = g.respond;
let release: () => void = () => {};
g.respond = async (method: string, args: any, body: any) => {
  if (method === "send") {
    await new Promise<void>((resolve) => { release = resolve; });
    // 服务端照 contracts.message_text 去掉首尾空白后存下（回声随补拉晚到），回应断在路上
    site.log.push(ev("echoA", "user.message", 10, { content: text(String(body.text).trim()) }), ev("r1", "session.status_running", 11));
    site.engine = "running";
    throw new TypeError("Failed to fetch");
  }
  return respond(method, args, body);
};
const att = { id: "/mnt/session/uploads/r.pdf", type: "document", name: "r.pdf", contentType: "application/pdf", content: [], status: { type: "complete" } };
const sending = attemptSend(T, { composer: box, text: "", attachments: [att as any] },
  async (sent) => { await c.sendMessage("", [{ filename: "r.pdf", mountPath: "/mnt/session/uploads/r.pdf" }]); sent(); });
await g.sleep(10);
assert.ok(g.calls.some((x: any) => x.method === "send" && x.body.text.startsWith("\n\n[Attached file")));
release();
assert.equal(await sending, false);
await g.sleep(5);
assert.deepEqual(state.attachments.map((a: any) => a.id), ["/mnt/session/uploads/r.pdf"]);   // 看不出收没收下：先放回不丢
assert.equal(sendFailureOf(T), "出了点问题，请重试。");
// 回声随补拉晚到：放回的附件撤掉、原因清掉，对话里只有这一条
c.refreshStatus();
await g.sleep(30);
assert.deepEqual(landed, [""]);
assert.equal(state.attachments.length, 0);
assert.equal(sendFailureOf(T), "");
assert.equal(c.getSnapshot().messages.filter((m: any) => m.role === "user").length, 2);
g.respond = respond;
""")

    def test_the_top_line_stays_while_the_network_is_down_past_the_stall(self):
        # 第 11 轮重测 E 段（g34）：断网超过 30 秒，第 5–25 秒顶部「正在重新连接…」，第 30 秒起这一行自己消失、网络其实还断着。
        # 原因：这一轮卡住（stalled）时顶部那一行按设计不说（让给状态行的「连接中断，正在重连…」），看门重开尾巴本身没清掉出错状态。
        # 现在顶部只看连接：补拉仍失败、推送通道仍断时一直在，联网、补拉成功才收起；状态行照旧说「连接中断」
        self.run_page(r"""
import { SessionController, getSessionController } from "@/lib/managed-agents/session-controller";
import { showsReconnecting, turnActivity } from "@/lib/managed-agents/turn";
---
SessionController.stallMs = 80;
const S = "sesn_offline30";
site.engine = "running";
site.echo = false;
const c = getSessionController(S);
c.attach();
await g.sleep(30);
await c.sendMessage("hello", []);
push(S, ev("u1", "user.message", 1, { content: text("hello") }));
push(S, ev("r1", "session.status_running", 2));
await g.sleep(10);
assert.equal(showsReconnecting(c.getSnapshot()), false);
// 断网：推送通道断开，补拉一律失败（fetch 抛 TypeError）
let offline = true;
const respond = g.respond;
g.respond = async (method: string, args: any, body: any) => {
  if (offline) throw new TypeError("Failed to fetch");
  return respond(method, args, body);
};
g.socket.drop();
c.refreshStatus();
const seen: string[] = [];
let stalledSeen = false;
for (let i = 0; i < 30; i++) {
  await g.sleep(10);
  const s: any = c.getSnapshot();
  if (s.stalled) stalledSeen = true;
  seen.push(`${showsReconnecting(s) ? 1 : 0}${s.stalled ? "s" : ""}`);
}
assert.ok(stalledSeen, "the watchdog fired and reopened the tail: " + seen.join(" "));
const first = seen.findIndex((x) => x.startsWith("1"));
assert.ok(first >= 0, seen.join(" "));
assert.ok(seen.slice(first).every((x) => x.startsWith("1")), "the top line stays while offline: " + seen.join(" "));
assert.equal(turnActivity(c.getSnapshot()).kind, "reconnecting");   // 状态行照旧说连接中断
// 联网：通道连上、补拉成功，两处都收起
offline = false;
g.socket.back();
g.fire("online");
await g.sleep(150);
const back: any = c.getSnapshot();
assert.equal(showsReconnecting(back), false);
assert.equal(back.stalled, false);
g.respond = respond;
""")

    def test_an_attachment_only_send_cut_off_puts_the_tile_back_in_the_real_composer(self):
        # 第 11 轮重测 E 段（e26c）：只带附件的消息发送断在路上（服务端其实收下），测试窗口 1.3 秒那一次读到原因、没读到缩略图，0.3 秒后
        # 回声到、原因消失。用组件库自己的输入框与本站的附件适配器走一遍：附件按原编号放回（传好了的），原因写出；回声到了放回的附件与
        # 原因一起撤，不会发第二遍。缩略图要比原因晚一次渲染才画出来（组件库附件列表多一轮），1.3 秒那次读数落在两次渲染之间、紧接着
        # 回声就把它撤了——这里守的是数据：放回确实发生、回声到了确实撤掉
        self.run_page(r"""
import { ExternalStoreThreadRuntimeCore } from "@assistant-ui/core/internal";
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { attemptSend, sendFailureOf, sendLanded } from "@/lib/managed-agents/send-failure";
import { createAttachmentAdapter, filesToSend } from "@/lib/managed-agents/attachments";
import { isBusy } from "@/lib/managed-agents/reducer";
---
const S = "sesn_att_real";
const T = "thread-att-real";
site.log.length = 0;
site.log.push(ev("u0", "user.message", 1, { content: text("hi") }), ev("i0", "session.status_idle", 2, { stop_reason: { type: "end_turn" } }));
const c = getSessionController(S);
c.attach();
await g.sleep(30);
(globalThis as any).URL.createObjectURL = () => "blob:x";
const fetch0 = g.fetch;
g.fetch = (url: string, init: any = {}) => fetch0(url, init.body instanceof FormData ? { ...init, body: undefined } : init);
let core: any;
const composer = { getState: () => ({ text: core.composer.text, attachments: core.composer.attachments }), setText: (t: string) => core.composer.setText(t),
  addAttachment: (a: any) => core.composer.addAttachment(a), clearAttachments: () => core.composer.clearAttachments() };
const textOf = (m: any) => m.content.filter((p: any) => p.type === "text").map((p: any) => p.text).join("\n").trim();
const attachments = createAttachmentAdapter(async () => S);
const adapter = () => ({
  messages: c.getSnapshot().messages, isRunning: isBusy(c.getSnapshot()), convertMessage: (m: any) => m, adapters: { attachments },
  onNew: async (message: any) => {
    const typed = textOf(message);
    await attemptSend(T, { composer, text: typed, attachments: message.attachments },
      async (sent) => { await c.sendMessage(typed, filesToSend(message.attachments)); sent(); });
  },
});
core = new ExternalStoreThreadRuntimeCore({ getModelContext: () => ({}), subscribe: () => () => {} } as any, adapter() as any);
c.subscribe(() => core.__internal_setAdapter(adapter() as any));
c.onDelivered((typed) => sendLanded(T, typed));
const respond = g.respond;
let release: () => void = () => {};
g.respond = async (method: string, args: any, body: any) => {
  if (method === "upload") return ok({ fileId: "f1", filename: "e26.png", mountPath: "/mnt/session/uploads/f1/e26.png", size_bytes: 100 });
  if (method === "send") {
    await new Promise<void>((resolve) => { release = resolve; });
    // 服务端收下了（回声与开跑只在事件记录里，稍后随补拉 / 推送到），回应断在路上
    site.log.push(ev("echoA", "user.message", 10, { content: text(String(body.text).trim()) }), ev("r1", "session.status_running", 11));
    site.engine = "running";
    throw new TypeError("Failed to fetch");
  }
  return respond(method, args, body);
};
await core.composer.addAttachment(new File(["png"], "e26.png", { type: "image/png" }));
const id = core.composer.attachments[0].id;
assert.equal(core.composer.attachments[0].status.type, "requires-action");
void core.composer.send();
await g.sleep(10);
assert.equal(core.composer.attachments.length, 0);                     // 组件库按下发送就清空了输入框
assert.equal(g.calls.filter((x: any) => x.method === "send").length, 1);
release();
await g.sleep(10);
// 看不出收没收下：原样放回（同一编号、传好了的），原因写出
assert.deepEqual(core.composer.attachments.map((a: any) => [a.id, a.status.type]), [[id, "complete"]]);
assert.equal(sendFailureOf(T), "出了点问题，请重试。");
// 回声随补拉到了：放回的附件与原因一起撤，不再发第二遍
c.refreshStatus();
await g.sleep(30);
assert.equal(core.composer.attachments.length, 0);
assert.equal(sendFailureOf(T), "");
assert.equal(g.calls.filter((x: any) => x.method === "send").length, 1);
g.respond = respond;
g.fetch = fetch0;
""")

    def test_a_reconnected_push_channel_waits_for_the_backfill(self):
        # 实时通道断开又连上（补拉一直正常）：断开期间的事件只在事件记录里，连上后先推来的要等补齐再并（缺口，session-controller 文件头）
        self.run_page(r"""
import { getSessionController } from "@/lib/managed-agents/session-controller";
---
const S = "sesn_gap2";
site.log.length = 0;
site.log.push(ev("u0", "user.message", 1, { content: text("hi") }), ev("r0", "session.status_running", 2));
site.engine = "running";
const c = getSessionController(S);
c.attach();
await g.sleep(30);
const respond = g.respond;
let slow = false;
g.respond = async (method: string, args: any, body: any) => {
  if (method === "events" && slow) await g.sleep(30);
  return respond(method, args, body);
};
g.socket.drop();
site.log.push(ev("m1", "agent.message", 3, { content: text("first") }));   // 断开期间：没推到
slow = true;
g.socket.back();
push(S, ev("m2", "agent.message", 4, { content: text("second") }));       // 连上后先推来
await g.sleep(5);
const parts = () => c.getSnapshot().messages.flatMap((m: any) => m.content).filter((p: any) => p.type === "text").map((p: any) => p.text);
assert.deepEqual(parts(), ["hi"]);                                          // 压着，等补齐
await g.sleep(120);
slow = false;
assert.deepEqual(parts(), ["hi", "first", "second"]);
g.respond = respond;
""")

    def test_a_backfill_that_could_not_read_the_engine_does_not_close_the_gap(self):
        # 第 11 轮合并交叉审查 1：实时通道断开又连上（缺口开着），收口的那次补拉碰上引擎「列出事件」超时——服务端回 200、只带本站提醒
        # （sessions.events 的 except 分支）。以前页面分不出「没有新事件」和「这次没读到引擎」，拿这页收了口，压着的推送先于缺口里的
        # 引擎事件交出，顺序永久错乱。现在服务端标 partial，这页不收口、不交压着的；下一页读到引擎再按顺序交
        self.run_page(r"""
import { getSessionController } from "@/lib/managed-agents/session-controller";
---
const S = "sesn_partial";
site.log.length = 0;
site.log.push(ev("u0", "user.message", 1, { content: text("hi") }), ev("r0", "session.status_running", 2));
site.engine = "running";
const c = getSessionController(S);
c.attach();
await g.sleep(30);
const respond = g.respond;
let partial = 0;
g.respond = async (method: string, args: any, body: any) => {
  if (method === "events" && args.after && partial > 0) {
    partial--;
    await g.sleep(20);
    return ok({ events: [], full: false, partial: true });   // 引擎这次没读到：只有提醒（这里没有）
  }
  return respond(method, args, body);
};
g.socket.drop();
site.log.push(ev("m1", "agent.message", 3, { content: text("first") }));   // 断开期间：没推到，只在事件记录里
partial = 1;
const before = eventsCalls(S).length;
g.socket.back();                                                           // 重连：开缺口，马上补拉（这次读不到引擎）
await g.sleep(5);
push(S, ev("m2", "agent.message", 4, { content: text("second") }));       // 连上后先推来
const parts = () => c.getSnapshot().messages.flatMap((m: any) => m.content).filter((p: any) => p.type === "text").map((p: any) => p.text);
await g.sleep(40);
assert.ok(eventsCalls(S).length > before);                                 // 那次补拉回来了（partial）
assert.deepEqual(parts(), ["hi"]);                                         // 没读到引擎：不收口，second 仍压着
g.fire("online");                                                          // 下一次补拉（不等 SLOW_MS）读到引擎
await g.sleep(30);
assert.deepEqual(parts(), ["hi", "first", "second"]);                      // first 在前
g.respond = respond;
""")

    def test_a_tail_opened_while_the_push_channel_is_down_treats_the_first_connect_as_a_reconnect(self):
        # 第 11 轮合并交叉审查 2：实时通道断开期间新建的尾巴（看门重开、切走切回恰好断网），之后第一次连上以前不当重连：不开缺口、
        # 不补拉，断开期间的事件排在连上后推来的后面
        self.run_page(r"""
import { PollingSource } from "@/lib/managed-agents/poll-source";
import { listen } from "@/lib/managed-agents/realtime";
---
const S = "sesn_newtail";
site.log.length = 0;
site.log.push(ev("u0", "user.message", 1, { content: text("hi") }), ev("r0", "session.status_running", 2));
site.engine = "running";
listen();
g.socket.drop();
const src = new PollingSource(S);                                          // 建起来时通道不在线
const got: string[] = [];
src.onmessage = (msg) => { const e = JSON.parse(msg.data).event; if (e.type === "agent.message") got.push(e.id); };
await g.sleep(30);
site.log.push(ev("m1", "agent.message", 3, { content: text("first") }));   // 断开期间：没推到
const respond = g.respond;
g.respond = async (method: string, args: any, body: any) => {
  if (method === "events") await g.sleep(30);
  return respond(method, args, body);
};
g.socket.back();                                                           // 第一次连上：按重连开缺口、马上补拉
push(S, ev("m2", "agent.message", 4, { content: text("second") }));
await g.sleep(5);
assert.deepEqual(got, []);                                                 // 压着，等补齐
await g.sleep(80);
assert.deepEqual(got, ["m1", "m2"]);
src.close();
g.respond = respond;
""")

    def test_a_reconnect_while_the_gap_is_open_reopens_it_for_the_backfill_in_flight(self):
        # 第 11 轮合并交叉审查 2：缺口已开着（补拉失败时通道其实已断、还没报断开）又连上：在途那次补拉发出时推送还不通，它回来
        # 不算补齐。以前这里只补拉、不换缺口序号，在途那次回来照样收口，连上后推来的先于断开期间的事件交出
        self.run_page(r"""
import { PollingSource } from "@/lib/managed-agents/poll-source";
import { listen } from "@/lib/managed-agents/realtime";
---
const S = "sesn_gapopen";
site.log.length = 0;
site.log.push(ev("u0", "user.message", 1, { content: text("hi") }), ev("r0", "session.status_running", 2));
site.engine = "running";
listen();
const src = new PollingSource(S);                                          // 建起来时通道在线
const got: string[] = [];
src.onmessage = (msg) => { const e = JSON.parse(msg.data).event; if (e.type === "agent.message") got.push(e.id); };
await g.sleep(30);
const respond = g.respond;
let mode = "fail";
g.respond = async (method: string, args: any, body: any) => {
  if (method === "events" && mode === "fail") return { ok: false, status: 502, json: async () => ({}) };
  if (method === "events" && mode === "slow") {
    const page = await respond(method, args, body);                        // 按发出那一刻的事件记录回
    await g.sleep(30);
    return page;
  }
  return respond(method, args, body);
};
g.socket.lose();                                                           // 通道其实已断、还没报断开
src.refresh();                                                             // 补拉失败、通道不在线：开缺口
await g.sleep(10);
mode = "slow";
src.refresh();                                                             // 在途：发出时 m1 还没有
await g.sleep(5);
site.log.push(ev("m1", "agent.message", 3, { content: text("first") }));   // 推送不通期间：只在事件记录里
g.socket.back();                                                           // 连上（没报过断开）
push(S, ev("m2", "agent.message", 4, { content: text("second") }));
await g.sleep(40);
assert.deepEqual(got, []);                                                 // 在途那次回来不收口，m2 仍压着
await g.sleep(60);
assert.deepEqual(got, ["m1", "m2"]);
src.close();
g.respond = respond;
""")

    def test_an_echo_pairs_with_its_bubble_wherever_later_events_landed(self):
        # 回声与回复谁先到取决于推送与补拉的先后：回声认本页还没对上回声的那个乐观气泡（最早的同字那个），不论它后面已经并进了什么；
        # 已对上的不再认；本页自己的乐观气泡（同字连发）不互认
        self.run_page(r"""
import { applyEvent, project } from "@/lib/managed-agents/reducer";
---
let s: any = project([ev("u0", "user.message", 1, { content: text("hi") }), ev("i0", "session.status_idle", 2, { stop_reason: { type: "end_turn" } })] as any);
s = applyEvent(s, { type: "user.message", id: "optimistic-1", content: text("ten points"), processed_at: at(3) } as any);
s = applyEvent(s, { type: "user.message", id: "optimistic-2", content: text("ten points"), processed_at: at(3) } as any);
assert.deepEqual(s.messages.map((m: any) => m.id), ["u0", "optimistic-1", "optimistic-2"]);   // 同字连发：两条都在
s = applyEvent(s, ev("m1", "agent.message", 4, { content: text("1. File by the 15th") }) as any);
s = applyEvent(s, ev("echo1", "user.message", 3.5, { content: text("ten points") }) as any);    // 回复之后才到
assert.deepEqual(s.messages.map((m: any) => `${m.id}:${m.role}`), ["u0:user", "optimistic-1:user", "optimistic-2:user", "asst-3:assistant"]);
assert.ok(s.echoedOptimistic.has("optimistic-1") && !s.echoedOptimistic.has("optimistic-2"));
s = applyEvent(s, ev("echo2", "user.message", 3.6, { content: text("ten points") }) as any);
assert.equal(s.messages.length, 4);
assert.ok(s.echoedOptimistic.has("optimistic-2"));
s = applyEvent(s, ev("u9", "user.message", 9, { content: text("ten points") }) as any);         // 都对上了：别处发的同字照常是新的一条
assert.deepEqual(s.messages.at(-1).id, "u9");
""")

    def test_a_late_echo_only_takes_back_what_was_put_back(self):
        # 放回输入框后用户改过：晚到的回声不动用户的字（只撤原样还在的那段）；附件原样都在才一起撤
        self.run_page(r"""
import { sendFailed, sendFailureOf, sendLanded } from "@/lib/managed-agents/send-failure";
---
const box = (text: string, ids: string[] = []) => {
  const state = { text, attachments: ids.map((id) => ({ id })) };
  return { state, getState: () => state, setText: (t: string) => { state.text = t; }, addAttachment: async (a: any) => { state.attachments.push({ id: a.id ?? "file" }); },
    clearAttachments: () => { state.attachments = []; } };
};
// 原样还在：撤掉、原因清掉
const a = box("");
sendFailed("t-a", new TypeError("Failed to fetch"), { composer: a, text: "hello" });
assert.equal(a.state.text, "hello");
sendLanded("t-a", "hello");
assert.equal(a.state.text, "");
assert.equal(sendFailureOf("t-a"), "");
// 等的时候又打了字（放回时接在后面）：只撤放回的那段
const b = box("more");
sendFailed("t-b", new TypeError("Failed to fetch"), { composer: b, text: "hello" });
assert.equal(b.state.text, "hello\nmore");
sendLanded("t-b", "hello");
assert.equal(b.state.text, "more");
// 用户改过：不动
const d = box("");
sendFailed("t-d", new TypeError("Failed to fetch"), { composer: d, text: "hello" });
d.state.text = "hello, edited";
sendLanded("t-d", "hello");
assert.equal(d.state.text, "hello, edited");
assert.equal(sendFailureOf("t-d"), "");
// 别的字的回声：不动
const e = box("");
sendFailed("t-e", new TypeError("Failed to fetch"), { composer: e, text: "hello" });
sendLanded("t-e", "other");
assert.equal(e.state.text, "hello");
assert.equal(sendFailureOf("t-e"), "出了点问题，请重试。");
// 附件：放回的原样都在才撤
const f = box("");
const att = { id: "/mnt/session/uploads/r.pdf", type: "document", name: "r.pdf", contentType: "application/pdf", content: [], status: { type: "complete" } };
sendFailed("t-f", new TypeError("Failed to fetch"), { composer: f, text: "see file", attachments: [att as any] });
await g.sleep(0);
assert.deepEqual(f.state.attachments.map((x: any) => x.id), ["/mnt/session/uploads/r.pdf"]);
sendLanded("t-f", "see file");
await g.sleep(0);
assert.deepEqual([f.state.text, f.state.attachments.length], ["", 0]);
""")

    def test_a6_a_pushed_tool_wait_fetches_the_card_at_once(self):
        # 第 11 轮重测 A6（手机 A14 的 CC 应用）：别的设备上发起的一轮，推来 agent.custom_tool_use 与「停下来等工具」后，卡片的问句与
        # 改动（run_tool）当场取，不等低频补拉；同一次调用只提醒一次（重复推来、读回状态、补拉带回都不再发）
        self.run_page(r"""
import { getSessionController } from "@/lib/managed-agents/session-controller";
---
const S = "sesn_a6";
site.log.push(ev("u0", "user.message", 1, { content: text("hi") }), ev("i0", "session.status_idle", 2, { stop_reason: { type: "end_turn" } }));
site.toolReply = async (id: string) => {
  site.runs[id] = "awaiting_approval";
  return { status: "awaiting_approval", ask: "Disable this task?", preview: [] };
};
const c = getSessionController(S);
c.attach();
await g.sleep(30);
const polls = eventsCalls(S).length;
push(S, ev("u1", "user.message", 3, { content: text("disable it") }));
push(S, ev("r1", "session.status_running", 4));
push(S, ev("t1", "agent.custom_tool_use", 5, { name: "cowork_set_folder_task_enabled", input: {} }));
await g.sleep(5);
assert.equal(toolCalls().length, 0);                                        // 还没停下来等
push(S, ev("p1", "session.status_idle", 6, { stop_reason: { type: "requires_action", event_ids: ["t1"] } }));
await g.sleep(10);
assert.deepEqual(toolCalls().map((x: any) => x.body.tool_use_id), ["t1"]);  // 当场一次
assert.equal(eventsCalls(S).length, polls);                                  // 补拉不是前提
assert.equal(toolPart(c.getSnapshot(), "t1").approval.ask, "Disable this task?");
assert.deepEqual(c.getSnapshot().pendingApprovals, ["t1"]);
// 同一条又推来一次、回到前台读回状态（停在 t1、本站等「允许」）、补拉带回同一页：都不再提醒
push(S, ev("p1", "session.status_idle", 6, { stop_reason: { type: "requires_action", event_ids: ["t1"] } }));
g.document.fire("visibilitychange");
await g.sleep(40);
assert.ok(eventsCalls(S).length > polls);
assert.equal(toolCalls().length, 1);
assert.deepEqual(c.getSnapshot().pendingApprovals, ["t1"]);
""")

    def test_a6_a_tool_wait_held_in_the_push_gap_fetches_the_card_when_released(self):
        # 缺口压着推送时（实时通道重连、收口的补拉还在路上）：推来的「停下来等工具」先压着，补拉回来放出时当场提醒一次，压着的那条
        # 交出时不再重复
        self.run_page(r"""
import { getSessionController } from "@/lib/managed-agents/session-controller";
---
const S = "sesn_a6gap";
site.log.push(ev("u0", "user.message", 1, { content: text("hi") }), ev("r0", "session.status_running", 2));
site.engine = "running";
const c = getSessionController(S);
c.attach();
await g.sleep(30);
const respond = g.respond;
let release: () => void = () => {};
let hold = false;
g.respond = async (method: string, args: any, body: any) => {
  if (method === "events" && hold) await new Promise<void>((resolve) => { release = resolve; });
  return respond(method, args, body);
};
g.socket.drop();
await g.sleep(30);                                                           // 断开时的那次补拉照常回来
hold = true;
g.socket.back();                                                             // 重连：开缺口，收口的补拉压在路上
await g.sleep(5);
push(S, ev("t1", "agent.custom_tool_use", 3, { name: "cowork_set_folder_task_enabled", input: {} }));
site.engine = "idle";
push(S, ev("p1", "session.status_idle", 4, { stop_reason: { type: "requires_action", event_ids: ["t1"] } }));
await g.sleep(10);
assert.equal(toolCalls().length, 0);                                         // 压着，还没并入
hold = false;
release();
await g.sleep(30);
assert.deepEqual(toolCalls().map((x: any) => x.body.tool_use_id), ["t1"]);   // 放出时当场一次
await g.sleep(30);
assert.equal(toolCalls().length, 1);
g.respond = respond;
""")

    def test_a6_no_tool_reminder_from_a_hidden_page_until_it_comes_back(self):
        # 页面在后台（锁屏、切到别的应用）：不发提醒（服务端推送线程自己执行，卡片内容回来再取）；回到前台当场补上一次
        self.run_page(r"""
import { getSessionController } from "@/lib/managed-agents/session-controller";
---
const S = "sesn_a6bg";
site.log.push(ev("u0", "user.message", 1, { content: text("hi") }), ev("r0", "session.status_running", 2));
site.engine = "running";
site.toolReply = async (id: string) => {
  site.runs[id] = "awaiting_approval";
  return { status: "awaiting_approval", ask: "Disable this task?", preview: [] };
};
const c = getSessionController(S);
c.attach();
await g.sleep(30);
g.document.visibilityState = "hidden";
push(S, ev("t1", "agent.custom_tool_use", 3, { name: "cowork_set_folder_task_enabled", input: {} }));
site.engine = "idle";
push(S, ev("p1", "session.status_idle", 4, { stop_reason: { type: "requires_action", event_ids: ["t1"] } }));
await g.sleep(20);
assert.equal(toolCalls().length, 0);
assert.deepEqual(c.getSnapshot().pendingCustomTools, ["t1"]);
g.document.visibilityState = "visible";
g.document.fire("visibilitychange");
await g.sleep(40);
assert.deepEqual(toolCalls().map((x: any) => x.body.tool_use_id), ["t1"]);
assert.equal(toolPart(c.getSnapshot(), "t1").approval.ask, "Disable this task?");
""")

    def test_a6b_a_turn_started_elsewhere_brings_the_next_backfill_forward(self):
        # 第 11 轮重测 A6b：手机空闲时排下的补拉在 30 秒后（PUSHED_IDLE_MS）；别的设备上发起的一轮推来「开跑」后，已排的那次提前到
        # 在跑的间隔（PUSHED_RUNNING_MS），推送漏了「停下来等工具」时 8 秒内也有一次补拉兜底
        self.run_page(r"""
import { getSessionController } from "@/lib/managed-agents/session-controller";
---
const S = "sesn_a6b";
site.log.push(ev("u0", "user.message", 1, { content: text("hi") }), ev("i0", "session.status_idle", 2, { stop_reason: { type: "end_turn" } }));
const delays: number[] = [];
const realSetTimeout = g.setTimeout;
g.setTimeout = (fn: any, ms?: number, ...rest: any[]) => { delays.push(ms ?? 0); return realSetTimeout(fn, ms, ...rest); };
const c = getSessionController(S);
c.attach();
await g.sleep(30);
assert.ok(delays.includes(30000), String(delays));                          // 空闲：下一次补拉在 30 秒后
delays.length = 0;
push(S, ev("u1", "user.message", 3, { content: text("disable it") }));
push(S, ev("r1", "session.status_running", 4));
assert.ok(delays.includes(8000), String(delays));                           // 开跑：提前到 8 秒
delays.length = 0;
push(S, ev("t1", "agent.custom_tool_use", 5, { name: "cowork_set_folder_task_enabled", input: {} }));
assert.ok(!delays.includes(8000), String(delays));                          // 已在跑：不再重排
g.setTimeout = realSetTimeout;
""")

    def test_send_failure_wiring(self):
        # onNew 经 attemptSend（不抛出）；原因写在输入框上方，同对话里出错那一行的样子；放回的图片按会话位置取本页预览
        provider = (SRC / "lib/managed-agents/runtime-provider.tsx").read_text(encoding="utf-8")
        on_new = provider.split("onNew: async (message: AppendMessage) => {", 1)[1].split("\n    // Stop button", 1)[0]
        self.assertIn("ok = await attemptSend(threadId, restore, async (sent) => {", on_new)
        self.assertIn("const restore = { composer: composer.current, text, attachments: message.attachments };", on_new)
        self.assertIn("if (!ok) return;", on_new)
        self.assertLess(on_new.index("if (!ok) return;"), on_new.index("generateTitle()"))   # 没发出去不取标题
        self.assertIn("composer.current = runtime.thread.composer;", provider)
        # 附件先过 filesToSend（有没传上的就整条不发，第 9 轮第 15 条）；停止经 attemptStop（不抛出，第 16 条）
        self.assertIn("const files = filesToSend(message.attachments);", on_new)
        self.assertLess(on_new.index("filesToSend("), on_new.index("const send = async"))
        self.assertIn("sendMessage(text, files, pendingShownAs)", on_new)
        self.assertNotIn("attachedFilesOf", provider)
        # 停止不经组件库（不给 onCancel，第 11 轮 F39）：登记给停止键、Esc，经 attemptStop
        self.assertNotIn("onCancel:", provider)
        stop = provider.split("stop.current = async () => {", 1)[1].split("\n  };", 1)[0]
        self.assertIn("if (newChat.cancel()) return;", stop)
        self.assertIn("if (controller) await attemptStop(threadId, () => controller.interrupt());", stop)
        self.assertIn("useEffect(() => registerStop(threadId, () => stop.current()), [threadId]);", provider)
        self.assertIn("if (!running) clearStopFailure(threadId);", provider)
        adapter = (SRC / "lib/managed-agents/attachments.ts").read_text(encoding="utf-8")
        send = adapter.split("async send(attachment): Promise<CompleteAttachment> {", 1)[1]
        self.assertNotIn("throw err", send)                 # 发送这一步不抛：组件库不接，抛了就是未捕获的错误
        self.assertIn("info = await store(attachment.id, attachment.file);", send)
        thread = (SRC / "components/assistant-ui/thread.tsx").read_text(encoding="utf-8")
        self.assertIn("<SendFailure />\n            <ApprovalSheet />\n            <Composer />", thread)
        failure = thread.split("const SendFailure: FC = () => {", 1)[1].split("\n};\n", 1)[0]
        self.assertIn('<div role="alert" data-slot="cc-send-failure" className={ERROR_BOX}>', failure)
        self.assertIn('const ERROR_BOX = "cc-alert";', thread)  # 同对话里出错那一行：统一的出错框（V12）
        self.assertIn('<p className="line-clamp-(--cc-alert-lines)">{message}</p>', failure)
        self.assertIn('<ErrorPrimitive.Root className={cn("aui-message-error-root mt-2", ERROR_BOX)}>', thread)
        attachment = (SRC / "components/assistant-ui/attachment.tsx").read_text(encoding="utf-8")
        self.assertIn("(placed ? localPreview(placed) : undefined)", attachment)

    def test_a_new_chat_says_at_once_when_cc_is_not_set_up_here(self):
        # H8（2026-10-16 测试窗口，lab testerp 没配 CC）：打开新对话页不先说，要发了第一条被拒才在输入框上方出现原因。
        # 现在外壳启动数据带上原因（shell.describe 的 assistant_unavailable，服务端已翻译的同一句）：新对话一打开就写在同一个位置，
        # 不另写文案；再发送时照常清掉、按这次的回应重写；已有的原因不盖掉；能用（空串）什么都不显示。
        self.run_page(r"""
import { attemptSend, noteUnavailable, sendFailureOf } from "@/lib/managed-agents/send-failure";
---
// profile.ts 用 Vite 的 import.meta.glob（这里打不进来）：取启动数据那一步按源码核对（见下），这里从拿到的原因开始
const NOT_SET = "本站点尚未设置 CC。";
noteUnavailable("thread-new", NOT_SET);
assert.equal(sendFailureOf("thread-new"), NOT_SET);               // 一打开就在输入框上方
assert.equal(sendFailureOf("thread-other"), "");                  // 只在这段新对话
noteUnavailable("thread-ok", "");                                 // 能用：不显示
assert.equal(sendFailureOf("thread-ok"), "");
// 已有原因（上次发送没成功）不盖掉
const box = { text: "", setText(v: string) { box.text = v; }, getState: () => ({ text: box.text }), addAttachment: async () => {} };
await attemptSend("thread-failed", { composer: box, text: "x" }, async () => { throw new TypeError("Failed to fetch"); });
noteUnavailable("thread-failed", NOT_SET);
assert.equal(sendFailureOf("thread-failed"), "出了点问题，请重试。");
// 再发送：照常先清掉；这次发出去了就不再显示
const sent = await attemptSend("thread-new", { composer: box, text: "hi" }, async (done) => { assert.equal(sendFailureOf("thread-new"), ""); done(); });
assert.equal(sent, true);
assert.equal(sendFailureOf("thread-new"), "");
""")
        provider = (SRC / "lib/managed-agents/runtime-provider.tsx").read_text(encoding="utf-8")
        effect = provider.split("// 本站还开不了 CC 对话", 1)[1].split("\n  }, [controller, threadId]);", 1)[0]
        self.assertIn("if (controller) return;", effect)  # 只在还没有会话的新对话
        self.assertIn("if (live && profile.assistant_unavailable) noteUnavailable(threadId, profile.assistant_unavailable);",
                      effect)
        self.assertIn("void loadProfile().then(", effect)
        profile = (SRC / "lib/profile.ts").read_text(encoding="utf-8")
        self.assertIn("assistant_unavailable?: string", profile)
        self.assertIn('siteFetch("/api/method/pearnly_core.shell.describe", { cache: "no-store" })', profile)

    def test_new_chat_wiring_is_per_conversation(self):
        # 组件库每段对话一个 useSessionThreadRuntime 实例（后台挂着的对话也在）：「正在建会话」是这一段自己的，不读模块级的
        # 发送中气泡（pending-send.ts 是整页一个）
        provider = (SRC / "lib/managed-agents/runtime-provider.tsx").read_text(encoding="utf-8")
        self.assertIn("const [newChat] = useState(() => new NewChatSend());", provider)
        self.assertIn("const running = isBusy(snapshot) || creating;", provider)
        self.assertIn("isRunning: running,", provider)
        self.assertIn("if (newChat.cancel()) return;", provider)
        self.assertIn("if (controller) await send(await ensureSession());", provider)
        self.assertNotIn("usePendingSend", provider)

    def test_the_top_line_only_says_reconnecting_in_the_page_language(self):
        # 2026-10-15 安卓（F5）：回复中途开飞行模式，对话顶部露出英文原文「Failed to fetch」。现在顶部只说本地化的
        # 「正在重新连接…」，原文只进控制台。顶部这一行只看连接本身（第 11 轮重测 E 段起，卡住时也照旧说）。
        self.run_page(r"""
import { getSessionController } from "@/lib/managed-agents/session-controller";
import { connectionDetail, showsReconnecting } from "@/lib/managed-agents/turn";
---
const respond = g.respond;
g.respond = async (method: string, args: any, body: any) => {
  if (method === "events") throw new TypeError("Failed to fetch");
  return respond(method, args, body);
};
const c = getSessionController("sesn_net5");
c.attach();
await g.sleep(30);
const s: any = c.getSnapshot();
assert.equal(connectionDetail(s), "Failed to fetch");    // 原文留给控制台
assert.equal(showsReconnecting(s), true);
// 第 11 轮重测 E 段：只看连接本身。这一轮卡住（状态行已在说「连接中断」）而连接仍断着时顶部照旧说，不在第 30 秒自己消失
assert.equal(showsReconnecting({ ...s, stalled: true }), true);
const base = { isLoaded: true, isTailing: true, transportError: null, stalled: false, lastError: null };
assert.equal(showsReconnecting(base), false);
assert.equal(showsReconnecting({ ...base, isTailing: false }), true);
const retrying = { ...base, lastError: { message: "Overloaded", retryStatus: "retrying" } } as any;
assert.equal(showsReconnecting(retrying), true);
assert.equal(connectionDetail(retrying), "Overloaded");
assert.equal(showsReconnecting({ ...retrying, lastError: { message: "Overloaded", retryStatus: "exhausted" } } as any), false);
g.respond = respond;
""", lang="zh")
        page = (SRC / "assistant.tsx").read_text(encoding="utf-8")
        bar = page[page.index("function SessionStatusBar"):page.index("async function signOut")]
        self.assertIn('{t("reconnecting")}', bar)
        self.assertIn("showsReconnecting(snapshot)", bar)
        self.assertIn("console.debug(", bar)
        for raw in ("transportError ||", "lastError.message", "{message"):
            self.assertNotIn(raw, bar)

    def test_page_wiring(self):
        # 这些接线在 node 里跑不了整页，守住源码：只有一个「在忙」标记、没有圆点光标、输入框挂了输入法判断
        thread = (SRC / "components/assistant-ui/thread.tsx").read_text(encoding="utf-8")
        markdown = (SRC / "components/assistant-ui/markdown-text.tsx").read_text(encoding="utf-8")
        steps = (SRC / "components/tool-uis/steps.tsx").read_text(encoding="utf-8")
        self.assertNotIn('import "@assistant-ui/react-markdown/styles/dot.css"', markdown)
        self.assertNotIn("\n      defer\n", markdown)
        self.assertIn('indicator="never"', thread)
        self.assertNotIn('case "indicator"', thread)
        # 状态行挂在临时气泡下、这一轮的回复（或组件库补的占位）里：放在回复外面会被「推到顶上」留的空白挤到屏幕最下面
        bubble = thread[thread.index("const PendingSendBubble"):thread.index("function useShownPending")]
        # 状态行在 CC 一侧（左，px-2 同回复），不在用户气泡那一列；用户气泡发送时不扫光（用户 10-16，同 Claude）
        self.assertIn('<RunIndicator className="px-(--cc-space-8) pt-(--cc-run-gap)" />', bubble)
        self.assertNotIn("col-start-1", bubble)
        self.assertNotIn("data-sending", bubble)
        css = (SRC / "globals.css").read_text(encoding="utf-8")
        self.assertNotIn("data-sending", css)
        self.assertNotIn("cc-sheen", css)
        reply = thread[thread.index("const AssistantMessage"):thread.index("const LastReplyIndicator")]
        self.assertLess(reply.index("<LastReplyIndicator />"), reply.index('data-slot="aui_assistant-message-footer"'))
        self.assertNotIn("<RunIndicator />\n          </div>", thread)
        self.assertIn("onKeyDown={keyDown}", thread)
        self.assertIn("holdImeEnter(event); // Safari 选字结束的回车（keyCode 229）不发送，lib/ime.ts", thread)
        self.assertIn("ref={settle.viewport}", thread)
        self.assertIn("pendingLanded(pending, s.thread.messages.map((m) => m.id))", thread)
        # 步骤组：不再另放一个在动的点，只有思考没有工具的不显示
        group = steps[steps.index("export const StepsGroup"):]
        self.assertNotIn("TypingDots", group)
        self.assertIn("if (!tools.length) return null;", group)


if __name__ == "__main__":
    unittest.main()
