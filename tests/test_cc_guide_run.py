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
# 站点时区当曼谷（+7）：记录的改动时间是本地的，停止时刻是 UTC 的（`write_plan._to_utc`）
_plan._to_utc = lambda moment: moment - datetime.timedelta(hours=7)
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



def _codes(found):
    """拒收结果里的码（`run._refusal`：{key: {code, text}}）。"""
    return {key: value["code"] for key, value in found["refused"].items()}

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


class HintTests(Base):
    """打字说要办时开卡（11-16 用户「按建议做」）：说到的客户名 / 单号只预填进第一道带搜索的单选题的搜索框，选项按它搜，不当答案；
    答过这一题后不再跟到后面的题上。"""

    def setUp(self):
        super().setUp()
        self.searched = []

        def customers(ctx, txt="", start=0, page_length=20, codes=None):
            if codes is None:
                self.searched.append(txt)
            rows = [code for code in self.customers if codes is not None or txt.casefold() in code.casefold()]
            return self.options(rows, codes)
        ATTRS["app.customers"] = customers

    def test_the_hint_fills_the_search_box_of_the_first_search_step_and_is_not_an_answer(self):
        found = run.start("S1", "collection", hint="  c002 ")
        question = found["card"]["questions"][0]
        self.assertEqual((question["key"], question["query"]), ("customer", "c002"))
        self.assertEqual([row["code"] for row in question["options"]], ["C002"])
        self.assertEqual(self.searched[-1], "c002")
        self.assertNotIn("value", question)
        self.assertEqual(found["card"]["answers"], {})
        self.assertEqual(self.docs[found["run"]].hint, "c002")

    def test_once_the_step_is_answered_the_hint_is_gone(self):
        name = run.start("S1", "collection", hint="C002")["run"]
        found = run.answer(name, {"customer": "C002"})
        self.assertTrue(all("query" not in q for q in found["card"]["questions"]))

    def test_without_a_hint_nothing_is_prefilled_and_a_long_one_is_cut(self):
        found = run.start("S1", "collection")
        self.assertNotIn("query", found["card"]["questions"][0])
        self.assertEqual(self.searched[-1], "")
        name = run.start("S1", "collection", hint="x" * 300)["run"]
        self.assertEqual(len(self.docs[name].hint), 140)


class AnswerTests(Base):
    def test_answers_walk_the_steps_and_end_in_a_draft_under_review(self):
        name, found = self.to_review()
        self.assertEqual((found["status"], found["version"]), ("reviewing", 4))
        self.assertEqual(found["review"], {"draft": f"R:{name}", "rows": ["C001"]})
        self.assertEqual(self.docs[name].draft, f"R:{name}")

    def test_a_step_not_asked_now_or_a_value_not_offered_is_refused_and_nothing_is_saved(self):
        name = run.start("S1", "collection")["run"]
        early = run.answer(name, {"invoices": IV})
        self.assertEqual((early["outcome"], _codes(early)), ("refused", {"invoices": "not asked now"}))
        gone = run.answer(name, {"customer": "C999"})
        self.assertEqual(_codes(gone), {"customer": "not offered"})
        shape = run.answer(name, {"customer": ["C001"]})
        self.assertEqual(_codes(shape), {"customer": "one option code"})
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
        # 停止时刻是 UTC：复核出来 5 秒之后点的停止（本地时间 +7，以前直接比本地与 UTC，这一下拦不住）
        self.now["stopped_at"] = self.docs[name].modified - datetime.timedelta(hours=7) + datetime.timedelta(seconds=5)
        self.assertEqual(run.confirm(name, found["version"])["outcome"], "stopped")
        self.assertEqual(self.released, [])

    def test_a_stop_before_the_review_does_not_block_the_confirm(self):
        name, found = self.to_review()
        self.now["stopped_at"] = self.docs[name].modified - datetime.timedelta(hours=7) - datetime.timedelta(minutes=1)
        self.assertNotEqual(run.confirm(name, found["version"]).get("outcome"), "stopped")
        self.assertEqual(len(self.released), 1)

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
        self.assertEqual(_codes(over), {"invoices": "over max"})
        self.assertNotIn("invoices", json.loads(self.docs[name].answers))

    def test_a_step_check_refuses_with_its_reason_and_keeps_nothing(self):
        name = run.start("S1", "collection")["run"]
        run.answer(name, {"customer": "C001"})
        run.answer(name, {"invoices": [{"code": "IV001", "amount": "600"}]})
        short = run.answer(name, {"method": [{"code": "cash", "amount": "500"}]})
        self.assertEqual(_codes(short), {"method": "collection.amount_mismatch"})
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
        self.assertEqual(_codes(refused), {"voucher": "not in this conversation"})
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
            self.assertEqual(_codes(run.answer(name, {"columns": given})), {"columns": reason}, given)
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
        self.assertEqual((refused["outcome"], _codes(refused)), (run.REFUSED, {"questions": "not offered"}))
        stale = run.follow(name, {"gl_account": "1100"}, done["version"] - 1)
        self.assertEqual(stale["outcome"], run.CHANGED)
        after = run.follow(name, {"gl_account": "1100"}, done["version"])
        self.assertEqual(self.followed, [({"status": "queued", "job": "J1"}, {"gl_account": "1100"})])
        self.assertEqual((after["result"], after["version"]), ({"status": "ready", "job": "J2"}, done["version"] + 1))
        self.assertEqual(run.follow(name, {"gl_account": "1100"}, after["version"])["outcome"], run.REFUSED)   # 不再有问题

    def test_a_guide_without_follow_or_not_done_takes_no_follow_up(self):
        name, _found = self.to_review()
        self.assertEqual(run.follow(name, {"x": "y"})["outcome"], run.REFUSED)


class LedgerTests(Base):
    def test_a_guide_not_declared_for_the_conversation_ledger_does_not_start(self):
        # 11-19：菜单上本来就没有它；页面硬交也不开（不信页面）
        HOOKS["pearnly_guide_ledger"] = ["app.ledger"]
        self.addCleanup(HOOKS.pop, "pearnly_guide_ledger", None)
        ATTRS["app.ledger"] = lambda session_id: "mrerp"  # GUIDE 只声明了 express_local
        self.assertRaises(ValueError, run.start, "S1", "collection")
        ATTRS["app.ledger"] = lambda session_id: "express_local"
        self.assertEqual(run.start("S1", "collection")["status"], "asking")


class MaxRowsTests(Base):
    """多行输入一次最多几行（选项函数此刻给 ``max_rows``；带 ``free`` 的选项不占名额）：MR.ERP 账本一笔只收一种方式、折扣可另加。"""

    def setUp(self):
        super().setUp()
        ATTRS["app.methods"] = lambda ctx, txt="", start=0, page_length=20, codes=None: {
            "options": [{"code": c, "name": c, **({"free": True} if c == "discount" else {})}
                        for c in ("cash", "transfer", "discount") if codes is None or c in codes],
            "more": False, "max_rows": 1}

    def to_methods(self):
        name = run.start("S1", "collection")["run"]
        run.answer(name, {"customer": "C001"})
        return name, run.answer(name, {"invoices": IV})

    def test_the_question_carries_max_rows(self):
        _name, found = self.to_methods()
        self.assertEqual(found["card"]["questions"][0]["max_rows"], 1)

    def test_two_paying_rows_are_refused_and_a_free_row_does_not_count(self):
        name, _found = self.to_methods()
        refused = run.answer(name, {"method": [{"code": "cash", "amount": "500"}, {"code": "transfer", "amount": "500"}]})
        self.assertEqual(_codes(refused), {"method": run.TOO_MANY_ROWS})
        original = run._
        run._ = lambda text: {"guide.refused.too_many_rows": "Too many rows"}.get(text, text)
        self.addCleanup(setattr, run, "_", original)
        again = run.answer(name, {"method": [{"code": "cash", "amount": "500"}, {"code": "transfer", "amount": "500"}]})
        self.assertEqual(again["refused"]["method"], {"code": run.TOO_MANY_ROWS, "text": "Too many rows"})  # 一句事实，按本次语言
        ok = run.answer(name, {"method": [{"code": "cash", "amount": "900"}, {"code": "discount", "amount": "100"}]})
        self.assertNotIn("refused", ok)


class StopEndTests(Base):
    """引导进行中的退出（主会话 11-16）：点停止 → 失效 / stopped；说不做了 → 模型调 pearnly_guide_end → 取消 / user。"""

    def test_a_stop_ends_the_open_guide_and_announces_it(self):
        name, _found = self.to_review()
        SENT.clear()
        self.assertEqual(run.end_open("S1", "stopped"), [name])
        found = run.view(self.docs[name])
        self.assertEqual((found["status"], found["reason"]), ("expired", "stopped"))
        self.assertEqual(len(SENT), 1)
        self.assertEqual(self.released, [])
        self.assertEqual(run.end_open("S1", "stopped"), [])  # 再停一次：没有开着的了

    def test_the_end_tool_cancels_the_open_guide_and_writes_nothing(self):
        name, _found = self.to_review()
        frappe.local = types.SimpleNamespace(pearnly_assistant={"session_id": "S1"})
        self.addCleanup(delattr, frappe, "local")
        self.assertEqual(json.loads(run.tool_end({})), {"status": "ended"})
        self.assertEqual((self.docs[name].status, self.docs[name].ended_reason), ("cancelled", "user"))
        self.assertEqual(json.loads(run.tool_end({})), {"status": "no_open_guide"})
        self.assertEqual(self.released, [])
        # 结束了的引导点「确定」不办
        self.assertEqual(run.confirm(name, self.docs[name].version)["outcome"], "refused")

    def test_the_end_tool_without_a_conversation_ends_nothing(self):
        frappe.local = types.SimpleNamespace(pearnly_assistant=None)
        self.addCleanup(delattr, frappe, "local")
        self.assertEqual(json.loads(run.tool_end({})), {"status": "no_open_guide"})


class RecommendedFirstTests(Base):
    """推荐项服务端排第一（主会话 11-16）：第一页第一个是它；不在第一页从产品按码取回插最前；后面的页不再出现。"""

    def setUp(self):
        super().setUp()
        self.codes = [f"C{n:03d}" for n in range(1, 46)]   # 45 个客户，一页 20 个
        self.recommended = "C044"                             # 在第 3 页
        guide = {**GUIDE, "steps": [{**GUIDE["steps"][0], "default": "app.customer_default"}, *GUIDE["steps"][1:]]}
        ATTRS["app.GUIDES"] = [guide]
        ATTRS["app.customer_default"] = lambda ctx: {"value": self.recommended}
        ATTRS["app.customers"] = self.paged

    def paged(self, ctx, txt="", start=0, page_length=20, codes=None):
        found = [c for c in self.codes if (codes is None or c in codes) and txt in c]
        return {"options": [{"code": c, "name": c} for c in found[start:start + page_length]],
                "more": start + page_length < len(found)}

    def test_the_recommended_option_comes_first_on_the_first_page_even_when_it_is_further_down(self):
        name = run.start("S1", "collection")["run"]
        question = run.view(self.docs[name])["card"]["questions"][0]
        self.assertEqual(question["preselected"], "C044")
        self.assertNotIn("value", question)  # 推荐只作标记，不当已答
        self.assertEqual([o["code"] for o in question["options"]][:3], ["C044", "C001", "C002"])

    def test_paging_and_search_do_not_repeat_it(self):
        frappe.db.get_value = lambda doctype, filters, field, **kw: filters["name"] if self.locked(filters["name"]) else None
        frappe.get_doc = lambda doctype, found: self.docs[found]
        name = run.start("S1", "collection")["run"]
        first = [o["code"] for o in run.search(name, "customer", "", 0)["options"]]
        third = [o["code"] for o in run.search(name, "customer", "", 40)["options"]]
        self.assertEqual(first[0], "C044")
        self.assertNotIn("C044", third)
        self.assertEqual([o["code"] for o in run.search(name, "customer", "C04", 0)["options"]][0], "C044")
        self.assertEqual([o["code"] for o in run.search(name, "customer", "C01", 0)["options"]][0], "C010")  # 搜不到它就照原序

    def test_a_recommended_option_on_the_first_page_moves_to_the_front(self):
        self.recommended = "C005"
        name = run.start("S1", "collection")["run"]
        codes = [o["code"] for o in run.view(self.docs[name])["card"]["questions"][0]["options"]]
        self.assertEqual(codes[:2], ["C005", "C001"])
        self.assertEqual(len(codes), 20)


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

    def test_a_guide_that_switches_to_the_allow_card_by_answer_asks_the_question_of_that_answer(self):
        ATTRS["app.GUIDES"] = [{**GUIDE, "consent": {"key": "customer", "in": ["C001"]}}]
        _name, found = self.reviewing()
        self.assertEqual((found["consent"], found["consent_title_key"]), ("allow", "guide.consent.collection.C001"))
        ATTRS["app.GUIDES"] = [{**GUIDE, "consent": {"key": "customer", "in": ["C002"]}}]
        _name, found = self.reviewing()
        self.assertEqual((found["consent"], found["consent_title_key"]), ("confirm", "guide.consent.collection"))

    def test_a_finished_run_carries_no_question(self):
        name, _found = self.reviewing()
        done = run.confirm(name, run.view(self.docs[name])["version"])
        self.assertNotIn("consent_title", done)


class CommitBeforeViewTests(Base):
    """11-19 回滚审计 P3 / P4 / _note：画卡（``view``）会调产品的选项、判定、说明，产品那边有的会回滚（Cowork 判卡片「只是看」、
    说明出错的回滚）。引擎每次改了这次引导（开、答、改、确定、取消、追问、补上下文），先提交再画卡，没提交的改动不被冲掉。"""

    def setUp(self):
        super().setUp()
        self.trail = []
        saved, shown, inserted = run._save, run.view, run._insert
        run._save = lambda doc: (self.trail.append("save"), saved(doc))[1]
        run._insert = lambda values: (self.trail.append("save"), inserted(values))[1]
        run.view = lambda doc: (self.trail.append("view"), shown(doc))[1]
        self.addCleanup(setattr, run, "view", shown)
        db = frappe.db
        frappe.db = types.SimpleNamespace(commit=lambda: self.trail.append("commit"), rollback=lambda: self.trail.append("rollback"))
        self.addCleanup(setattr, frappe, "db", db)

    def assert_committed_before_each_view(self):
        last_save = None
        for at, step in enumerate(self.trail):
            if step == "save":
                last_save = at
            if step == "view" and last_save is not None:
                self.assertIn("commit", self.trail[last_save:at], self.trail)
                last_save = None

    def test_start_answer_and_cancel_commit_before_drawing_the_card(self):
        name = run.start("S1", "collection")["run"]
        run.answer(name, {"customer": "C001"})
        run.cancel(name)
        self.assertIn("view", self.trail)
        self.assert_committed_before_each_view()

    def test_filling_in_the_conversation_context_is_committed_before_the_card(self):
        self.now["company"] = ""
        name = run.start("S1", "collection")["run"]
        self.now["company"] = "CO1"  # 右栏后补上公司：下一次作答先补上下文、存下
        self.trail.clear()
        run.answer(name, {"customer": "C001"})
        self.assert_committed_before_each_view()


class PageFieldTests(Base):
    """页面要的两样题面字段（A2 等的服务端，约定见共享笔记「引导卡页面接口-约定-11-19.md」）：声明 ``zero`` 的金额多选题带
    ``zero: true``（盘点数到 0 有效）；分摊题带 ``total``（要分摊的合计 = 它前面最近一道金额多选题此刻答的各行之和，字符串两位小数）。"""

    def test_a_split_question_carries_the_total_of_the_amounts_before_it(self):
        name = run.start("S1", "collection")["run"]
        run.answer(name, {"customer": "C001"})
        found = run.answer(name, {"invoices": [{"code": "IV001", "amount": "600.5"}, {"code": "IV002", "amount": "99.50"}]})
        method = next(q for q in found["card"]["questions"] if q["key"] == "method")
        self.assertEqual(method["total"], "700.00")
        self.assertNotIn("zero", method)

    def test_a_zero_step_says_so_and_others_do_not(self):
        counted = {**GUIDE, "steps": [dict(GUIDE["steps"][0]), {**GUIDE["steps"][1], "zero": True}, *GUIDE["steps"][2:]]}
        ATTRS["app.GUIDES"] = [counted]
        name = run.start("S1", "collection")["run"]
        self.assertNotIn("zero", run.view(self.docs[name])["card"]["questions"][0])
        found = run.answer(name, {"customer": "C001"})
        invoices = next(q for q in found["card"]["questions"] if q["key"] == "invoices")
        self.assertIs(invoices["zero"], True)
        self.assertNotIn("total", invoices)


class ReopenTests(Base):
    """复核卡 / 已定项上点某一步「改」（guide_reopen）：回到那一步重问，原答案作为题的 ``value``（不当 preselected），依赖它的后面各步
    作废、状态回 asking、版本 +1；后台定的那一步（auto）也能改，改的时候照常问；版本不对回 changed；不是这次引导的步骤拒收。"""

    def test_reopening_an_answered_step_from_review_asks_it_again_with_the_old_answer(self):
        name, found = self.to_review()
        self.assertEqual(found["status"], "reviewing")
        back = run.reopen(name, "invoices", found["version"])
        self.assertEqual((back["status"], back["version"]), ("asking", found["version"] + 1))
        asked = {q["key"]: q for q in back["card"]["questions"]}
        self.assertEqual(list(asked), ["invoices"])
        self.assertEqual(asked["invoices"]["value"], IV)
        self.assertNotIn("preselected", asked["invoices"])
        self.assertEqual(back["card"]["answers"], {"customer": "C001"})   # 方式依赖发票：作废
        again = run.answer(name, {"invoices": IV})
        self.assertEqual([q["key"] for q in again["card"]["questions"]], ["method"])
        self.assertNotIn("value", again["card"]["questions"][0])

    def test_a_decided_step_can_be_reopened_and_is_asked(self):
        name = run.start("S1", "collection")["run"]
        found = run.reopen(name, "on_date", 1)
        asked = {q["key"]: q for q in found["card"]["questions"]}
        self.assertEqual(asked["on_date"]["value"], "2026-11-14")
        self.assertNotIn("on_date", [item["key"] for item in found["card"]["decided"]])
        found = run.answer(name, {"on_date": "2026-11-13"})
        self.assertNotIn("on_date", [q["key"] for q in found["card"]["questions"]])
        self.assertEqual(found["card"]["answers"]["on_date"], "2026-11-13")

    def test_the_draft_made_before_reopening_is_handed_to_the_next_draft(self):
        # A2 11-19 实验栈：改一步回去重问后再到复核，第一次复核建的收款草稿留在库里成了孤儿。草稿号要留到下一次生成草稿，
        # 产品据 ctx["draft"] 作废上一张（同收款引导 `collection.guide.draft`）
        seen = []
        ATTRS["app.draft"] = lambda ctx, values: seen.append(ctx["draft"]) or f"R:{ctx['run']}:{len(seen)}"
        name, found = self.to_review()
        self.assertEqual(seen, [""])
        back = run.reopen(name, "invoices", found["version"])
        self.assertEqual(self.docs[name].draft, f"R:{name}:1")   # 重问期间不丢
        run.answer(name, {"invoices": IV})
        again = run.answer(name, {"method": CASH})
        self.assertEqual((again["status"], seen), ("reviewing", ["", f"R:{name}:1"]))
        self.assertEqual(self.docs[name].draft, f"R:{name}:2")
        self.assertEqual(back["status"], "asking")
        run.reopen(name, "customer", again["version"])   # 再改一次：上一张照样交下去
        run.answer(name, {"customer": "C002"})
        run.answer(name, {"invoices": [{"code": "IV009", "amount": "1000"}]})
        third = run.answer(name, {"method": CASH})
        self.assertEqual((third["status"], seen[-1], self.docs[name].draft), ("reviewing", f"R:{name}:2", f"R:{name}:3"))

    def test_a_stale_version_or_an_unknown_step_is_not_reopened(self):
        name, found = self.to_review()
        self.assertEqual(run.reopen(name, "invoices", found["version"] - 1)["outcome"], "changed")
        refused = run.reopen(name, "nope", found["version"])
        self.assertEqual(refused["outcome"], "refused")
        self.assertEqual(refused["status"], "reviewing")
        unanswered = run.start("S1", "collection")["run"]
        self.assertEqual(run.reopen(unanswered, "invoices", 1)["outcome"], "refused")   # 还没答过的不用「改」


class SearchAnyOptionStepTests(Base):
    """guide_search 对任何选项步骤都给（不只声明了 search 的）：不带字时给第一页，「已定」里点改、翻页都用它（A2 约定第 3 条；
    wire 上已经如此，这条守住）。"""

    def test_a_step_without_search_still_pages(self):
        name = run.start("S1", "collection")["run"]
        run.answer(name, {"customer": "C001"})
        frappe.db.get_value = lambda doctype, filters, field, **kw: filters["name"] if self.locked(filters["name"]) else None
        frappe.get_doc = lambda doctype, found: self.docs[found]
        self.assertEqual([row["code"] for row in run.search(name, "invoices")["options"]], ["IV001", "IV002"])
        self.assertEqual([row["code"] for row in run.search(name, "method")["options"]], ["cash", "transfer"])


class FormEditTests(Base):
    """复核卡就地改一格（guide_edit）对表单业务：声明点名的 ``edit(ctx, draft, row, field, value)`` 回 ``{"draft": 新草稿}`` 就换成
    新草稿、版本 +1、复核卡照新草稿出；回 ``{"refused": {格: 码}, "refused_text"?}`` 不改，拒收照引擎的形状；不在复核、版本不对、
    没声明 ``edit`` 的不收。"""

    def setUp(self):
        super().setUp()
        self.edits = []

        def edit(ctx, draft, row, field, value):
            self.edits.append((draft, row, field, value))
            if field == "rate" and value == "99":
                return {"refused": {"rate": "too_many_rows"}, "refused_text": {"rate": "Rate 99 is not allowed"}}
            return {"draft": draft + "+1"}
        ATTRS["app.edit"] = edit
        ATTRS["app.GUIDES"] = [{**GUIDE, "edit": "app.edit"}]

    def test_an_edit_replaces_the_draft_and_the_review_follows(self):
        name, found = self.to_review()
        done = run.edit(name, "ROW1", "base", "1200", found["version"])
        self.assertEqual(self.edits, [(f"R:{name}", "ROW1", "base", "1200")])
        self.assertEqual((done["status"], done["version"], done["review"]["draft"]), ("reviewing", found["version"] + 1, f"R:{name}+1"))
        self.assertEqual(done.get("outcome", ""), "")

    def test_a_refused_edit_changes_nothing(self):
        name, found = self.to_review()
        back = run.edit(name, "ROW1", "rate", "99", found["version"])
        self.assertEqual(back["outcome"], "refused")
        self.assertEqual(back["refused"], {"rate": {"code": "too_many_rows", "text": ""}})
        self.assertEqual(back["refused_text"], {"rate": "Rate 99 is not allowed"})
        self.assertEqual((back["version"], back["review"]["draft"]), (found["version"], f"R:{name}"))

    def test_only_on_review_with_the_seen_version_and_a_declared_edit(self):
        name, found = self.to_review()
        self.assertEqual(run.edit(name, "ROW1", "base", "1", found["version"] - 1)["outcome"], "changed")
        asking = run.start("S1", "collection")["run"]
        self.assertEqual(run.edit(asking, "ROW1", "base", "1", 1)["outcome"], "refused")
        ATTRS["app.GUIDES"] = [GUIDE]
        name, found = self.to_review()
        self.assertEqual(run.edit(name, "ROW1", "base", "1", found["version"])["outcome"], "refused")
        self.assertEqual(len(self.edits), 0)


if __name__ == "__main__":
    unittest.main()
