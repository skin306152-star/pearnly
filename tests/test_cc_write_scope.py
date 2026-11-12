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
A_OLD = "\\\\accserver\\d$\\account\\68exp\\test"  # 本公司上一年度
B_REAL = "\\\\192.168.0.212\\pas212\\2569\\exp69\\69sincer"  # 别家（卖方 SINCERE）的真账套
SETS = {"CO-A": [{"connection": "EXP1", "key": A_TEST, "label": "9.บริษัท Pearnly จำกัด", "period": "2026"},
                 {"connection": "EXP1", "key": A_TEST2, "label": "9.บริษัท Pearnly จำกัด TEST2", "period": "2026"},
                 {"connection": "EXP1", "key": A_OLD, "label": "8.บริษัท Pearnly จำกัด", "period": "2025"}],
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


def _provider():
    """Cowork 的 provider（只用它的编号拆法 `ledger_parts`，纯函数）：按文件载入，依赖的几个模块给替身。"""
    stubs = {"pearnly_cowork.access": types.ModuleType("pearnly_cowork.access"),
             "pearnly_cowork.access.scope": types.ModuleType("pearnly_cowork.access.scope"),
             "pearnly_cowork.companies.identity": types.ModuleType("pearnly_cowork.companies.identity"),
             "pearnly_cowork.companies.access": types.SimpleNamespace(COMPANY="Cowork Company"),
             "pearnly_cowork.companies": types.ModuleType("pearnly_cowork.companies")}
    stubs["pearnly_cowork.access"].scope = stubs["pearnly_cowork.access.scope"]
    stubs["pearnly_cowork.companies"].identity = stubs["pearnly_cowork.companies.identity"]
    spec = importlib.util.spec_from_file_location("cowork_provider_for_scope", COWORK / "companies/provider.py")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


class _Conversation(unittest.TestCase):
    """本对话挂在 ``filed``（空串 = 没挂公司）；``None`` = 不在 CC 对话里执行。"""

    filed = "CO-A"

    def setUp(self):
        patches = [mock.patch.dict(sys.modules, {"pearnly_cowork.assistant.write_scope": write_scope}),
                   mock.patch.object(sys.modules["pearnly_cowork.assistant"], "write_scope", write_scope, create=True),
                   mock.patch.object(write_scope.projects, "of_session",
                                     lambda session: (self.filed or "", "2026-10", ""))]
        identity = types.SimpleNamespace(covering=lambda sets, period: [
            item for item in sets if str(item.get("period")) == period[:4]])
        service = types.SimpleNamespace(listing=lambda search, start, limit: [
            {"id": "CO-A", "title": "บริษัท Pearnly จำกัด", "tax_id": "0105555000001"},
            {"id": "CO-C", "title": "Gamma", "tax_id": ""}])
        # 本对话账套（A 线 11-12，`pearnly_core.assistant.ledger`）：``self.ledger`` 是本对话的（None = 没有，照公司判），
        # ``self.task_ledgers`` 是各代录任务会话的；编号的拆法用 Cowork provider 的真函数
        self.ledger, self.task_ledgers = None, {}
        ledger = types.ModuleType("pearnly_core.assistant.ledger")
        ledger.SET, ledger.NEEDED = "set", "needed"
        ledger.of_session = lambda session=None: self.ledger
        ledger.of_task = lambda task: self.task_ledgers.get(task)
        provider = types.SimpleNamespace(ledger_parts=_provider().ledger_parts)
        companies = types.SimpleNamespace(identity=identity, service=service, provider=provider)
        patches += [mock.patch.dict(sys.modules, {"pearnly_cowork.companies": companies,
                                                  "pearnly_cowork.companies.identity": identity,
                                                  "pearnly_cowork.companies.service": service,
                                                  "pearnly_cowork.companies.provider": provider,
                                                  "pearnly_core.assistant.ledger": ledger}),
                    mock.patch.object(sys.modules["pearnly_core.assistant"], "ledger", ledger, create=True)]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        frappe.local.pearnly_assistant = {"session_id": "sesn_1"} if self.filed is not None else None
        self.addCleanup(setattr, frappe.local, "pearnly_assistant", None)
        self.submitted, self.failures = [], []
        self.doc = types.SimpleNamespace(name="H1", invoice_date="2026-08-25", last_push_status="", push_connection="EXP1",
                                         push_account_set=B_REAL,
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
        # 写入只走计划（A 线 B，推送入口也问计划）：这里看写入闸本身，用户已同意计划；计划的门见 test_cc_conversation_ledger_cowork
        allowed = mock.patch.object(tools.direct_push, "permitted", lambda names: (list(names), []))
        allowed.start()
        self.addCleanup(allowed.stop)


class DirectPushTests(_Conversation):
    def test_a_document_aimed_at_another_companys_book_is_refused_and_nothing_is_stamped(self):
        # S7 原样：票上卖方税号属于别家，单据记的去向是卖方的真账套
        out = tools._push_listed("H1", None, None, None)
        self.assertEqual((out["status"], out["reason"]), ("failed", write_scope.NOT_THIS_COMPANY))
        self.assertEqual(self.submitted, [])
        self.assertEqual(self.failures, [])  # 单据上不留别家去向（否则待处理页把它当这张单的去向）
        # 可写的只列本公司覆盖单据日期（2026-08）的两本，上一年度的、别家的都不列
        self.assertEqual({item["key"] for item in out["allowed_account_sets"]}, {A_TEST, A_TEST2})
        self.assertNotIn(B_REAL, json.dumps(out["allowed_account_sets"]))
        self.assertIn("neither side of the document is this company", out["note"])

    def test_a_ticket_of_neither_side_goes_into_this_companys_book_once_named(self):
        # 用户 11-10：「买方不是本公司」照实说明、用户选「照样录进」后，写进本对话公司的账套
        out = tools._push_listed("H1", "EXP1", A_TEST, None)
        self.assertNotEqual(out.get("reason"), write_scope.NOT_THIS_COMPANY)
        self.assertEqual(len(self.submitted), 1)

    def test_the_model_naming_another_companys_book_is_refused(self):
        self.doc.push_account_set = ""
        # 用户已同意写入计划（计划的门由共用层 write_plan 的测试锁住）：这里看计划里的单推往别家账套照样拒
        allowed = mock.patch.object(tools.direct_push, "permitted", lambda names: (list(names), []))
        allowed.start()
        self.addCleanup(allowed.stop)
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
        # 提问卡用：本人能用的公司
        self.assertEqual([item["id"] for item in out["companies"]], ["CO-A", "CO-C"])
        self.assertIn("ask_user", out["note"])
        # 答完由 CC 把对话挂到用户选的那家再写（主会话 11-10；服务端只认用户在卡片上选的，见 test_assistant ProjectTests）
        self.assertIn("pearnly_file_conversation", out["note"])

    def test_candidates_are_still_shown_there_since_writing_is_refused_anyway(self):
        options = [{"key": B_REAL}, {"key": A_TEST}]
        self.assertIsNone(write_scope.keep(options, lambda item: "EXP1"))


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
        companies = types.SimpleNamespace(pending=pending, identity=sys.modules["pearnly_cowork.companies.identity"],
                                          service=sys.modules["pearnly_cowork.companies.service"],
                                          provider=sys.modules["pearnly_cowork.companies.provider"])
        patches = [mock.patch.dict(sys.modules, {"pearnly_cowork.companies": companies,
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

    def test_a_ticket_of_neither_side_still_offers_this_companys_books(self):
        # 票按税号归别家，候选全是别家的：给本公司覆盖单据日期的账套，别家的不出现
        self.options["account_sets"] = [{"key": B_REAL, "label": "69SINCER"}]
        stop = tools._stop_target(self.doc)
        self.assertEqual([item["key"] for item in stop["account_set_options"]], [A_TEST, A_TEST2])

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


class FolderTaskTests(_Conversation):
    """文件夹任务以后自己写账：公司对话里存 / 改的任务只能指向本对话公司的账套（用户 11-10 第 2 条）；不出「允许」卡片、不存。
    跑任务时按任务记下的公司在它的账套里挑（`folder_intake.follow.choose`），别家的不会被选到。"""

    def test_a_task_aimed_at_another_companys_book_is_not_saved(self):
        saved = []
        task = {"title": "SINCERE", "connection": "EXP1", "account_set_key": B_REAL, "direction": "sales",
                "reader": "PC|EXP1", "root_id": "r1", "folder": "x", "run_day": "1", "run_time": "08:00"}
        with mock.patch.object(tools.rules, "save", lambda *a, **k: saved.append(a) or {"name": "T1"}, create=True), \
                mock.patch.object(tools.rules, "save_many", lambda *a, **k: saved.append(a) or {"saved": []}, create=True):
            refused = json.loads(tools.folder_task_refusal(dict(task)))
            self.assertEqual((refused["status"], refused["reason"]), ("not_saved", write_scope.NOT_THIS_COMPANY))
            self.assertEqual(json.loads(tools.save_folder_task(dict(task)))["reason"], write_scope.NOT_THIS_COMPANY)
            many = json.loads(tools.save_folder_tasks({"tasks": [dict(task)]}))
            self.assertEqual(many["failed"][0]["reason"], write_scope.NOT_THIS_COMPANY)
            self.assertEqual(saved, [])
            self.assertIsNone(json.loads(tools.folder_task_refusal({**task, "account_set_key": A_TEST}) or "null"))


if __name__ == "__main__":
    unittest.main()
