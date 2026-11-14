"""CC 引导卡（工具 pearnly_guide，``frontend/assistant/src/components/tool-uis/guide-card.tsx``，引导式录入第 1 期）。

断言的是应有状态：
- 卡由服务端出（调用的输入 ``card``）：页面只照它画——题目文字、选项文字是服务端翻好的；
- 三档：「怎么录」的题推荐项带「推荐」徽章并默认选中；「做不做」的题不预选、不带徽章；已定的不出题、收进「已定 n 项」，展开可改；
- 交回 ``[{key, value}]``：跳过的题不交，已定项只交改过的；
- 用标准组件库（Card / Questionnaire / Badge / Collapsible / NativeSelect），四语词条齐；
- 单独成卡（不进步骤组）、手机上不浮成允许卡。

    python3 tests/test_cc_guide_card.py
"""
import sys
import unittest
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, ROOT + "/tests")

from test_assistant_ui import run_ts  # noqa: E402
from test_cc_ask import STUBS  # noqa: E402

SRC = Path(ROOT) / "frontend/assistant/src"

CARD = r"""
const card = {
  questions: [
    { key: "k-pay", kind: "payment", tier: "how", title: "PAY-TITLE", title_params: { label: "ABC", count: 5 }, count: 5,
      preselected: "credit",
      options: [{ code: "credit", label: "CREDIT", recommended: true, note: "NOTE-CREDIT" }, { code: "cash", label: "CASH" }] },
    { key: "k-detail", kind: "detail", tier: "whether", title: "DETAIL-TITLE", title_params: { label: "XYZ", count: 1 }, count: 1,
      preselected: "",
      options: [{ code: "amount_only", label: "AMOUNT-ONLY" }, { code: "skip", label: "SKIP-DOC" }] },
  ],
  decided: [
    { key: "k-loc", kind: "location", title: "LOC-TITLE", value: "01", value_label: "Main",
      source_label: "REMEMBERED", options: [{ code: "01", label: "Main" }, { code: "02", label: "Second" }] },
  ],
};
"""

PURE = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { answersFrom, guideCardOf, nothingAsked, optionText, preselectOf } from "@/components/tool-uis/guide-card";
""" + CARD + r"""
// 调用的输入 → 卡；读不出回空卡
assert.deepEqual(guideCardOf({ card }).questions.length, 2);
assert.deepEqual(guideCardOf(null), { questions: [], decided: [] });
assert.equal(optionText({ code: "X", name: "Name" }), "Name");
assert.equal(optionText({ code: "X" }), "X");
// 交回：答了的题；跳过的不交；没值的不交；已定项只交改过的
assert.deepEqual(answersFrom(card, { "k-pay": "cash", "k-detail": "skip", "decided:k-loc": "01" }, new Set()),
  [{ key: "k-pay", value: "cash" }, { key: "k-detail", value: "skip" }]);
assert.deepEqual(answersFrom(card, { "k-pay": "cash", "k-detail": "skip" }, new Set(["k-detail"])),
  [{ key: "k-pay", value: "cash" }]);
assert.deepEqual(answersFrom(card, { "k-pay": "credit", "decided:k-loc": "02" }, new Set()),
  [{ key: "k-pay", value: "credit" }, { key: "k-loc", value: "02" }]);
assert.deepEqual(answersFrom(card, {}, new Set()), []);
// 预选：怎么录那一题是服务端的预选值；做不做那一题、预选值不在选项里的都不选
assert.equal(preselectOf(card.questions[0]), "credit");
assert.equal(preselectOf(card.questions[1]), "");
assert.equal(preselectOf({ ...card.questions[1], preselected: "skip" }), "");
assert.equal(preselectOf({ ...card.questions[0], preselected: "bitcoin" }), "");
// 服务端没出卡（nothing_to_ask）：对话里不画卡（不写「已答 0 项」）；答过 / 读不出的照旧
assert.equal(nothingAsked('{"status": "nothing_to_ask", "writable": []}'), true);
assert.equal(nothingAsked({ text: '{"status": "nothing_to_ask"}' }), true);
assert.equal(nothingAsked('{"status": "answered", "answered": 1}'), false);
assert.equal(nothingAsked(undefined), false);
(globalThis as any).done();
"""

RENDER = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.browser";
import { GuideCardBody, askQuestionsOf, guideAnswersOf } from "@/components/tool-uis/guide-card";
import { ask, guide, steps } from "@/i18n";
""" + CARD + r"""
const html = (extra: any = {}) =>
  renderToStaticMarkup(createElement(GuideCardBody, { card, phase: "ask", busy: false, onSubmit: () => {}, ...extra }));
// 同一个提问卡组件（用户 11-13：两张卡同一种版式）：一页一题，卡头题目 + 翻页器，第一项左箭头禁用
const out = html();
assert.ok(out.includes('data-slot="cc-askq-question"') && out.includes("PAY-TITLE") && !out.includes("DETAIL-TITLE"));
assert.ok(out.includes(ask.nth(1, 2)) && out.includes("cc-ui-questionnaire-card"));
assert.match(out, new RegExp(`<button(?=[^>]*aria-label="${ask.prev}")(?=[^>]*disabled="")`));
// 题目下一行：这一题管哪家几张；选项名、灰色说明；推荐徽章只一个、推荐项默认选中
assert.ok(out.includes(guide.documents("ABC", 5)));
assert.ok(out.includes("CREDIT") && out.includes("CASH") && out.includes("NOTE-CREDIT"));
assert.equal(out.split(`>${guide.recommended}<`).length - 1, 1);
const checked = [...out.matchAll(/<input[^>]* checked=""[^>]*value="([^"]+)"/g)].map((m) => m[1]);
assert.deepEqual(checked, ["credit"]);
// 「其他」只在服务端标了 free 的题
assert.ok(!out.includes('data-slot="cc-askq-other-option"'));
// 第二题（做不做）：不预选、不带徽章；末项右箭头禁用、主按钮「提交」
const last = html({ startAt: 1 });
assert.ok(last.includes("DETAIL-TITLE") && !last.includes(`>${guide.recommended}<`));
assert.ok(![...last.matchAll(/<input[^>]* checked=""/g)].length);
assert.match(last, new RegExp(`<button(?=[^>]*aria-label="${ask.after}")(?=[^>]*disabled="")`));
assert.ok(last.includes(`>${ask.submit}<`));
// 已定 n 项
assert.ok(out.includes(guide.decided(1)));
// 允许自己填的题出「其他」
const free = renderToStaticMarkup(createElement(GuideCardBody, { card: { questions: [{ ...card.questions[0], free: true }], decided: [] }, phase: "ask", onSubmit: () => {} }));
assert.ok(free.includes('data-slot="cc-askq-other-option"'));
// 题目映射与答案换回：推荐项排第一；按选项编码交回；跳过 / 没问的不交；free 题「其他」交填的字
const qs = askQuestionsOf(card);
assert.equal(qs[0].options[0].value, "credit");
assert.equal(qs[0].options[0].recommended, true);
assert.equal(qs[1].noOther, true);
assert.deepEqual(guideAnswersOf(card, { answers: [
  { header: "", question: "", labels: ["CASH"] }, { header: "", question: "", labels: [], skipped: true }] }), [{ key: "k-pay", value: "cash" }]);
assert.deepEqual(guideAnswersOf({ ...card, questions: [{ ...card.questions[0], free: true }] },
  { answers: [{ header: "", question: "", labels: [], other: "12.50" }] }), [{ key: "k-pay", value: "12.50" }]);
// 大列表题：带 search 的题按公司与第一张单去服务端搜，出「其他」；搜出来选的交编码，不交显示的字；没给公司照旧不出「其他」
const big = { ...card, questions: [{ ...card.questions[0], search: "accounts", docs: ["D1", "D2"] }] };
assert.deepEqual(askQuestionsOf(big, "C1")[0].search, { company: "C1", doc_id: "D1", kind: "accounts" });
assert.equal(askQuestionsOf(big, "C1")[0].noOther, undefined);
assert.equal(askQuestionsOf(big)[0].noOther, true);
assert.deepEqual(guideAnswersOf(big, { answers: [{ header: "", question: "", labels: [], other: "5100 Sales", code: "5100" }] }),
  [{ key: "k-pay", value: "5100" }]);
// 答完 / 没答就没了
assert.ok(html({ phase: "answered", answered: 2 }).includes(guide.answered(2)));
assert.ok(html({ phase: "cancelled" }).includes(steps.cancelled));
(globalThis as any).done();
"""


def read(rel: str) -> str:
    return (SRC / rel).read_text(encoding="utf-8")


class GuideCardTests(unittest.TestCase):
    def test_answers_follow_the_three_tiers(self):
        run_ts(PURE, stubs=STUBS, lang="zh")

    def test_card_renders_the_server_card_with_kit_components(self):
        for lang in ("zh", "en", "th", "ja"):
            run_ts(RENDER, stubs=STUBS, lang=lang)

    def test_registered_as_a_single_card_not_floated_on_phones(self):
        index = read("components/tool-uis/index.tsx")
        self.assertIn('pearnly_guide: { type: "backend", render: GuideCard }', index)
        self.assertIn('"pearnly_guide"]', index)
        self.assertIn("GUIDE_TOOL) return null;", read("components/tool-uis/approval-sheet.tsx"))

    def test_same_component_as_the_question_card(self):
        """两张卡同一个组件（用户 11-13）：引导卡渲染的就是 AskCard；AskCard 用问卷部件与按钮，不再手写选项行。"""
        source = read("components/tool-uis/guide-card.tsx")
        self.assertIn("<AskCard", source)
        for name in ("@/components/ui/collapsible", "@/components/ui/native-select", "@/components/ui/button"):
            self.assertIn(name, source)
        self.assertNotIn("<button", source)
        self.assertNotIn("<input", source)
        card = read("components/tool-uis/ask-card.tsx")
        self.assertIn("@/components/ui/questionnaire", card)
        self.assertNotIn('className="cc-askq-opt"', card)


if __name__ == "__main__":
    unittest.main()
