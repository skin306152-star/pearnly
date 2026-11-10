"""CC 通用提问卡片（工具 ask_user，用户 11-05：1:1 照 Claude Code 的原生提问卡片，组件固定、内容由 CC 定）。

断言的是应有状态：
  - 入参照 AskUserQuestion 读：最多 4 题、每题最多 4 个选项；推荐项（第一个、带「(推荐)」，三语）默认选中，其余空着；
  - 答：单选点了换成它（取消「其他」），多选开关；「其他」打的字或选的编码；交回 {answers: [{header, question, labels, other?, skipped?}]}，
    按题序，跳过的和没答的标 skipped；点完那一行每题「标签：所选」；
  - 页面自己出题（代录中途那一张、待处理）：一项一题，预选值排第一带「(推荐)」，答案换回判定接口的 [{key, value}] 与代录 task_answer 的
    {单据: [...]}，跳过的不交；
  - 站点请求：交回答案、判定、存答案、「其他」的服务端搜索（一页 20 条，不一次取全）；
  - 画出来：头部问题 +「第 1 项，共 N 项」与左右箭头、单选圆点 / 多选复选框、选项名与说明、最后固定「其他」、「跳过」「下一步 / 提交」
    带快捷键、卡片下方「需要您的输入」；只有一题时不出翻页；
  - ask_user 走「允许」那一套（W2：工具声明要等人作答）：页面照常提醒服务端执行、门回来就是等作答的卡片；「提交」= 对话的「允许」带上
    作答（confirm 的 answers：按题序 [{labels, other?, skipped?}]）；手机上不浮成允许卡；不进步骤组；代录开始卡只剩写入目标与开始，
    中途那张用同一张卡。

    python3 tests/test_cc_ask.py
"""
import sys
import unittest

from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, ROOT + "/tests")
from test_assistant_ui import ESBUILD, NODE, SOCKET_STUB, run_ts  # noqa: E402

SRC = Path(ROOT) / "frontend/assistant/src"

STUBS = {"socket.io-client": SOCKET_STUB, "@/lib/profile": 'export const APP_NAME = "CC"; export const useProfile = () => undefined; '
         'export const loadProfile = async () => null; export const assistantMascot = () => null; export const assistantLogo = () => null;'}

INPUT = r"""
const input: any = { questions: [
  { question: "Which account set?", header: "Book", multiSelect: false, options: [
    { label: ask.recommended("Acme 2026"), description: "Used all of 2026-10" }, { label: "Acme 2025", description: "Last year" }] },
  { question: "Which supplier record?", header: "Supplier", options: [{ label: "V0012", description: "" }, { label: "V0019" }],
    search: { company: "CO-1", doc_id: "D3", kind: "parties" } },
  { question: "Post which?", header: "Post", multiSelect: true, options: [{ label: "Slips" }, { label: "50 Tawi" }, { label: "" }] },
  { question: "q4", options: [{ label: "a" }, { label: "b" }] },
  { question: "q5 is dropped", options: [{ label: "a" }, { label: "b" }] },
  { header: "no question" },
] };
"""

PURE = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import {
  answered, answeredLine, askActions, confirmAnswers, docAnswers, initialStates, isRecommended, itemQuestion, itemQuestions, itemsOfCheck,
  itemsOfNeeded, keyAnswers, PRECHECK_API, questionsOf, resultFrom, SEARCH_PAGE, setAskSite, shownQuestions, toggle, toggleOther,
} from "@/components/tool-uis/ask-shared";
import { ask } from "@/i18n";

const g = globalThis as any;
""" + INPUT + r"""
(async () => {
  const qs = questionsOf(input);
  assert.equal(qs.length, 4);                                     // 最多 4 题，没有问题的不算
  assert.equal(qs[2]!.options.length, 2);                         // 空选项去掉
  assert.equal(qs[2]!.multiSelect, true);
  assert.deepEqual(qs[1]!.search, { company: "CO-1", doc_id: "D3", kind: "parties" });
  assert.deepEqual(questionsOf(null), []);
  // 推荐项（三语写法都认）默认选中
  for (const word of ["(推荐)", "(Recommended)", "(แนะนำ)", "（推荐）"]) assert.ok(isRecommended({ label: `X ${word}` }), word);
  assert.ok(!isRecommended({ label: "Acme 推荐版" }));
  let states = initialStates(qs);
  assert.deepEqual(states.map((s) => s.labels), [[ask.recommended("Acme 2026")], [], [], []]);
  // 单选换成点的那个；「其他」与选项互斥；多选开关
  states[0] = toggle(qs[0]!, states[0]!, "Acme 2025");
  assert.deepEqual(states[0]!.labels, ["Acme 2025"]);
  states[1] = toggleOther(qs[1]!, states[1]!);
  assert.equal(answered(states[1]), false);                       // 「其他」还没填
  states[1] = { ...states[1]!, other: "V0031 New", otherCode: "V0031" };
  assert.equal(answered(states[1]), true);
  assert.deepEqual(toggle(qs[1]!, states[1]!, "V0012"), { labels: ["V0012"], other: null });
  states[2] = toggle(qs[2]!, toggle(qs[2]!, states[2]!, "Slips"), "50 Tawi");
  assert.deepEqual(states[2]!.labels, ["Slips", "50 Tawi"]);
  assert.deepEqual(toggle(qs[2]!, states[2]!, "Slips").labels, ["50 Tawi"]);
  // 交回：按题序，「其他」交编码，没答的标跳过
  const result = resultFrom(qs, states);
  assert.deepEqual(result, { answers: [
    { header: "Book", question: "Which account set?", labels: ["Acme 2025"] },
    { header: "Supplier", question: "Which supplier record?", labels: [], other: "V0031 New", code: "V0031" },
    { header: "Post", question: "Post which?", labels: ["Slips", "50 Tawi"] },
    { header: "", question: "q4", labels: [], skipped: true },
  ] });
  // 随「允许」交给服务端的作答：按题序，只留选的、「其他」、跳过
  // 「其他」选的条目交「编码 名称」给模型（W2 约定），编码只留在页面
  assert.deepEqual(confirmAnswers(result), [{ labels: ["Acme 2025"] }, { labels: [], other: "V0031 New" }, { labels: ["Slips", "50 Tawi"] }, { labels: [], skipped: true }]);
  const line = answeredLine(result);
  assert.ok(line.includes("Acme 2025") && line.includes("V0031 New") && line.includes(ask.skip), line);

  // 页面自己出题：共用判定的回包 → 没定下的项；一项一题，预选排第一带「(推荐)」，其余进「其他」
  const reply = { groups: [
    { group: "purchase", items: [
      { key: "k1", kind: "party", subject: "TAX1", label: "Shop A", state: "proposed", value: "V0012", value_label: "V0012 Shop A", docs: ["D1", "D2"],
        options: [{ code: "V0012", name: "Shop A" }, { code: "V0019", name: "Shop A 2" }, { code: "__new__", name: "" }] },
      { key: "k2", kind: "party", subject: "TAX2", label: "Shop B", state: "decided", value: "V0001", docs: ["D3"] },
    ] },
    { group: "service", items: [{ key: "k3", kind: "account", subject: "Kerry", label: "Kerry", state: "open", value: "", docs: ["D4"], search: "accounts" }] },
  ] };
  const items = itemsOfCheck(reply);
  assert.deepEqual(items.map((i) => i.key), ["k1", "k3"]);        // 已定的不出题
  const questions = items.map((i) => itemQuestion(i, "CO-1"));
  assert.equal(questions[0]!.options[0]!.label, ask.recommended("V0012 Shop A"));
  assert.ok(isRecommended(questions[0]!.options[0]));
  assert.equal(questions[0]!.options.length, 3);
  assert.equal(questions[1]!.options.length, 1);                  // 未定：只有「新建」，其余去「其他」搜
  assert.ok(!isRecommended(questions[1]!.options[0]));
  assert.deepEqual(questions[1]!.search, { company: "CO-1", doc_id: "D4", kind: "accounts" });
  const picked = { answers: [{ header: "", question: "", labels: [questions[0]!.options[1]!.label] }, { header: "", question: "", labels: [], other: "5310 Power", code: "5310" }] };
  assert.deepEqual(keyAnswers(items, questions, picked as any), [{ key: "k1", value: "V0019" }, { key: "k3", value: "5310" }]);
  assert.deepEqual(keyAnswers(items, questions, { answers: [{ header: "", question: "", labels: [], skipped: true }, { header: "", question: "", labels: [], skipped: true }] }), []);
  // 代录中途那一张：precheck 的 needed，同一类同一对象只问一次；答案展开到每张单
  const needed = itemsOfNeeded([
    { doc_id: "A", state: "choose", needed: [{ kind: "item", subject: "S1", label: "Paper", options: [{ code: "__new__", name: "" }, { code: "P1", name: "Paper A4" }] }] },
    { doc_id: "B", state: "choose", needed: [{ kind: "item", subject: "S1", label: "Paper", options: [] }] },
    { doc_id: "C", state: "ready" },
  ] as any);
  assert.deepEqual(needed.map((i) => i.docs), [["A", "B"]]);
  const nq = needed.map((i) => itemQuestion(i, "CO-1"));
  assert.equal(isRecommended(nq[0]!.options[0]), false);           // 没有预选：不默认选
  assert.deepEqual(docAnswers(needed, nq, { answers: [{ header: "", question: "", labels: [nq[0]!.options[1]!.label] }] }), {
    A: [{ kind: "item", subject: "S1", value: "P1", label: "Paper" }], B: [{ kind: "item", subject: "S1", value: "P1", label: "Paper" }] });

  // 测试机 11-09（shots/12）：选了「按服务录入」还问从哪个仓库出库。show_if：前面那一题选中其中之一才问，否则藏起、交回 not_asked
  const cond = questionsOf({ questions: [
    { question: "Stock or service?", header: "Entry", options: [{ label: "Stock (推荐)" }, { label: "Service" }] },
    { question: "Which warehouse?", header: "Warehouse", options: [{ label: "01" }, { label: "02" }], show_if: { question: 1, labels: ["Stock (推荐)"] } },
    { question: "Which customer?", header: "Customer", options: [{ label: "C1" }, { label: "C2" }] },
  ] });
  assert.deepEqual(cond[1]!.showIf, { question: 1, labels: ["Stock (推荐)"] });
  let cs = initialStates(cond);
  assert.deepEqual(shownQuestions(cond, cs), [0, 1, 2]);              // 预选走库存：出库仓照问
  cs[0] = toggle(cond[0]!, cs[0]!, "Service");
  cs[1] = toggle(cond[1]!, cs[1]!, "01");
  cs[2] = toggle(cond[2]!, cs[2]!, "C2");
  assert.deepEqual(shownQuestions(cond, cs), [0, 2]);                 // 改成按服务：出库仓收起
  const condResult = resultFrom(cond, cs);
  assert.deepEqual(condResult.answers[1], { header: "Warehouse", question: "Which warehouse?", labels: [], not_asked: true });
  assert.deepEqual(confirmAnswers(condResult)[1], { labels: [], not_asked: true });
  assert.ok(!answeredLine(condResult).includes("Warehouse"), answeredLine(condResult));
  // 页面自己出题：带 when 的项排到它依赖的那一项后面，题目带 show_if；依赖的那一项答成别的值，这一项不交
  const conditional = itemsOfCheck({ groups: [{ group: "stock", items: [
    { key: "loc", kind: "location", subject: "*", label: "", state: "open", value: "", docs: ["D1"], when: { key: "gp", value: "stock" },
      options: [{ code: "01", name: "Main" }, { code: "02", name: "Branch" }] },
    { key: "gp", kind: "goods_posting", subject: "tax:1", label: "", state: "proposed", value: "stock", docs: ["D1"],
      options: [{ code: "stock", name: "" }, { code: "service", name: "" }] },
  ] }] });
  assert.deepEqual(conditional.map((i) => i.key), ["gp", "loc"]);
  const cq = itemQuestions(conditional, "CO-1");
  assert.deepEqual(cq[1]!.showIf, { question: 1, labels: [cq[0]!.options[0]!.label] });
  const serviceAnswer = resultFrom(cq, [toggle(cq[0]!, initialStates(cq)[0]!, cq[0]!.options[1]!.label), { labels: [cq[1]!.options[0]!.label], other: null }]);
  assert.deepEqual(keyAnswers(conditional, cq, serviceAnswer), [{ key: "gp", value: "service" }]);

  // 站点请求
  const calls: any[] = [];
  setAskSite({
    post: async (method: string, args: any) => (calls.push(["post", method, args]), {}) as any,
    get: async (method: string, args: any) => (calls.push(["get", method, args]), { options: [{ code: "5310", name: "Power" }, { code: "" }], more: true }) as any,
  });
  await askActions.check("CO-1", ["D1"]);
  assert.deepEqual(calls.at(-1), ["post", PRECHECK_API + "check", { company: "CO-1", doc_ids: '["D1"]', mode: "pending" }]);
  await askActions.remember("CO-1", ["D1"], [{ key: "k1", value: "V0019" }]);
  assert.deepEqual(calls.at(-1), ["post", PRECHECK_API + "answer", { company: "CO-1", doc_ids: '["D1"]', answers: '[{"key":"k1","value":"V0019"}]' }]);
  const page = await askActions.search({ company: "CO-1", doc_id: "D4", kind: "accounts" }, "pow", 20);
  assert.deepEqual(calls.at(-1), ["get", PRECHECK_API + "search", { company: "CO-1", doc_id: "D4", search: "accounts", txt: "pow", start: 20, page_length: SEARCH_PAGE }]);
  assert.equal(SEARCH_PAGE, 20);
  assert.deepEqual(page, { options: [{ code: "5310", name: "Power" }], more: true });
  g.done();
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""

RENDER = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.browser";
import { AskCard, askPhase, askResultOf } from "@/components/tool-uis/ask-card";
import { questionsOf } from "@/components/tool-uis/ask-shared";
import { ask, t } from "@/i18n";

const g = globalThis as any;
""" + INPUT + r"""
const qs = questionsOf(input);
const html = (questions: any, extra: any = {}) =>
  renderToStaticMarkup(createElement(AskCard, { questions, phase: "ask", busy: false, failure: "", onSubmit: () => {}, ...extra }));
const out = html(qs);
// 头部：问题 + 第 1 项，共 4 项 + 左右箭头（第一项时左箭头不能点）
assert.ok(out.includes(">Which account set?<"));
assert.ok(out.includes(ask.nth(1, 4)));
assert.match(out, new RegExp(`aria-label="${ask.prev}" disabled=""`));
assert.ok(out.includes(`aria-label="${ask.after}"`));
// 单选圆点：推荐项默认选中；选项名与说明；最后固定「其他」
const radios = [...out.matchAll(/role="radio" aria-checked="(true|false)"/g)].map((m) => m[1]);
assert.deepEqual(radios, ["true", "false", "false"]);
assert.ok(out.includes("Used all of 2026-10"));
assert.match(out, new RegExp(`data-slot="cc-askq-other-option"[\\s\\S]*>${ask.other}<`));
// 底部：「跳过」与「下一步」带快捷键；卡片下方「需要您的输入」
assert.ok(out.includes(`>${ask.skip}<`));
assert.match(out, new RegExp(`data-slot="cc-askq-next">${ask.next}<span class="cc-menu-key">(Ctrl|⌘)↵</span>`));
assert.ok(out.includes(t("needsInput")));
// 只有一题：不出翻页，主按钮是「提交」
const one = html([qs[2]]);
assert.ok(!one.includes("cc-askq-pager"));
assert.match(one, new RegExp(`data-slot="cc-askq-next" disabled="">${ask.submit}<`));   // 没选不能提交
assert.equal([...one.matchAll(/role="checkbox"/g)].length, 3);                       // 多选题：复选框（含「其他」）
// 带 show_if 的题不占位置：第二题只在第一题选 Stock 时问，第一题预选 Service → 只剩两题
const gated = questionsOf({ questions: [
  { question: "Stock or service?", header: "Entry", options: [{ label: "Service (推荐)" }, { label: "Stock" }] },
  { question: "Which warehouse?", header: "Warehouse", options: [{ label: "01" }, { label: "02" }], show_if: { question: 1, labels: ["Stock"] } },
  { question: "Which customer?", header: "Customer", options: [{ label: "C1" }, { label: "C2" }] },
] });
assert.ok(html(gated).includes(ask.nth(1, 2)), "hidden question does not count");
// 嵌在列表里（不带框）时不出「需要您的输入」；ask_user 在电脑上由对话的工作状态行画（status=false）
assert.ok(!html(qs, { framed: false }).includes(t("needsInput")));
assert.ok(!html(qs, { status: false }).includes(t("needsInput")));
// 点完：问话 + 一行所选；没答就没了写「已取消」
const done = html(qs, { phase: "answered", result: { answers: [{ header: "Book", question: "", labels: ["Acme 2025"] }] } });
assert.ok(done.includes("Book：Acme 2025") && !done.includes("cc-askq-next"));
assert.ok(html(qs, { phase: "cancelled" }).includes('data-slot="cc-ask-answer"'));
// 工具卡片的样子同「允许」卡片的判定：门回来等作答 = ask；点了 / 有结果 = 点完；没答这一轮就结束了 = 已取消；门还没回来 = null（画着、不能点）
const gates = { ask: { ask_user: "Waiting for answers" } };
assert.equal(askPhase({ toolName: "ask_user", approval: { id: "a" }, run: "awaiting_approval" } as any, gates), "ask");
assert.equal(askPhase({ toolName: "ask_user", approval: { id: "a", approved: true } } as any, gates), "answered");
assert.equal(askPhase({ toolName: "ask_user", approval: { id: "a" }, run: "done", result: { text: "{}" } } as any, gates), "answered");
assert.equal(askPhase({ toolName: "ask_user", status: { type: "complete" } } as any, gates), "cancelled");
assert.equal(askPhase({ toolName: "ask_user", status: { type: "running" } } as any, gates), null);
assert.deepEqual(askResultOf({ text: '{"answers":[{"header":"a","question":"b","labels":["c"]}]}' }), { answers: [{ header: "a", question: "b", labels: ["c"] }] });
assert.equal(askResultOf({ text: "oops" }), null);
g.done();
"""

FEED = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { project } from "@/lib/managed-agents/reducer";

const g = globalThis as any;
const at = (s: number) => `2026-11-05T08:00:${String(s).padStart(2, "0")}.000000Z`;
// CC 调 ask_user：同别的本站工具，页面照常提醒服务端执行（服务端登记成等作答、回门），不当代录卡片跳过
const state = project([
  { type: "user.message", id: "sevt_1", processed_at: at(0), content: [{ type: "text", text: "开始" }] },
  { type: "agent.custom_tool_use", id: "sevt_2", name: "ask_user", processed_at: at(1), input: { questions: [{ question: "q", options: [{ label: "a" }, { label: "b" }] }] } },
  { type: "session.status_idle", id: "sevt_3", processed_at: at(2), stop_reason: { type: "requires_action", event_ids: ["sevt_2"] } },
] as any);
assert.deepEqual(state.pendingCustomTools, ["sevt_2"]);
const card = state.messages.flatMap((m) => m.content).find((p: any) => p.toolCallId === "sevt_2") as any;
assert.equal(card.toolName, "ask_user");
g.done();
"""


def read(rel):
    return (SRC / rel).read_text(encoding="utf-8")


class SourceRules(unittest.TestCase):
    def test_ask_user_is_a_card_tool(self):
        kit = read("components/tool-uis/index.tsx")
        # 作答随「允许」交给服务端（confirm 的 answers），不另开接口；手机上不浮成允许卡
        card = read("components/tool-uis/ask-card.tsx")
        self.assertIn("getSessionController(sessionId).respondToApproval(part.approval.id, true, undefined, confirmAnswers(result));", card)
        controller = read("lib/managed-agents/session-controller.ts")
        self.assertIn("respondToApproval(toolUseId: string, approved: boolean, reason?: string, answers?: unknown) {", controller)
        self.assertIn("...(approved && answers !== undefined ? { answers } : {}),", controller)
        self.assertIn("part.toolName === ASK_TOOL) return null;", read("components/tool-uis/approval-sheet.tsx"))
        self.assertIn('ask_user: { type: "backend", render: AskUserCard },', kit)
        self.assertIn('new Set([...OPERATOR_CARDS, "ask_user"])', kit)
        self.assertIn("CARD_TOOLS.has(part.toolName)", read("components/assistant-ui/thread.tsx"))
        self.assertNotIn("ask_user", read("lib/managed-agents/events.ts"))   # 不当代录卡片：照常提醒服务端执行

    def test_operator_cards_use_the_question_card(self):
        start = read("components/tool-uis/operator-start.tsx")
        self.assertNotIn("<select", start)
        # 没定的项开始前在这张卡里用提问卡一次问完、答案随「开始」交（11-08 生产事故）；都定了照旧不带答案
        self.assertIn("operatorActions.start(input, docIds, answers, groupsToStart(", start)
        self.assertIn("const start = () => begin({});", start)
        self.assertIn("<AskCard questions={questions}", start)
        choose = read("components/tool-uis/operator-choose.tsx")
        self.assertIn("<AskCard questions={questions}", choose)
        self.assertNotIn("<select", choose)
        for rel in ("components/tool-uis/ask-card.tsx", "components/tool-uis/ask-shared.ts"):
            for gone in ("confirmTools", "runTool", "answer_question"):
                self.assertNotIn(gone, read(rel), rel)

    def test_big_lists_are_searched_on_the_server(self):
        card = read("components/tool-uis/ask-card.tsx")
        self.assertIn("askActions.search(search, txt.trim(), 0)", card)
        self.assertIn("askActions.search(search!, txt.trim(), found?.options.length ?? 0)", card)


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class Behaviour(unittest.TestCase):
    def test_questions_answers_and_requests(self):
        for lang in ("zh", "th", "en"):
            run_ts(PURE, stubs=STUBS, lang=lang)

    def test_card_renders_like_the_code_question_card(self):
        for lang in ("zh", "en"):
            run_ts(RENDER, stubs=STUBS, lang=lang)

    def test_ask_user_is_run_like_other_site_tools(self):
        run_ts(FEED, stubs=STUBS, lang="zh")


if __name__ == "__main__":
    unittest.main()
