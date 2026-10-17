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
site.toolReply = async () => ({ status: "awaiting_approval", ask: "把这个选择记到公司？", preview: changes });
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
assert.equal(tz().approval.ask, "把这个选择记到公司？");
c.respondToApproval("tz", true);
assert.equal(tz().approval.approved, true);      // 点下去先按允许画（乐观）
await g.sleep(320);
const confirms = () => g.calls.filter((call: any) => call.method === "confirm").length;
assert.equal(confirms(), 1);
let part = tz();
assert.equal(part.approval.approved, undefined);  // 没送到：不写成功，按钮回来
assert.equal(part.approval.ask, "把这个选择记到公司？");
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
assert.equal(part.approval.ask, "把这个选择记到公司？");
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

    def test_scrolling_up_while_streaming_stays_put_and_following_resumes_near_the_bottom(self):
        self.run_page(r"""
import { COOLDOWN_MS, NEAR_BOTTOM_PX, StreamFollower } from "@/lib/follow-scroll";
---
let clock = 1000;
const listeners: Record<string, Array<(e: any) => void>> = {};
let reserve = 0;
const vp: any = {
  scrollTop: 0, scrollHeight: 2000, clientHeight: 800, scrolls: 0,
  addEventListener(type: string, fn: any) { (listeners[type] ??= []).push(fn); },
  removeEventListener(type: string, fn: any) { listeners[type] = (listeners[type] ?? []).filter((f) => f !== fn); },
  scrollTo({ top }: any) { this.scrolls++; this.scrollTop = Math.min(top, this.scrollHeight - this.clientHeight); },
  querySelector() { return { offsetHeight: reserve }; },
};
const fire = (type: string, e: any = {}) => (listeners[type] ?? []).forEach((fn) => fn(e));
let grow: () => void = () => {};
let live = true;
const f = new StreamFollower(vp, {}, () => live, () => clock, (_t, onResize) => { grow = onResize; return () => {}; });
vp.scrollTop = 1200;  // 在底部
const more = (px: number) => { vp.scrollHeight += px; grow(); };
// 跟随：内容长了就跟到底
more(100);
assert.equal(vp.scrollTop, 1300);
// 组件库还在把这一轮推到顶上（占位有高度）时不插手
reserve = 300; more(50); reserve = 0;
assert.equal(vp.scrollTop, 1300);
more(0); vp.scrollTop = 1350;
// 手指往下拖（内容往上翻）：立刻停住，之后内容再长也不拉回去
fire("touchstart", { touches: [{ clientY: 100 }] });
fire("touchmove", { touches: [{ clientY: 160 }] });
vp.scrollTop = 900; fire("scroll");
more(200);
assert.equal(vp.scrollTop, 900);
fire("touchend");
clock += 5000; more(200);
assert.equal(vp.scrollTop, 900);  // 停在用户翻到的地方，多久都不动
// 回到底部附近（容差内）：冷却过了，接着跟
fire("touchstart", { touches: [{ clientY: 300 }] });
fire("touchmove", { touches: [{ clientY: 200 }] });  // 手指往上推 = 往下翻，不算往上翻
vp.scrollTop = vp.scrollHeight - vp.clientHeight - (NEAR_BOTTOM_PX - 10);
fire("touchend");
fire("scroll");
more(100);
assert.equal(vp.scrollTop, vp.scrollHeight - vp.clientHeight);
// 滚轮往上：停；冷却内马上滚回底部附近不算，冷却过后再滚一下才接着跟
fire("wheel", { deltaY: -40 });
vp.scrollTop -= 20; fire("scroll");
more(100);
assert.notEqual(vp.scrollTop, vp.scrollHeight - vp.clientHeight);
clock += COOLDOWN_MS; vp.scrollTop = vp.scrollHeight - vp.clientHeight; fire("scroll");
more(100);
assert.equal(vp.scrollTop, vp.scrollHeight - vp.clientHeight);
// 在输入框里按上键不算往上翻；在对话里按 PageUp 算
fire("keydown", { key: "ArrowUp", target: { tagName: "TEXTAREA" } });
more(50);
assert.equal(vp.scrollTop, vp.scrollHeight - vp.clientHeight);
fire("keydown", { key: "PageUp", target: { tagName: "DIV" } });
vp.scrollTop -= 300; fire("scroll");
more(50);
assert.notEqual(vp.scrollTop, vp.scrollHeight - vp.clientHeight);
// 新的一轮从跟随开始；不在跑时内容变化不滚
f.resume(); more(10);
assert.equal(vp.scrollTop, vp.scrollHeight - vp.clientHeight);
live = false; const top = vp.scrollTop; more(500);
assert.equal(vp.scrollTop, top);
f.dispose();
assert.equal(Object.values(listeners).every((l) => l.length === 0), true);
""")

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

    def test_send_failure_wiring(self):
        # onNew 经 attemptSend（不抛出）；原因写在输入框上方，同对话里出错那一行的样子；放回的图片按会话位置取本页预览
        provider = (SRC / "lib/managed-agents/runtime-provider.tsx").read_text(encoding="utf-8")
        on_new = provider.split("onNew: async (message: AppendMessage) => {", 1)[1].split("\n    // Stop button", 1)[0]
        self.assertIn("ok = await attemptSend(threadId, restore, async (sent) => {", on_new)
        self.assertIn("const restore = { composer: composer.current, text, attachments: message.attachments };", on_new)
        self.assertIn("if (!ok) return;", on_new)
        self.assertLess(on_new.index("if (!ok) return;"), on_new.index("generateTitle()"))   # 没发出去不取标题
        self.assertIn("composer.current = runtime.thread.composer;", provider)
        thread = (SRC / "components/assistant-ui/thread.tsx").read_text(encoding="utf-8")
        self.assertIn("<SendFailure />\n            <ApprovalSheet />\n            <Composer />", thread)
        failure = thread.split("const SendFailure: FC = () => {", 1)[1].split("\n};\n", 1)[0]
        self.assertIn('<div role="alert" data-slot="cc-send-failure" className={ERROR_BOX}>', failure)
        self.assertIn('<p className="line-clamp-2">{message}</p>', failure)
        self.assertIn('<ErrorPrimitive.Root className={cn("aui-message-error-root mt-2", ERROR_BOX)}>', thread)
        attachment = (SRC / "components/assistant-ui/attachment.tsx").read_text(encoding="utf-8")
        self.assertIn("(placed ? localPreview(placed) : undefined)", attachment)

    def test_new_chat_wiring_is_per_conversation(self):
        # 组件库每段对话一个 useSessionThreadRuntime 实例（后台挂着的对话也在）：「正在建会话」是这一段自己的，不读模块级的
        # 发送中气泡（pending-send.ts 是整页一个）
        provider = (SRC / "lib/managed-agents/runtime-provider.tsx").read_text(encoding="utf-8")
        self.assertIn("const [newChat] = useState(() => new NewChatSend());", provider)
        self.assertIn("isRunning: isBusy(snapshot) || creating,", provider)
        self.assertIn("if (newChat.cancel()) return;", provider)
        self.assertIn("if (controller) await send(await ensureSession());", provider)
        self.assertNotIn("usePendingSend", provider)

    def test_the_top_line_only_says_reconnecting_in_the_page_language(self):
        # 2026-10-15 安卓（F5）：回复中途开飞行模式，对话顶部露出英文原文「Failed to fetch」。现在顶部只说本地化的
        # 「正在重新连接…」，原文只进控制台；对话内状态行已在说「连接中断，正在重连…」时顶部不说。
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
assert.equal(showsReconnecting({ ...s, stalled: true }), false);
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
        self.assertIn('<RunIndicator className="px-2 pt-4" />', bubble)
        self.assertNotIn("col-start-1", bubble)
        self.assertNotIn("data-sending", bubble)
        css = (SRC / "globals.css").read_text(encoding="utf-8")
        self.assertNotIn("data-sending", css)
        self.assertNotIn("cc-sheen", css)
        reply = thread[thread.index("const AssistantMessage"):thread.index("const LastReplyIndicator")]
        self.assertLess(reply.index("<LastReplyIndicator />"), reply.index('data-slot="aui_assistant-message-footer"'))
        self.assertNotIn("<RunIndicator />\n          </div>", thread)
        self.assertIn("onKeyDown={holdImeEnter", thread)
        self.assertIn("ref={follow.viewport}", thread)
        self.assertIn("pendingLanded(pending, s.thread.messages.map((m) => m.id))", thread)
        # 步骤组：不再另放一个在动的点，只有思考没有工具的不显示
        group = steps[steps.index("export const StepsGroup"):]
        self.assertNotIn("TypingDots", group)
        self.assertIn("if (!tools.length) return null;", group)


if __name__ == "__main__":
    unittest.main()
