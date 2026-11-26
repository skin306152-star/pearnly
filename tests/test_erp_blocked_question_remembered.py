"""被电脑拦下、要在 ERP 里选一次的单（对方档 / 存货科目组 / 服务品进项口径，`express.api._party_blocked`）：答过就不再问（11-26）。

11-26 真机：testerp 副本 PE2569-000021 上次被 CC 以 ``STOCK_ACC_GROUP_UNFIT`` 拦下；CC 对话里选了存货科目组（ERP Express Choice 记下
DM），开始前确认卡片照旧补回这道题、值为空，写入计划判「未定」，CC 反复追问，直写、代录都一样。原因：补题只看上一次写账任务的失败码，
不看人在那之后答没答。修法：这张单停下**之后**答过、答的仍是能选的值 → 不再出题，单据照「再写一次」重新准备一件（旧任务被
``replaced``，不清原因放行）；答在停下之前的照旧问。三类被拦单同一个出口，逐类测。
"""
import datetime
import sys
import types
import unittest
from unittest import mock

import test_erp_express_direct as base  # noqa: E402  共用替身（同时装好 frappe 替身）

fake, frappe = base.fake, base.frappe
STOPPED = datetime.datetime(2026, 11, 19, 18, 7)
AFTER, BEFORE = STOPPED + datetime.timedelta(days=7), STOPPED - datetime.timedelta(days=1)
GROUP = {"kind": "stock_group", "subject": "BOOK-1", "label": "9.Pearnly",
         "options": [{"code": "DM", "name": "วัตถุดิบ"}, {"code": "P002", "name": "นมพาสฯ"}], "current": ""}
PARTY = {"kind": "party", "subject": "purchase|0105561116001|00000", "label": "วิศวภัณฑ์",
         "options": [{"code": "V001", "name": "วิศวภัณฑ์", "branch": "00000"}], "current": ""}
SERVICE_VAT = {"kind": "service_vat", "subject": "purchase|0105561116001|BOOK-1", "label": "วิศวภัณฑ์",
               "options": [{"code": "accrual"}, {"code": "cash"}], "current": ""}
CASES = (("STOCK_ACC_GROUP_UNFIT", "group_needed", GROUP, "DM"),
         ("SUPPLIER_DUP_SUSPECTED", "party_needed", PARTY, "V001"),
         ("SERVICE_VAT_BASIS_UNKNOWN", "service_vat_needed", SERVICE_VAT, "cash"))


class Row(dict):
    __getattr__ = dict.get


class BlockedQuestion(base.PushModule):
    def setUp(self):
        super().setUp()
        utils = sys.modules["frappe.utils"]
        for name, value in (("now", lambda: "2026-11-26 00:00:00"), ("flt", lambda v, *a: float(v or 0)),
                            ("cint", lambda v: int(v or 0)), ("getdate", lambda v=None: v), ("add_days", lambda d, n: d)):
            setattr(utils, name, getattr(utils, name, value))
        sys.modules.pop("pearnly_erp_portal.express.api", None)
        self.addCleanup(sys.modules.pop, "pearnly_erp_portal.express.api", None)
        from pearnly_erp_portal.express import api, prepare
        self.api, self.prepare = api, prepare
        self.answers = {}   # (kind, subject) -> (value, at)
        self.stopped = STOPPED
        self.real_get_value = api.frappe.db.get_value
        for patch in (
                mock.patch.object(api.push, "document", lambda doctype, name: {"date": "2026-11-19"}),
                mock.patch.object(api.push, "_company", lambda name: {"name": name}),
                mock.patch.object(api.facts, "load", lambda key: {}),
                mock.patch.object(api.guard, "has", lambda capability, user=None: True),
                mock.patch.object(api.choices, "answered",
                                  lambda company, kind, subject: self.answers.get((kind, subject), ("", None))),
                mock.patch.object(api.frappe.db, "get_value", self.get_value, create=True)):
            patch.start()
            self.addCleanup(patch.stop)
        self.code_of = {"SERVICE_VAT_BASIS_UNKNOWN": next(iter(prepare.SERVICE_VAT_CODES))}

    def get_value(self, doctype, name, fields=None, *args, **kwargs):
        if doctype == self.api.jobs.JOB and fields == "modified":
            return self.stopped
        return self.real_get_value(doctype, name, fields, *args, **kwargs)

    def job(self, code):
        code = self.code_of.get(code, code)
        return {"job": "J1", "status": "manual", "reason": f"[{code}] stopped", "request_key": "erp:purchase:PUR-1@x"}

    def row(self):
        return Row(name="PUR-1", company="CO-1", ledger_state="held", ledger_job="J1", ledger_book="BOOK-1",
                   ledger_docnum="", ledger_reason="", ledger_needed=None)

    def blocked(self, code, needed_fn, item):
        with mock.patch.object(self.prepare, needed_fn, lambda *args: [dict(item)]):
            return self.api._party_blocked("Purchase Document", self.row(), self.job(code))

    def view(self, code, needed_fn, item):
        with mock.patch.object(self.prepare, needed_fn, lambda *args: [dict(item)]), \
                mock.patch.object(self.api.jobs, "find_by_name", lambda name: self.job(code)), \
                mock.patch.object(self.api, "_pos_stuck", lambda *args: False), \
                mock.patch.object(self.api.waiting, "of_job", lambda *args: ""):
            return self.api._view("Purchase Document", self.row())

    def test_a_question_answered_after_the_stop_is_not_asked_again_and_the_document_can_be_written_again(self):
        for code, needed_fn, item, value in CASES:
            with self.subTest(code=code):
                self.answers = {(item["kind"], item["subject"]): (value, AFTER)}
                self.assertEqual(self.blocked(code, needed_fn, item), [])
                view = self.view(code, needed_fn, item)
                # 不再出题；单据页 / CC 直写（`cc_tools._route`）走「再写一次」重新准备一件，由新的结果说话
                self.assertEqual((view["needed"], view["can_answer"], view["can_retry"]), ([], False, True))

    def test_not_answered_yet_still_asks_with_no_value_filled_in(self):
        for code, needed_fn, item, _value in CASES:
            with self.subTest(code=code):
                self.answers = {}
                needed = self.blocked(code, needed_fn, item)
                self.assertEqual([(row["kind"], row["current"]) for row in needed], [(item["kind"], "")])
                view = self.view(code, needed_fn, item)
                self.assertEqual((view["can_answer"], view["can_retry"]), (True, False))

    def test_an_answer_older_than_the_stop_still_asks(self):
        """带着这个答案写过、电脑仍拦下：答案没解决，照旧问（重答一次就是新答案，放行再写）。"""
        for code, needed_fn, item, value in CASES:
            with self.subTest(code=code):
                self.answers = {(item["kind"], item["subject"]): (value, BEFORE)}
                self.assertEqual([row["kind"] for row in self.blocked(code, needed_fn, item)], [item["kind"]])
                self.answers = {(item["kind"], item["subject"]): (value, STOPPED)}   # 同一时刻不算之后
                self.assertEqual(len(self.blocked(code, needed_fn, item)), 1)

    def test_an_answer_that_is_no_longer_a_choice_still_asks(self):
        """记住的值已不在这一项能选的值里（组判为不合格、档被删）：当没答过。"""
        for code, needed_fn, item, _value in CASES:
            with self.subTest(code=code):
                self.answers = {(item["kind"], item["subject"]): ("GONE", AFTER)}
                self.assertEqual(len(self.blocked(code, needed_fn, item)), 1)

    def test_other_stops_and_a_job_without_a_time_are_unchanged(self):
        self.answers = {("stock_group", "BOOK-1"): ("DM", AFTER)}
        self.assertEqual(self.api._party_blocked("Purchase Document", self.row(), self.job("ACCOUNT_BUSY_LOCKED")), [])
        self.assertEqual(self.api._party_blocked("Purchase Document", self.row(), None), [])
        self.stopped = None   # 读不到停下时刻：照旧问（不因此放行）
        self.assertEqual(len(self.blocked("STOCK_ACC_GROUP_UNFIT", "group_needed", GROUP)), 1)

    def test_stock_documents_ask_the_book_group_the_same_way(self):
        self.answers = {("stock_group", "BOOK-1"): ("DM", AFTER)}
        with mock.patch.object(self.prepare, "book_group_needed", lambda *args: [dict(GROUP)]):
            for doctype in (self.api.push.STOCK, self.api.push.COUNT):
                self.assertEqual(self.api._party_blocked(doctype, self.row(), self.job("STOCK_ACC_GROUP_UNFIT")), [])

    def test_an_operator_sent_job_does_not_offer_write_again_once_answered(self):
        """代录交的那件：答过不再出题，但单据页不给「再写一次」（`retry` 拒：代录的单由代录再录），由代录再录。"""
        self.answers = {("stock_group", "BOOK-1"): ("DM", AFTER)}
        job = {**self.job("STOCK_ACC_GROUP_UNFIT"), "request_key": self.api.push.OPERATOR_KEY + "b1:PUR-1@x"}
        with mock.patch.object(self.prepare, "group_needed", lambda *args: [dict(GROUP)]), \
                mock.patch.object(self.api.jobs, "find_by_name", lambda name: job), \
                mock.patch.object(self.api, "_pos_stuck", lambda *args: False), \
                mock.patch.object(self.api.waiting, "of_job", lambda *args: ""):
            view = self.api._view("Purchase Document", self.row())
        self.assertEqual((view["needed"], view["can_answer"], view["can_retry"]), ([], False, False))


class BlockedCardItem(BlockedQuestion):
    test_a_question_answered_after_the_stop_is_not_asked_again_and_the_document_can_be_written_again = None
    test_not_answered_yet_still_asks_with_no_value_filled_in = None
    test_an_answer_older_than_the_stop_still_asks = None
    test_an_answer_that_is_no_longer_a_choice_still_asks = None
    test_other_stops_and_a_job_without_a_time_are_unchanged = None
    test_stock_documents_ask_the_book_group_the_same_way = None
    test_an_operator_sent_job_does_not_offer_write_again_once_answered = None

    def test_the_blocked_question_on_the_card_has_no_value_filled_in_but_keeps_the_recommendation_first(self):
        """被拦的题不带当前值：带了，对话里的写入计划会照预选放行，可这张单要人点一次记下才会再写（实验室流程查出）。"""
        sys.modules.pop("pearnly_erp_portal.express.precheck", None)
        self.addCleanup(sys.modules.pop, "pearnly_erp_portal.express.precheck", None)
        from pearnly_erp_portal.express import precheck
        api = self.api
        proposed = {"kind": "stock_group", "subject": "BOOK-1", "label": "9.Pearnly", "value": "DM", "source": "history",
                    "value_label": "วัตถุดิบ", "options": [{"code": "DM", "note": "most used"}, {"code": "P002"}]}
        with mock.patch.object(api, "blocked_needed", lambda doctype, name: [dict(GROUP)]), \
                mock.patch.object(precheck, "_item", lambda *args: dict(proposed)), \
                mock.patch.object(precheck.push, "document", lambda doctype, name: {}), \
                mock.patch.object(precheck.push, "_company", lambda name: {"name": name}):
            found = precheck._with_blocked("Purchase Document", "PUR-1", "CO-1", {"result": {"payload": {}}, "items": [],
                                                                                 "data": {}})
        item, = found["items"]
        self.assertEqual((item["value"], item["source"], "value_label" in item), ("", "", False))
        self.assertEqual([option["code"] for option in item["options"]], ["DM", "P002"])
        self.assertEqual(item["options"][0]["note"], "most used")


class Answered(unittest.TestCase):
    def test_answered_reads_the_stored_value_and_its_time_not_the_assumed_one(self):
        from pearnly_erp_portal.express import choices
        stored = {"value": "DM", "modified": AFTER}
        with mock.patch.object(choices.frappe.db, "get_value", lambda *args, **kwargs: dict(stored), create=True), \
                choices.assuming("CO-1", {"stock_group": {"BOOK-1": "P002"}}):
            self.assertEqual(choices.answered("CO-1", "stock_group", "BOOK-1"), ("DM", AFTER))
        with mock.patch.object(choices.frappe.db, "get_value", lambda *args, **kwargs: None, create=True):
            self.assertEqual(choices.answered("CO-1", "stock_group", "BOOK-1"), ("", None))


if __name__ == "__main__":
    unittest.main()
