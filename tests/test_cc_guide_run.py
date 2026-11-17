"""CC 引导运行：一次按表单的引导怎么走（``pearnly_core.assistant.guide.run``；总设计 2.4，P0 第 3、6 个提交）。

断言的是应有状态（用一份收款样子的表单声明、替身产品函数走）：
- 点业务开一次引导，记下当时的公司 / 账套 / 月份；同一段对话里还开着的那次作废；这个模式不能办、这个人没有该功能的开不了；
- 作答：只收此刻可以问的步骤、值在选项里；改了客户，勾过的发票作废；适用的步骤都有值就生成草稿、进复核；
- 确定：点的人是本人、此刻仍有这项功能、版本与看到的一致（不一致不办、交回最新的卡）、点过停止不办；
  同一确定再来一次回上次的结果、业务函数只被调一次；业务函数拒了记失败与原因码；
- 失效：本对话的账套变了，这次引导标失效，作答与确定都不收；取消后不能再答；
- 事件编号带版本，同一状态两次投影相同。

替身 frappe 与内存里的记录，不连库（真实站点上的安装、并发抢占另在实验栈验）。

    python3 tests/test_cc_guide_run.py
"""
import datetime
import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

HOOKS, ATTRS, LOGGED, SENT = {}, {}, [], []
frappe = types.ModuleType("frappe")
frappe._ = lambda text: text
frappe.get_hooks = lambda name: HOOKS.get(name, [])
frappe.get_attr = lambda path: ATTRS[path]
frappe.log_error = lambda title="", message="": LOGGED.append(title)
frappe.session = types.SimpleNamespace(user="u@example.com")
frappe.DoesNotExistError = LookupError
frappe.PermissionError = PermissionError
frappe.QueryDeadlockError = type("QueryDeadlockError", (Exception,), {})


def _throw(message, exc=ValueError):
    raise exc(message)


frappe.throw = _throw
frappe.db = types.SimpleNamespace(commit=lambda: None, rollback=lambda: None)
frappe.utils = types.SimpleNamespace(get_datetime=lambda value: value, now_datetime=lambda: NOW[0])
NOW = [datetime.datetime(2026, 11, 14, 10, 30)]
frappe.publish_realtime = lambda event, message, user=None, after_commit=False: SENT.append(message["event"])
sys.modules["frappe"] = frappe
_contracts = types.ModuleType("pearnly_core.assistant.contracts")
_contracts.REALTIME_EVENT = "pearnly_assistant_event"
sys.modules["pearnly_core.assistant.contracts"] = _contracts
_plan = types.ModuleType("pearnly_core.assistant.write_plan")
_plan._iso = lambda moment: moment.isoformat() + "Z"
sys.modules["pearnly_core.assistant.write_plan"] = _plan

from pearnly_core.assistant.guide import run  # noqa: E402

GUIDE = {"id": "collection", "label_key": "guide.business.collection", "icon": "wallet", "order": 10, "modes": ["chat"],
         "ledgers": ["express_local"], "opening_key": "guide.open.collection", "source": "form", "writes": True,
         "allowed": "app.allowed", "expires": ["ledger", "company"],
         "steps": [{"key": "customer", "input": "choice", "rule": "always", "search": True, "options": "app.customers"},
                   {"key": "invoices", "input": "amounts", "rule": "always", "after": ["customer"], "options": "app.invoices"},
                   {"key": "method", "input": "split", "rule": "always", "after": ["invoices"], "options": "app.methods"},
                   {"key": "voucher", "input": "file", "rule": "always", "when": {"key": "method", "has": "transfer"}},
                   {"key": "on_date", "input": "date", "rule": "auto", "default": "app.today"}],
         "draft": "app.draft", "review": "app.review", "confirm": "app.release", "result": "app.status"}
INVOICES = {"C001": ["IV001", "IV002"], "C002": ["IV009"]}
IV = [{"code": "IV001", "amount": "1000"}]
CASH = [{"code": "cash", "amount": "1000"}]


class Doc(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


class Base(unittest.TestCase):
    def setUp(self):
        self.docs, self.now, self.clock = {}, {"session_id": "S1", "mode": "chat", "company": "CO1", "ledger": "B1",
                                                "period": "2026-11", "stopped_at": None}, [0]
        self.released, self.allowed, self.customers = [], True, ["C001", "C002"]
        HOOKS["pearnly_guides"] = ["app.GUIDES"]
        ATTRS.clear(), SENT.clear(), LOGGED.clear()
        ATTRS.update({
            "app.GUIDES": [GUIDE], "app.allowed": lambda user: self.allowed,
            "app.customers": lambda ctx, txt="", start=0, page_length=20, codes=None: self.options(self.customers, codes),
            "app.invoices": lambda ctx, txt="", start=0, page_length=20, codes=None:
                self.options(INVOICES.get(ctx["answers"].get("customer"), []), codes),
            "app.methods": lambda ctx, txt="", start=0, page_length=20, codes=None: self.options(["cash", "transfer"], codes),
            "app.today": lambda ctx: {"value": "2026-11-14", "source": "today"},
            "app.draft": lambda ctx, values: f"R:{ctx['run']}",
            "app.review": lambda ctx, draft: {"draft": draft, "rows": [ctx["answers"]["customer"]]},
            "app.release": self.release, "app.status": lambda ctx, draft: {"status": "entered", "docnum": "RE0001"},
        })
        original = run._commit_files
        run._commit_files = lambda session_id, by_step: ""  # 交文件的登记（`assistant.files.commit`）：这里都认下
        self.addCleanup(setattr, run, "_commit_files", original)
        run._insert, run._locked, run._save, run._open_runs, run._conversation = (
            self.insert, self.locked, self.save, self.open_runs, lambda session_id: dict(self.now))

    def options(self, codes, wanted):
        return {"options": [{"code": c, "name": c} for c in codes if wanted is None or c in wanted], "more": False}

    def release(self, ctx, values, draft, consent):
        self.released.append((draft, consent, values))
        return {"status": "queued"}

    def tick(self):
        self.clock[0] += 1
        return datetime.datetime(2026, 11, 14, 10, 0, self.clock[0])

    def insert(self, values):
        doc = Doc(values, name=f"RUN{len(self.docs) + 1}", modified=self.tick())
        self.docs[doc.name] = doc
        return doc

    def locked(self, name):
        doc = self.docs.get(name)
        return doc if doc and doc.user == frappe.session.user else None

    def save(self, doc):
        doc.modified = self.tick()

    def open_runs(self, session_id):
        return [d.name for d in self.docs.values() if d.session_id == session_id and d.status in run.OPEN]

    def to_review(self):
        name = run.start("S1", "collection")["run"]
        run.answer(name, {"customer": "C001"})
        run.answer(name, {"invoices": IV})
        return name, run.answer(name, {"method": CASH})


class StartTests(Base):
    def test_starting_records_the_conversation_context_and_asks_the_first_step(self):
        found = run.start("S1", "collection")
        self.assertEqual((found["status"], found["version"], found["company"], found["ledger"], found["period"]),
                         ("asking", 1, "CO1", "B1", "2026-11"))
        self.assertEqual([q["key"] for q in found["card"]["questions"]], ["customer"])
        self.assertEqual(found["card"]["questions"][0]["options"], [{"code": "C001", "name": "C001"}, {"code": "C002", "name": "C002"}])
        self.assertEqual(found["card"]["questions"][0]["title_key"], "guide.step.collection.customer")
        self.assertEqual([item["key"] for item in found["card"]["decided"]], ["on_date"])
        self.assertFalse(found["card"]["ready"])

    def test_a_second_start_in_the_same_conversation_cancels_the_first(self):
        first = run.start("S1", "collection")["run"]
        second = run.start("S1", "collection")["run"]
        self.assertEqual((self.docs[first].status, self.docs[first].ended_reason), ("cancelled", "replaced"))
        self.assertEqual(self.docs[second].status, "asking")

    def test_a_business_this_mode_or_this_person_cannot_do_does_not_start(self):
        self.now["mode"] = "operator"
        self.assertRaises(ValueError, run.start, "S1", "collection")
        self.now["mode"], self.allowed = "chat", False
        self.assertRaises(ValueError, run.start, "S1", "collection")
        self.assertRaises(ValueError, run.start, "S1", "payment")


class AnswerTests(Base):
    def test_answers_walk_the_steps_and_end_in_a_draft_under_review(self):
        name, found = self.to_review()
        self.assertEqual((found["status"], found["version"]), ("reviewing", 4))
        self.assertEqual(found["review"], {"draft": f"R:{name}", "rows": ["C001"]})
        self.assertEqual(self.docs[name].draft, f"R:{name}")

    def test_a_step_not_asked_now_or_a_value_not_offered_is_refused_and_nothing_is_saved(self):
        name = run.start("S1", "collection")["run"]
        early = run.answer(name, {"invoices": IV})
        self.assertEqual((early["outcome"], early["refused"]), ("refused", {"invoices": "not asked now"}))
        gone = run.answer(name, {"customer": "C999"})
        self.assertEqual(gone["refused"], {"customer": "not offered"})
        shape = run.answer(name, {"customer": ["C001"]})
        self.assertEqual(shape["refused"], {"customer": "one option code"})
        self.assertEqual(self.docs[name].answers, "{}")
        self.assertEqual(self.docs[name].version, 1)

    def test_changing_the_customer_voids_the_invoices_chosen_for_the_old_one(self):
        name = run.start("S1", "collection")["run"]
        run.answer(name, {"customer": "C001"})
        run.answer(name, {"invoices": IV})
        found = run.answer(name, {"customer": "C002"})
        self.assertEqual(json.loads(self.docs[name].answers), {"customer": "C002"})
        self.assertEqual([q["key"] for q in found["card"]["questions"]], ["invoices"])
        self.assertEqual([o["code"] for o in found["card"]["questions"][0]["options"]], ["IV009"])

    def test_an_answer_given_against_an_older_card_is_not_taken(self):
        name = run.start("S1", "collection")["run"]
        run.answer(name, {"customer": "C001"}, version=1)
        late = run.answer(name, {"customer": "C002"}, version=1)
        self.assertEqual(late["outcome"], "changed")
        self.assertEqual(json.loads(self.docs[name].answers), {"customer": "C001"})

    def test_a_transfer_asks_for_the_voucher_before_review(self):
        name = run.start("S1", "collection")["run"]
        run.answer(name, {"customer": "C001"})
        run.answer(name, {"invoices": IV})
        found = run.answer(name, {"method": [{"code": "transfer", "amount": "1000"}]})
        self.assertEqual((found["status"], [q["key"] for q in found["card"]["questions"]]), ("asking", ["voucher"]))
        self.assertEqual(run.answer(name, {"voucher": ["upl_1"]})["status"], "reviewing")

    def test_someone_else_cannot_touch_the_run(self):
        name = run.start("S1", "collection")["run"]
        frappe.session.user = "other@example.com"
        try:
            self.assertRaises(LookupError, run.answer, name, {"customer": "C001"})
            self.assertRaises(LookupError, run.confirm, name, 1)
        finally:
            frappe.session.user = "u@example.com"


class ConfirmTests(Base):
    def test_confirm_releases_once_with_the_click_as_consent(self):
        name, found = self.to_review()
        done = run.confirm(name, found["version"])
        self.assertEqual((done["status"], done["outcome"] if "outcome" in done else ""), ("done", ""))
        self.assertEqual(done["result"], {"status": "entered", "docnum": "RE0001"})
        self.assertEqual(len(self.released), 1)
        draft, consent, values = self.released[0]
        self.assertEqual((draft, consent), (f"R:{name}", f"guide:{name}:4"))
        self.assertEqual(values, {"customer": "C001", "invoices": IV, "method": CASH, "on_date": "2026-11-14"})

    def test_the_same_confirm_again_returns_the_result_and_does_not_release_twice(self):
        name, found = self.to_review()
        run.confirm(name, found["version"])
        again = run.confirm(name, found["version"])
        self.assertEqual((again["status"], again["outcome"]), ("done", ""))
        self.assertEqual(len(self.released), 1)
        other = run.confirm(name, found["version"] + 5)
        self.assertEqual(other["outcome"], "refused")
        self.assertEqual(len(self.released), 1)

    def test_a_confirm_for_a_version_the_user_did_not_see_is_not_carried_out(self):
        name, found = self.to_review()
        stale = run.confirm(name, found["version"] - 1)
        self.assertEqual((stale["outcome"], stale["status"], stale["version"]), ("changed", "reviewing", found["version"]))
        self.assertEqual(self.released, [])

    def test_confirm_needs_the_function_at_the_moment_of_the_click(self):
        name, found = self.to_review()
        self.allowed = False
        self.assertRaises(PermissionError, run.confirm, name, found["version"])
        self.assertEqual(self.released, [])
        self.assertEqual(self.docs[name].status, "reviewing")

    def test_confirm_before_every_step_is_answered_is_refused(self):
        name = run.start("S1", "collection")["run"]
        found = run.answer(name, {"customer": "C001"})
        self.assertEqual(run.confirm(name, found["version"])["outcome"], "refused")
        self.assertEqual(self.released, [])

    def test_a_stop_after_the_review_was_shown_blocks_the_confirm(self):
        name, found = self.to_review()
        self.now["stopped_at"] = self.docs[name].modified + datetime.timedelta(seconds=5)
        self.assertEqual(run.confirm(name, found["version"])["outcome"], "stopped")
        self.assertEqual(self.released, [])

    def test_a_refusal_by_the_business_function_is_recorded_with_its_reason_code(self):
        class Refused(Exception):
            code = "collection.over_collected"

        def refuse(ctx, values, draft, consent):
            raise Refused()
        ATTRS["app.release"] = refuse
        name, found = self.to_review()
        failed = run.confirm(name, found["version"])
        self.assertEqual((failed["status"], failed["result"]), ("failed", {"status": "failed", "reason": "collection.over_collected"}))
        self.assertEqual(run.confirm(name, found["version"])["status"], "failed")


class EndTests(Base):
    def test_a_changed_account_set_expires_the_run_and_nothing_more_is_taken(self):
        name, found = self.to_review()
        self.now["ledger"] = "B2"
        gone = run.confirm(name, found["version"])
        self.assertEqual((gone["status"], gone["reason"], gone["outcome"]), ("expired", "ledger", "refused"))
        self.assertEqual(run.answer(name, {"customer": "C002"})["outcome"], "refused")
        self.assertEqual(self.released, [])

    def test_a_changed_period_does_not_expire_a_guide_that_does_not_depend_on_it(self):
        name, found = self.to_review()
        self.now["period"] = "2026-12"
        self.assertEqual(run.confirm(name, found["version"])["status"], "done")

    def test_cancel_closes_the_run(self):
        name = run.start("S1", "collection")["run"]
        self.assertEqual(run.cancel(name), {"run": name, "business": "collection", "status": "cancelled", "version": 2,
                                            "company": "CO1", "ledger": "B1", "period": "2026-11", "reason": "user"})
        self.assertEqual(run.answer(name, {"customer": "C001"})["outcome"], "refused")

    def test_each_step_announces_one_event_whose_id_carries_the_version(self):
        name, found = self.to_review()
        self.assertEqual([e["id"] for e in SENT], [f"pgd_{name}_{v}" for v in (1, 2, 3, 4)])
        self.assertEqual(SENT[-1]["status"], "reviewing")
        self.assertEqual(run.event(self.docs[name]), run.event(self.docs[name]))
        self.assertEqual(run.event(self.docs[name])["type"], "pearnly.guide")

    def test_search_pages_one_step_options_and_refuses_other_steps(self):
        name = run.start("S1", "collection")["run"]
        ATTRS["frappe.db"] = None
        frappe.db.get_value = lambda doctype, filters, field, **kw: filters["name"] if self.locked(filters["name"]) else None
        frappe.get_doc = lambda doctype, found: self.docs[found]
        self.assertEqual([o["code"] for o in run.search(name, "customer", "C")["options"]], ["C001", "C002"])
        self.assertRaises(ValueError, run.search, name, "on_date")


class WritingGuideTests(Base):
    """写账本的表单引导要的几样（P1 收款）：每步的服务端校验、选后动作、题目下的事实行与「在读」、金额不超可收、
    生成草稿 / 放行时业务函数退回某一步、复核后草稿在别处改过、开始时没定的账套在引导里定下来不算失效、按码前缀判条件。"""

    def setUp(self):
        super().setUp()
        self.applied, self.made = [], []
        guide = json.loads(json.dumps(GUIDE))
        steps = {step["key"]: step for step in guide["steps"]}
        steps["customer"]["apply"] = "app.pick_customer"
        steps["invoices"]["options"] = "app.invoice_rows"
        steps["method"]["check"] = "app.method_total"
        steps["method"]["options"] = "app.method_rows"
        steps["voucher"]["when"] = {"key": "method", "starts": "transfer:"}
        steps["on_date"]["facts"] = "app.periods"
        guide["draft"] = "app.make"
        ATTRS["app.GUIDES"] = [guide]
        self.reading = [False]
        ATTRS.update({
            "app.pick_customer": lambda ctx, value: self.applied.append(value),
            "app.invoice_rows": lambda ctx, txt="", start=0, page_length=20, codes=None: {
                "options": [{"code": "IV001", "name": "IV001", "amount": "1000.00", "max": "1000.00"}],
                "more": False, **({"facts": ["Reading from Express"], "wait": True} if self.reading[0] else {})},
            "app.method_rows": lambda ctx, txt="", start=0, page_length=20, codes=None:
                self.options(["cash", "transfer:T1"], codes),
            "app.method_total": lambda ctx, value: "" if sum(float(r["amount"]) for r in value) == sum(
                float(r["amount"]) for r in ctx["answers"]["invoices"]) else "collection.amount_mismatch",
            "app.periods": lambda ctx: ["Accounting period: 2026-01-01–2026-12-31"],
            "app.make": self.make,
        })
        self.returned = [None]

    def make(self, ctx, values):
        self.made.append(ctx["draft"])
        if self.returned[0]:
            found, self.returned[0] = self.returned[0], None
            return {"returned": found}
        return {"draft": f"COL1@{len(self.made)}"}

    def walk(self, method=None):
        name = run.start("S1", "collection")["run"]
        run.answer(name, {"customer": "C001"})
        run.answer(name, {"invoices": [{"code": "IV001", "amount": "600"}]})
        return name, run.answer(name, {"method": method or [{"code": "cash", "amount": "600"}]})

    def test_facts_and_a_still_reading_flag_come_with_the_question(self):
        self.reading[0] = True
        name = run.start("S1", "collection")["run"]
        found = run.answer(name, {"customer": "C001"})
        question = found["card"]["questions"][0]
        self.assertEqual((question["key"], question["facts"], question["wait"]), ("invoices", ["Reading from Express"], True))
        self.assertEqual(found["card"]["decided"][0]["key"], "on_date")

    def test_an_amount_over_what_can_be_collected_now_is_refused(self):
        name = run.start("S1", "collection")["run"]
        run.answer(name, {"customer": "C001"})
        over = run.answer(name, {"invoices": [{"code": "IV001", "amount": "1000.01"}]})
        self.assertEqual(over["refused"], {"invoices": "over max"})
        self.assertNotIn("invoices", json.loads(self.docs[name].answers))

    def test_a_step_check_refuses_with_its_reason_and_keeps_nothing(self):
        name = run.start("S1", "collection")["run"]
        run.answer(name, {"customer": "C001"})
        run.answer(name, {"invoices": [{"code": "IV001", "amount": "600"}]})
        short = run.answer(name, {"method": [{"code": "cash", "amount": "500"}]})
        self.assertEqual(short["refused"], {"method": "collection.amount_mismatch"})
        self.assertEqual(self.made, [])

    def test_the_action_runs_once_when_the_answer_is_taken_and_again_only_when_it_changes(self):
        name = run.start("S1", "collection")["run"]
        run.answer(name, {"customer": "C001"})
        run.answer(name, {"customer": "C001"})
        run.answer(name, {"customer": "C002"})
        self.assertEqual(self.applied, ["C001", "C002"])

    def test_a_file_step_registers_the_files_as_handed_over_and_refuses_files_from_elsewhere(self):
        committed = []
        run._commit_files = lambda session_id, by_step: committed.extend((session_id, ids) for ids in by_step.values()) or next((k for k, ids in by_step.items() if any(not i.startswith("upl_") for i in ids)), "")
        name, found = self.walk([{"code": "transfer:T1", "amount": "600"}])
        refused = run.answer(name, {"voucher": ["upl_1", "other_9"]})
        self.assertEqual(refused["refused"], {"voucher": "not in this conversation"})
        self.assertEqual(run.answer(name, {"voucher": ["upl_1"]})["status"], "reviewing")
        self.assertEqual(committed[-1], ("S1", ["upl_1"]))

    def test_the_voucher_is_asked_only_when_a_method_code_starts_with_transfer(self):
        name, found = self.walk([{"code": "transfer:T1", "amount": "600"}])
        self.assertEqual([q["key"] for q in found["card"]["questions"]], ["voucher"])
        _name, plain = self.walk()
        self.assertEqual(plain["status"], "reviewing")

    def test_a_step_sent_back_by_the_draft_is_asked_again_with_its_reason_and_the_rest_kept(self):
        self.returned[0] = {"invoices": {"reason": "collection.over_collected", "text": "Already collected"}}
        name, found = self.walk()
        self.assertEqual(found["status"], "asking")
        self.assertEqual([q["key"] for q in found["card"]["questions"]], ["invoices"])
        self.assertEqual(found["card"]["returned"], {"invoices": {"reason": "collection.over_collected", "text": "Already collected"}})
        self.assertEqual(json.loads(self.docs[name].answers), {"customer": "C001"})  # 发票作废，依赖它的方式也作废
        again = run.answer(name, {"invoices": [{"code": "IV001", "amount": "400"}]})
        self.assertNotIn("returned", again["card"])
        self.assertEqual(run.answer(name, {"method": [{"code": "cash", "amount": "400"}]})["status"], "reviewing")
        self.assertEqual(self.made, ["", ""])

    def test_answering_again_under_review_makes_a_new_draft_and_hands_over_the_old_one(self):
        name, _found = self.walk()
        self.assertEqual(self.docs[name].draft, "COL1@1")
        run.answer(name, {"method": [{"code": "cash", "amount": "600"}], "invoices": [{"code": "IV001", "amount": "600"}]})
        self.assertEqual(self.made, ["", "COL1@1"])  # 上一张草稿号交给业务函数（由它作废）

    def test_a_draft_changed_elsewhere_after_review_returns_to_review_with_the_new_version(self):
        name, found = self.walk()
        ATTRS["app.release"] = lambda ctx, values, draft, consent: {"outcome": "changed", "draft": "COL1@7"}
        back = run.confirm(name, found["version"])
        self.assertEqual((back["outcome"], back["status"]), ("changed", "reviewing"))
        self.assertEqual((self.docs[name].draft, self.docs[name].consent), ("COL1@7", ""))
        ATTRS["app.release"] = self.release
        done = run.confirm(name, back["version"])
        self.assertEqual((done["status"], self.released[0][0]), ("done", "COL1@7"))

    def test_a_release_refused_now_sends_the_step_back(self):
        name, found = self.walk()
        ATTRS["app.release"] = lambda ctx, values, draft, consent: {
            "returned": {"invoices": {"reason": "collection.over_collected", "text": ""}}}
        back = run.confirm(name, found["version"])
        self.assertEqual((back["outcome"], back["status"]), ("refused", "asking"))
        self.assertEqual([q["key"] for q in back["card"]["questions"]], ["invoices"])
        self.assertEqual(self.docs[name].consent, "")

    def test_an_account_set_decided_during_the_guide_fills_in_instead_of_expiring(self):
        self.now["ledger"] = ""
        name = run.start("S1", "collection")["run"]
        self.now["ledger"] = "B2"   # 引导里选了账套、记进本对话
        found = run.answer(name, {"customer": "C001"})
        self.assertEqual((found["status"], found["ledger"]), ("asking", "B2"))
        self.now["ledger"] = "B3"   # 之后再换就是变了
        self.assertEqual(run.answer(name, {"invoices": [{"code": "IV001", "amount": "600"}]})["status"], "expired")

    def test_a_default_that_gives_nothing_does_not_stop_a_later_default_from_deciding(self):
        """11-15 收款测试查出：前一步的默认值给不出（降成问）时，后一步算默认值读「已定」各步的值出错，后一步也被降成问。"""
        guide = ATTRS["app.GUIDES"][0]
        guide["steps"].append({"key": "staff", "input": "choice", "rule": "auto", "after": ["customer"],
                               "options": "app.method_rows", "default": "app.staff"})
        ATTRS["app.today"] = lambda ctx: None
        ATTRS["app.staff"] = lambda ctx: {"value": "cash", "source": "first invoice"}
        name = run.start("S1", "collection")["run"]
        found = run.answer(name, {"customer": "C001"})
        self.assertIn("staff", [item["key"] for item in found["card"]["decided"]])
        self.assertNotIn("staff", [q["key"] for q in found["card"]["questions"]])

    def test_a_run_left_alone_for_two_hours_expires_and_takes_nothing_more(self):
        name = run.start("S1", "collection")["run"]
        NOW[0] = datetime.datetime(2026, 11, 14, 12, 1)  # 开始约在 10:00，过了 120 分钟
        try:
            found = run.answer(name, {"customer": "C001"})
            self.assertEqual((found["status"], found["reason"]), ("expired", "idle"))
        finally:
            NOW[0] = datetime.datetime(2026, 11, 14, 10, 30)

    def test_fact_lines_of_a_step_come_from_its_facts_function(self):
        ATTRS["app.today"] = lambda ctx: None
        found = run.start("S1", "collection")
        dated = next(q for q in found["card"]["questions"] if q["key"] == "on_date")
        self.assertEqual(dated["facts"], ["Accounting period: 2026-01-01–2026-12-31"])


LAYOUT = {"id": "layout", "label_key": "guide.business.layout", "icon": "store", "order": 60, "modes": ["chat"],
          "ledgers": ["express_local"], "opening_key": "guide.open.layout", "source": "form", "writes": True, "consent": "allow",
          "steps": [{"key": "export", "input": "file", "rule": "always"},
                    {"key": "columns", "input": "questions", "rule": "always", "after": ["export"], "options": "app.columns"}],
          "draft": "app.layout_draft", "review": "app.layout_review", "confirm": "app.layout_confirm",
          "result": "app.layout_result", "follow": "app.layout_follow"}


class CapabilityTests(Base):
    """引擎通用能力（约定 11-16）：N1a 子题、N1b 结果里的追问、N5 允许卡、N10 结果函数拿到确定时的回包。"""

    def setUp(self):
        super().setUp()
        self.followed, self.seen = [], []
        ATTRS["app.GUIDES"] = [GUIDE, LAYOUT]
        ATTRS.update({
            "app.columns": lambda ctx: {"questions": [
                {"key": "date", "input": "choice", "title": "Date", "options": [{"code": "C1", "name": "A"}, {"code": "C2", "name": "B"}],
                 "preselected": "C1"},
                {"key": "vat_included", "input": "choice", "title": "VAT", "options": [{"code": "yes"}, {"code": "no"}]},
                {"key": "note", "input": "text", "title": "Note", "required": False},
                {"key": "broken", "input": "dropdown", "title": "x"}],
                "facts": ["2 sample rows"]} if ctx["answers"].get("export") else {"questions": []},
            "app.layout_draft": lambda ctx, values: f"L:{ctx['run']}",
            "app.layout_review": lambda ctx, draft: {"lines": [], "ready": True},
            "app.layout_confirm": lambda ctx, values, draft, consent: {"status": "queued", "job": "J1"},
            "app.layout_result": self.layout_result,
            "app.layout_follow": self.layout_follow,
        })

    def layout_result(self, ctx, draft):
        self.seen.append(ctx.get("done"))
        if not self.followed:
            return {"status": "ready", "job": ctx["done"]["job"],
                    "questions": [{"key": "gl_account", "input": "choice", "title": "Account",
                                   "options": [{"code": "1100"}, {"code": "1110"}]}]}
        return None

    def layout_follow(self, ctx, draft, answers):
        self.followed.append((ctx["done"], answers))
        return {"status": "ready", "job": "J2"}

    def confirmed(self):
        name = run.start("S1", "layout")["run"]
        run.answer(name, {"export": ["upl_1"]})
        found = run.answer(name, {"columns": {"date": "C2", "vat_included": "no"}})
        return name, run.confirm(name, found["version"])

    def test_questions_come_from_the_earlier_answers_and_a_malformed_one_is_left_out(self):
        name = run.start("S1", "layout")["run"]
        found = run.answer(name, {"export": ["upl_1"]})
        question = next(q for q in found["card"]["questions"] if q["key"] == "columns")
        self.assertEqual([sub["key"] for sub in question["questions"]], ["date", "vat_included", "note"])
        self.assertEqual(question["questions"][0]["preselected"], "C1")
        self.assertEqual(question["questions"][1]["options"], [{"code": "yes", "name": "yes"}, {"code": "no", "name": "no"}])
        self.assertEqual(question["facts"], ["2 sample rows"])
        self.assertIn("Assistant guide question rejected", LOGGED)

    def test_question_answers_are_checked_one_by_one(self):
        name = run.start("S1", "layout")["run"]
        run.answer(name, {"export": ["upl_1"]})
        for given, reason in (({"date": "C9", "vat_included": "no"}, "not offered"),
                              ({"date": "C2"}, "incomplete"),
                              ({"date": "C2", "vat_included": "no", "other": "x"}, "not offered"),
                              ({"date": ["C2"], "vat_included": "no"}, "one option code")):
            self.assertEqual(run.answer(name, {"columns": given})["refused"], {"columns": reason}, given)
        found = run.answer(name, {"columns": {"date": "C2", "vat_included": "no"}})   # 可不答的 note 不答也收
        self.assertEqual(found["status"], run.REVIEWING)

    def test_a_guide_that_deletes_asks_with_the_allow_card_and_others_with_confirm(self):
        name = run.start("S1", "layout")["run"]
        self.assertEqual(run.view(self.docs[name])["consent"], "allow")
        self.assertEqual(run.start("S1", "collection")["consent"], "confirm")

    def test_consent_may_follow_an_answer(self):
        """guided-cowork-2 11-16 约定：一张卡里删除 / 结束用允许卡、其余用确定，按那一步此刻的值判。"""
        LAYOUT["consent"] = {"key": "export", "has": "upl_del"}
        try:
            name = run.start("S1", "layout")["run"]
            self.assertEqual(run.view(self.docs[name])["consent"], "confirm")   # 还没答：条件不成立
            self.assertEqual(run.answer(name, {"export": ["upl_del"]})["consent"], "allow")
            self.assertEqual(run.answer(name, {"export": ["upl_1"]})["consent"], "confirm")
        finally:
            LAYOUT["consent"] = "allow"

    def test_the_result_function_gets_what_the_confirm_returned(self):
        name, done = self.confirmed()
        self.assertEqual(self.seen[-1], {"status": "queued", "job": "J1"})
        self.assertEqual(done["result"]["job"], "J1")

    def test_a_question_in_the_result_is_answered_once_and_the_new_result_is_kept(self):
        name, done = self.confirmed()
        self.assertEqual([sub["key"] for sub in done["result"]["questions"]], ["gl_account"])
        refused = run.follow(name, {"gl_account": "9999"}, done["version"])
        self.assertEqual((refused["outcome"], refused["refused"]), (run.REFUSED, {"questions": "not offered"}))
        stale = run.follow(name, {"gl_account": "1100"}, done["version"] - 1)
        self.assertEqual(stale["outcome"], run.CHANGED)
        after = run.follow(name, {"gl_account": "1100"}, done["version"])
        self.assertEqual(self.followed, [({"status": "queued", "job": "J1"}, {"gl_account": "1100"})])
        self.assertEqual((after["result"], after["version"]), ({"status": "ready", "job": "J2"}, done["version"] + 1))
        self.assertEqual(run.follow(name, {"gl_account": "1100"}, after["version"])["outcome"], run.REFUSED)   # 不再有问题

    def test_a_guide_without_follow_or_not_done_takes_no_follow_up(self):
        name, _found = self.to_review()
        self.assertEqual(run.follow(name, {"x": "y"})["outcome"], run.REFUSED)


class ConsentCardTests(Base):
    """确定卡的问句与下面那行事实（同线上允许卡；主会话 11-16 批）。"""

    def reviewing(self):
        name = run.start("S1", "collection")["run"]
        run.answer(name, {"customer": "C001"})
        run.answer(name, {"invoices": IV})
        return name, run.answer(name, {"method": CASH})

    def test_the_question_comes_from_the_glossary_key_and_the_note_from_the_product(self):
        ATTRS["app.GUIDES"] = [{**GUIDE, "note": "app.note"}]
        ATTRS["app.note"] = lambda ctx, draft: f"Company A · Book {draft}"
        original = run._
        run._ = lambda text: {"guide.consent.collection": "Record this collection?"}.get(text, text)
        self.addCleanup(setattr, run, "_", original)
        name, found = self.reviewing()
        self.assertEqual(found["status"], "reviewing")
        self.assertEqual((found["consent_title_key"], found["consent_title"]),
                         ("guide.consent.collection", "Record this collection?"))
        self.assertEqual(found["consent_note"], f"Company A · Book R:{name}")

    def test_no_translation_and_no_note_give_empty_strings(self):
        _name, found = self.reviewing()
        self.assertEqual((found["consent_title"], found["consent_note"]), ("", ""))

    def test_a_note_that_fails_leaves_the_card_up_with_no_note(self):
        ATTRS["app.GUIDES"] = [{**GUIDE, "note": "app.note"}]
        ATTRS["app.note"] = lambda ctx, draft: 1 / 0
        _name, found = self.reviewing()
        self.assertEqual((found["status"], found["consent_note"]), ("reviewing", ""))
        self.assertIn("Assistant guide note failed", LOGGED)

    def test_a_finished_run_carries_no_question(self):
        name, _found = self.reviewing()
        done = run.confirm(name, run.view(self.docs[name])["version"])
        self.assertNotIn("consent_title", done)


if __name__ == "__main__":
    unittest.main()
