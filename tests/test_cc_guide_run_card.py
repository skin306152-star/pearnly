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
        self.assertIn("if (!forms.includes(business)) {\n    chooseBusiness(sessionId, business);", body)
        # 新建页（还没有会话）点按表单的业务：先建会话（同发第一句，带下巴选的公司 / 月份 / 账套），再开引导（实验栈 11-17：以前只记业务、卡不出）
        self.assertIn("const id = sessionId ?? (await ensureSessionOf(item, getSessionController)", body)
        self.assertIn("startGuide(id, business)", body)
        self.assertIn("noteStartedRun(id", body)
        self.assertIn("pickBusiness(sessionId, business, offered?.forms ?? [], aui.threadListItem())", thread)
        # 点菜单开的卡也是对话内容：不出欢迎语；卡还开着时菜单不出（不再开第二张）；卡取到的新一版跟进本页记的状态
        self.assertIn("const isEmpty = useAuiState(isNewChatView) && !pending && !guides && !prompted;", thread)
        self.assertIn('const OPEN_GUIDE: ReadonlySet<string> = new Set(["asking", "reviewing"]);', thread)
        self.assertIn("noteRunView(sessionId, view)", read("components/tool-uis/guide-run-card.tsx"))
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
        # 开始先后按服务端给的建起来时刻（不随每一步变）；旧事件没有才回落到第一次收到的那条（11-26 待处理 → 销售引导顺序颠倒）
        self.assertIn("startedAt: event.started_at || was?.startedAt || event.processed_at", reducer)
        self.assertIn("started_at?: string;", read("lib/managed-agents/events.ts"))

    def test_the_card_sends_only_chosen_values_and_confirms_with_the_shown_version(self):
        card = read("components/tool-uis/guide-run-card.tsx")
        lib = read("lib/guide-run.ts")
        # 金额行按 rowsToSend 交（lib/guide-run.ts 的 toSend，A2 线 11-16 把一题的值的规则收进纯函数）
        self.assertIn('if (question.input === "amounts" || question.input === "split") return rowsToSend(value as AmountRow[]);', lib)
        self.assertIn("answerGuide(view.run, given, view.version)", card)
        self.assertIn("confirmGuide(view.run, view.version, seen)", read("components/tool-uis/guide-review.tsx"))  # 单据复核另带复核指纹（documents 源）
        review = read("components/tool-uis/guide-review.tsx")
        self.assertIn('label: label ?? (allow ? t("allow") : t("recordNow"))', review)  # 复核卡沿用已上线的词
        # 没有复核的表单答完了（11-23）：出确定卡，同一组按钮交 guide_confirm，不写账的叫「确定」
        self.assertIn('buttons={decideButtons(props, card.ready, undefined, t("confirm"))}', review)
        self.assertIn('if (!documents && view.status === "asking" && view.card?.ready && view.card.questions.every((q) => !q.required)) '
                      'return <GuideReady {...props} />;', card)
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


class PageEndpointTests(unittest.TestCase):
    """页面（A2 的 lib/guide-run.ts）调的每个引导接口在服务端都有，方法对、参数名对、交给引擎同名的函数（约定见共享笔记
    「引导卡页面接口-约定-11-19.md」）：改一步 guide_reopen、就地改一格 guide_edit、结果里的追问 guide_follow、确定带复核指纹 seen。"""

    API = (ROOT / "apps/pearnly_core/pearnly_core/assistant/api.py").read_text(encoding="utf-8")

    def body(self, name):
        start = self.API.index(f"\ndef {name}(")
        end = self.API.find("\n@frappe.whitelist", start)
        return self.API[start:end if end > 0 else None]

    def test_every_page_call_has_its_endpoint(self):
        for name, method, args, call in (
                ("guide_reopen", "POST", "(run_name, step, version)", "run.reopen("),
                ("guide_edit", "POST", "(run_name, doc_id, field, value=\"\", version=0)", "run.edit("),
                ("guide_follow", "POST", "(run_name, answers, version=None)", "run.follow("),
                ("guide_confirm", "POST", "(run_name, version, seen=None)", "run.confirm(")):
            self.assertIn(f'@frappe.whitelist(methods=["{method}"])\ndef {name}{args}:', self.API, name)
            self.assertIn(call, self.body(name), name)

    def test_follow_takes_the_answers_as_json_and_confirm_hands_seen_on(self):
        self.assertIn("json.loads(answers) if isinstance(answers, str) else answers", self.body("guide_follow"))
        self.assertIn('None if seen in (None, "") else str(seen)', self.body("guide_confirm"))


if __name__ == "__main__":
    unittest.main()
