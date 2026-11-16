"""按表单的引导卡接进对话页（引导式 P0 接线）：菜单上按表单的业务开一次引导运行，对话里按运行画卡。

断言的是应有状态（源码与纯逻辑）：
- 菜单：服务端 ``forms`` 里的业务且已有会话 → ``startGuide``（不走第 1 期的「选业务 + CC 说一句」）；其余照第 1 期；
- 对话末尾：事件里的与本页刚开的运行合在一起，同一次取版本大的，按开始先后排；
- 卡：按服务端的 ``view`` 画——问的时候只交点选 / 填的值（金额行按 ``rowsToSend``），复核里「写入」用已上线的词、确定带版本，
  结果一行；值不经页面拼接；标准组件（Button、RadioGroup、Input、Item、guide-inputs）。

    python3 tests/test_cc_guide_run_card.py
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend/assistant/src"


def read(rel):
    return (SRC / rel).read_text(encoding="utf-8")


class GuideRunCardTests(unittest.TestCase):
    def test_the_menu_opens_a_form_guide_only_for_form_businesses_in_a_conversation(self):
        thread = read("components/assistant-ui/thread.tsx")
        body = thread[thread.index("function pickBusiness"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("if (!sessionId || !forms.includes(business)) {\n    chooseBusiness(sessionId, business);", body)
        self.assertIn("startGuide(sessionId, business)", body)
        self.assertIn("noteStartedRun(sessionId", body)
        self.assertIn("pickBusiness(sessionId, business, offered?.forms ?? [])", thread)
        self.assertIn('forms: out?.forms ?? []', read("lib/business.ts"))

    def test_the_conversation_draws_each_run_once_with_the_newest_version_in_start_order(self):
        thread = read("components/assistant-ui/thread.tsx")
        body = thread[thread.index("function useGuideRunList(): GuideRun[]"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("if (!was || was.version <= run.version) byRun.set(run.run, run);", body)
        self.assertIn("a.startedAt.localeCompare(b.startedAt)", body)
        self.assertIn("<BusinessPrompt />\n            <GuideRuns />", thread)
        self.assertIn("<GuideRunCard key={run.run} run={run} sessionId={snapshot.sessionId} />", thread)
        reducer = read("lib/managed-agents/reducer.ts")
        self.assertIn('case "pearnly.guide": {', reducer)
        self.assertIn("if (was && was.version >= event.version) return state;", reducer)

    def test_the_card_sends_only_chosen_values_and_confirms_with_the_shown_version(self):
        card = read("components/tool-uis/guide-run-card.tsx")
        lib = read("lib/guide-run.ts")
        # 金额行按 rowsToSend 交（lib/guide-run.ts 的 toSend，A2 线 11-16 把一题的值的规则收进纯函数）
        self.assertIn('if (question.input === "amounts" || question.input === "split") return rowsToSend(value as AmountRow[]);', lib)
        self.assertIn("answerGuide(view.run, given, view.version)", card)
        self.assertIn("confirmGuide(view.run, view.version, seen)", read("components/tool-uis/guide-review.tsx"))  # 单据复核另带复核指纹（documents 源）
        self.assertIn('label: allow ? t("allow") : t("recordNow")', read("components/tool-uis/guide-review.tsx"))  # 已上线的词，不新增文案
        for kit in ('from "@/components/ui/button"', 'from "@/components/ui/questionnaire"', 'from "./guide-inputs"'):
            self.assertIn(kit, card)
        self.assertNotIn("<button", card)  # 不手写通用控件

    def test_the_page_calls_go_to_the_guide_endpoints(self):
        lib = read("lib/guide-run.ts")
        for name in ("guide_start", "guide_view", "guide_answer", "guide_confirm", "guide_cancel", "guide_search"):
            self.assertIn(f'"{name}"', lib)
        api = (ROOT / "apps/pearnly_core/pearnly_core/assistant/api.py").read_text(encoding="utf-8")
        for name in ("guide_start", "guide_answer", "guide_confirm", "guide_cancel"):
            self.assertIn(f'@frappe.whitelist(methods=["POST"])\ndef {name}(', api)
        self.assertIn('"labels": labels, "icons": {guide["id"]: guide["icon"] for guide in declared}', api)
        for name in ("guide_view", "guide_search"):
            self.assertIn(f'@frappe.whitelist(methods=["GET"])\ndef {name}(', api)


if __name__ == "__main__":
    unittest.main()
