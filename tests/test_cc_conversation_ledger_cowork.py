"""Cowork 的 CC 只写本对话账套（A 线 11-12）：R2-5 线上事故回放与写入闸、识别、推荐、代录、定时任务。

R2-5（生产只读查清）：公司 Cowork 有 T.7(2024)、T.8(2025，次年段到 2026-12-31)、T.9(2025 同期)；对话 2026-10、右栏账套 T.8；
票据日期 2026-06。模型没走「待选账套」，按公司资料 / 录入选项里的账套与期间自己判，单据目标一直是 not chosen yet，最后在
cowork_push_documents 里自己填了一本。修法：识别时目标落到本对话账套；推送不收模型另填的（与本对话账套不同就拒，回包只列它）；
没填或填 T.8 照本对话账套写。同公司另一本的票执行时改投本对话账套，别家公司的不改投。定时任务保存仍按公司判。

替身同 test_cc_write_scope（写入闸真模块、工具真模块、推送服务替身）；本对话账套由 ``self.ledger`` 给（共用层另有测试）。
"""
import json
import sys
import types
import unittest
from unittest import mock

import test_cc_write_scope as base

frappe, tools, write_scope = base.frappe, base.tools, base.write_scope
T7 = "c:\\pearnlyexpress\\70exp\\t7sincer"
T8 = "c:\\pearnlyexpress\\70exp\\t8sincer"
T9 = "c:\\pearnlyexpress\\70exp\\t9sincer"
OTHER = base.B_REAL  # 别家公司的真账套
BOOKS = [{"connection": "EXP1", "key": T7, "label": "T.7", "period": "2024"},
         {"connection": "EXP1", "key": T8, "label": "T.8", "period": "2025"},
         {"connection": "EXP1", "key": T9, "label": "T.9", "period": "2025"}]
OPTIONS = [{"id": "EXP1|" + T8, "label": "T.8", "covers": True}, {"id": "EXP1|" + T9, "label": "T.9", "covers": True},
           {"id": "EXP1|" + T7, "label": "T.7", "covers": False}]
CHOSEN_T8 = {"status": "set", "id": "EXP1|" + T8, "label": "T.8", "options": OPTIONS}
UNDECIDED = {"status": "needed", "id": "", "label": "", "options": OPTIONS}


class _Ledger(base._Conversation):
    filed = "CO-T"

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(base.SETS, {"CO-T": [dict(item) for item in BOOKS]})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ledger = dict(CHOSEN_T8)
        # R2-5 的票：没有目标（识别时模型没给）、票据日期 2026-06
        vars(self.doc).update(push_connection="", push_account_set="", invoice_date="2026-06-15")
        allowed = mock.patch.object(tools.direct_push, "permitted", lambda names: (list(names), []))
        allowed.start()
        self.addCleanup(allowed.stop)

    def _pushed(self, **tool_input):
        return json.loads(tools.push_documents({"documents": ["H1"], **tool_input}))["results"][0]


class R2_5ReplayTests(_Ledger):
    def test_recognition_without_a_target_lands_on_the_conversation_account_set(self):
        admitted = []
        with mock.patch.object(tools, "_admit", lambda file_id, choices: admitted.append(dict(choices)) or {
                "file": file_id, "status": "saved"}), \
                mock.patch.object(tools, "_adapter_of", lambda connection: "express" if connection == "EXP1" else ""):
            out = json.loads(tools.recognize_attachments({"files": ["file_abc"]}))
            tools.recognize_attachments({"files": ["file_def"], "target": "none"})
        self.assertEqual(out["results"][0]["status"], "saved")
        self.assertEqual([(c["target"], c["connection"], c["account_set_key"]) for c in admitted],
                         [("express", "EXP1", T8), ("express", "EXP1", T8)])

    def test_the_model_filling_in_t9_is_refused_and_only_t8_is_offered(self):
        out = self._pushed(connection="EXP1", account_set_key=T9)
        self.assertEqual((out["status"], out["reason"]), ("failed", write_scope.NOT_THIS_LEDGER))
        self.assertEqual(out["allowed_account_sets"], [{"connection": "EXP1", "key": T8, "label": "T.8"}])
        self.assertNotIn("Two of them covering the same date", out["note"])
        self.assertEqual(self.submitted, [])
        self.assertEqual(self.failures, [])  # 票上不留模型填的那本

    def test_t8_or_nothing_writes_into_t8(self):
        for given in ({"account_set_key": T8}, {"connection": "EXP1", "account_set_key": T8.upper()}, {}):
            self.submitted.clear()
            out = self._pushed(**given)
            self.assertNotIn(out.get("reason"), (write_scope.NOT_THIS_LEDGER, write_scope.NOT_THIS_COMPANY))
            self.assertEqual(len(self.submitted), 1, given)
            name, connection, _key, _version, _revision, account_set = self.submitted[0][:6]
            self.assertEqual((connection, account_set), ("EXP1", T8), given)

    def test_a_document_aimed_at_another_book_of_this_company_goes_to_t8_when_pushed(self):
        vars(self.doc).update(push_connection="EXP1", push_account_set=T9)  # 网页 / LINE 选过 T.9
        out = self._pushed()
        self.assertNotIn(out.get("reason"), (write_scope.NOT_THIS_LEDGER, write_scope.NOT_THIS_COMPANY))
        self.assertEqual(self.submitted[0][5], T8)

    def test_a_document_aimed_at_another_companys_book_is_not_retargeted(self):
        vars(self.doc).update(push_connection="EXP1", push_account_set=OTHER)
        out = self._pushed()
        self.assertEqual(out["reason"], write_scope.NOT_THIS_COMPANY)
        self.assertEqual(out["allowed_account_sets"], [{"connection": "EXP1", "key": T8, "label": "T.8"}])
        self.assertEqual(self.submitted, [])

    def test_another_connection_is_refused_too(self):
        out = self._pushed(connection="MR1")
        self.assertIn(out["reason"], (write_scope.NOT_THIS_LEDGER, write_scope.NOT_THIS_COMPANY))
        self.assertEqual(self.submitted, [])


class UndecidedTests(_Ledger):
    def setUp(self):
        super().setUp()
        self.ledger = dict(UNDECIDED)

    def test_writing_before_the_account_set_is_decided_asks_once(self):
        for given in ({}, {"account_set_key": T8}):
            out = self._pushed(**given)
            self.assertEqual((out["status"], out["reason"]), ("failed", write_scope.LEDGER_NEEDED))
            self.assertEqual([item["label"] for item in out["allowed_account_sets"]], ["T.8", "T.9", "T.7"])
            self.assertEqual(out["allowed_account_sets"][0]["id"], "EXP1|" + T8)
            self.assertIn("pearnly_conversation_ledger", out["note"])
        self.assertEqual(self.submitted, [])

    def test_recognition_is_not_blocked_and_gets_no_target(self):
        admitted = []
        with mock.patch.object(tools, "_admit", lambda file_id, choices: admitted.append(dict(choices)) or {
                "file": file_id, "status": "saved"}):
            tools.recognize_attachments({"files": ["file_abc"]})
        self.assertEqual(admitted[0]["account_set_key"], None)


class OperatorTests(_Ledger):
    filed = None  # 代录交单不在对话线程里：公司与任务由批次信息明传

    def test_an_operator_task_writes_into_its_conversations_account_set(self):
        self.task_ledgers["TK1"] = dict(CHOSEN_T8)
        vars(self.doc).update(push_connection="EXP1", push_account_set=T9)
        tools._push_one("H1", operator={"company": "CO-T", "task": "TK1", "index": 1, "doc": "H1"})
        self.assertEqual(self.submitted[0][5], T8)
        self.submitted.clear()
        self.task_ledgers["TK1"] = dict(UNDECIDED)
        out = tools._push_one("H1", operator={"company": "CO-T", "task": "TK1", "index": 1, "doc": "H1"})
        self.assertEqual(out["reason"], write_scope.LEDGER_NEEDED)
        self.assertEqual(self.submitted, [])

    def test_an_old_batch_without_a_task_is_checked_by_company_as_before(self):
        vars(self.doc).update(push_connection="EXP1", push_account_set=T9)
        tools._push_one("H1", operator={"company": "CO-T", "index": 1, "doc": "H1"})
        self.assertEqual(self.submitted[0][5], T9)


class CompanyLevelTests(_Ledger):
    def test_a_folder_task_for_another_book_of_this_company_is_still_saved(self):
        # 定时任务以后自己按日期选当年账套写（跟随公司），保存时仍按公司判，不因本对话账套是 T.8 被拒
        task = {"title": "T", "connection": "EXP1", "account_set_key": T9, "direction": "purchase",
                "reader": "PC|EXP1", "root_id": "r1", "folder": "x", "run_day": "1", "run_time": "08:00"}
        self.assertIsNone(json.loads(tools.folder_task_refusal(dict(task)) or "null"))
        refused = json.loads(tools.folder_task_refusal({**task, "account_set_key": OTHER}))
        self.assertEqual(refused["reason"], write_scope.NOT_THIS_COMPANY)

    def test_outside_a_conversation_nothing_is_pinned(self):
        frappe.local.pearnly_assistant = None
        self.assertIsNone(write_scope.pinned())
        self.assertIsNone(write_scope.check("EXP1", T9))


class OptionsTests(_Ledger):
    """录入选项、待选账套在对话里只给本对话账套并明说它是写入目标（R2-5 的模型按录入选项里的账套自己判）。"""

    def setUp(self):
        super().setUp()
        search = types.ModuleType("pearnly_cowork.connections.account_search")
        search.usable = lambda adapter, rows: rows
        search.label = lambda adapter, row: row["label"]
        search.search = lambda adapter, rows, query: []
        patches = [mock.patch.dict(sys.modules, {"pearnly_cowork.connections.account_search": search}),
                   mock.patch.object(tools.connections, "targets", lambda name: {"account_sets": [
                       {"source_id": T7, "label": "T.7"}, {"source_id": T8, "label": "T.8"},
                       {"source_id": T9, "label": "T.9"}, {"source_id": OTHER, "label": "69SINCER"}]}),
                   mock.patch.object(tools.connections, "records", lambda *a: [
                       {"name": "EXP1", "adapter": "express", "enabled": True, "label": "Office", "main": True}]),
                   mock.patch.object(tools, "_adapter_of", lambda connection: "express"),
                   mock.patch.object(tools, "_last_used", lambda: {"target": "express", "connection": "EXP1",
                                                                    "account_set_key": T9, "direction": "purchase"})]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_intake_options_suggest_only_the_conversation_account_set(self):
        out = json.loads(tools.intake_options({}))
        self.assertEqual((out["suggested"]["account_set_key"], out["suggested"]["conversation_account_set"]), (T8, True))
        self.assertEqual([item["key"] for item in out["targets"][0]["account_sets"]], [T8])
        self.assertIn("without asking", out["note"])
        # 未定：照公司列出（给提问卡用），推荐上次用的
        self.ledger = dict(UNDECIDED)
        out = json.loads(tools.intake_options({}))
        self.assertEqual({item["key"] for item in out["targets"][0]["account_sets"]}, {T7, T8, T9})
        self.assertNotIn("conversation_account_set", out["suggested"] or {})

    def test_a_stopped_document_offers_only_the_conversation_account_set(self):
        pending = types.SimpleNamespace(
            account_set_stop=lambda row: True,
            account_set_options=lambda row: {"account_sets": [{"key": T8, "label": "T.8"}, {"key": T9, "label": "T.9"}],
                                             "current": T9})
        doc = {"name": "H1", "push_connection": "EXP1", "push_account_set": T9, "invoice_date": "2026-06-15",
               "last_push_reason": "cowork.folder.account_set_ambiguous"}
        companies = types.SimpleNamespace(pending=pending, identity=sys.modules["pearnly_cowork.companies.identity"],
                                          service=sys.modules["pearnly_cowork.companies.service"],
                                          provider=sys.modules["pearnly_cowork.companies.provider"])
        with mock.patch.dict(sys.modules, {"pearnly_cowork.companies": companies,
                                           "pearnly_cowork.companies.pending": pending}), \
                mock.patch.object(tools, "_stopped", lambda doc: True):
            stop = tools._stop_target(doc)
        self.assertEqual((stop["push_account_set"], stop["conversation_account_set"]), (T8, True))
        self.assertEqual(stop["account_set_options"], [{"key": T8, "label": "T.8"}])
        self.assertNotIn("account_set_remembered", stop)


class AskedForAnotherBookTests(_Ledger):
    """测试机1 11-12（四条交单路按类查全，Cowork 版）：票上记着的「要人选」（choice_needed:…）是推往另一本时问的，而这段对话的
    账套已定（T.8）：那一问对这段对话不算数（推的时候按 T.8 重新判），详情、列单都不拿它给模型看；推往 T.8 时问的照实给话。"""

    def _detail(self, account_set):
        doc = {"name": "H1", "push_connection": "EXP1", "push_account_set": account_set, "last_push_status": "manual",
               "last_push_reason": "choice_needed:account", "invoice_date": "2026-06-15", "pages": []}
        with mock.patch.object(tools, "_stop_target", lambda doc, cache=None: None), \
                mock.patch.object(tools, "_current_book_options", lambda doc: []), \
                mock.patch.object(tools, "_automation_of", lambda name: None), \
                mock.patch.object(tools, "_written_of", lambda *a: None), \
                mock.patch.object(tools.document_checks, "checks", lambda doc, fields: None, create=True), \
                mock.patch.object(tools.document_checks, "recent", lambda name: [], create=True), \
                mock.patch.object(tools, "_account_named", lambda out, *a, **k: out):
            return tools._detail(doc)

    def test_a_question_asked_for_another_book_is_not_shown_in_the_conversation(self):
        elsewhere = self._detail(T9)
        self.assertNotIn("push_reason", elsewhere)
        self.assertNotIn("push_status", elsewhere)
        here = self._detail(T8)
        self.assertEqual((here["push_status"], here["push_reason"]), ("manual", tools._reason("choice_needed:account")))
        self.assertTrue(tools.asked_for_another_book({"push_connection": "EXP1", "push_account_set": T9,
                                                     "last_push_reason": "choice_needed:account"}))
        self.assertFalse(tools.asked_for_another_book({"push_connection": "EXP1", "push_account_set": T9,
                                                      "last_push_reason": "cowork.folder.account_set_ambiguous"}))
        self.ledger = None  # 不在对话里 / 没有本对话账套：照旧
        self.assertEqual(self._detail(T9)["push_reason"], tools._reason("choice_needed:account"))


class PlanGateTests(_Ledger):
    """写入只走计划下沉到推送入口（A 线 B）：CC 对话里直写（非代录）的每一张都要在本对话已开始的计划里，绕过推送工具的路
    （判定卡 resume、选现用账套当场推）也拦得住；代录交单、网页、定时任务不经这里。"""

    def setUp(self):
        super().setUp()
        self.planned = set()
        refused = lambda name: {"document": name, "status": "not_pushed", "reason": "not_started", "note": "plan"}
        patcher = mock.patch.object(tools.direct_push, "permitted", lambda names: (
            [name for name in names if name in self.planned], [refused(name) for name in names if name not in self.planned]))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_direct_push_in_a_conversation_needs_a_started_plan(self):
        out = tools._push_one("H1")
        self.assertEqual((out["status"], out["reason"]), ("not_pushed", "not_started"))
        self.assertEqual((self.submitted, self.failures), ([], []))  # 票上什么都不留
        self.planned.add("H1")
        tools._push_one("H1")
        self.assertEqual(len(self.submitted), 1)

    def test_operator_hand_in_and_pages_are_not_gated(self):
        self.task_ledgers["TK1"] = dict(CHOSEN_T8)
        tools._push_one("H1", operator={"company": "CO-T", "task": "TK1", "index": 1, "doc": "H1"})
        self.assertEqual(len(self.submitted), 1)
        frappe.local.pearnly_assistant = None  # 网页待处理 / 定时任务（票上有去向）
        vars(self.doc).update(push_connection="EXP1", push_account_set=T8)
        tools._push_one("H1")
        self.assertEqual(len(self.submitted), 2)


class CurrentBookTests(_Ledger):
    """B2：在对话里选「现用账套」只记下选择（同组接着判），不当场推；推送交回计划。网页 / 复核页照旧当场推。"""

    def setUp(self):
        super().setUp()
        self.pushed, self.remembered = [], []
        pending = types.SimpleNamespace(account_set_stop=lambda row: True,
                                        current_book_options=lambda row: [{"key": T8, "label": "T.8"},
                                                                          {"key": T9, "label": "T.9"}])
        pending_api = types.SimpleNamespace(
            push_to_account_set=lambda name, key, posting_kind=None: self.pushed.append(key) or (
                {"document": name, "status": "sent", "current_book": key}, {"document": name, "status": "sent"}),
            remember_current_book=lambda doc, key: self.remembered.append(key) or {"current_book": key, "settled": 0})
        self.doc = {"name": "H1", "push_connection": "EXP1", "push_account_set": T9, "last_push_status": "",
                    "last_push_reason": "cowork.folder.account_set_ambiguous", "invoice_date": "2026-06-15"}
        tools.storage.read = lambda name: self.doc
        companies = types.SimpleNamespace(pending=pending, identity=sys.modules["pearnly_cowork.companies.identity"],
                                          service=sys.modules["pearnly_cowork.companies.service"],
                                          provider=sys.modules["pearnly_cowork.companies.provider"])
        patches = [mock.patch.dict(sys.modules, {"pearnly_cowork.companies": companies,
                                                 "pearnly_cowork.companies.pending": pending,
                                                 "pearnly_cowork.assistant.pending_api": pending_api}),
                   mock.patch.object(sys.modules["pearnly_cowork.assistant"], "pending_api", pending_api, create=True),
                   mock.patch.object(tools.storage, "project", lambda doc: doc, create=True)]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_choosing_the_current_book_in_a_conversation_only_records_it(self):
        out = json.loads(tools._choose_current_book("H1", T8))
        self.assertEqual((out["status"], out["current_book"]), ("saved", T8))
        self.assertEqual((self.remembered, self.pushed), ([T8], []))
        self.assertIn("pearnly_write_plan", out["note"])

    def test_outside_a_conversation_it_still_pushes_on_the_spot(self):
        frappe.local.pearnly_assistant = None
        json.loads(tools._choose_current_book("H1", T8))
        self.assertEqual((self.remembered, self.pushed), ([], [T8]))


if __name__ == "__main__":
    unittest.main()
