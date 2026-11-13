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
import { answersFrom, guideCardOf, optionText, preselectOf } from "@/components/tool-uis/guide-card";
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
(globalThis as any).done();
"""

RENDER = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.browser";
import { GuideCardBody } from "@/components/tool-uis/guide-card";
import { ask, guide, steps } from "@/i18n";
""" + CARD + r"""
const html = (extra: any = {}) =>
  renderToStaticMarkup(createElement(GuideCardBody, { card, phase: "ask", busy: false, onSubmit: () => {}, ...extra }));
const out = html();
// 服务端出的题目与选项文字照画；同一家几张单写明张数
assert.ok(out.includes("PAY-TITLE") && out.includes("DETAIL-TITLE"));
assert.ok(out.includes("CREDIT") && out.includes("CASH") && out.includes("AMOUNT-ONLY"));
assert.ok(out.includes(guide.documents("ABC", 5)));
// 「推荐」徽章只有一个（怎么录那一题的推荐项）
assert.equal(out.split(`>${guide.recommended}<`).length - 1, 1);
// 已定 n 项（展开可改）
assert.ok(out.includes(guide.decided(1)));
// 标准组件
assert.ok(out.includes('data-slot="questionnaire"') && out.includes('data-slot="cc-guide-card"') && out.includes("cc-ui-questionnaire-card"));
// Claude 提问卡版式（用户 11-13）：卡头题目 + 翻页器（第一项左箭头是禁用态，变淡不消失）；选项下一行说明；底部跳过 + 下一步
assert.ok(out.includes("cc-ui-questionnaire-pager"));
assert.match(out, new RegExp(`<button[^>]*disabled=""[^>]*aria-label="${ask.prev}"|<button[^>]*aria-label="${ask.prev}"[^>]*disabled=""`));
assert.ok(out.includes("NOTE-CREDIT") && out.includes('data-slot="questionnaire-choice-description"'));
assert.ok(out.includes(`>${ask.skip}<`));
// 「其他」只在允许自己填的题（这张卡两题都不许）
assert.ok(!out.includes('data-slot="questionnaire-input"'));
const free = renderToStaticMarkup(createElement(GuideCardBody, { card: { questions: [{ ...card.questions[0], free: true }], decided: [] }, phase: "ask", onSubmit: () => {} }));
assert.ok(free.includes('data-slot="questionnaire-input"') && free.includes(`placeholder="${ask.other}"`));
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

    def test_only_kit_components(self):
        source = read("components/tool-uis/guide-card.tsx")
        for name in ("@/components/ui/questionnaire", "@/components/ui/badge", "@/components/ui/button",
                     "@/components/ui/collapsible", "@/components/ui/native-select"):
            self.assertIn(name, source)
        self.assertNotIn("<button", source)
        self.assertNotIn("<input", source)


if __name__ == "__main__":
    unittest.main()
