"""按表单的引导卡（引导式分支 3 前端 F1–F5，A2 线 G1）：组件吃服务端 ``run.view()`` 的形状，值交回的形状同服务端 ``steps.value_problem``。

断言的是应有状态：
- 何时算答好：单选要有码、多选 / 交文件要非空、金额多选每行是正数且不超上限（声明 ``zero`` 时 0 也算）、分摊合计等于总额
  （题目没带总额时取本卡金额多选那一步的合计）、月份 / 日期 / 时刻（HH:MM 24 小时）/ 数字 / 文字、子题要答的都答了；
- 交回的形状：金额两位小数只带填了的、数字去空白、子题只带答了的；开头的值：答过的 > 怎么办题的推荐 > 空（做不做的题不预选）；
- 等读取时退避再取（3、5、8、13 秒……封顶 30 秒），有次数上限；结束 / 失效 / 上下文变了（stale）只剩一行；
- 退回的题：卡停在第一道退回的题；
- 菜单：按声明的 order 排、菜单字用服务端翻好的、没给退回第 1 期的三个；
- 替身引擎（展示页用的）照约定走完收款整条：选客户 → 发票在读 → 勾发票 → 方式 + 凭证 → 复核 → 写入 → 排队 → 已入账；版本不对回 changed；
- 卡在四种语言下都画得出来（问 / 复核 / 结果 / 失效），只用标准组件与令牌。

    python3 tests/test_cc_guide_run_ui.py
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend/assistant/src"
sys.path.insert(0, str(ROOT / "tests"))

from test_assistant_ui import ESBUILD, NODE, run_ts  # noqa: E402
from test_cc_ask import STUBS as ASK_STUBS  # noqa: E402

# 复核 / 题卡引了第 1 期引导卡（→ 提问卡 → 会话控制器、快捷键 → profile 用 Vite 的 import.meta.glob）：同第 1 期卡的测试桩掉
STUBS = ASK_STUBS


def read(rel):
    return (SRC / rel).read_text(encoding="utf-8")


LOGIC = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { answeredOk, firstPosition, initialValue, isClosed, isWaiting, splitTotal, toSend, WAIT_TRIES, waitDelay,
  type GuideQuestion } from "@/lib/guide-run";
import { menuOf, businessName, useBusiness } from "@/lib/business";

const q = (input: string, extra: Partial<GuideQuestion> = {}): GuideQuestion =>
  ({ key: input, input: input as GuideQuestion["input"], rule: "always", tier: "how", title: input, required: true, ...extra });

// 单选 / 多选 / 交文件
assert.equal(answeredOk(q("choice"), "2100883"), true);
assert.equal(answeredOk(q("choice"), ""), false);
assert.equal(answeredOk(q("multi"), []), false);
assert.equal(answeredOk(q("file"), ["UPL-1"]), true);
// 金额多选：上限、0
const inv = q("amounts", { options: [{ code: "IV1", max: "1000.00" }] });
assert.equal(answeredOk(inv, [{ code: "IV1", amount: "600" }]), true);
assert.equal(answeredOk(inv, [{ code: "IV1", amount: "1000.01" }]), false);
assert.equal(answeredOk(inv, [{ code: "IV1", amount: "0" }]), false);
assert.equal(answeredOk({ ...inv, zero: true }, [{ code: "IV1", amount: "0" }]), true);
// 分摊：题目带总额用它，否则用本卡金额多选的合计
const split = q("split", { options: [{ code: "cash" }, { code: "transfer:KB" }] });
const all = [inv, split];
assert.equal(splitTotal(split, all, { amounts: [{ code: "IV1", amount: "600" }] }), "600.00");
assert.equal(splitTotal({ ...split, total: "50.00" }, all, {}), "50.00");
assert.equal(answeredOk(split, [{ code: "cash", amount: "100" }, { code: "transfer:KB", amount: "500" }], "600.00"), true);
assert.equal(answeredOk(split, [{ code: "cash", amount: "100" }], "600.00"), false);
// 一格输入
assert.equal(answeredOk(q("time"), "09:30"), true);
assert.equal(answeredOk(q("time"), "24:00"), false);
assert.equal(answeredOk(q("time"), "9:30"), false);
assert.equal(answeredOk(q("month"), "2026-13"), false);
assert.equal(answeredOk(q("date"), "2026-02-29"), false);
assert.equal(answeredOk(q("number"), " 12.5 "), true);
// 子题
const subs = q("questions", { questions: [
  { key: "kind", input: "choice", title: "k", required: true, options: [{ code: "sales", name: "s" }] },
  { key: "from", input: "time", title: "t", required: false },
] });
assert.equal(answeredOk(subs, { kind: "sales" }), true);
assert.equal(answeredOk(subs, { kind: "other" }), false);
assert.equal(answeredOk(subs, { kind: "sales", from: "25:00" }), false);
assert.equal(answeredOk(subs, {}), false);

// 交回的形状
assert.deepEqual(toSend(inv, [{ code: "IV1", amount: "1,000" }, { code: "IV2", amount: "" }]), [{ code: "IV1", amount: "1000.00" }]);
assert.equal(toSend(q("number"), " 12 "), "12");
assert.deepEqual(toSend(subs, { kind: "sales", from: "" }), { kind: "sales" });

// 开头的值：答过的 > 推荐（只有怎么办的题）> 空
assert.equal(initialValue(q("choice", { value: "A", preselected: "B" })), "A");
assert.equal(initialValue(q("choice", { preselected: "B" })), "B");
assert.equal(initialValue(q("choice", { tier: "whether", preselected: "B" })), undefined);

// 等待退避有上限
assert.deepEqual([0, 1, 2, 3].map(waitDelay), [3000, 5000, 8000, 13000]);
assert.equal(waitDelay(20), 30000);
assert.ok(WAIT_TRIES > 0 && WAIT_TRIES <= 20);
const asking = { run: "R", business: "collection", status: "asking", version: 1 };
assert.equal(isWaiting({ ...asking, card: { questions: [q("amounts", { wait: true })], decided: [], ready: false } }), true);
assert.equal(isWaiting({ ...asking, stale: true, card: { questions: [q("amounts", { wait: true })], decided: [], ready: false } }), false);
assert.equal(isWaiting({ ...asking, status: "confirmed", result: { status: "queued", wait: true } }), true);
// 结束 / 失效
assert.equal(isClosed({ status: "expired" }), true);
assert.equal(isClosed({ status: "cancelled" }), true);
assert.equal(isClosed({ status: "asking", stale: true }), true);
assert.equal(isClosed({ status: "reviewing" }), false);
// 退回的题
assert.equal(firstPosition([q("customer"), q("amounts")], { amounts: { reason: "over_collected" } }), 1);
assert.equal(firstPosition([q("customer")], undefined), 0);

// 菜单：按 order 排、服务端的字；没给退回老三个
const offered = { chat: ["collection", "recon", "purchase"], operator: [], forms: ["collection", "recon"],
  labels: { recon: "对账" }, order: { recon: 1, collection: 2, purchase: 3 } };
assert.deepEqual(menuOf(offered, "chat"), ["recon", "collection", "purchase"]);
assert.deepEqual(menuOf({ chat: ["sales", "purchase"], operator: [] }, "chat"), ["sales", "purchase"]);
useBusiness.setState({ offered });
assert.equal(businessName("recon"), "对账");
assert.equal(businessName("collection"), "Collection");
assert.equal(businessName("unknown_biz"), "unknown_biz");
(globalThis as any).done();
"""

FLOW = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { fixtureTransport as t, seed } from "@/kit/guide-run-fixtures";

const run = seed("collection");
let v = await t.view(run);
assert.equal(v.status, "asking");
assert.deepEqual(v.card!.questions.map((x) => x.key), ["customer"]);
assert.equal(v.card!.questions[0].search, true);
assert.deepEqual(v.card!.decided.map((x) => x.key), ["company", "day", "book"]);
const page = await t.search(run, "customer", "ป้าแดง", 0);
assert.deepEqual(page.options.map((o) => o.code), ["2100883"]);
// 版本不对：changed，不收
v = await t.answer(run, { customer: "2100883" }, 99);
assert.equal(v.outcome, "changed");
v = await t.answer(run, { customer: "2100883" }, v.version);
assert.equal(v.card!.questions[0].key, "invoices");
assert.equal(v.card!.questions[0].wait, true);                  // 第一次取：在读
v = await t.view(run);
assert.equal(v.card!.questions[0].wait, undefined);              // 再取：有了
assert.equal(v.card!.questions[0].options!.length, 3);
v = await t.answer(run, { invoices: [{ code: "IV6900123", amount: "10700.00" }] }, v.version);
assert.deepEqual(v.card!.questions.map((x) => x.key), ["methods"]);
v = await t.answer(run, { methods: [{ code: "transfer:KB", amount: "10700.00" }] }, v.version);
assert.deepEqual(v.card!.questions.map((x) => x.key), ["voucher"]);   // 选了转账才问凭证
v = await t.answer(run, { voucher: ["UPL-1"] }, v.version);
assert.equal(v.status, "reviewing");
assert.equal(v.review!.total, "10700.00");
assert.equal(v.review!.tables![0].rows[0].code, "IV6900123");
v = await t.confirm(run, v.version);
assert.equal(v.status, "confirmed");
assert.equal(v.result!.status, "queued");
assert.equal(v.result!.wait, true);
v = await t.view(run);
assert.equal(v.status, "done");
assert.equal(v.result!.docnum, "RE6911-0042");
v = await t.cancel(seed("collection"));
assert.equal(v.status, "cancelled");
(globalThis as any).done();
"""

RENDER = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.browser";
import { GuideRunView } from "@/components/tool-uis/guide-run-card";
import { fixtureTransport as t, seed } from "@/kit/guide-run-fixtures";

const html = async (name: string) => renderToStaticMarkup(createElement(GuideRunView,
  { view: await t.view(seed(name)), sessionId: "S", busy: false, failure: "", act: () => {} }));
const ask = await html("collection");
assert.match(ask, /data-slot="cc-guide-run"/);
assert.match(ask, /data-slot="cc-guide-question"[^>]*data-key="customer"/);
assert.match(ask, /data-slot="cc-guide-search"/);
assert.match(ask, /data-slot="cc-guide-decided"/);
assert.match(ask, /data-slot="cc-askq-next"/);
const back = await html("collection-returned");
assert.match(back, /data-slot="cc-guide-returned"/);
const review = await html("review");
assert.match(review, /data-slot="cc-guide-review"/);
assert.match(review, /data-slot="cc-guide-confirm"/);
const gone = await html("expired");
assert.match(gone, /data-ended/);
assert.doesNotMatch(gone, /<button/);                            // 失效只剩一行，不能点
const inputs = await html("inputs");
assert.match(inputs, /data-input="month"/);
console.log(JSON.stringify({ ended: gone.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim() }));
(globalThis as any).done();
"""

DOCS = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { canEdit, cellValue, isDocResult, isDocReview, refusedText, type DocRow } from "@/lib/guide-run";
import { askQuestionsOf, guideAnswersOf } from "@/components/tool-uis/guide-card";
import { answered, resultFrom } from "@/components/tool-uis/ask-shared";
import { fixtureTransport as t, seed } from "@/kit/guide-run-fixtures";

// 可改的格：行上带 editable 照它（明细按列 lines.*.<列>）；没带按 review.EDITABLE（表头八格 + 明细四列）
const face = { doc_no: "A", party_name: "P", party_tax: "", party_branch: "", date: "2026-11-03", subtotal: "1", vat: "0.07", total: "1.07",
  lines: [{ description: "x", qty: "1", unit_price: "1", amount: "1" }] };
const row: DocRow = { doc_id: "D", face, marks: [], cells: {}, blocking: [], editable: ["doc_no", "lines.*.qty"] };
assert.equal(canEdit(row, "doc_no"), true);
assert.equal(canEdit(row, "total"), false);
assert.equal(canEdit(row, "lines.0.qty"), true);
assert.equal(canEdit(row, "lines.0.amount"), false);
assert.equal(canEdit({ ...row, editable: undefined }, "total"), true);
assert.equal(canEdit({ ...row, editable: undefined }, "lines.0.amount"), true);
assert.equal(canEdit({ ...row, editable: undefined }, "lines.0.bogus"), false);
assert.equal(cellValue(face, "lines.0.description"), "x");
assert.equal(cellValue(face, "total"), "1.07");

// 按单据整条（替身）：交文件 → 识别中 → 第 1 期题卡 → 复核（改一格、算出来的格拒）→ 指纹不对回 changed → 写入 → 每张一行
const run = seed("purchase");
let v = await t.view(run);
assert.equal(v.source, "documents");
assert.equal(v.stage, "files");
assert.equal(v.card!.questions[0].input, "file");
v = await t.answer(run, { documents: ["UPL-1", "UPL-2"] }, v.version);
assert.equal(v.stage, "reading");
assert.equal(v.uploads!.wait, true);
assert.deepEqual(v.uploads!.files.map((f) => f.state), ["reading", "reading"]);
v = await t.view(run);
assert.equal(v.stage, "asking");
assert.equal((v.card!.questions[0] as any).kind, "payment");          // 第 1 期卡的形状
v = await t.answer(run, { answers: [{ key: "pay|D-1", value: "credit" }] }, v.version);
assert.equal(v.stage, "review");
assert.ok(isDocReview(v.review));
const review = v.review as any;
assert.deepEqual(review.rows.map((r: any) => r.doc_id), ["D-1", "D-2"]);
assert.deepEqual(review.rows[1].blocking, ["doc_no"]);                // 没单号：这一张这次不写
assert.ok(review.rows[1].marks.includes("number"));
v = await t.edit(run, "D-1", "total", "9", v.version);
assert.equal(v.outcome, "refused");                                    // 算出来的格不收
assert.ok(Object.values(v.refused!)[0]);
v = await t.edit(run, "D-1", "lines.0.qty", "12", v.version);
const d1 = (v.review as any).rows[0];
assert.equal(d1.face.subtotal, "1200.00");
assert.equal(d1.face.total, "1284.00");
v = await t.confirm(run, v.version, "old-seen");
assert.equal(v.outcome, "changed");                                    // 复核看到的样子变了：不写
v = await t.confirm(run, v.version, (v.review as any).seen);
assert.ok(isDocResult(v.result));
assert.deepEqual((v.result as any).rows.map((r: any) => r.status), ["queued", "held"]);
assert.equal(v.result!.wait, true);
v = await t.view(run);
assert.deepEqual((v.result as any).rows.map((r: any) => r.status), ["entered", "held"]);
assert.equal((v.result as any).rows[0].docnum, "PO6911-0007");

// 表单复核点一行回那一步：那一步与之后的答案作废，回到问
const back = seed("review");
let r = await t.view(back);
r = await t.reopen(back, "invoices", r.version);
assert.equal(r.status, "asking");
assert.equal(r.card!.questions[0].key, "invoices");
// 结果里的追问（N1b）：答了重算结果
const fol = seed("follow");
let f = await t.view(fol);
assert.equal((f.result as any).questions[0].key, "fee");
f = await t.follow(fol, { fee: "5310" }, f.version);
assert.equal((f.result as any).questions, undefined);

// 拒绝原因只显示服务端翻好的字：引擎 {键: {code, text}}、documents 的 refused_text；只有码时不给人看
assert.equal(refusedText({ refused: { invoices: { code: "over_collected", text: "已被收" } } }, "invoices"), "已被收");
assert.equal(refusedText({ refused: { total: "computed" }, refused_text: { total: "自动计算" } }), "自动计算");
assert.equal(refusedText({ refused: { total: "computed" } }), "");

// 选项带 entry（A3）：交「码:数」，没填数不算答好
const card = { questions: [{ key: "unit|D-1|0", kind: "unit", tier: "how", title: "u",
  options: [{ code: "same", label: "S" }, { code: "factor", label: "F", entry: { input: "number", min_exclusive: "0" } }] }], decided: [] } as any;
const [q] = askQuestionsOf(card);
const state = { labels: ["F"], other: null, entries: { F: "0" } };
assert.equal(answered(state, q), false);                                // 不大于 0
assert.equal(answered({ ...state, entries: { F: "6" } }, q), true);
const result = resultFrom([q], [{ ...state, entries: { F: "6" } }]);
assert.deepEqual(guideAnswersOf(card, result), [{ key: "unit|D-1|0", value: "factor:6" }]);
assert.deepEqual(guideAnswersOf(card, resultFrom([q], [{ labels: ["S"], other: null }])), [{ key: "unit|D-1|0", value: "same" }]);

// 代录开始前（A4）：operator 块、事实行、做不做的「再录一次」
const opRun = seed("operator");
const ov = await t.view(opRun);
assert.equal(ov.operator!.login!.set, false);
assert.equal(ov.operator!.targets[0].computer, "ACC-PC01");
assert.ok(Object.values(ov.card!.facts!).flat().length > 0);
assert.ok((ov.card!.questions as any[]).some((x) => x.kind === "again" && x.tier === "whether" && !x.preselected));
(globalThis as any).done();
"""

RENDER2 = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.browser";
import { GuideRunView } from "@/components/tool-uis/guide-run-card";
import { fixtureTransport as t, seed } from "@/kit/guide-run-fixtures";

const html = async (name: string, steps: (n: string, v: any) => Promise<any> = async (_n, v) => v) => {
  const run = seed(name);
  const view = await steps(run, await t.view(run));
  return renderToStaticMarkup(createElement(GuideRunView, { view, sessionId: "S", busy: false, failure: "", act: () => {} }));
};
const files = await html("purchase");
assert.match(files, /data-input="file"/);
assert.match(files, /data-slot="cc-guide-add-file"/);
const reading = await html("purchase-reading");
assert.match(reading, /data-slot="cc-guide-uploads"/);
const asking = await html("purchase-asking", async (n) => { await t.view(n); return t.view(n); });
assert.match(asking, /data-slot="cc-guide-card"/);                    // 第 1 期那张卡
const review = await html("purchase-review");
assert.match(review, /data-source="documents"/);
assert.match(review, /data-blocking="true"/);
assert.match(review, /data-mark="number"/);
assert.match(review, /data-field="total"[^>]*data-readonly/);               // 算出来的格只读
assert.match(review, /data-slot="cc-guide-confirm"/);
const allow = await html("allow");
assert.match(allow, /data-consent="allow"/);
const follow = await html("follow");
assert.match(follow, /data-slot="cc-guide-files-out"/);
assert.match(follow, /download="recon-2026-10.xlsx"/);
assert.match(follow, /data-slot="cc-guide-follow"/);
const opHead = await html("operator");
assert.match(opHead, /data-slot="cc-op-target"/);
assert.match(opHead, /data-slot="cc-op-authorize"/);
assert.match(opHead, /data-slot="cc-guide-fact"/);
const formReview = await html("review");
assert.match(formReview, /data-slot="cc-guide-back"[^>]*data-step="invoices"/);
console.log(JSON.stringify({ allow: allow.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim(), review: review.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim().slice(0, 400) }));
(globalThis as any).done();
"""


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class GuideRunBehaviourTests(unittest.TestCase):
    def test_values_waiting_menu_logic(self):
        run_ts(LOGIC, stubs=STUBS)

    def test_the_fixture_engine_walks_the_whole_collection(self):
        run_ts(FLOW, stubs=STUBS)

    def test_the_card_draws_in_four_languages(self):
        words = {"zh": "已失效", "th": "หมดอายุ", "en": "Expired", "ja": "期限切れ"}
        for lang, word in words.items():
            with self.subTest(lang=lang):
                # 快捷键提示经 shortcut → lib/desktop → lib/profile（Vite 的 import.meta.glob）：同别的页面测试桩掉 profile
                out = run_ts(RENDER, lang=lang, stubs=STUBS)
                self.assertIn(word, out)


    def test_documents_review_edit_confirm_and_follow(self):
        run_ts(DOCS, stubs=STUBS)

    def test_review_allow_and_result_draw_in_four_languages(self):
        words = {"zh": ("允许", "拒绝", "税号"), "th": ("อนุญาต", "ปฏิเสธ", "เลขประจำตัวผู้เสียภาษี"), "en": ("Allow", "Deny", "Tax ID"),
                 "ja": ("許可", "拒否", "納税者番号")}
        for lang, expect in words.items():
            with self.subTest(lang=lang):
                out = run_ts(RENDER2, lang=lang, stubs=STUBS)
                for word in expect:
                    self.assertIn(word, out)


class GuideRunSourceTests(unittest.TestCase):
    def test_only_standard_parts_and_tokens(self):
        for rel in ("components/tool-uis/guide-run-card.tsx", "components/tool-uis/guide-review.tsx", "components/tool-uis/guide-inputs.tsx",
                    "components/business-icon.tsx"):
            src = read(rel)
            self.assertNotRegex(src, r"#[0-9a-fA-F]{3,6}\b", rel)        # 不写死颜色
            self.assertNotRegex(src, r"\b\d+px\b", rel)                  # 不写死尺寸
            self.assertNotIn("style={{", src, rel)
        card = read("components/tool-uis/guide-run-card.tsx")
        for part in ('from "@/components/ui/questionnaire"', 'from "@/components/ui/button"', 'from "@/components/ui/collapsible"'):
            self.assertIn(part, card)
        self.assertNotIn("<button", card)

    def test_values_come_only_from_the_card(self):
        # 写账的值只来自卡片上的选择与填写：交回走 toSend（码、文件 id、卡上填的数），不拼显示的字
        card = read("components/tool-uis/guide-run-card.tsx")
        self.assertIn("given[question.key] = toSend(question, values[question.key])", card)
        self.assertIn("answerGuide(view.run, given, view.version)", card)
        inputs = read("components/tool-uis/guide-inputs.tsx")
        self.assertIn("onChange(code)", inputs)                         # 单选交码
        self.assertIn("guideFiles(sessionId)", inputs)                  # 文件 id 来自本对话的文件

    def test_menu_reads_labels_icons_order_from_the_server(self):
        thread = read("components/assistant-ui/thread.tsx")
        self.assertIn("const list = menuOf(offered, mode);", thread)
        self.assertIn("<BusinessIcon business={business} icon={offered?.icons?.[business]} />", thread)
        self.assertIn("{businessName(business)}", thread)
        icons = read("components/business-icon.tsx")
        decl = (ROOT / "apps/pearnly_core/pearnly_core/assistant/guide/declaration.py").read_text(encoding="utf-8")
        start = decl.index("ICONS = frozenset({")
        names = [n.strip().strip('"') for n in decl[start + len("ICONS = frozenset({"):decl.index("})", start)].replace("\n", " ").split(",") if n.strip()]
        for name in names:                                               # 声明白名单里的每个图标前端都认识
            self.assertRegex(icons, rf'["\s]{name}"?:', name)


if __name__ == "__main__":
    unittest.main()
