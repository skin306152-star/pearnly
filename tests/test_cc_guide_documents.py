"""引导的 documents 出题源（``pearnly_core.assistant.guide.documents``，约定「引导式-documents源-约定-11-16」）：按单据办的业务
用共用引擎 ``guide.run`` 真实现走一遍，产品函数、写账判定、写入计划用替身。

断言的是应有状态（两产品、直写 / 代录同一套）：
- 点「采购」开一次：第一张卡只有交文件一题（标题是开场那一句）；交了文件经 ``files.commit(via="guide")`` 交给产品识别；
- 识别中：卡上只有等待（页面过一会儿再取），不出题；识别出票后出题卡（第 1 期那张卡：三档、已定 N 项、带 ``input``）；
- 同一对方几张票的付款并成一题（题上带张数），点一次覆盖这几张；答案按引导口径记（``record(guided=True)``）；
- 记录自己的题定了以后冒出来的账本那边的题（第二轮新题）接着问；问过、没点的那几张这次留下，不再追问；
- 复核：每张一行票面（产品给），改一格版本 +1、产品写回；算出来的格不让改；
- 确定 = 页面上这一下（``guide:<运行>:<版本>``）：写入计划放行（只写可写的），直写调产品 ``confirm``、代录交 ``hand_in``；
  复核后票在别处改过（指纹对不上）不办、回到复核；同一确定再来一次不办第二次；
- 选了采购交了销售票：一道「做不做」题，答不写入的那几张留下；
- 声明没点名 documents 源要的产品函数的，页面上开不了。

替身 frappe 与内存里的记录，不连库。

    python3 tests/test_cc_guide_documents.py
"""
import datetime
import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

HOOKS, ATTRS, LOGGED = {}, {}, []
frappe = types.ModuleType("frappe")
frappe._ = lambda text: text
frappe.get_hooks = lambda name: HOOKS.get(name, [])
frappe.get_attr = lambda path: ATTRS[path]
frappe.log_error = lambda title="", message="": LOGGED.append(title)
frappe.session = types.SimpleNamespace(user="u@example.com")
frappe.local = types.SimpleNamespace(pearnly_assistant=None)
frappe.DoesNotExistError = LookupError
frappe.PermissionError = PermissionError
frappe.QueryDeadlockError = type("QueryDeadlockError", (Exception,), {})
frappe.whitelist = lambda **kw: (lambda fn: fn)
frappe.parse_json = json.loads


def _throw(message, exc=ValueError):
    raise exc(message)


frappe.throw = _throw
frappe.db = types.SimpleNamespace(commit=lambda: None, rollback=lambda: None)
frappe.utils = types.SimpleNamespace(get_datetime=lambda value: value, now_datetime=lambda: datetime.datetime(2026, 11, 16, 10, 5))  # 放太久失效（run._idle）：同测试里的时钟
frappe.publish_realtime = lambda *a, **kw: None
sys.modules["frappe"] = frappe


def _module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


_module("pearnly_core.assistant.contracts", REALTIME_EVENT="pearnly_assistant_event")
COMMITTED, HANDED, PLANS = [], [], []
_module("pearnly_core.assistant.files", commit=lambda row, ids, via: COMMITTED.append((row, list(ids), via)))
_module("pearnly_core.assistant.sessions", owned=lambda session_id: {"session_id": session_id})
LEDGER = {"view": {"current": "B1", "options": []}}
_module("pearnly_core.assistant.ledger", SET="set", view=lambda session: LEDGER["view"],
        choose=lambda session, ledger_id, by="page": {"status": "set"})
_plan = _module("pearnly_core.assistant.write_plan", STARTED="started", _iso=lambda moment: moment.isoformat() + "Z")
_module("pearnly_core.express_operator", __path__=[str(ROOT / "apps/pearnly_core/pearnly_core/express_operator")])
_module("pearnly_core.express_operator.api",
        hand_in=lambda company, task, doc_ids, again=None: HANDED.append((company, task, list(doc_ids))) or {"handed": len(doc_ids)})

import pearnly_core.assistant as _assistant  # noqa: E402
for _name in ("files", "sessions", "ledger", "write_plan", "contracts"):
    setattr(_assistant, _name, sys.modules[f"pearnly_core.assistant.{_name}"])
from pearnly_core.assistant.guide import declaration, documents, run  # noqa: E402
from pearnly_core.write_precheck import api as precheck_api  # noqa: E402
from pearnly_core.write_precheck import contracts as c  # noqa: E402

PURCHASE = {"id": "purchase", "label_key": "guide.business.purchase", "icon": "shopping-cart", "order": 20,
            "modes": ["chat", "operator"], "ledgers": ["express_local"], "opening_key": "guide.open.purchase",
            "source": "documents", "writes": True, "steps": [{"key": "documents", "input": "file", "rule": "always"}],
            "expires": ["ledger", "company"], "intake": "app.intake", "uploads": "app.uploads", "review": "app.review",
            "edit": "app.edit", "confirm": "app.confirm", "result": "app.result"}
PAY = {"kind": "payment", "subject": "erp-intake-pay:purchase|0105", "label": "ISUZU",
       "options": [{"code": "cash"}, {"code": "credit"}], "value": "cash", "source": c.RULE}
ACCOUNT = {"kind": "account", "subject": "purchase|paper", "label": "Paper", "options": [{"code": "51-01"}, {"code": "52-01"}]}


class Doc(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


class Base(unittest.TestCase):
    def setUp(self):
        self.docs, self.clock = {}, [0]
        self.now = {"session_id": "S1", "mode": "chat", "company": "CO1", "ledger": "B1", "period": "2026-11", "stopped_at": None}
        self.reading, self.doc_ids, self.second_round = True, ["I:1", "I:2"], False
        self.items = {"I:1": [dict(PAY)], "I:2": [dict(PAY)]}     # 每张票此刻要问的（判定替身）
        self.directions = {"I:1": "purchase", "I:2": "purchase"}
        self.recorded, self.confirmed, self.edits = [], [], []
        self.faces = {doc_id: {"doc_no": f"IV{doc_id[-1]}", "total": "107.00"} for doc_id in self.doc_ids}
        self.row_version = {doc_id: 1 for doc_id in self.doc_ids}
        COMMITTED.clear(), HANDED.clear(), PLANS.clear(), LOGGED.clear()
        HOOKS["pearnly_guides"] = ["app.GUIDES"]
        ATTRS.clear()
        ATTRS.update({
            "app.GUIDES": [PURCHASE],
            "app.intake": lambda ctx, files: {"batches": [f"B-{f}" for f in files]},
            "app.uploads": lambda ctx, batches: {"files": [{"batch": b, "state": "reading" if self.reading else "done"}
                                                           for b in batches],
                                                 "doc_ids": [] if self.reading else list(self.doc_ids), "wait": self.reading},
            "app.review": lambda ctx, doc_ids: {"rows": [{"doc_id": d, "version": self.row_version[d], "face": self.faces[d],
                                                          "marks": [], "cells": {}, "blocking": []} for d in doc_ids]},
            "app.edit": self.edit,
            "app.confirm": lambda ctx, doc_ids, consent: self.confirmed.append((list(doc_ids), consent)) or {
                "status": "done", "docs": [{"doc_id": d, "status": "queued"} for d in doc_ids]},
            "app.result": self.result,
        })
        run._insert, run._locked, run._save, run._open_runs, run._conversation = (
            self.insert, lambda name: self.docs.get(name), self.save,
            lambda sid: [d.name for d in self.docs.values() if d.session_id == sid and d.status in run.OPEN],
            lambda session_id: dict(self.now))
        self.saved_api = (precheck_api.card, precheck_api.record, precheck_api.search)
        precheck_api.card = self.card
        precheck_api.record = self.record
        _plan._judge = lambda company, ids: ({}, {d: f"v{self.row_version.get(d)}" for d in ids})
        _plan.from_page = self.from_page

    def tearDown(self):
        precheck_api.card, precheck_api.record, precheck_api.search = self.saved_api

    # ── 替身 ──
    def card(self, company, doc_ids, hint=""):
        if self.second_round and any(i["kind"] == "payment" and i.get("source") == c.REMEMBERED for i in self.items["I:1"]) \
                and not any(i["kind"] == "account" for i in self.items["I:1"]):
            self.items["I:1"].append(dict(ACCOUNT))  # 付款定了以后账本那边才判得出科目（ERP 收单记录的第二轮）
        docs = [{"doc_id": d, "direction": self.directions[d], "items": [dict(i) for i in self.items[d]]}
                for d in doc_ids if d in self.items]  # 产品只回本人能写、这家公司的单
        return {**c.merge(docs), "company": company, "company_title": "Co",
                "directions": {d: self.directions[d] for d in doc_ids if d in self.items}}

    def record(self, company, doc_ids, answers, resume=False, guided=False):
        self.recorded.append({"answers": answers, "guided": guided})
        answered = {a["key"] for a in answers}
        held = []
        for doc_id in doc_ids:
            open_items = [i for i in self.items[doc_id] if c.item_key(i["kind"], i["subject"]) not in answered]
            if open_items:
                held.append(doc_id)
            # 点过的记住了：下次判定算已定
            self.items[doc_id] = [{**i, "source": c.REMEMBERED, "value": next(
                a["value"] for a in answers if a["key"] == c.item_key(i["kind"], i["subject"]))}
                if c.item_key(i["kind"], i["subject"]) in answered else i for i in self.items[doc_id]]
        return {"writable": [d for d in doc_ids if d not in held], "held": held}

    def result(self, ctx, doc_ids):
        self.asked_results = list(doc_ids)
        return {"rows": [{"doc_id": d, "status": "queued"} for d in doc_ids], "wait": True}

    def from_page(self, session_id, company, doc_ids, consent):
        PLANS.append((session_id, company, list(doc_ids), consent))
        return {"status": "started", "plan": "PLAN1", "mode": "operator" if self.now["mode"] == "operator" else "direct",
                "operator_task": "TK1" if self.now["mode"] == "operator" else "", "documents": list(doc_ids), "skipped": []}

    def edit(self, ctx, doc_id, field, value):
        if field == "total":
            return {"refused": "computed"}
        self.edits.append((doc_id, field, value))
        self.faces[doc_id] = {**self.faces[doc_id], field: value}
        self.row_version[doc_id] += 1
        return {"doc_id": doc_id, "face": self.faces[doc_id]}

    def tick(self):
        self.clock[0] += 1
        return datetime.datetime(2026, 11, 16, 10, 0, self.clock[0])

    def insert(self, values):
        doc = Doc(values, name=f"RUN{len(self.docs) + 1}", modified=self.tick())
        self.docs[doc.name] = doc
        return doc

    def save(self, doc):
        doc.modified = self.tick()

    # ── 走到哪 ──
    def to_card(self):
        name = run.start("S1", "purchase")["run"]
        run.answer(name, {"documents": ["F1"]})
        self.reading = False
        return name, run.view(self.docs[name])

    def answer_card(self, name, answers):
        found = run.view(self.docs[name])
        return run.answer(name, {"answers": answers}, found["version"])

    def to_review(self):
        name, found = self.to_card()
        pay = found["card"]["questions"][0]
        return name, self.answer_card(name, [{"key": pay["key"], "value": "credit"}])


class Walk(Base):
    def test_the_first_card_only_asks_for_the_files_and_they_go_to_the_product_through_the_one_entry(self):
        found = run.start("S1", "purchase")
        self.assertEqual((found["source"], found["stage"]), ("documents", documents.FILES))
        [question] = found["card"]["questions"]
        self.assertEqual((question["key"], question["input"], question["title_key"]), ("documents", "file", "guide.open.purchase"))
        found = run.answer(found["run"], {"documents": ["F1", "F2"]}, found["version"])
        self.assertEqual(COMMITTED, [({"session_id": "S1"}, ["F1", "F2"], "guide")])
        self.assertEqual(found["stage"], documents.READING)
        self.assertEqual((found["card"]["questions"], found["card"]["wait"]), ([], True))  # 识别中不出题，页面过一会儿再取
        self.assertEqual(json.loads(self.docs[found["run"]].documents)["batches"], ["B-F1", "B-F2"])
        again = run.answer(found["run"], {"documents": ["F3"]}, found["version"])  # 交过了不再交
        self.assertEqual(again["outcome"], run.REFUSED)

    def test_a_payment_of_one_party_on_two_tickets_is_one_question_and_answers_go_through_the_guided_record(self):
        name, found = self.to_card()
        self.assertEqual(found["stage"], documents.ASKING)
        [pay] = found["card"]["questions"]
        self.assertEqual((pay["kind"], pay["count"], sorted(pay["docs"]), pay["input"]), ("payment", 2, ["I:1", "I:2"], "choice"))
        self.assertEqual((pay["tier"], pay["preselected"]), ("how", "cash"))  # 推荐预选，点了才算
        found = self.answer_card(name, [{"key": pay["key"], "value": "credit"}])
        self.assertEqual(self.recorded[-1], {"answers": [{"key": pay["key"], "value": "credit"}], "guided": True})
        self.assertEqual(found["stage"], documents.REVIEW)
        self.assertEqual([row["doc_id"] for row in found["review"]["rows"]], ["I:1", "I:2"])

    def test_questions_that_appear_once_the_record_is_decided_are_asked_next(self):
        name, found = self.to_card()
        pay = found["card"]["questions"][0]
        self.second_round = True
        found = self.answer_card(name, [{"key": pay["key"], "value": "cash"}])
        self.assertEqual(found["stage"], documents.ASKING)
        self.assertEqual([q["kind"] for q in found["card"]["questions"]], ["account"])
        found = self.answer_card(name, [{"key": found["card"]["questions"][0]["key"], "value": "51-01"}])
        self.assertEqual(found["stage"], documents.REVIEW)

    def test_a_question_left_unanswered_holds_those_tickets_and_is_not_asked_again(self):
        name, found = self.to_card()
        self.items["I:2"] = [dict(ACCOUNT, subject="purchase|ink")]
        found = run.view(self.docs[name])
        keys = {q["kind"]: q["key"] for q in found["card"]["questions"]}
        found = self.answer_card(name, [{"key": keys["payment"], "value": "cash"}])  # 没点 I:2 的科目
        self.assertEqual(found["stage"], documents.REVIEW)
        self.assertEqual([row["doc_id"] for row in found["review"]["rows"]], ["I:1"])
        self.assertEqual(found["review"]["held"], ["I:2"])
        found = run.confirm(name, found["version"], found["review"]["seen"])
        rows = {row["doc_id"]: row for row in found["result"]["rows"]}
        self.assertEqual((rows["I:1"]["status"], rows["I:2"]["status"]), ("queued", "held"))  # 留下的那张结果行可见
        self.assertEqual(self.confirmed[0][0], ["I:1"])
        self.assertEqual(self.asked_results, ["I:1"])  # 结果只向产品要交出去的那几张
        self.assertEqual(rows["I:2"]["reason"], "held")

    def test_review_edits_go_to_the_product_and_computed_cells_do_not_change(self):
        name, found = self.to_review()
        found = run.edit(name, "I:1", "doc_no", "IV-9", found["version"])
        self.assertEqual(self.edits, [("I:1", "doc_no", "IV-9")])
        self.assertEqual(found["review"]["rows"][0]["face"]["doc_no"], "IV-9")
        before = found["version"]
        found = run.edit(name, "I:1", "total", "999", before)
        self.assertEqual((found["outcome"], found["refused"]["total"]["code"]), (run.REFUSED, "computed"))
        self.assertEqual(set(found["refused"]["total"]), {"code", "text"})   # 同表单与作答的拒收形状 {格: {code, text}}（约定 11-19）
        self.assertEqual(run.edit(name, "I:9", "doc_no", "x", found["version"])["outcome"], run.REFUSED)  # 不在复核里的票
        self.assertEqual(run.edit(name, "I:1", "doc_no", "x", before - 1)["outcome"], run.CHANGED)

    def test_confirm_in_direct_write_goes_through_the_write_plan_then_the_products_confirm_once(self):
        name, found = self.to_review()
        found = run.confirm(name, found["version"], found["review"]["seen"])
        consent = f"guide:{name}:{found['version'] - 2}"
        self.assertEqual(PLANS, [("S1", "CO1", ["I:1", "I:2"], consent)])
        self.assertEqual(self.confirmed, [(["I:1", "I:2"], consent)])
        self.assertEqual(HANDED, [])
        self.assertEqual((found["status"], [row["status"] for row in found["result"]["rows"]]), ("done", ["queued", "queued"]))
        self.assertTrue(found["result"]["wait"])
        again = run.confirm(name, int(consent.rsplit(":", 1)[1]))
        self.assertEqual((again["outcome"], len(self.confirmed)), ("", 1))  # 同一确定再来一次不办第二次

    def test_confirm_in_an_operator_task_hands_the_same_tickets_to_the_computer(self):
        self.now["mode"] = "operator"
        name, found = self.to_review()
        run.confirm(name, found["version"], found["review"]["seen"])
        self.assertEqual(HANDED, [("CO1", "TK1", ["I:1", "I:2"])])
        self.assertEqual(self.confirmed, [])  # 代录不调直写的确定

    def test_a_ticket_changed_elsewhere_after_review_is_not_written(self):
        name, found = self.to_review()
        self.row_version["I:2"] += 1  # 网页 / LINE 编辑页改过
        found = run.confirm(name, found["version"], found["review"]["seen"])
        self.assertEqual(found["outcome"], run.CHANGED)
        self.assertEqual((PLANS, self.confirmed), ([], []))
        self.assertIn(found["status"], run.OPEN)
        found = run.confirm(name, found["version"], found["review"]["seen"])
        self.assertEqual(len(self.confirmed), 1)

    def test_sales_tickets_in_a_purchase_guide_ask_whether_to_write_them(self):
        self.directions["I:2"] = "sales"
        name, found = self.to_card()
        keys = {q["kind"]: q for q in found["card"]["questions"]}
        self.assertEqual((keys["direction"]["docs"], keys["direction"]["tier"]), (["I:2"], "whether"))
        found = self.answer_card(name, [{"key": keys["payment"]["key"], "value": "cash"},
                                        {"key": keys["direction"]["key"], "value": documents.SKIP}])
        self.assertEqual(found["review"]["held"], ["I:2"])
        self.assertNotIn("direction", [q["kind"] for q in run.view(self.docs[name]).get("card", {}).get("questions", [])])

    def test_answers_not_on_the_card_are_refused_and_nothing_is_recorded(self):
        name, _found = self.to_card()
        found = self.answer_card(name, [{"key": "payment|nope", "value": "cash"}])
        self.assertEqual(found["outcome"], run.REFUSED)
        self.assertEqual(self.recorded, [])


class Entries(unittest.TestCase):
    """选项带 entry（A3：单位题「填换算」、成本题「填单位成本」）：答案「码:数」，数要合 entry 的下限。"""

    CARD = {"questions": [{"key": "unit|1", "options": [{"code": "same"},
                                                       {"code": "factor", "entry": {"input": "number", "min_exclusive": "0"}}]}]}

    def test_a_number_after_an_entry_option_is_taken_and_a_bad_one_is_refused(self):
        self.assertEqual(documents.check_answers(self.CARD, [{"key": "unit|1", "value": "factor:6"}]),
                         [{"key": "unit|1", "value": "factor:6"}])
        self.assertEqual(documents.check_answers(self.CARD, [{"key": "unit|1", "value": "same"}])[0]["value"], "same")
        # 数的口径同共用记答（contracts.entry_answer）：交出去的是规整后的「码:数」
        self.assertEqual(documents.check_answers(self.CARD, [{"key": "unit|1", "value": "factor:1,200"}])[0]["value"], "factor:1200")
        for bad in ("factor:0", "factor:x", "same:3", "factor", "factor:nan"):
            self.assertRaises(documents.AnswerError, documents.check_answers, self.CARD, [{"key": "unit|1", "value": bad}])


    def test_free_question_takes_the_typed_value(self):
        card = {"questions": [{"key": "cost|1", "free": True, "options": [{"code": "none"},
                                                                         {"code": "given", "entry": {"input": "number", "min_exclusive": "0"}}]}]}
        self.assertEqual(documents.check_answers(card, [{"key": "cost|1", "value": "120"}])[0]["value"], "120")
        self.assertRaises(documents.AnswerError, documents.check_answers, self.CARD, [{"key": "unit|1", "value": "120"}])


class HandOver(Base):
    """公开入口：别的引导（待处理改票面、收银日汇总建好的票）/ 代录开始前把已有单据交进来，从判定或复核接着走。"""

    def test_handed_documents_go_straight_to_the_card_without_files_or_the_business_question(self):
        self.directions["I:2"] = "sales"
        found = run.start("S1", "purchase", doc_ids=["I:1", "I:2", "I:1"])
        self.assertEqual(found["stage"], documents.ASKING)
        self.assertEqual([q["kind"] for q in found["card"]["questions"]], ["payment"])  # 不是按菜单交的票：不问业务对不上
        self.assertEqual(COMMITTED, [])

    def test_handed_documents_at_review_skip_the_card_and_hold_what_is_undecided(self):
        self.items["I:2"] = [dict(PAY, subject="erp-intake-pay:purchase|0999", source=c.REMEMBERED)]  # 另一个对方、记住过
        found = run.start("S1", "purchase", doc_ids=["I:1", "I:2"], at="review")
        self.assertEqual(found["stage"], documents.REVIEW)
        self.assertEqual(([row["doc_id"] for row in found["review"]["rows"]], found["review"]["held"]), (["I:2"], ["I:1"]))

    def test_bad_hand_overs_are_refused_before_anything_is_replaced(self):
        first = run.start("S1", "purchase")["run"]
        self.assertRaises(ValueError, run.start, "S1", "purchase", doc_ids=[])
        self.assertRaises(ValueError, run.start, "S1", "purchase", doc_ids=["I:1"], at="files")
        self.assertRaises(ValueError, run.start, "S1", "purchase", doc_ids=[f"I:{n}" for n in range(201)])
        self.assertRaises(PermissionError, run.start, "S1", "purchase", doc_ids=["I:1", "I:9"])  # 不是本人能写的
        self.assertEqual(self.docs[first].status, "asking")  # 核不过不作废开着的那次


class OperatorStart(Base):
    """A4：代录开始前那张卡并进引导（`express_operator.guide_start`）：这个月的完整待录清单开一次引导；写过的问再录不录、结果不明的
    问入没入账、没单时问月份；三张卡的题在同一张卡上；确定后带「再录一次」交 `hand_in`。"""

    def setUp(self):
        super().setUp()
        self.now["mode"] = "operator"
        self.pre = {"I:1": {"state": "ready"}, "I:2": {"state": "written", "docnum": "RR0007", "ref": "IV2"}}
        self.months, self.moved, self.resolved, self.sources = [], [], [], ["I:1", "I:2"]
        tasks = _module("pearnly_core.express_operator.tasks", RESULT_UNKNOWN="push.result_unknown",
                        read=lambda name: {"company": "CO1", "period": self.now["period"], "status": "preparing", "session_id": "S1"})
        products = _module("pearnly_core.express_operator.products",
                           sources=lambda company, period, offset, limit: {
                               "documents": [{"doc_id": d, "kind": "purchase"} for d in self.sources] if offset == 0 else [],
                               "next_offset": None},
                           precheck=lambda company, ids: {"documents": [{"doc_id": d, **self.pre.get(d, {"state": "ready"})} for d in ids]},
                           elsewhere=lambda company, period: list(self.months),
                           computer_words=lambda company, user: "")
        api = sys.modules["pearnly_core.express_operator.api"]
        api.SOURCES_MAX, api.BATCH_MAX = 1000, 200
        api.precheck = lambda company, ids, task="": {"targets": [{"account_set_title": "T.8", "count": len(ids)}]}
        api.resolve = lambda company, doc_id, entered: self.resolved.append((doc_id, entered))
        sys.modules["pearnly_core.express_operator"].tasks = tasks
        sys.modules["pearnly_core.express_operator"].products = products
        sys.modules["pearnly_core.express_operator"].api = api
        sessions = sys.modules["pearnly_core.assistant.sessions"]
        sessions.SESSION = "Pearnly Assistant Session"

        def move(session, project, period="", scope=""):
            self.moved.append(period)
            self.now["period"] = period
            return {"period": period}

        sessions.move = move
        frappe.db.get_value = lambda doctype, filters, fields=None, as_dict=False: {"project": "CO1", "project_scope": ""}
        from pearnly_core.express_operator import guide_start
        self.guide_start = guide_start

    def test_one_card_asks_again_or_skip_for_a_written_ticket_before_the_write_questions(self):
        found = self.guide_start.start("TK1")
        kinds = [q["kind"] for q in found["card"]["questions"]]
        self.assertEqual(kinds, ["operator_again", "payment"])  # 代录自己的题在前，写账判定的题在后，同一张卡
        again = found["card"]["questions"][0]
        self.assertEqual((again["docs"], again["tier"], again["preselected"]), (["I:2"], "whether", ""))
        self.assertEqual(found["operator"]["targets"][0]["account_set_title"], "T.8")
        pay = found["card"]["questions"][1]
        found = self.answer_card(found["run"], [{"key": again["key"], "value": "again"},
                                                {"key": pay["key"], "value": "credit"}])
        self.assertEqual(found["stage"], documents.REVIEW)
        found = run.confirm(found["run"], found["version"], found["review"]["seen"])
        self.assertEqual(HANDED, [("CO1", "TK1", ["I:1", "I:2"])])
        self.assertEqual(self.confirmed, [])

    def test_a_written_ticket_left_as_skip_is_not_handed_in(self):
        found = self.guide_start.start("TK1")
        again, pay = found["card"]["questions"]
        found = self.answer_card(found["run"], [{"key": again["key"], "value": "skip"}, {"key": pay["key"], "value": "cash"}])
        self.assertEqual(found["review"]["held"], ["I:2"])
        run.confirm(found["run"], found["version"], found["review"]["seen"])
        self.assertEqual(HANDED[-1][2], ["I:1"])

    def test_a_result_unknown_ticket_is_resolved_on_the_card(self):
        self.pre["I:2"] = {"state": "blocked", "reason": "push.result_unknown", "ref": "IV2"}
        found = self.guide_start.start("TK1")
        unknown = found["card"]["questions"][0]
        self.assertEqual(unknown["kind"], "operator_resolve")
        self.answer_card(found["run"], [{"key": unknown["key"], "value": "entered"}])
        self.assertEqual(self.resolved, [("I:2", True)])

    def test_an_empty_month_asks_for_another_month_and_reopens_the_card_there(self):
        self.sources, self.months = [], [{"month": "2026-08", "count": 25}, {"month": "2026-07", "count": 3}]
        found = self.guide_start.start("TK1")
        [month] = found["card"]["questions"]
        self.assertEqual([o["code"] for o in month["options"]], ["2026-08", "2026-07", "stay"])
        self.assertEqual(month["options"][0]["name"], "2026-08 · 25")
        self.sources = ["I:1"]
        found = self.answer_card(found["run"], [{"key": month["key"], "value": "2026-08"}])
        self.assertEqual(self.moved, ["2026-08"])
        self.assertEqual((found["period"], found["stage"]), ("2026-08", documents.ASKING))  # 按新月份重开的那次
        self.assertEqual(self.docs["RUN1"].status, run.EXPIRED)


class LedgerAnswerKept(unittest.TestCase):
    """11-19 回滚审计 P1：卡上选的本对话账套（``ledger.choose``）当场提交，再交产品记其余答案。产品判卡片「只是看」要回滚，
    没提交的账套会被冲掉：卡记下了这题不再问，单据却一直「账套未定」。"""

    def test_the_chosen_book_is_committed_before_the_product_records(self):
        trail = []
        ledger = sys.modules["pearnly_core.assistant.ledger"]
        saved = (ledger.choose, frappe.db, precheck_api.record)
        ledger.choose = lambda session, ledger_id, by="page": trail.append("choose") or {"status": "set"}
        frappe.db = types.SimpleNamespace(commit=lambda: trail.append("commit"), rollback=lambda: trail.append("rollback"))
        precheck_api.record = lambda company, doc_ids, rows, guided=False: trail.append("record") or {"writable": list(doc_ids)}
        try:
            card = {"questions": [{"key": documents.LEDGER_PREFIX + "S1"}]}
            out = documents.record(card, "CO1", ["D1"], [{"key": documents.LEDGER_PREFIX + "S1", "value": "B2"}])
        finally:
            ledger.choose, frappe.db, precheck_api.record = saved
        self.assertEqual(out["writable"], ["D1"])
        self.assertEqual(trail, ["choose", "commit", "record"])


class Declaration(unittest.TestCase):
    def test_a_documents_guide_names_all_six_product_functions_or_none(self):
        self.assertEqual(declaration.problems(PURCHASE), [])
        partial = {**PURCHASE}
        partial.pop("edit")
        self.assertTrue(any("names all of" in text for text in declaration.problems(partial)))
        menu_only = {key: value for key, value in PURCHASE.items() if key not in declaration.DOCUMENT_HOOKS}
        self.assertEqual(declaration.problems(menu_only), [])
        self.assertTrue(any("for documents guides" in text for text in declaration.problems(
            {**menu_only, "source": "chat", "steps": [], "intake": "app.intake"})))
        self.assertIn("app.intake", declaration.paths(PURCHASE))

    def test_a_menu_only_documents_guide_cannot_start_on_the_page(self):
        base = Base("run")
        base.setUp()
        try:
            ATTRS["app.GUIDES"] = [{key: value for key, value in PURCHASE.items() if key not in declaration.DOCUMENT_HOOKS}]
            self.assertRaises(ValueError, run.start, "S1", "purchase")
        finally:
            base.tearDown()


if __name__ == "__main__":
    unittest.main()
