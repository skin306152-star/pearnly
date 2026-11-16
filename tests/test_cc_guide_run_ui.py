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


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class GuideRunBehaviourTests(unittest.TestCase):
    def test_values_waiting_menu_logic(self):
        run_ts(LOGIC)

    def test_the_fixture_engine_walks_the_whole_collection(self):
        run_ts(FLOW)

    def test_the_card_draws_in_four_languages(self):
        words = {"zh": "已失效", "th": "หมดอายุ", "en": "Expired", "ja": "期限切れ"}
        for lang, word in words.items():
            with self.subTest(lang=lang):
                # 快捷键提示经 shortcut → lib/desktop → lib/profile（Vite 的 import.meta.glob）：同别的页面测试桩掉 profile
                out = run_ts(RENDER, lang=lang, stubs={"@/lib/profile": 'export const APP_NAME = "CC"; export const useProfile = () => undefined;'})
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
