"""CC 只写对话选中的公司与账套（用户 11-10；线上压测 S7：选中公司 A，CC 按票上卖方税号说要录进卖方的真账套 69SINCER）。

服务端在每个 CC 写入点核「这份账套是本对话那家公司的」（`assistant.write_scope`），不是就拒、原因照实交回模型；本公司的账套
（两本同期、大小写 / 斜杠不同）照常放行；不在 CC 对话里执行的（网页待处理由人点的）不限。
"""
import importlib.util
import json
import sys
import types
import unittest
from unittest import mock

from test_cowork_assistant_tools import COWORK, frappe, tools

A_TEST = "\\\\accserver\\d$\\account\\69exp\\test"
A_TEST2 = "\\\\accserver\\d$\\account\\69exp\\test2"
B_REAL = "\\\\192.168.0.212\\pas212\\2569\\exp69\\69sincer"  # 别家（卖方 SINCERE）的真账套
SETS = {"CO-A": [{"connection": "EXP1", "key": A_TEST, "label": "9.บริษัท Pearnly จำกัด"},
                 {"connection": "EXP1", "key": A_TEST2, "label": "9.บริษัท Pearnly จำกัด TEST2"}],
        "CO-B": [{"connection": "EXP1", "key": B_REAL, "label": "69SINCER SINCERE ICE 2569"}]}


def _load_scope():
    spec = importlib.util.spec_from_file_location("pearnly_cowork.assistant.write_scope", COWORK / "assistant/write_scope.py")
    module = importlib.util.module_from_spec(spec)
    projects = types.ModuleType("pearnly_core.assistant.projects")
    projects.of_session = lambda session: ("", "", "")
    with mock.patch.dict(sys.modules, {"pearnly_core.assistant.projects": projects}):
        sys.modules.setdefault("pearnly_core.assistant", types.ModuleType("pearnly_core.assistant"))
        setattr(sys.modules["pearnly_core.assistant"], "projects", projects)
        spec.loader.exec_module(module)
    module._company_sets = lambda company, work_scope="": [dict(item) for item in SETS.get(company, [])]
    return module


write_scope = _load_scope()


class _Conversation(unittest.TestCase):
    """本对话挂在 ``filed``（空串 = 没挂公司）；``None`` = 不在 CC 对话里执行。"""

    filed = "CO-A"

    def setUp(self):
        patches = [mock.patch.dict(sys.modules, {"pearnly_cowork.assistant.write_scope": write_scope}),
                   mock.patch.object(sys.modules["pearnly_cowork.assistant"], "write_scope", write_scope, create=True),
                   mock.patch.object(write_scope.projects, "of_session",
                                     lambda session: (self.filed or "", "2026-10", ""))]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        frappe.local.pearnly_assistant = {"session_id": "sesn_1"} if self.filed is not None else None
        self.addCleanup(setattr, frappe.local, "pearnly_assistant", None)
        self.submitted, self.failures = [], []
        self.doc = types.SimpleNamespace(name="H1", last_push_status="", push_connection="EXP1", push_account_set=B_REAL,
                                         posting_kind="service", record_version=1, last_push_record="",
                                         last_push_reason="")
        for target, value in ((tools.storage, "read"), (tools.push_state, "record_failure"),
                              (tools.application, "submit"), (tools.application, "choices_view")):
            if hasattr(target, value):
                original = getattr(target, value)
                self.addCleanup(setattr, target, value, original)
        tools.storage.read = lambda name: self.doc
        tools.push_state.record_failure = lambda *a, **k: self.failures.append((a, k))
        tools.application.choices_view = lambda name, target, account_set, kind: {"needed": [], "account_set": account_set}
        tools.application.submit = lambda *a, **k: self.submitted.append(a) or {"status": "failed", "reason": "stop here"}
        frappe.db.get_value = lambda *a, **k: ("express", 3)


class DirectPushTests(_Conversation):
    def test_a_document_aimed_at_another_companys_book_is_refused_and_nothing_is_stamped(self):
        # S7 原样：票上卖方税号属于别家，单据记的去向是卖方的真账套
        out = tools._push_listed("H1", None, None, None)
        self.assertEqual((out["status"], out["reason"]), ("failed", write_scope.NOT_THIS_COMPANY))
        self.assertEqual(self.submitted, [])
        self.assertEqual(self.failures, [])  # 单据上不留别家去向（否则待处理页把它当这张单的去向）
        self.assertEqual({item["key"] for item in out["allowed_account_sets"]}, {A_TEST, A_TEST2})
        self.assertNotIn(B_REAL, json.dumps(out["allowed_account_sets"]))

    def test_the_model_naming_another_companys_book_is_refused(self):
        self.doc.push_account_set = ""
        out = json.loads(tools.push_documents({"documents": ["H1"], "connection": "EXP1", "account_set_key": B_REAL}))
        self.assertEqual(out["results"][0]["reason"], write_scope.NOT_THIS_COMPANY)
        self.assertEqual(self.submitted, [])

    def test_a_book_resolved_by_the_push_service_is_checked_too(self):
        # 识别时没带账套（target none），推送服务按单据日期 / 税号解析出别家那份
        self.doc.push_account_set = ""
        tools.application.choices_view = lambda name, target, account_set, kind: {"needed": [], "account_set": B_REAL}
        out = tools._push_listed("H1", None, None, None)
        self.assertEqual(out["reason"], write_scope.NOT_THIS_COMPANY)
        self.assertEqual(self.submitted, [])

    def test_both_books_of_this_company_are_allowed_whatever_the_spelling(self):
        for key in (A_TEST.upper(), A_TEST2.replace("\\", "/") + "/"):
            self.submitted.clear()
            self.doc.push_account_set = key
            out = tools._push_listed("H1", None, None, None)
            self.assertNotEqual(out.get("reason"), write_scope.NOT_THIS_COMPANY)
            self.assertEqual(len(self.submitted), 1)

    def test_a_choice_needed_stop_does_not_stamp_another_companys_book(self):
        tools.application.choices_view = lambda name, target, account_set, kind: {
            "needed": [{"kind": "account", "subject": "S"}], "account_set": account_set}
        out = tools._push_listed("H1", None, None, None)
        self.assertEqual(out["reason"], write_scope.NOT_THIS_COMPANY)
        self.assertEqual(self.failures, [])


class NoCompanyTests(_Conversation):
    filed = ""

    def test_a_conversation_without_a_company_writes_nothing(self):
        self.doc.push_account_set = A_TEST
        out = tools._push_listed("H1", None, None, None)
        self.assertEqual(out["reason"], write_scope.NO_COMPANY)
        self.assertEqual(self.submitted, [])


class OutsideConversationTests(_Conversation):
    filed = None

    def test_a_push_a_person_clicks_on_a_page_is_not_limited(self):
        out = tools._push_one("H1")
        self.assertNotEqual(out.get("reason"), write_scope.NOT_THIS_COMPANY)
        self.assertEqual(len(self.submitted), 1)


class OperatorTests(_Conversation):
    filed = None  # 代录交单不在对话线程里：公司由任务明传

    def test_an_operator_document_aimed_at_another_companys_book_is_not_written(self):
        out = tools._push_one("H1", operator={"company": "CO-A", "index": 1, "doc": "H1"})
        self.assertEqual((out["status"], out["reason"]), ("failed", write_scope.NOT_THIS_COMPANY))
        self.assertEqual(self.submitted, [])
        self.doc.push_account_set = A_TEST
        tools._push_one("H1", operator={"company": "CO-A", "index": 1, "doc": "H1"})
        self.assertEqual(len(self.submitted), 1)


class StoppedAccountSetTests(_Conversation):
    """停在「待选账套」：候选按单据归属（票上税号）算，可能是别家的。公司对话里只给本公司的，传别家的键拒。"""

    def setUp(self):
        super().setUp()
        self.chosen = []
        self.options = {"account_sets": [{"key": B_REAL, "label": "69SINCER"}, {"key": A_TEST, "label": "9.Pearnly"}],
                        "current": ""}
        pending = types.SimpleNamespace(
            account_set_stop=lambda row: True, account_set_options=lambda row: dict(self.options),
            current_book_options=lambda row: list(self.options["account_sets"]))
        pending_api = types.SimpleNamespace(
            push_to_account_set=lambda name, key, posting_kind=None: self.chosen.append(key) or (
                {"document": name, "status": "sent", "current_book": key}, {"document": name, "status": "sent"}),
            rechoose_current_book=lambda doc, key: self.chosen.append(key) or {"current_book": key})
        self.doc = {"name": "H1", "push_connection": "EXP1", "push_account_set": B_REAL, "last_push_status": "",
                    "last_push_reason": "cowork.folder.account_set_ambiguous", "invoice_date": "2026-09-08",
                    "pages": [{"fields": {}}], "record_version": 1}
        tools.storage.read = lambda name: self.doc
        patches = [mock.patch.dict(sys.modules, {"pearnly_cowork.companies": types.SimpleNamespace(pending=pending),
                                                 "pearnly_cowork.companies.pending": pending,
                                                 "pearnly_cowork.assistant.pending_api": pending_api}),
                   mock.patch.object(sys.modules["pearnly_cowork.assistant"], "pending_api", pending_api, create=True),
                   mock.patch.object(tools, "_stopped", lambda doc: True),
                   mock.patch.object(tools.storage, "project", lambda doc: doc, create=True)]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_only_this_companys_books_are_offered(self):
        stop = tools._stop_target(self.doc)
        self.assertEqual([item["key"] for item in stop["account_set_options"]], [A_TEST])

    def test_a_remembered_book_of_another_company_is_not_used(self):
        self.options["current"] = B_REAL
        stop = tools._stop_target(self.doc)
        self.assertNotIn("account_set_remembered", stop)
        self.assertEqual([item["key"] for item in stop["account_set_options"]], [A_TEST])

    def test_pushing_with_another_companys_key_is_refused(self):
        out = tools._push_listed("H1", None, B_REAL, None)
        self.assertEqual(out["reason"], write_scope.NOT_THIS_COMPANY)
        self.assertEqual(self.chosen, [])
        tools._push_listed("H1", None, A_TEST, None)
        self.assertEqual(self.chosen, [A_TEST])

    def test_choosing_another_companys_book_as_current_is_refused(self):
        out = json.loads(tools._choose_current_book("H1", B_REAL))
        self.assertEqual((out["status"], out["reason"]), ("not_saved", write_scope.NOT_THIS_COMPANY))
        self.assertEqual(self.chosen, [])


class RecognizeAndOptionsTests(_Conversation):
    def setUp(self):
        super().setUp()
        search = types.ModuleType("pearnly_cowork.connections.account_search")
        search.usable = lambda adapter, rows: rows
        search.label = lambda adapter, row: row["label"]
        search.search = lambda adapter, rows, query: []
        patches = [mock.patch.dict(sys.modules, {"pearnly_cowork.connections.account_search": search}),
                   mock.patch.object(tools.connections, "targets", lambda name: {"account_sets": [
                       {"source_id": B_REAL, "label": "69SINCER SINCERE ICE 2569"},
                       {"source_id": A_TEST, "label": "9.บริษัท Pearnly จำกัด"},
                       {"source_id": A_TEST2, "label": "9.บริษัท Pearnly จำกัด TEST2"}]}),
                   mock.patch.object(tools.connections, "records", lambda *a: [
                       {"name": "EXP1", "adapter": "express", "enabled": True, "label": "Office", "main": True}]),
                   mock.patch.object(tools, "_admit", lambda file_id, choices: self.fail("must not recognize"))]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_recognizing_into_another_companys_book_does_not_start(self):
        out = json.loads(tools.recognize_attachments({"files": ["file_abc"], "target": "express", "connection": "EXP1",
                                                      "account_set_key": B_REAL}))["results"][0]
        self.assertEqual(out["status"], "not_started")
        self.assertIn(write_scope.NOT_THIS_COMPANY, out["reason"])

    def test_the_last_used_book_of_another_company_is_not_suggested_or_listed(self):
        with mock.patch.object(tools, "_last_used", lambda: {"target": "express", "connection": "EXP1",
                                                              "account_set_key": B_REAL, "direction": "sales"}):
            out = json.loads(tools.intake_options({}))
        self.assertIsNone(out["suggested"])
        express = out["targets"][0]
        self.assertEqual({item["key"] for item in express["account_sets"]}, {A_TEST, A_TEST2})
        self.assertEqual(express["account_sets_total"], 2)

    def test_pos_import_into_another_companys_book_is_refused(self):
        out = json.loads(tools.pos_import({"files": ["file_pos"], "connection": "EXP1", "account_set_key": B_REAL}))
        self.assertIn(write_scope.NOT_THIS_COMPANY, out["results"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
