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
frappe.utils = types.SimpleNamespace(get_datetime=lambda value: value)
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


if __name__ == "__main__":
    unittest.main()
