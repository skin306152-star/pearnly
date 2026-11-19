"""CC 引导卡工具 ``pearnly_guide``（``pearnly_core.assistant.guide_tool``，引导式录入第 1 期）：问什么由后台定。

- 出卡前服务端按写账前置判定出卡（题目、选项、推荐、已定项、按本轮语言的文字），全部已定就不出卡、直接回可写的单；
- 页面交回的答案先核对（键在卡上、值在选项里），抢到卡后按引导口径记下（``record(guided=True)``：没点过的预选不生效）；
- ``ask_user`` 出卡前：形状照旧核；引导卡挂着时拒；题目带业务搜索（科目 / 物料 / 对方）时拒并叫用 pearnly_guide。

替身 frappe 与替身判定接口，不连库。

    python3 tests/test_cc_guide_tool.py
"""
import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

TEXTS = {"guide.q.payment": "付款方式", "guide.option.payment.cash": "现金", "guide.option.payment.credit": "赊账",
         "guide.source.remembered": "记住的"}
EXISTS = {"value": False}
SESSION = {"business": ""}


def _install_frappe():
    frappe = types.ModuleType("frappe")
    frappe._ = lambda text: TEXTS.get(text, text)
    frappe.local = types.SimpleNamespace(pearnly_assistant={"session_id": "S1"})
    frappe.db = types.SimpleNamespace(exists=lambda doctype, filters: EXISTS["value"],
                                      get_value=lambda doctype, filters, field: SESSION.get(field),
                                      commit=lambda: None, rollback=lambda: None)
    frappe.throw = lambda message, *a: (_ for _ in ()).throw(ValueError(message))
    frappe.whitelist = lambda **kw: (lambda fn: fn)
    frappe.parse_json = json.loads
    frappe.PermissionError = PermissionError
    frappe.get_hooks = lambda name: {}
    frappe.DoesNotExistError = LookupError
    sys.modules["frappe"] = frappe
    return frappe


_install_frappe()
LEDGER = {"view": {"current": "B1", "options": []}, "chosen": [], "status": "set"}
_ledger = types.ModuleType("pearnly_core.assistant.ledger")
_ledger.SET = "set"
_ledger.view = lambda session: LEDGER["view"]
_ledger.choose = lambda session, ledger_id, by="page": LEDGER["chosen"].append((session, ledger_id, by)) or {"status": LEDGER["status"]}
sys.modules["pearnly_core.assistant.ledger"] = _ledger
import pearnly_core.assistant as _assistant  # noqa: E402
_assistant.ledger = _ledger
from pearnly_core.assistant import ask, guide_tool  # noqa: E402
from pearnly_core.write_precheck import contracts as c  # noqa: E402

PAY = {"kind": "payment", "subject": "purchase|1|abc", "label": "ABC", "options": [{"code": "cash"}, {"code": "credit"}]}


def merged(*items):
    return {**c.merge([{"doc_id": "d1", "items": list(items)}]), "company": "C1", "company_title": "Co"}


class Fakes(unittest.TestCase):
    def setUp(self):
        self.card_value = merged(dict(PAY))
        self.recorded = []
        self.saved = (guide_tool.precheck_api._tool_ids, guide_tool.precheck_api.card, guide_tool.precheck_api.record)
        guide_tool.precheck_api._tool_ids = lambda tool_input: ("C1", list(tool_input.get("doc_ids") or ()))
        guide_tool.precheck_api.card = lambda company, doc_ids, hint="": self.card_value
        guide_tool.precheck_api.record = self.record
        EXISTS["value"] = False
        SESSION["business"] = ""
        LEDGER.update(view={"current": "B1", "options": []}, chosen=[], status="set")

    def tearDown(self):
        guide_tool.precheck_api._tool_ids, guide_tool.precheck_api.card, guide_tool.precheck_api.record = self.saved

    def record(self, company, doc_ids, answers, resume=False, guided=False):
        self.recorded.append({"answers": answers, "guided": guided})
        return {"writable": ["d1"], "held": []}


class Prepare(Fakes):
    def test_open_items_make_a_card_with_server_texts(self):
        found = guide_tool.prepare({"company": "C1", "doc_ids": ["d1"]})
        card = found["input"]["card"]
        question = card["questions"][0]
        self.assertEqual(question["title"], "付款方式")
        self.assertEqual([o["label"] for o in question["options"]], ["现金", "赊账"])
        self.assertEqual(found["input"]["doc_ids"], ["d1"])

    def test_everything_decided_is_no_card(self):
        self.card_value = merged({**PAY, "value": "cash", "source": c.REMEMBERED})
        found = guide_tool.prepare({"company": "C1", "doc_ids": ["d1"]})
        self.assertNotIn("input", found)
        result = json.loads(found["result"])
        self.assertEqual((result["status"], result["writable"]), ("nothing_to_ask", ["d1"]))
        self.assertEqual(result["instruction"], guide_tool.NOTHING_TO_ASK)

    def test_nothing_to_ask_and_nothing_writable_does_not_send_to_the_write_plan(self):
        """不用问、但一张都写不了（都在等电脑读科目表）：不叫模型建写入计划，叫它照实说原因（真模型 11-14 销售格）。"""
        self.card_value = {"ready": [], "items": [], "waiting": [{"doc_id": "d1", "reason": "offline"}], "not_writable": []}
        result = json.loads(guide_tool.prepare({"company": "C1", "doc_ids": ["d1"]})["result"])
        self.assertEqual((result["status"], result["writable"]), ("nothing_to_ask", []))
        self.assertEqual(result["instruction"], guide_tool.NOTHING_WRITABLE)
        self.assertNotIn("pearnly_write_plan) next", result["instruction"])


class Answers(Fakes):
    def card(self):
        return guide_tool.prepare({"company": "C1", "doc_ids": ["d1"]})["input"]

    def test_answers_are_checked_against_the_card(self):
        tool_input = self.card()
        key = tool_input["card"]["questions"][0]["key"]
        with self.assertRaises(ask.AnswerError):
            guide_tool.check(tool_input, [{"key": "nope", "value": "cash"}])
        with self.assertRaises(ask.AnswerError):
            guide_tool.check(tool_input, [{"key": key, "value": "bitcoin"}])
        self.assertEqual(guide_tool.check(tool_input, [{"key": key, "value": "cash"}]), [{"key": key, "value": "cash"}])
        self.assertEqual(self.recorded, [])  # 只核、不记

    def test_result_records_in_guided_mode(self):
        tool_input = self.card()
        key = tool_input["card"]["questions"][0]["key"]
        out = json.loads(guide_tool.result(tool_input, [{"key": key, "value": "cash"}]))
        self.assertEqual(self.recorded, [{"answers": [{"key": key, "value": "cash"}], "guided": True}])
        self.assertEqual((out["status"], out["answered"], out["writable"]), ("answered", 1, ["d1"]))

    def test_refused_by_the_product_saves_nothing(self):
        tool_input = self.card()
        guide_tool.precheck_api.record = lambda *a, **kw: {"errors": [{"key": "k", "code": "precheck.value_not_offered"}]}
        out = json.loads(guide_tool.result(tool_input, []))
        self.assertEqual(out["status"], "refused")

    def test_closing_the_card_holds_every_document(self):
        out = json.loads(guide_tool.skipped(self.card()))
        self.assertEqual((out["status"], out["held"]), ("skipped", ["d1"]))


class BusinessMismatch(Fakes):
    """选了采购却传了销售票（评估 6.3）：卡最前面一道「做不做」题、不预选；没点或答「不写入」那几张留下，答「按票面写入」照常。"""

    def setUp(self):
        super().setUp()
        self.card_value = {**merged(dict(PAY)), "directions": {"d1": "purchase", "d2": "sales"}}
        self.card_value["ready"] = ["d2"]
        SESSION["business"] = "purchase"

    def test_question_comes_first_without_preselect(self):
        card = guide_tool.prepare({"company": "C1", "doc_ids": ["d1", "d2"]})["input"]["card"]
        first = card["questions"][0]
        self.assertEqual((first["kind"], first["tier"], first["preselected"], first["docs"]), ("direction", "whether", "", ["d2"]))
        self.assertEqual([o["code"] for o in first["options"]], ["follow", "skip"])

    def test_no_business_or_same_direction_asks_nothing_extra(self):
        SESSION["business"] = ""
        self.assertNotEqual(guide_tool.prepare({"company": "C1", "doc_ids": ["d1"]})["input"]["card"]["questions"][0]["kind"], "direction")
        SESSION["business"] = "sales"
        self.card_value["directions"] = {"d2": "sales"}
        self.assertNotEqual(guide_tool.prepare({"company": "C1", "doc_ids": ["d1"]})["input"]["card"]["questions"][0]["kind"], "direction")

    def test_unanswered_or_skip_holds_follow_writes(self):
        tool_input = guide_tool.prepare({"company": "C1", "doc_ids": ["d1", "d2"]})["input"]
        self.record = lambda *a, **kw: {"writable": ["d1", "d2"], "held": []}
        guide_tool.precheck_api.record = self.record
        out = json.loads(guide_tool.result(tool_input, []))
        self.assertEqual((out["writable"], out["held"]), (["d1"], ["d2"]))
        key = tool_input["card"]["questions"][0]["key"]
        out = json.loads(guide_tool.result(tool_input, [{"key": key, "value": "follow"}]))
        self.assertEqual(out["writable"], ["d1", "d2"])


class LedgerQuestion(Fakes):
    """账套题由引导卡出（A 线接缝约定）：本对话账套未定才出、最前面、不预选、候选同共用层；答了只经 ledger.choose 存会话；没答这批都留下。"""

    def setUp(self):
        super().setUp()
        LEDGER["view"] = {"current": "", "options": [{"id": "BK-2026", "label": "TEST 2026", "covers": True},
                                                       {"id": "BK-2025", "label": "TEST 2025", "covers": False}]}

    def test_undecided_ledger_asks_first_without_preselect(self):
        card = guide_tool.prepare({"company": "C1", "doc_ids": ["d1"]})["input"]["card"]
        first = card["questions"][0]
        self.assertEqual((first["kind"], first["tier"], first["preselected"]), ("account_set", "whether", ""))
        self.assertEqual([o["code"] for o in first["options"]], ["BK-2026", "BK-2025"])

    def test_decided_ledger_is_not_asked(self):
        LEDGER["view"] = {"current": "BK-2026", "options": [{"id": "BK-2026", "label": "TEST 2026"}]}
        card = guide_tool.prepare({"company": "C1", "doc_ids": ["d1"]})["input"]["card"]
        self.assertNotIn("account_set", [q["kind"] for q in card["questions"]])

    def test_answer_goes_through_ledger_choose_and_unanswered_holds_all(self):
        tool_input = guide_tool.prepare({"company": "C1", "doc_ids": ["d1"]})["input"]
        key = tool_input["card"]["questions"][0]["key"]
        out = json.loads(guide_tool.result(tool_input, [{"key": key, "value": "BK-2025"}]))
        self.assertEqual(LEDGER["chosen"], [("S1", "BK-2025", "page")])
        self.assertEqual(out["writable"], ["d1"])
        self.assertNotIn(key, [a["key"] for a in self.recorded[-1]["answers"]])  # 不交给产品记
        out = json.loads(guide_tool.result(tool_input, []))
        self.assertEqual((out["writable"], out["held"]), ([], ["d1"]))


class AskUserRefusal(Fakes):
    QUESTION = {"question": "Which?", "header": "Pick", "options": [{"label": "A"}, {"label": "B"}]}

    def test_plain_question_passes(self):
        self.assertIsNone(guide_tool.refuse_ask({"questions": [dict(self.QUESTION)]}))

    def test_shape_is_still_checked(self):
        self.assertTrue(guide_tool.refuse_ask({"questions": []}))

    def test_business_search_is_refused(self):
        question = {**self.QUESTION, "search": {"company": "C1", "doc_id": "d1", "kind": "accounts"}}
        self.assertEqual(guide_tool.refuse_ask({"questions": [question]}), guide_tool.ASK_REFUSED)

    def test_waiting_guide_card_refuses(self):
        EXISTS["value"] = True
        self.assertEqual(guide_tool.refuse_ask({"questions": [dict(self.QUESTION)]}), guide_tool.GUIDE_WAITING)


class Businesses(unittest.TestCase):
    """输入框上方的业务菜单：只列这个产品 × 这个模式真能做的（Cowork 两种模式、ERP 代录没有收款；ERP 直写只有收款：采购 / 销售等第 2 期整链验过再放出，主会话 11-14）。"""

    def offered(self, hooks):
        import frappe
        from pearnly_core.assistant import guided
        saved = frappe.get_hooks
        frappe.get_hooks = lambda name: hooks if name == guided.BUSINESS_HOOK else {}
        try:
            return guided.offered()
        finally:
            frappe.get_hooks = saved

    def test_reads_the_declared_lists_and_drops_unknown(self):
        self.assertEqual(self.offered({"chat": ["purchase", "sales", "collection"], "operator": ["purchase", "payment"]}),
                         {"chat": ["purchase", "sales", "collection"], "operator": ["purchase"]})
        self.assertEqual(self.offered({}), {"chat": [], "operator": []})

    def test_products_declare_what_they_can_do(self):
        import ast
        for app, chat, operator in (("pearnly_cowork", ["purchase", "sales"], ["purchase", "sales"]),
                                    ("pearnly_erp_portal", ["collection"], ["purchase", "sales"])):
            tree = ast.parse((ROOT / f"apps/{app}/{app}/hooks.py").read_text(encoding="utf-8"))
            found = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                         and getattr(n.targets[0], "id", "") == "pearnly_guide_businesses")
            self.assertEqual(found, {"chat": chat, "operator": operator}, app)

    def test_setting_checks_the_mode_list(self):
        source = (ROOT / "apps/pearnly_core/pearnly_core/assistant/sessions.py").read_text(encoding="utf-8")
        start = source.index("def set_business(")
        body = source[start:source.index("\ndef ", start + 10)]
        self.assertIn("owned(session_id)", body)
        self.assertIn("guided.offered().get(mode, [])", body)
        # 记选的时刻（页面那句「请上传…」据此去重）；补交的按会话建立时刻；取消清空
        self.assertIn('"business_at": at', body)
        self.assertIn('frappe.db.get_value(SESSION, row.name, "creation") if from_start else frappe.utils.now_datetime()', body)

    def test_model_is_told_the_business_and_what_the_page_asked(self):
        """页面那句不进对话记录：模型从每条消息的系统说明里知道选了哪个业务、页面已请用户做什么（主会话 11-14）。"""
        from pearnly_core.assistant import contracts
        self.assertEqual(contracts.business_note(""), "")
        self.assertEqual(contracts.business_note("payment"), "")
        self.assertIn("purchase documents", contracts.business_note("purchase"))
        self.assertIn("sales documents", contracts.business_note("sales"))
        self.assertIn("customer and the amount received", contracts.business_note("collection"))
        self.assertIn("short replies", contracts.business_note("purchase"))
        source = (ROOT / "apps/pearnly_core/pearnly_core/assistant/sessions.py").read_text(encoding="utf-8")
        self.assertIn('contracts.business_note(row.get("business"))', source)


class Declared(unittest.TestCase):
    def test_core_tool_declarations(self):
        source = (ROOT / "apps/pearnly_core/pearnly_core/assistant/tools.py").read_text(encoding="utf-8")
        self.assertIn('"prepare": "pearnly_core.assistant.guide_tool.prepare"', source)
        self.assertIn('"refuse": "pearnly_core.assistant.guide_tool.refuse_ask"', source)
        self.assertNotIn("You may add your own questions", source)
        self.assertNotIn("turn the proposed and open items into ask_user questions", source)


if __name__ == "__main__":
    unittest.main()
