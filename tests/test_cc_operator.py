"""CC「实操」（代录）在统一对话框架里（方案 cc-unified-plan 14，事件清单以 U1 的 14.10 为准）：代录任务就是一段标准对话会话。

断言的是应有状态：
  - 原因代码与 CC 桌面版 contract.py 一致，三语都有文案，原码不外露，不出现旧词；
  - 代录事件（假数据，形状同 14.10）进同一个 reducer：卡片停着不算在跑、不向服务端要执行、答完不算引擎接着跑；同一步认最新的
    （结果或进度）；收尾那条写一句、带报告；pop_ / local- 开头的不当补拉锚点；
  - 卡片能点 = awaiting_approval 且还没结果；卡片按钮照旧调代录接口（start / task_answer / task_resume / resolve + task_settle），不走 confirm；
  - 执行电脑上的桥：合成 pearnly.progress 并进那段会话、task_running 一次、取到 stop 走桥停下、做完以站点为准收尾；
  - 侧栏两种模式列同一批对话（listing 不带 mode，11-08）、代录模式新建带 mode=operator、切模式跟着改；旧的代录任务链接按任务号找会话；
  - 旧的代录工作区、状态模型、任务侧栏都不在了，components/operator 只留切换与桥。

    python3 tests/test_cc_operator.py
"""
import re
import sys
import unittest

from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, ROOT + "/tests")
from test_assistant_ui import SOCKET_STUB, run_ts  # noqa: E402

SRC = Path(ROOT) / "frontend/assistant/src"

DOM_SETUP = r"""
const g = globalThis as any;
g.addEventListener ??= () => {};
g.removeEventListener ??= () => {};
const store = new Map<string, string>();
g.localStorage ??= { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, String(v)), removeItem: (k: string) => void store.delete(k), clear: () => store.clear() };
g.requestAnimationFrame ??= (f: () => void) => setTimeout(f, 0);
g.cancelAnimationFrame ??= (id: number) => clearTimeout(id);
g.matchMedia ??= () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
g.navigator ??= { userAgent: "node", onLine: true };
"""

STUBS = {"socket.io-client": SOCKET_STUB, "cc-dom-setup": DOM_SETUP, "@/lib/profile": 'export const APP_NAME = "CC"; export const useProfile = () => undefined; '
         'export const loadProfile = async () => null; export const assistantMascot = () => null; export const assistantLogo = () => null;'}

FRAPPE_STUB = r"""
export const getMethod = async (method: string, args: Record<string, unknown>) => (globalThis as any).reply(method, args);
"""

RECOGNITION = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { recognitionNow, recognitionOf, stopRecognition, watchRecognition } from "@/lib/managed-agents/attachment-recognition";

const g = globalThis as any;
const ticks: Array<() => void> = [];
g.setTimeout = (f: () => void) => { ticks.push(f); return ticks.length; };
g.clearTimeout = () => {};
const asked: unknown[] = [];
let row: Record<string, unknown> = { batch: "B-1", state: "reading", pages_done: 1, page_count: 3 };
g.reply = (method: string, args: Record<string, unknown>) => { asked.push([method, args]); return { uploads: [row] }; };
const flush = async () => { const f = ticks.shift(); assert.ok(f); await f(); await new Promise((r) => setImmediate(r)); };

assert.deepEqual(recognitionOf({ batch: "B", state: "queued" }), { busy: true, failed: false, text: "排队中" });
assert.equal(recognitionOf({ batch: "B", state: "reading", pages_done: 2, page_count: 5 }).text, "识别中 2/5 页");
assert.deepEqual(recognitionOf({ batch: "B", state: "done", documents: [{}, {}] }), { busy: false, failed: false, text: "已识别 2 张" });
assert.equal(recognitionOf({ batch: "B", state: "failed" }).failed, true);

watchRecognition("att-1", "t001", { batch: "B-1", state: "queued" });
assert.equal(recognitionNow("att-1")?.text, "排队中");
await flush();
assert.deepEqual(asked[0], ["pearnly_core.express_operator.api.task_uploads", { name: "t001", batches: JSON.stringify(["B-1"]) }]);
assert.equal(recognitionNow("att-1")?.text, "识别中 1/3 页");
row = { batch: "B-1", state: "done", documents: [{}] };
await flush();
assert.equal(recognitionNow("att-1")?.text, "已识别 1 张");
assert.equal(ticks.length, 0);  // 有结论就不再读

watchRecognition("att-2", "t001", { batch: "B-2", state: "queued" });
stopRecognition("att-2");  // 发出或移除：不再显示、不再读
assert.equal(recognitionNow("att-2"), undefined);
await flush();
assert.equal(asked.length, 2);
console.log("HARNESS OK");
"""

OLD_WORDS = re.compile(r"代录|录入|已录入|再录一次|不录了|录入前确认|录入后处理|等开始|正在录入|批次|照录|挂起|转人工|读回")

WORDS = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { BATCH_STOPPERS, isRawCode, levelOf, REASONS, reasonText } from "@/components/tool-uis/operator-shared";
import { op } from "@/i18n";

const g = globalThis as any;
function words(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (typeof value === "function") return [String((value as (...a: unknown[]) => unknown)(3, 2, "1 min"))];
  if (value && typeof value === "object") return Object.values(value).flatMap(words);
  return [];
}
// 级别与 contract.py 一致；不认识的按必须人工
assert.equal(levelOf("company_pick"), "human");
assert.equal(levelOf("duplicate_ref"), "auto_reported");
assert.equal(levelOf("screen_locked"), "auto");
assert.equal(levelOf("brand_new"), "human");
assert.equal(Object.keys(REASONS).length, 32);
assert.deepEqual([...BATCH_STOPPERS].sort(), ["account_in_use", "account_set_unavailable", "budget", "company_mismatch", "license", "login_failed", "login_missing", "network_down"]);
// 每个原因代码都有这种语言的文案，且不是代码本身；原码不外露
for (const code of Object.keys(REASONS)) {
  assert.ok(op.reason[code], code);
  assert.notEqual(op.reason[code], code);
  assert.equal(reasonText(code), op.reason[code]);
}
assert.equal(reasonText("[OPERATOR_LOGIN_TIMEOUT]"), op.unknownReason);
assert.equal(reasonText("choice_needed:item"), op.unknownReason);
assert.equal(reasonText("some_new_code"), op.unknownReason);
assert.equal(reasonText("", "Traceback (most recent call last)"), op.unknownReason);
assert.equal(reasonText("", "ใบกำกับภาษีไม่ครบ"), "ใบกำกับภาษีไม่ครบ");
assert.equal(isRawCode("OPERATOR_X"), true);
// 新加的几句三语都有、不带旧词
assert.ok(op.nth(7, 22).includes("7") && op.nth(7, 22).includes("22"));
assert.ok(op.resume && op.stopped && op.end.stopped(3).includes("3") && op.end.finished(5, 1).includes("5"));
for (const name of ["operator_upload", "operator_document", "operator_sources"]) assert.equal(op.steps[name]!.length, 2, name);
g.words = words(op);
process.stdout.write(JSON.stringify(g.words) + "\n");
g.done();
"""

FEED = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { applyRunStatus, isBusy, project } from "@/lib/managed-agents/reducer";
import { isLocalEvent, isOperatorTool } from "@/lib/managed-agents/events";
import { liveProgress, operatorEndText, progressText, stepProgress } from "@/lib/managed-agents/progress";
import { cardPhase } from "@/components/tool-uis/operator-shared";
import { documentRow, sourcesLine, uploadStateOf } from "@/components/tool-uis/operator-steps";
import { co, op, run } from "@/i18n";

const g = globalThis as any;
const T = "OPT-1";
const at = (s: number) => `2026-11-04T08:00:${String(s).padStart(2, "0")}.000000Z`;
const text = (value: unknown) => [{ type: "text", text: JSON.stringify(value) }];
// 假数据：形状同 U1 的 14.10（准备中的资料与开始前卡片、交单、一张录成、一张排着、整段对话的进度、收尾）
const events: any[] = [
  { type: "user.message", id: "sevt_1", processed_at: at(0), content: [{ type: "text", text: "这个月的采购" }] },
  { type: "agent.tool_use", id: `pop_${T}_sources`, name: "operator_sources", processed_at: at(1), input: { task: T, company: "CO-1", company_title: "Acme", period: "2026-10" } },
  { type: "agent.custom_tool_use", id: `pop_${T}_start`, name: "operator_start", processed_at: at(1), input: { task: T, company: "CO-1", company_title: "Acme", period: "2026-10" } },
  { type: "pearnly.tool_run", id: `ptoolrun_pop_${T}_start_awaiting_approval`, processed_at: at(1), tool_use_id: `pop_${T}_start`, status: "awaiting_approval" },
  { type: "session.status_idle", id: `pop_${T}_st_1`, processed_at: at(2), stop_reason: { type: "requires_action", event_ids: [`pop_${T}_start`] } },
];
let state = project(events);
// 开始前卡片停着：能点、不算在跑（输入框照常能打字）、不向服务端要执行
const start = state.messages.flatMap((m) => m.content).find((p: any) => p.toolCallId === `pop_${T}_start`) as any;
assert.equal(start.toolName, "operator_start");
assert.equal(cardPhase(start), "ask");
assert.deepEqual(state.pendingCustomTools, []);
assert.deepEqual(state.pendingApprovals, []);
assert.equal(isBusy(state), false);
assert.ok(isOperatorTool("operator_start") && !isOperatorTool("save_rule"));
// 整段重放带回的服务端运行状态（events?status=1 的 run：requires_action，parked / awaiting_approval = 卡片编号）同样不向服务端要
// 执行（run_tool 拒 pop_ 编号，回「未找到该操作」，实验室 cc.operator 11-04 在 Error Log 里记下过）、不算在跑
const ran = applyRunStatus(state, { status: "requires_action", parked: [`pop_${T}_start`], awaiting_approval: [`pop_${T}_start`], executing: [], as_of: at(2) } as any);
assert.deepEqual(ran.pendingCustomTools, []);
assert.deepEqual(ran.pendingApprovals, []);
assert.equal(isBusy(ran), false);
// 交单：卡片结果（user.custom_tool_result）+ done；答完不算引擎接着跑，运行状态等事件源投影的状态
state = project([
  { type: "user.custom_tool_result", id: `pop_${T}_start_r`, processed_at: at(3), custom_tool_use_id: `pop_${T}_start`, content: text({ sent: 2, not_sent: [], computer: "", start_at: 0 }), is_error: false },
  { type: "pearnly.tool_run", id: `ptoolrun_pop_${T}_start_done`, processed_at: at(3), tool_use_id: `pop_${T}_start`, status: "done" },
], state);
const started = state.messages.flatMap((m) => m.content).find((p: any) => p.toolCallId === `pop_${T}_start`) as any;
assert.equal(cardPhase(started), "answered");
assert.equal(state.isRunning, false);
state = project([
  { type: "session.status_running", id: `pop_${T}_st_2`, processed_at: at(4) },
  { type: "agent.tool_use", id: `pop_${T}_dA`, name: "operator_document", processed_at: at(4), input: { task: T, n: 1, doc_id: "A", ref: "INV-1", party: "Supplier" } },
  { type: "agent.tool_use", id: `pop_${T}_dB`, name: "operator_document", processed_at: at(4), input: { task: T, n: 2, doc_id: "B", ref: "INV-2", party: "Supplier" } },
  { type: "pearnly.progress", id: `pop_${T}_pB_q`, processed_at: at(5), tool_use_id: `pop_${T}_dB`, done: 0, total: 0, label: "blocked", detail: "INV-0 · X" },
  { type: "agent.tool_result", id: `pop_${T}_dA_rsaved_1`, processed_at: at(6), tool_use_id: `pop_${T}_dA`, content: text({ state: "saved", docnum: "PO-9" }), is_error: false },
  { type: "pearnly.progress", id: `pop_${T}_p_entering_1`, processed_at: at(6), tool_use_id: "", done: 1, total: 2, label: "entering" },
], state);
assert.equal(isBusy(state), true);
const parts = () => state.messages.flatMap((m) => m.content) as any[];
const docA = parts().find((p) => p.toolCallId === `pop_${T}_dA`);
const docB = parts().find((p) => p.toolCallId === `pop_${T}_dB`);
// 自定义工具以外的步骤：结果到了就是有结论（不当中断结果）
assert.deepEqual(documentRow(docA.result, !!docA.isError, stepProgress(state.progress, docA)), { tone: "done", detail: "PO-9" });
const blocked = documentRow(docB.result, !!docB.isError, stepProgress(state.progress, docB));
assert.equal(blocked.tone, "queued");
assert.equal(blocked.detail, `INV-0 · X · ${co.ledger.code.result_unknown}`);
// 整段对话的进度在状态行：正在第 2/2 张
assert.equal(progressText(liveProgress(state)!), op.nth(2, 2));
// 重交的单：更新的进度盖过旧结果（同一步认 processed_at 最新的）
state = project([{ type: "pearnly.progress", id: `pop_${T}_pA_q2`, processed_at: at(9), tool_use_id: `pop_${T}_dA`, done: 0, total: 0, label: "queued" }], state);
const again = parts().find((p) => p.toolCallId === `pop_${T}_dA`);
assert.equal(documentRow(again.result, !!again.isError, stepProgress(state.progress, again)).tone, "queued");
// 没录成：原因码 → 文案，is_error
state = project([{ type: "agent.tool_result", id: `pop_${T}_dA_rnot_done_2`, processed_at: at(10), tool_use_id: `pop_${T}_dA`, content: text({ state: "not_done", reason: "party_missing", level: "human" }), is_error: true }], state);
const failed = parts().find((p) => p.toolCallId === `pop_${T}_dA`);
assert.deepEqual(documentRow(failed.result, !!failed.isError, stepProgress(state.progress, failed)), { tone: "failed", detail: op.reason.party_missing });
// 暂停（执行电脑本页合成，local- 编号）：第 2/2 张 · 电脑已锁屏；旧的不盖新的
state = project([{ type: "pearnly.progress", id: `local-${T}_1_screen_locked`, processed_at: at(11), tool_use_id: "", done: 1, total: 2, label: "screen_locked" }], state);
assert.equal(progressText(liveProgress(state)!), `${op.nth(2, 2)} · ${op.reason.screen_locked}`);
state = project([{ type: "pearnly.progress", id: `pop_${T}_p_old`, processed_at: at(7), tool_use_id: "", done: 0, total: 2, label: "waiting_computer" }], state);
assert.equal(liveProgress(state)!.label, "screen_locked");
assert.equal(progressText({ done: 0, total: 2, label: "waiting_computer" }), `${op.nth(0, 2)} · ${op.queued}`);
assert.equal(progressText({ done: 3, total: 12, label: "reading_pages" }), run.readingPages(3, 12));
// 中途要选的卡片与继续卡片：没有 awaiting_approval 也没结果（任务已往前走）= 已取消；有结果 = 点完
assert.equal(cardPhase({ toolName: "operator_choose" } as any), "cancelled");
assert.equal(cardPhase({ toolName: "operator_choose", run: "awaiting_approval" } as any), "ask");
assert.equal(cardPhase({ toolName: "operator_choose", run: "awaiting_approval", result: { text: "{}", isError: false } } as any), "answered");
// 收尾：空正文 + operator 字段 → 一句，报告记在消息上
state = project([{ type: "agent.message", id: `pop_${T}_end_stopped_1`, processed_at: at(12), content: [{ type: "text", text: "" }],
  operator: { kind: "stopped", task: T, counts: { saved: 1, not_done: 1 }, report: { filename: "Acme 2026-10.csv", url: `/api/method/pearnly_core.express_operator.feed.report?name=${T}` } } }], state);
const last = state.messages.at(-1)!;
assert.ok(last.content.some((p: any) => p.type === "text" && p.text === op.end.stopped(1)));
assert.deepEqual((last.metadata as any).custom.report, { filename: "Acme 2026-10.csv", url: `/api/method/pearnly_core.express_operator.feed.report?name=${T}` });
assert.equal(operatorEndText({ kind: "finished", task: T, counts: { saved: 5, not_done: 0 } }), op.end.finished(5, 0));
// 不当补拉锚点
assert.equal(isLocalEvent({ type: "agent.tool_use", id: `pop_${T}_dA` } as any), true);
assert.equal(isLocalEvent({ type: "pearnly.progress", id: "local-x" } as any), true);
assert.equal(isLocalEvent({ type: "agent.message", id: "sevt_9" } as any), false);
// 上传卡片：进度 → 排队 / 识别中，结果 → 已识别 N 张（N 张不在本月）/ 失败
assert.deepEqual(uploadStateOf(undefined, false, null), { kind: "queued" });
assert.deepEqual(uploadStateOf(undefined, false, { done: 2, total: 5, label: "reading_pages" } as any), { kind: "reading", pagesDone: 2, pageCount: 5 });
assert.deepEqual(uploadStateOf({ text: JSON.stringify({ state: "done", documents: [{ in_period: true }, { in_period: false }] }), isError: false }, false, null),
  { kind: "done", documents: 2, outside: 1 });
assert.equal(uploadStateOf({ text: JSON.stringify({ state: "failed", code: "unavailable" }), isError: true }, true, null).kind, "failed");
assert.equal(sourcesLine(22, { purchase: 20, expense: 2, "": 0 }), [op.sources(22), `${op.group.purchase} 20`, `${op.group.expense} 2`].join(" · "));
g.done();
"""

ACTIONS = r"""
import "cc-test-setup";
import "cc-dom-setup";
import assert from "node:assert/strict";
import { answersFor, operatorActions, questionsOf, setOperatorSite } from "@/components/tool-uis/operator-shared";
import { docsToStart, groupsToStart, mergeDocs, uploadsDoneKey, writeLine } from "@/components/tool-uis/operator-start";
import { resumeQuestion } from "@/components/tool-uis/operator-resume";
import { createBridgeWatch, progressOf } from "@/components/operator/bridge";
import { createSession, listGrouped, listSessions } from "@/lib/managed-agents/api";
import { noteSessionTasks, sessionMode, useListMode } from "@/lib/managed-agents/list-mode";
import { modeForKey, noteViewMode, ownMode, setThreadExit, switchMode, targetOf, useModePlace } from "@/lib/nav";
import { op } from "@/i18n";

const g = globalThis as any;
(async () => {
  const posts: Array<[string, any]> = [];
  const gets: Array<[string, any]> = [];
  setOperatorSite({
    post: async (method: string, args: any) => (posts.push([method, args]), { ok: true }) as any,
    get: async (method: string, args: any) => (gets.push([method, args]), { documents: [{ doc_id: 1, ref: "INV-1", group: "purchase" }], next_offset: null }) as any,
  });
  const API = "pearnly_core.express_operator.api.";
  // 开始前：资料服务端分页；预检的要选项按类、同一对象只问一次；不入账、结果不明还没标的不交
  const page = await operatorActions.documents("CO-1", "2026-10", 0);
  assert.deepEqual(gets.at(-1), [API + "documents", { company: "CO-1", period: "2026-10", offset: 0, limit: 50 }]);
  assert.equal(page.documents[0]!.doc_id, "1");
  const prechecks = [
    { doc_id: "A", state: "choose", needed: [{ kind: "party", subject: "S1", label: "Supplier 1", options: [{ code: "__new__", name: "" }] }] },
    { doc_id: "B", state: "choose", needed: [{ kind: "party", subject: "S1", label: "Supplier 1", options: [{ code: "__new__", name: "" }] }] },
    { doc_id: "C", state: "blocked", reason: "push.result_unknown" },
  ] as any;
  const questions = questionsOf(prechecks);
  assert.equal(questions.length, 1);
  assert.deepEqual(questions[0]!.doc_ids, ["A", "B"]);
  const answers = answersFor(questions, { "party\nS1": "__new__" });
  assert.deepEqual(Object.keys(answers), ["A", "B"]);
  assert.deepEqual(docsToStart([{ doc_id: "A" }, { doc_id: "B", group: "skip" }, { doc_id: "C" }] as any, prechecks), ["A"]);
  assert.equal(writeLine({ company: "CO-1", company_title: "Acme" }, [{ account_set: "S1", account_set_title: "Acme 2026" }]), op.writeTo("Acme · Acme 2026"));
  // 写入目标带上执行的电脑（几份账套同一台只写一次）
  assert.equal(writeLine({ company: "CO-1", company_title: "Acme" }, [{ account_set: "S1", account_set_title: "Acme 2026", computer: "PC-1" }]),
    op.writeTo("Acme · Acme 2026 · PC-1"));
  // 测试机 11-05：卡片出现后才上传、识别完的单要能交上去——识别完的上传（operator_upload 有结果、已识别）一变卡片就重取资料；
  // 重取的第一页在前、已用「显示更多」取过的页照留，同一张只留一次
  const upload = (id: string, state: string) => ({ type: "tool-call", toolName: "operator_upload", toolCallId: id, result: { text: JSON.stringify({ state }) } });
  assert.equal(uploadsDoneKey([{ content: [upload("u1", "done"), upload("u2", "failed"), { type: "text" }] }]), "u1");
  assert.equal(uploadsDoneKey([{ content: [upload("u1", "done")] }, { content: [upload("u3", "done"), { type: "tool-call", toolName: "operator_upload", toolCallId: "u4" }] }]), "u1|u3");
  assert.deepEqual(mergeDocs([{ doc_id: "N" }, { doc_id: "A" }] as any, [{ doc_id: "A" }, { doc_id: "B" }] as any).map((d) => d.doc_id), ["N", "A", "B"]);
  // 一批写进几份账套（U1 api.precheck：target 为空、targets 列出每一份，张数多的在前）：问话列出每一份，不只写公司
  const several = writeLine({ company: "CO-1", company_title: "Acme" }, [{ account_set: "S2", account_set_title: "Acme B", count: 2 }, { account_set: "S1", account_set_title: "Acme A", count: 1 }]);
  assert.ok(several.includes("Acme B") && several.includes("Acme A") && several.indexOf("Acme B") < several.indexOf("Acme A"), several);
  assert.equal(writeLine({ company: "CO-1", company_title: "Acme" }, []), op.writeTo("Acme"));
  g.setTargets = (reply: any) => setOperatorSite({ post: async () => reply, get: async () => ({}) as any });
  g.setTargets({ documents: [{ doc_id: 1, state: "ready" }], target: null, targets: [{ account_set: "S2", count: 2 }, { account_set: "S1", count: 1 }] });
  assert.deepEqual((await operatorActions.precheck("CO-1", ["1"])).targets.map((t: any) => t.account_set), ["S2", "S1"]);
  g.setTargets({ documents: [{ doc_id: 1, state: "ready" }], target: { account_set: "S1" } });          // 旧服务端只给 target
  assert.deepEqual((await operatorActions.precheck("CO-1", ["1"])).targets.map((t: any) => t.account_set), ["S1"]);
  setOperatorSite({
    post: async (method: string, args: any) => (posts.push([method, args]), { ok: true }) as any,
    get: async (method: string, args: any) => (gets.push([method, args]), { documents: [{ doc_id: 1, ref: "INV-1", group: "purchase" }], next_offset: null }) as any,
  });
  // 卡片按钮照旧调代录接口，不调对话的 confirm / run_tool；开始时带上交出的每张单的分组（U1 tasks.attach 记进单据行）
  const input = { task: "OPT-1", company: "CO-1", company_title: "Acme", period: "2026-10" };
  const groups = groupsToStart([{ doc_id: "A", group: "sales" }, { doc_id: "B", group: "skip" }, { doc_id: "D" }] as any, ["A", "D"]);
  assert.deepEqual(groups, { A: "sales", D: "purchase" });
  await operatorActions.start(input, ["A", "D"], answers, groups);
  assert.deepEqual(posts.at(-1), [API + "start", { company: "CO-1", doc_ids: '["A","D"]', computer: "", start_at: 0, answers: JSON.stringify(answers), task: "OPT-1",
    groups: JSON.stringify({ A: "sales", D: "purchase" }) }]);
  await operatorActions.answer("OPT-1", "A", answers);
  assert.deepEqual(posts.at(-1), [API + "task_answer", { name: "OPT-1", doc_id: "A", answers: JSON.stringify(answers.A) }]);
  await operatorActions.answer("OPT-1", "Z", {});
  assert.equal(posts.at(-1)![1].answers, "[]");                                   // 单张重试 = 空回答
  await operatorActions.resolve("CO-1", "C", false);
  await operatorActions.settle("OPT-1");
  assert.deepEqual(posts.slice(-2).map((p) => p[0]), [API + "resolve", API + "task_settle"]);
  await operatorActions.resume("OPT-1");
  assert.deepEqual(posts.at(-1), [API + "task_resume", { name: "OPT-1" }]);
  assert.ok(!posts.some(([m]) => /confirm|run_tool/.test(m)));
  assert.equal(resumeQuestion({ reason: "stopped" }), op.stopped);
  assert.equal(resumeQuestion({ reason: "login_missing", ref: "INV-1", party: "S" }), `${op.reason.login_missing} · INV-1 · S`);

  // 执行电脑上的桥：合成进度并进那段会话、开始了报一次、取到 stop 走桥停下、做完以站点为准收尾
  assert.deepEqual(progressOf({ id: "OPT-1", status: "paused", reason: "screen_locked", docs: [{ doc_id: "A", status: "saved" }, { doc_id: "B", status: "entering" }] }, "t"),
    { type: "pearnly.progress", id: "local-OPT-1_1_screen_locked", processed_at: "t", tool_use_id: "", done: 1, total: 2, label: "screen_locked", detail: "" });
  const bridgeCalls: Array<[string, any]> = [];
  let batches: any[] = [{ id: "OPT-1", status: "running", docs: [{ doc_id: "A", status: "saved" }, { doc_id: "B", status: "entering" }] }];
  const fake = {
    state: async () => ({ features: ["operator"] }),
    call: async (method: string, args?: any) => (bridgeCalls.push([method, args]), method === "operator_state" ? { ok: true, batches } : { ok: true }),
  } as any;
  const sitePosts: Array<[string, any]> = [];
  let controls: any[] = [{ action: "stop", doc_id: "" }];
  const applied: Array<[string, any]> = [];
  let now = 1000;
  const watch = createBridgeWatch({
    bridge: async () => fake,
    get: async (method: string) => (method.endsWith("task_get") ? { session_id: "op_OPT-1" } : {}) as any,
    post: async (method: string, args: any) => {
      sitePosts.push([method, args]);
      if (method.endsWith("task_controls")) { const out = { controls }; controls = []; return out as any; }
      if (method.endsWith("task_settle")) return { documents: [{ doc_id: "A", state: "saved" }] } as any;
      return {} as any;
    },
    apply: (session, event) => applied.push([session, event]),
    now: () => now,
  });
  await watch.tick();
  assert.deepEqual(sitePosts.map((p) => p[0].split(".").at(-1)), ["task_running", "task_controls"]);
  assert.ok(bridgeCalls.some(([m, a]) => m === "operator_control" && a.action === "stop" && a.batch === "OPT-1"));
  assert.equal(applied.length, 1);
  assert.equal(applied[0]![0], "op_OPT-1");
  assert.equal(applied[0]![1].label, "entering");
  now += 10;
  await watch.tick();
  assert.equal(sitePosts.filter((p) => p[0].endsWith("task_running")).length, 1);   // 每个任务只报一次
  batches = [{ id: "OPT-1", status: "finished", docs: [{ doc_id: "A", status: "saved" }, { doc_id: "B", status: "saved" }] }];
  await watch.tick();
  assert.ok(sitePosts.some((p) => p[0].endsWith("task_settle")));
  assert.ok(bridgeCalls.some(([m, a]) => m === "operator_control" && a.action === "settle" && a.results.A.state === "saved"));

  // 侧栏两种模式列同一批对话（listing 不带 mode；11-08 生产：代录那段在对话模式下看不到，用户以为记录丢了），代录模式新建带 mode=operator
  const fetched: string[] = [];
  const bodies: any[] = [];
  g.fetch = async (url: string, init: any = {}) => {
    fetched.push(String(url));
    if (init.body) bodies.push(JSON.parse(init.body));
    return { ok: true, status: 200, json: async () => ({ message: { sessions: [], nextCursor: null, id: "op_X", task: "X", mode: "operator" } }) };
  };
  await listSessions();
  assert.doesNotMatch(fetched.at(-1)!, /mode=/);
  switchMode("operator", "chat", () => {});
  assert.equal(useModePlace.getState().mode, "operator");
  assert.equal(useListMode.getState().mode, "operator");
  await listGrouped({});
  assert.doesNotMatch(fetched.at(-1)!, /mode=/);
  await createSession({ project: "CO-1", period: "2026-10" }, "operator");
  assert.equal(bodies.at(-1).mode, "operator");
  await createSession({ project: "CO-1" });
  assert.equal(bodies.at(-1).mode, undefined);
  switchMode("chat", "operator", () => {});
  assert.equal(useListMode.getState().mode, "chat");
  assert.equal(modeForKey({ code: "Digit2", ctrlKey: true, metaKey: false, shiftKey: true, altKey: false }), "operator");
  // 换模式时正在看的对话不属于新模式：离开、落到新建页（用户 11-06：侧栏换了、右边还留着那段）；属于新模式的、还没发出的新对话、
  // 还不知道是哪种的照旧；直接进另一个模式自己的视图（换了模式）同样，进本模式的视图不换模式也不离开
  // （两种模式共有的公司列表、公司页 11-07 删了：每个视图都属于一个模式）
  let watching = "";
  let toNew = 0;
  setThreadExit({ current: () => watching, toNew: () => { toNew += 1; watching = ""; } });
  noteSessionTasks([{ id: "sesn_chat", mode: "chat" }, { id: "op_A", mode: "operator", task: "A" }]);
  assert.equal(sessionMode("sesn_chat"), "chat");
  assert.equal(sessionMode("op_A"), "operator");
  assert.equal(useListMode.getState().tasks.op_A, "A");
  watching = "sesn_chat"; switchMode("operator", "chat", () => {});
  assert.equal(toNew, 1);
  watching = "op_A"; switchMode("chat", "operator", () => {});
  assert.equal(toNew, 2);
  watching = "op_A"; switchMode("operator", "chat", () => {});
  assert.equal(toNew, 2);
  watching = ""; switchMode("chat", "operator", () => {});
  assert.equal(toNew, 2);
  watching = "sesn_never_seen"; switchMode("operator", "chat", () => {});
  assert.equal(toNew, 2);
  assert.deepEqual(["chat", "tasks", "operator"].map((v) => ownMode(v as any)), ["chat", "chat", "operator"]);
  watching = "op_A"; noteViewMode("operator");
  assert.equal(useModePlace.getState().mode, "operator");
  assert.equal(toNew, 2);
  noteViewMode("tasks");
  assert.equal(useModePlace.getState().mode, "chat");
  assert.equal(toNew, 3);
  setThreadExit(null);
  // 深链接：?c=<会话>；旧的代录任务链接按任务号
  assert.deepEqual(targetOf("/assistant?c=op_OPT-1"), { c: "op_OPT-1", view: "" });
  assert.deepEqual(targetOf("/assistant?view=tasks&task=OPT-1"), { c: "", view: "tasks", task: "OPT-1" });
  g.done();
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""


def read(rel):
    return (SRC / rel).read_text(encoding="utf-8")


class SameReasonsAsDesktop(unittest.TestCase):
    def test_typescript_reasons_match_contract_py(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "op_contract", Path(ROOT) / "companion/src/companion/express_operator/contract.py")
        contract = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(contract)
        ts = read("components/tool-uis/operator-shared.ts")
        block = ts[ts.index("export const REASONS"):]
        block = block[:block.index("};")]
        found = dict(re.findall(r"^\s+(\w+): \"(\w+)\"", block, re.M))
        self.assertEqual(found, contract.REASONS)


class SourceRules(unittest.TestCase):
    def test_only_the_switch_and_the_bridge_are_left_of_the_old_workspace(self):
        # 代录任务就是一段会话（方案 14.4）：工作区、状态模型、任务侧栏、自己的新建页都删了
        left = sorted(p.name for p in (SRC / "components/operator").iterdir())
        self.assertEqual(left, ["bridge.ts", "index.ts", "mode-switch.tsx"])
        app = read("assistant.tsx")
        for gone in ("OperatorMain", "OperatorTitle", "OperatorProvider", "createLiveSource", "watchTasks", "cc-op-frame"):
            self.assertNotIn(gone, app)
        self.assertIn('import { ModeSwitch, useOperatorBridge } from "@/components/operator";', app)
        self.assertIn("useOperatorBridge(operator);", app)
        # 代录模式的主栏就是对话页，顶栏同对话页，没有右侧栏
        self.assertIn('const inChat = (view === "chat" || view === "operator") && hasMessages;', app)
        self.assertNotIn('view === "operator" ? (', app)

    def test_cards_look_like_the_allow_card_and_skip_the_step_group(self):
        card = read("components/tool-uis/operator-card.tsx")
        self.assertIn('<div className="cc-steps-card" data-slot={slot}>', card)
        self.assertIn('<div className="aui-step cc-ask" data-phase={phase}>', card)
        self.assertIn('<div className="cc-ask-q">{question}</div>', card)
        self.assertIn('className="cc-card-btn"', card)
        self.assertIn("{steps.cancelled}", card)
        kit = read("components/tool-uis/index.tsx")
        for name, comp in (("operator_start", "OperatorStartCard"), ("operator_choose", "OperatorChooseCard"), ("operator_resume", "OperatorResumeCard"),
                           ("operator_upload", "OperatorUploadStep"), ("operator_document", "OperatorDocumentStep"), ("operator_sources", "OperatorSourcesStep")):
            self.assertIn(f'{name}: {{ type: "backend", render: {comp} }},', kit)
        # 上传那张就是 W2 的上传状态卡片
        self.assertIn('<UploadCard name={input.filename ?? ""}', read("components/tool-uis/operator-steps.tsx"))
        # 卡片自己调代录接口，不走对话的 confirm / run_tool / 自动答复
        for rel in ("components/tool-uis/operator-start.tsx", "components/tool-uis/operator-choose.tsx", "components/tool-uis/operator-resume.tsx",
                    "components/tool-uis/operator-shared.ts"):
            text = read(rel)
            self.assertNotIn("confirmTools", text)
            self.assertNotIn("runTool", text)
            self.assertNotIn("respondToApproval", text)

    def test_parked_operator_cards_are_not_answered_by_the_page(self):
        reducer = read("lib/managed-agents/reducer.ts")
        self.assertIn('if (toolUse.type === "agent.custom_tool_use" && isOperatorTool(toolUse.name)) continue;', reducer)
        self.assertIn("if (isOperatorTool(state.toolUses.get(id)?.name)) return false;", reducer)

    def test_stop_is_the_chat_stop(self):
        # ■ 与 Esc 照对话发 interrupt（服务端记任务控制 stop，执行电脑从 task_controls 取走），代录不另有停止
        self.assertNotIn("stop={", read("assistant.tsx"))
        provider = read("lib/managed-agents/runtime-provider.tsx")
        self.assertIn("if (controller) await attemptStop(threadId, () => controller.interrupt());", provider)
        self.assertIn('if (c.action === "stop") await b.call("operator_control", { batch: batch.id, action: "stop", doc: "" });', read("components/operator/bridge.ts"))

    def test_operator_sessions_use_the_chat_attachments_and_need_a_company(self):
        """2026-11-06「选中就上传」：代录与直写同一套附件组件和上传路径——选中、拖进、粘贴就传（服务端同时交任务识别），发出走
        直写同一条发送（附件在这条消息里、CC 读得到、只放附件也发得出）。以前代录的资料留在本页、发送时另调 intake、只有附件时
        不发消息：消息里没有附件、CC 不知道这些文件（11-06 测试机实测）。"""
        provider = read("lib/managed-agents/runtime-provider.tsx")
        self.assertIn("isSendDisabled: operator && !sessionId && !draftReady,", provider)
        self.assertIn("const draftReady = useProjects((s) => !!s.draft.project && (!s.period || !!s.draft.period));", provider)
        self.assertIn("adapters: { attachments },", provider)
        for gone in ("operatorFiles", "heldAttachments", "takeHeld", "intakeAll", "express_operator.api.intake", "if (operator) {"):
            self.assertNotIn(gone, provider)
        # 发出就是直写那一条：附件位置随消息带给 CC（attachments.ts 的 filesToSend）
        self.assertIn("const files = filesToSend(message.attachments);", provider)
        self.assertIn("await getSessionController(id).sendMessage(text, files, pendingShownAs);", provider)
        attachments = read("lib/managed-agents/attachments.ts")
        self.assertIn("if (stored.task && stored.upload && !sent.has(id)) watchRecognition(id, stored.task, stored.upload);", attachments)
        tile = read("components/assistant-ui/attachment.tsx")
        self.assertIn("const recognition = useRecognition(id);", tile)
        self.assertIn('<span className="cc-compose-type">{recognition?.text}</span>', tile)

    def test_a_composer_file_shows_its_recognition_until_it_has_a_result(self):
        out = run_ts(RECOGNITION, stubs={**STUBS, "@/lib/frappe": FRAPPE_STUB}, lang="zh")
        self.assertIn("HARNESS OK", out)

    def test_old_words_never_reach_the_page(self):
        for lang in ("zh", "th", "en"):
            out = run_ts(WORDS, stubs=STUBS, lang=lang)
            self.assertNotRegex(out if isinstance(out, str) else "", OLD_WORDS)


# 11-08 生产事故（Sincere Ice 代录 24 张 Big C 销售发票）：同一客户在 24 张上要选，开始前只问一题，一个答案交给 24 张
SAME_PARTY = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { docAnswers, itemQuestion, itemsOfNeeded, kindName } from "@/components/tool-uis/ask-shared";
import { docTitle } from "@/components/tool-uis/operator-words";
import { op } from "@/i18n";

const options = [{ code: "BIGC", name: "BIG C" }, { code: "BIGC-002", name: "BIG C-002 (ซอยประชุม)" }];
const docs = Array.from({ length: 24 }, (_v, i) => ({ doc_id: `H${i + 1}`, state: "choose",
  needed: [{ kind: "party", subject: "sales|0107536000633|00000|บิ๊กซี", label: "บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์", options }] }));
docs.push({ doc_id: "R1", state: "ready" } as any);
const items = itemsOfNeeded(docs);
assert.equal(items.length, 1);                       // 24 张同一题只问一次
assert.equal(items[0]!.docs.length, 24);
const questions = items.map((item) => itemQuestion(item, "CO-1"));
const answer = { answers: [{ header: "x", question: questions[0]!.question, labels: [questions[0]!.options[1]!.label] }] };
const perDoc = docAnswers(items, questions, answer);
assert.equal(Object.keys(perDoc).length, 24);        // 一个答案交给 24 张（站点记住，后面的单不再问）
assert.deepEqual(perDoc.H7, [{ kind: "party", subject: "sales|0107536000633|00000|บิ๊กซี", value: "BIGC-002", label: "บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์" }]);
// 题目的分类名：对方按方向写客户 / 供应商，不写「其他」（生产照片：「อื่นๆ · บมจ.บิ๊กซี…」）
assert.ok(questions[0]!.question.startsWith(op.precheck.kind.customer! + " · "));
assert.equal(kindName({ kind: "party", subject: "purchase|0105|00000" }), op.precheck.kind.supplier);
assert.equal(kindName({ kind: "party", subject: "x" }), op.kind.party);
// 标题不给内部编号（生产照片：任务卡列了 24 行 CWH-哈希）
assert.equal(docTitle({}, "CWH-af181ee25a4fb64fbe6c26a506a97959df4eb66bc140b112"), op.untitled);
assert.equal(docTitle({}, "P:PUR-0001"), op.untitled);
assert.equal(docTitle({ ref: "IV69/00751", party: "Big C" }, "CWH-x"), "IV69/00751 · Big C");
console.log("HARNESS OK");
"""


class OperatorTests(unittest.TestCase):
    def test_one_question_for_every_document_that_needs_the_same_answer(self):
        self.assertIn("HARNESS OK", run_ts(SAME_PARTY, stubs=STUBS, lang="th"))
        # 开始卡有没定的项就在卡里一次问完、答案随「开始」交（不靠 CC 先问；11-08 生产：没问就开始，24 张各停一次）
        start = read("components/tool-uis/operator-start.tsx")
        self.assertIn("const items = useMemo(() => itemsOfNeeded(prechecks), [prechecks]);", start)
        self.assertIn("if (asking && questions.length && !result) {", start)
        self.assertIn("begin(docAnswers(items, questions, answered), () => setSent(answered))", start)

    def test_a_made_up_tool_is_not_shown_as_a_step(self):
        # 11-08 生产：CC 调了站点没有的工具 pdftotext_placeholder，页面多一个看不懂的出错步骤；出处是引擎沙箱说明里举的工具名
        page = read("components/tool-uis/steps.tsx")
        self.assertIn('return part.toolKind === "custom" && !!labels.ready && !(String(part.toolName ?? "") in labels.done);', page)
        self.assertIn("if (unknownOwnTool(props as { toolName?: string; toolKind?: string }, labels)) return null;", page)
        engine = (Path(ROOT) / "apps/pearnly_core/pearnly_core/assistant/engines/anthropic_managed_agents.py").read_text(encoding="utf-8")
        self.assertNotIn("pdftotext", engine)
        self.assertIn("were given; there are no other tools.", engine)

    def test_events_fold_into_the_same_conversation(self):
        for lang in ("zh", "th", "en"):
            run_ts(FEED, stubs=STUBS, lang=lang)

    def test_cards_bridge_and_sidebar_modes(self):
        run_ts(ACTIONS, stubs=STUBS, lang="zh")
        # 外壳登记「正在看的对话 / 去新建页」，左上角、快捷键与进视图换模式都经 nav.ts 的同一个 leaveForeignThread
        shell = read("assistant.tsx")
        self.assertIn("  useThreadExit();\n", shell)
        nav = read("lib/nav.ts")
        self.assertEqual(nav.count("leaveForeignThread(mode);"), 2)
        # 换了模式、还没离开旧对话那一下不进后退记录（后退不会回到「代录模式开着直写对话」；画面场景 mode-leave-back）
        self.assertIn("    if (foreignTo(aui, thread, mode)) return;\n", nav)


if __name__ == "__main__":
    unittest.main()
