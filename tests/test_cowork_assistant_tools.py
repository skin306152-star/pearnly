"""Cowork 智能体工具（ADR-019）的离线检查：声明完整、步骤名有译文、推送请求号确定、筛选口径、逐张提交。

不连库：Frappe 与 Cowork 各域服务用最小替身，只看工具层自己的逻辑。
"""
import importlib.util
import json
import pathlib
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
COWORK = ROOT / "apps/pearnly_cowork/pearnly_cowork"


class _Frappe(types.ModuleType):
    class ValidationError(Exception):
        pass

    class PermissionError(Exception):
        pass

    class DoesNotExistError(Exception):
        pass

    class AuthenticationError(Exception):
        pass

    def __init__(self):
        super().__init__("frappe")
        self.calls, self.commits = [], 0
        self.session = types.SimpleNamespace(user="a@example.com")
        self.db = types.SimpleNamespace(commit=self._commit, rollback=lambda: None,
                                        get_value=lambda *a, **k: ("express", 3))

    def _commit(self):
        self.commits += 1

    def get_list(self, doctype, **kwargs):
        self.calls.append(kwargs)
        return []

    def clear_messages(self):
        pass

    def throw(self, message, exc=None):
        raise (exc or self.ValidationError)(message)

    @staticmethod
    def _(text):
        return text


def _install():
    frappe = _Frappe()
    sys.modules["frappe"] = frappe
    names = ["pearnly_cowork", "pearnly_cowork.access", "pearnly_cowork.connections", "pearnly_cowork.folder_intake",
             "pearnly_cowork.history", "pearnly_cowork.push", "pearnly_cowork.push.application"]
    for name in names:
        sys.modules[name] = types.ModuleType(name)
    leaf = {
        "pearnly_cowork.access.scope": dict(require_read=lambda: None, require=lambda feature: None),
        "pearnly_cowork.connections.api": dict(request_refresh=lambda *a: {}),
        "pearnly_cowork.connections.service": dict(records=lambda *a: [], targets=lambda name: {}),
        "pearnly_cowork.folder_intake.destinations": dict(online=lambda *a: True, of=lambda adapter: None),
        "pearnly_cowork.folder_intake.rules": dict(listing=lambda: {}, run_now=lambda n: {}, runs=lambda n: {}),
        "pearnly_cowork.history.push_state": dict(record_failure=lambda *a: None),
        "pearnly_cowork.history.storage": dict(read=lambda name: None),
        "pearnly_cowork.push.application.service": dict(),
        "pearnly_cowork.push.application.api": dict(),
    }
    for name, attrs in leaf.items():
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        sys.modules[name] = module
        parent, _, child = name.rpartition(".")
        setattr(sys.modules[parent], child, module)
    spec = importlib.util.spec_from_file_location("cowork_assistant_tools", COWORK / "assistant/tools.py")
    tools = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tools)
    return frappe, tools


frappe, tools = _install()


class DeclarationTests(unittest.TestCase):
    def test_every_tool_is_complete_and_translated(self):
        names = [t["name"] for t in tools.TOOLS]
        self.assertEqual(len(names), len(set(names)))
        terms = {line.split("\t", 1)[0] for line in (COWORK / "i18n/messages.tsv").read_text(encoding="utf-8").splitlines()}
        for tool in tools.TOOLS:
            self.assertTrue(tool["name"].startswith("cowork_"), tool["name"])
            self.assertTrue(tool["description"] and tool["input_schema"]["type"] == "object")
            module, _, attr = tool["handler"].rpartition(".")
            self.assertEqual(module, "pearnly_cowork.assistant.tools")
            self.assertTrue(callable(getattr(tools, attr)), attr)
            self.assertIn(tool["label"], terms, tool["name"])
        hooks = (COWORK / "hooks.py").read_text(encoding="utf-8")
        self.assertIn('pearnly_assistant_tools = ["pearnly_cowork.assistant.tools.TOOLS"]', hooks)

    def test_the_four_capabilities_are_covered(self):
        names = {t["name"] for t in tools.TOOLS}
        for name in ("cowork_companion_status", "cowork_push_documents", "cowork_folder_tasks", "cowork_push_results"):
            self.assertIn(name, names)

    def test_tools_only_use_public_services(self):
        source = (COWORK / "assistant/tools.py").read_text(encoding="utf-8")
        for forbidden in ("frappe.db.sql", "frappe.db.set_value", ".insert(", ".save(", "ignore_permissions"):
            self.assertNotIn(forbidden, source)


class BehaviourTests(unittest.TestCase):
    def setUp(self):
        frappe.calls.clear()

    def test_push_request_key_is_stable_per_version_and_changes_after_a_failure(self):
        first = tools._key("H-1", 3, "")
        self.assertEqual(first, tools._key("H-1", 3, ""))
        self.assertNotEqual(first, tools._key("H-1", 4, ""))
        self.assertNotEqual(first, tools._key("H-1", 3, "choice_needed:branch"))
        self.assertTrue(first.startswith("assistant:"))

    def test_document_filters(self):
        tools.list_documents({"push_state": "not_pushed", "uploaded_from": "2026-10-10", "limit": 500})
        call = frappe.calls[-1]
        self.assertEqual(call["filters"]["last_push_status"], ["is", "not set"])
        self.assertEqual(call["filters"]["creation"], [">=", "2026-10-10"])
        self.assertEqual(call["limit_page_length"], tools.LIST_MAX)
        tools.list_documents({"push_state": "failed"})
        self.assertEqual(frappe.calls[-1]["or_filters"],
                         [["last_push_status", "=", "manual"], ["last_push_reason", "is", "set"]])

    def test_documents_can_be_found_by_tax_id_or_party(self):
        tools.list_documents({"search": "0107-5650-00620"})
        call = frappe.calls[-1]
        self.assertIn(["seller_tax", "like", "%0107565000620%"], call["or_filters"])
        self.assertIn(["seller_name", "like", "%0107-5650-00620%"], call["or_filters"])
        self.assertIn("seller_tax", call["fields"])
        tools.list_documents({"search": "Advice", "push_state": "failed"})
        self.assertEqual(frappe.calls[-1]["limit_page_length"], tools.LIST_MAX)  # 失败条件改在取回后筛

    def test_companion_status_leaves_account_set_lists_to_intake_options(self):
        tools.connections.records = lambda *a: [{"name": "C1", "label": "Office", "adapter": "express", "enabled": True}]
        tools.connections.targets = lambda name: {"account_sets": [{"source_id": "x", "label": "Acme"}], "status": "fresh"}
        data = json.loads(tools.companion_status({}))["connections"][0]
        self.assertEqual(data["account_sets_status"], "fresh")
        self.assertNotIn("account_sets", data)

    def test_already_pushed_documents_are_not_pushed_again_and_each_is_settled(self):
        docs = {"A": types.SimpleNamespace(last_push_status="success"),
                "B": types.SimpleNamespace(last_push_status="", push_connection="")}
        tools.storage.read = lambda name: docs[name]
        result = json.loads(tools.push_documents({"documents": ["A", "B"]}))["results"]
        self.assertEqual(result[0], {"document": "A", "status": "already_success"})
        self.assertEqual(result[1]["status"], "failed")
        self.assertEqual(result[1]["reason"], "no_push_target")



class PushTargetTests(unittest.TestCase):
    """仅识别的单据也能推：这次说的连接与账套优先，没说就用识别时选的。"""

    def test_the_named_account_set_is_used_for_a_recognize_only_document(self):
        seen = []
        doc = types.SimpleNamespace(last_push_status="", push_connection="", push_account_set="", posting_kind="",
                                    record_version=2, last_push_record="", last_push_reason="")
        tools.storage.read = lambda name: doc
        tools.application.choices_view = lambda name, target, account_set, kind: seen.append((target, account_set, kind)) or {"needed": []}
        tools.application.submit = lambda *a: {"status": "failed", "reason": "stop here"}
        tools._push_one("H1", "EXP1", "c:\\70exp\\t9", "stock")
        self.assertEqual(seen[-1], ("EXP1", "c:\\70exp\\t9", "stock"))
        doc.push_connection, doc.push_account_set = "EXP1", "c:\\70exp\\t8"
        tools._push_one("H1")
        self.assertEqual(seen[-1][:2], ("EXP1", "c:\\70exp\\t8"))
        tools._push_one("H1", "EXP2")  # 换了连接就不沿用原连接的账套
        self.assertEqual(seen[-1][:2], ("EXP2", None))


class RecognizeAttachmentTests(unittest.TestCase):
    """对话里的附件交给录入工作台识别：同一附件同一请求号（不重复扣费）、只识别时不带推送目标、余额不足照实回。"""

    def setUp(self):
        self.created, self.submitted = [], []
        errors = types.ModuleType("pearnly_core.execution.errors")

        class Deferred(Exception):
            def __init__(self, reason):
                super().__init__(reason)
                self.reason = reason

        errors.Deferred, errors.QueueFull = Deferred, type("QueueFull", (Exception,), {})
        self.Deferred = Deferred
        chat = types.ModuleType("pearnly_core.assistant.files")
        chat.attachment = self._attachment
        chat.attachments = lambda: [{"file_id": "file_abc", "filename": "receipt.jpg"}]
        originals = types.ModuleType("pearnly_cowork.files.service")
        originals.create = lambda workspace, name, content, key: self.created.append((name, content, key)) or {"name": "CWS-1"}
        ocr = types.ModuleType("pearnly_cowork.ocr.execution.service")
        ocr.submit = self._submit
        ocr.detail = lambda batch: {"state": "saved", "page_count": 1, "billing_state": "pending", "requires_review": 0,
                                    "history_records": ["H1"]}
        history = types.ModuleType("pearnly_cowork.contracts.history")
        history.primary_page_fields = lambda pages: pages[0]["fields"]
        for name, module in {"pearnly_core.execution.errors": errors, "pearnly_core.assistant.files": chat,
                             "pearnly_cowork.files.service": originals, "pearnly_cowork.ocr.execution.service": ocr,
                             "pearnly_cowork.contracts.history": history}.items():
            sys.modules[name] = module
        for parent in ("pearnly_core", "pearnly_core.execution", "pearnly_core.assistant", "pearnly_cowork.files",
                       "pearnly_cowork.ocr", "pearnly_cowork.ocr.execution", "pearnly_cowork.contracts"):
            sys.modules.setdefault(parent, types.ModuleType(parent))
        sys.modules["pearnly_cowork.files"].service = originals
        sys.modules["pearnly_cowork.ocr.execution"].service = ocr
        sys.modules["pearnly_cowork.contracts"].history = history
        sys.modules["pearnly_core.assistant"].files = chat
        sys.modules["pearnly_core.execution"].errors = errors
        tools.scope.default_workspace = lambda: "WS"
        search = types.ModuleType("pearnly_cowork.connections.account_search")
        search.usable = lambda adapter, rows: rows
        sys.modules["pearnly_cowork.connections.account_search"] = search
        tools.connections.targets = lambda name: {"account_sets": [{"source_id": "K"}]}
        tools.storage.read = lambda name: name
        tools.storage.project = lambda doc: {"name": doc, "invoice_no": "R308", "seller_name": "CP ALL", "total_amount": 90,
                                             "staged": 1, "pages": [{"fields": {"seller_tax": "0107542000011", "vat": 5.89,
                                                                                 "items": [{"description": "Americano"}]}}]}

    def _attachment(self, file_id):
        if file_id == "file_gone":
            raise frappe.ValidationError("This attachment is not in this conversation.")
        return ("note.txt" if file_id == "file_txt" else "receipt.jpg"), b"img-" + file_id.encode()

    def _submit(self, source, key, **choices):
        self.submitted.append((source, key, choices))
        return {"name": "B1"}

    def test_recognize_only_uses_one_fixed_key_and_returns_the_recognized_values(self):
        out = json.loads(tools.recognize_attachments({"files": ["file_abc"], "direction": "purchase"}))["results"][0]
        self.assertEqual(self.created, [("receipt.jpg", b"img-file_abc", "assistant:file_abc")])
        source, key, choices = self.submitted[0]
        self.assertEqual((source, key), ("CWS-1", "assistant:file_abc"))
        self.assertEqual(choices["direction"], "purchase")
        self.assertIsNone(choices["connection"])
        self.assertNotIn("payment", choices)  # 空的付款方式会被识别服务拒绝：没选就不带
        doc = out["documents"][0]
        self.assertEqual((out["state"], doc["invoice_no"], doc["seller_tax"], doc["vat"]), ("saved", "R308", "0107542000011", 5.89))
        self.assertEqual(doc["items"], [{"description": "Americano"}])
        self.assertFalse(out["needs_review"])
        self.assertNotIn("billing", out)  # 刚存完那一刻的「待扣」会被模型说成扣了两次
        self.assertNotIn("currency", doc)  # 空项不给
        self.assertEqual(doc["push_target"], "not chosen yet")  # 仅识别不说成「不推送」
        self.assertNotIn("intake_target", doc)

    def test_wrong_ids_and_unsupported_files_are_explained_not_attempted(self):
        missing = json.loads(tools.recognize_attachments({"files": ["file_gone"]}))["results"][0]
        self.assertEqual(missing["status"], "not_found")
        self.assertEqual(missing["attachments_in_this_chat"], [{"file_id": "file_abc", "filename": "receipt.jpg"}])
        text = json.loads(tools.recognize_attachments({"files": ["file_txt"]}))["results"][0]
        self.assertEqual(text["status"], "unsupported_type")
        self.assertEqual(self.created, [])

    def test_a_push_target_is_only_sent_with_its_connection(self):
        tools.recognize_attachments({"files": ["file_a"], "target": "express", "connection": "C1", "account_set_key": "K"})
        tools.recognize_attachments({"files": ["file_b"], "target": "express"})
        self.assertEqual((self.submitted[0][2]["connection"], self.submitted[0][2]["account_set_key"]), ("C1", "K"))
        self.assertIsNone(self.submitted[1][2]["connection"])

    def test_an_account_set_no_longer_in_the_list_is_not_written_into_documents(self):
        out = json.loads(tools.recognize_attachments({"files": ["file_a"], "target": "express", "connection": "C1",
                                                      "account_set_key": "c:\\70exp\\gone"}))["results"][0]
        self.assertEqual(out["status"], "not_started")
        self.assertIn("account_set_unavailable", out["reason"])
        self.assertEqual(self.created, [])

    def test_not_enough_balance_is_reported_and_nothing_waits(self):
        def refuse(source, key, **choices):
            raise self.Deferred("insufficient_balance")

        sys.modules["pearnly_cowork.ocr.execution.service"].submit = refuse
        out = json.loads(tools.recognize_attachments({"files": ["file_c"]}))["results"][0]
        self.assertEqual(out, {"file": "file_c", "status": "not_started", "reason": "insufficient_balance"})



class EditDocumentTests(unittest.TestCase):
    """复核修改：只改主页、日期写原文、金额按十进制校验、税号校验、带版本号保存，冲突与错误照实回。"""

    def setUp(self):
        self.saved = []
        history = types.ModuleType("pearnly_cowork.contracts.history")
        history.primary_page = lambda pages: next(p for p in pages if not p.get("is_copy"))
        sys.modules["pearnly_cowork.contracts.history"] = history
        sys.modules.setdefault("pearnly_cowork.contracts", types.ModuleType("pearnly_cowork.contracts")).history = history
        amounts = types.ModuleType("pearnly_core.tax_documents.amounts")
        amounts.valid_tax_id = lambda raw: raw == "0107542000011"
        sys.modules["pearnly_core.tax_documents.amounts"] = amounts
        for parent in ("pearnly_core", "pearnly_core.tax_documents"):
            sys.modules.setdefault(parent, types.ModuleType(parent))
        sys.modules["pearnly_core.tax_documents"].amounts = amounts
        self.doc = {"name": "H1", "record_version": 4, "pages": [
            {"is_copy": True, "fields": {"total_amount": 1}},
            {"fields": {"seller_name": "CP ALL", "total_amount": 90, "date": "2026-09-29", "items": [{"name": "A"}]}}]}
        tools.storage.read = lambda name: name
        tools.storage.project = lambda doc: self.doc
        tools.storage.update_pages = self._update

    def _update(self, name, pages, version):
        self.saved.append((name, pages, version))
        return {**self.doc, "pages": pages, "record_version": 5}

    def test_only_the_primary_page_changes_and_the_version_is_passed(self):
        out = json.loads(tools.edit_document({"document": "H1", "version": 4, "changes": {
            "total_amount": "1,070.00", "date": "29/09/69", "seller_tax": "0107-5420-00011"}}))
        name, pages, version = self.saved[0]
        self.assertEqual((name, version), ("H1", "4"))
        self.assertEqual(pages[0], {"is_copy": True, "fields": {"total_amount": 1}})
        fields = pages[1]["fields"]
        self.assertEqual((fields["total_amount"], fields["date_raw"], fields["seller_tax"]), ("1070.00", "29/09/69", "0107542000011"))
        self.assertEqual(fields["items"], [{"name": "A"}])
        self.assertEqual((out["status"], out["version"]), ("saved", 5))

    def test_bad_values_and_unknown_fields_are_refused_before_saving(self):
        for changes in ({"total_amount": "abc"}, {"seller_tax": "123"}, {"direction": "refund"}, {"push_state": "x"}):
            out = json.loads(tools.edit_document({"document": "H1", "version": 4, "changes": changes}))
            self.assertEqual(out["status"], "not_saved", changes)
        self.assertEqual(self.saved, [])
        # 不带版本不保存（否则就跳过了「别人刚改过不覆盖」）
        out = json.loads(tools.edit_document({"document": "H1", "changes": {"notes": "x"}}))
        self.assertEqual(out["status"], "not_saved")
        self.assertEqual(self.saved, [])

    def test_someone_elses_newer_edit_is_reported_not_overwritten(self):
        def stale(name, pages, version):
            raise frappe.ValidationError("The record changed. Reload it before saving.")

        tools.storage.update_pages = stale
        out = json.loads(tools.edit_document({"document": "H1", "version": 3, "changes": {"notes": "x"}}))
        self.assertEqual(out["status"], "not_saved")
        self.assertIn("changed", out["reason"])



class IntakeChoiceTests(unittest.TestCase):
    """录入口径：每种 ERP 一条连接与状态、上次用的账套排第一、上次口径来自上一次识别；科目组与 MR.ERP 设置走连接服务。"""

    def setUp(self):
        search = types.ModuleType("pearnly_cowork.connections.account_search")
        search.usable = lambda adapter, rows: [r for r in rows if r.get("active", True)]
        search.label = lambda adapter, row: row["label"]
        search.search = lambda adapter, rows, query: [{"value": r["source_id"], "label": r["label"]}
                                                      for r in rows if query.lower() in r["label"].lower()]
        sys.modules["pearnly_cowork.connections.account_search"] = search
        tools.connections.records = lambda *a: [
            {"name": "EXP1", "adapter": "express", "enabled": True, "label": "Office"},
            {"name": "EXP2", "adapter": "express", "enabled": True},
            {"name": "MR1", "adapter": "mrerp", "enabled": False}]
        tools.connections.targets = lambda name: {"account_sets": [
            {"source_id": "c:\\70exp\\dat", "label": "Z. blank", "attributes": {"root_label": "70EXP"}},
            {"source_id": "A", "label": "Alpha Co", "attributes": {"root_label": "59EXP"}},
            {"source_id": "C", "label": "Gamma Co", "attributes": {"root_label": "70EXP"}},
            {"source_id": "B", "label": "Beta Co", "attributes": {"root_label": "68EXP"}},
            {"source_id": "X", "label": "Old Co", "active": False}]}
        tools.destinations.online = lambda adapter, name: True
        self.batch_filters = []
        frappe.get_all = lambda doctype, **kw: self.batch_filters.append(kw["filters"]) or [
            {"intake_target": "express", "target_connection": "EXP1", "target_account_set": "b",
             "direction": "purchase", "posting_kind": "stock", "payment": None}]

    def tearDown(self):
        del frappe.get_all

    def test_targets_states_and_the_last_used_account_set_first(self):
        out = json.loads(tools.intake_options({}))
        express, mrerp, none = out["targets"]
        self.assertEqual((express["connection"], express["state"], express["account_sets_total"]), ("EXP1", "ready", 4))
        # 上次用的第一（大小写不同也认），其余新年度在前，Express 自带的空白模板（DAT）最后
        self.assertEqual([a["key"] for a in express["account_sets"]], ["B", "C", "A", "c:\\70exp\\dat"])
        # 上次口径只取本人录进 ERP 的那次（仅识别的不算）
        self.assertEqual(self.batch_filters[0], {"actor": "a@example.com", "intake_target": ["in", ["express", "mrerp"]]})
        self.assertEqual(mrerp["state"], "disabled")
        self.assertEqual(none, {"target": "none", "state": "ready"})
        self.assertEqual(out["suggested"], {"target": "express", "connection": "EXP1", "account_set_key": "b",
                                            "direction": "purchase", "posting_kind": "stock"})
        found = json.loads(tools.intake_options({"search": "alp"}))["targets"][0]["account_sets"]
        self.assertEqual(found, [{"key": "A", "label": "Alpha Co"}])

    def test_a_last_used_account_set_that_is_gone_is_not_suggested(self):
        frappe.get_all = lambda doctype, **kw: [{"intake_target": "express", "target_connection": "EXP1",
                                                 "target_account_set": "c:\\70exp\\gone", "direction": "purchase"}]
        out = json.loads(tools.intake_options({}))
        self.assertIsNone(out["suggested"])
        self.assertEqual(out["last_used_unavailable"]["account_set_key"], "c:\\70exp\\gone")
        self.assertNotIn("_keys", out["targets"][0])

    def test_offline_companion_is_not_ready(self):
        tools.destinations.online = lambda adapter, name: False
        self.assertEqual(json.loads(tools.intake_options({}))["targets"][0]["state"], "companion_offline")

    def test_stock_group_and_mrerp_choices_go_through_the_connection_services(self):
        calls = []
        tools.connections_api.choose_stock_acc_group = lambda *a: calls.append(("stock",) + a) or {"chosen": a[2]}
        tools.connections_api.choose_mrerp_menu = lambda *a: calls.append(("menu",) + a) or {}
        tools.connections_api.choose_mrerp_default = lambda *a: calls.append(("default",) + a) or {}
        tools.connections_api.mrerp_menu = lambda *a: {"known": True}
        tools.stock_group({"connection": "EXP1", "account_set_key": "B", "acccod": "1140"})
        tools.mrerp_setup({"connection": "MR1", "account_set_key": "1:2", "menus": {"purchase": 7},
                           "defaults": {"department": "D1"}})
        self.assertEqual(calls, [("stock", "EXP1", "B", "1140"), ("menu", "MR1", "1:2", "purchase", "7"),
                                 ("default", "MR1", "1:2", "department", "D1")])



class PushFollowUpTests(unittest.TestCase):
    """推送前要人选的项（可搜、每类最多 30 个）、保存、结果不明的确认：都走复核页同一套服务。"""

    def setUp(self):
        self.calls = []
        doc = types.SimpleNamespace(push_connection="EXP1", push_account_set="K", posting_kind="stock",
                                    last_push_record="PR1")
        tools.storage.read = lambda name: doc
        self.doc = doc
        tools.application.choices_view = lambda *a: {"needed": [{"kind": "item", "subject": "Coffee"}],
                                                     "options": {"products": [{"code": f"P{i}", "name": f"Item {i}"}
                                                                              for i in range(100)]},
                                                     "account_set": "K"}
        tools.application_api.save_choices = lambda *a: self.calls.append(("save",) + a) or {"saved": 1}
        tools.application_api.resolve_unknown = lambda *a: self.calls.append(("resolve",) + a) or {"status": "success"}

    def test_options_are_capped_and_searchable(self):
        out = json.loads(tools.push_choices({"document": "H1"}))
        self.assertEqual((out["connection"], out["needed"][0]["kind"]), ("EXP1", "item"))
        self.assertEqual((out["options"]["products"]["total"], len(out["options"]["products"]["shown"])), (100, 30))
        found = json.loads(tools.push_choices({"document": "H1", "search": "item 42"}))["options"]["products"]
        self.assertEqual(found["shown"], [{"code": "P42", "name": "Item 42"}])

    def test_an_unread_account_set_is_read_by_the_companion_then_tried_again(self):
        attempts, calls = [], []

        def view(*a):
            attempts.append(a)
            if len(attempts) == 1:
                raise frappe.ValidationError("catalog_refresh_invalid")
            return {"needed": [], "options": {}, "account_set": "K"}

        tools.application.choices_view = view
        tools.connections_api.request_refresh = lambda *a: calls.append(("request",) + a) or {"request_id": "R1", "status": "requested"}
        tools.connections_api.refresh_status = lambda *a: calls.append(("status",) + a) or {"status": "succeeded"}
        saved_sleep, tools.time.sleep = tools.time.sleep, lambda s: None
        try:
            out = json.loads(tools.push_choices({"document": "H1"}))
        finally:
            tools.time.sleep = saved_sleep
        self.assertEqual(len(attempts), 2)
        self.assertEqual((calls[0][0], calls[0][1], calls[0][3]), ("request", "EXP1", "K"))
        self.assertEqual(out["needed"], [])

    def test_an_account_set_the_computer_cannot_read_is_explained(self):
        def stale(*a):
            raise frappe.ValidationError("catalog_refresh_invalid")

        tools.application.choices_view = stale
        tools.connections_api.request_refresh = lambda *a: {"request_id": "R1", "status": "requested"}
        tools.connections_api.refresh_status = lambda *a: {"status": "failed", "error_code": "account_set_missing"}
        saved_sleep, tools.time.sleep = tools.time.sleep, lambda s: None
        try:
            out = json.loads(tools.push_choices({"document": "H1"}))
        finally:
            tools.time.sleep = saved_sleep
        self.assertEqual(out["status"], "failed")
        self.assertIn("account_set_missing", out["reason"])
        self.assertIn("Choose another account set", out["reason"])

    def test_choices_are_saved_for_the_document_target(self):
        items = [{"kind": "item", "subject": "Coffee", "value": "P1"}]
        out = json.loads(tools.save_push_choices({"document": "H1", "items": items}))
        self.assertEqual(out["status"], "saved")
        self.assertEqual(self.calls[0][:4], ("save", "H1", "EXP1", "K"))
        self.assertEqual(json.loads(self.calls[0][4]), items)

    def test_unknown_result_is_confirmed_only_with_a_push_to_confirm(self):
        tools.resolve_push_result({"document": "H1", "entered": True, "docnum": "RR001"})
        self.assertEqual(self.calls[-1], ("resolve", "PR1", 1, "RR001"))
        self.doc.last_push_record = ""
        out = json.loads(tools.resolve_push_result({"document": "H1", "entered": False}))
        self.assertEqual(out["status"], "not_saved")



class DeleteAndExportTests(unittest.TestCase):
    """删除走列表同一个删除服务（已推送的由它跳过）；导出的工作簿放进对话、回位置给智能体。"""

    def setUp(self):
        self.calls = []
        removal = types.ModuleType("pearnly_cowork.history.removal")
        removal.delete = lambda records: self.calls.append(("delete", json.loads(records))) or {"deleted": 1, "skipped": 1}
        sys.modules["pearnly_cowork.history.removal"] = removal
        sys.modules["pearnly_cowork.history"].removal = removal
        roundtrip = types.ModuleType("pearnly_cowork.roundtrip.service")
        roundtrip.export = lambda names: self.calls.append(("export", json.loads(names))) or b"PK-xlsx"
        export = types.ModuleType("pearnly_cowork.roundtrip.export")
        export.ExportError = type("ExportError", (Exception,), {"code": "x"})
        sys.modules.setdefault("pearnly_cowork.roundtrip", types.ModuleType("pearnly_cowork.roundtrip"))
        sys.modules["pearnly_cowork.roundtrip.service"], sys.modules["pearnly_cowork.roundtrip.export"] = roundtrip, export
        sys.modules["pearnly_cowork.roundtrip"].service, sys.modules["pearnly_cowork.roundtrip"].export = roundtrip, export
        chat = sys.modules.setdefault("pearnly_core.assistant.files", types.ModuleType("pearnly_core.assistant.files"))
        chat.add_to_conversation = lambda name, content, mime: self.calls.append(("attach", name, content)) or \
            "/mnt/session/uploads/file_x/" + name
        sys.modules.setdefault("pearnly_core", types.ModuleType("pearnly_core"))
        sys.modules.setdefault("pearnly_core.assistant", types.ModuleType("pearnly_core.assistant")).files = chat

    def test_delete_passes_the_list_and_reports_skipped(self):
        out = json.loads(tools.delete_documents({"documents": ["H1", "H2"]}))
        self.assertEqual(self.calls, [("delete", ["H1", "H2"])])
        self.assertEqual(out, {"deleted": 1, "skipped": 1})

    def test_export_adds_the_workbook_to_the_conversation(self):
        out = json.loads(tools.export_review_workbook({"documents": ["H1", "H1", "H2"]}))
        self.assertEqual(self.calls[0], ("export", ["H1", "H2"]))
        self.assertEqual(self.calls[1][2], b"PK-xlsx")
        self.assertRegex(self.calls[1][1], r"^cowork-review-\d{8}-\d{6}\.xlsx$")  # 每次导出新名字，对话里出新卡片



class FolderTaskTests(unittest.TestCase):
    """定时任务：选项（文件夹只列电脑开放的、可搜、截断）、保存 / 启停 / 删除都走任务服务，服务拒绝时照实回。"""

    def setUp(self):
        self.calls = []
        tools.rules.options = lambda connection, key: {
            "readers": [{"connection": "EXP1", "label": "DESKTOP-1", "fresh": True,
                         "roots": [{"id": "r1", "label": "C:\\Bills", "folders": [f"2026-{i:02d}" for i in range(1, 80)]}]}],
            "accounts": None, "departments": None, "stock_group": None}
        tools.rules.save = lambda values: self.calls.append(("save", values)) or {"name": "R1", "title": values["title"], "enabled": 1}
        tools.rules.set_enabled = lambda name, enabled: self.calls.append(("enabled", name, enabled)) or {"name": name, "enabled": enabled}
        tools.rules.delete = lambda names: self.calls.append(("delete", json.loads(names))) or {"deleted": 1}

    def test_options_list_the_open_folders_trimmed_and_searchable(self):
        out = json.loads(tools.folder_task_options({"connection": "EXP1"}))
        root = out["computers"][0]["roots"][0]
        self.assertEqual((out["computers"][0]["computer"], root["root_id"], root["folders_total"], len(root["folders"])),
                         ("DESKTOP-1", "r1", 79, tools.FOLDERS_SHOWN))
        found = json.loads(tools.folder_task_options({"connection": "EXP1", "search": "2026-07"}))
        self.assertEqual(found["computers"][0]["roots"][0]["folders"], ["2026-07"])

    def test_save_enable_delete_go_through_the_task_service(self):
        values = {"title": "Ice", "connection": "EXP1", "direction": "purchase", "posting_kind": "stock",
                  "root_id": "r1", "folder": "2026-10", "run_day": 0, "run_time": "22:00", "ignored": 1}
        out = json.loads(tools.save_folder_task(values))
        self.assertEqual(out, {"status": "saved", "task": "R1", "title": "Ice", "enabled": 1})
        self.assertNotIn("ignored", self.calls[0][1])
        tools.set_folder_task_enabled({"task": "R1", "enabled": False})
        tools.delete_folder_tasks({"tasks": ["R1"]})
        self.assertEqual(self.calls[1:], [("enabled", "R1", 0), ("delete", ["R1"])])

    def test_refusals_from_the_service_come_back_as_not_saved(self):
        def overlap(values):
            raise frappe.ValidationError("The folder overlaps task Ice")

        tools.rules.save = overlap
        out = json.loads(tools.save_folder_task({"title": "B", "connection": "EXP1"}))
        self.assertEqual(out["status"], "not_saved")
        self.assertIn("overlaps", out["reason"])



class ReconTests(unittest.TestCase):
    """对账：两口文件取自对话附件交给对账任务、结果只给要点与没对上的行、导出放进对话；kind 只认三种。"""

    def setUp(self):
        self.calls = []
        mods = {}
        for name in ("pearnly_cowork.recon_center", "pearnly_cowork.reconciliation", "pearnly_core",
                     "pearnly_core.assistant"):
            mods[name] = sys.modules.setdefault(name, types.ModuleType(name))
        jobs = types.ModuleType("pearnly_cowork.recon_center.jobs")
        jobs.start = lambda kind, title, sides, options: self.calls.append(("start", kind, sides)) or {"job": "J1"}
        jobs.status = lambda job: {"state": "done", "stage": "done", "task": "T1",
                                   "files": [{"pages": 3, "done": 3}]}
        api = types.ModuleType("pearnly_cowork.recon_center.api")
        api.detail = lambda kind, task, start, limit: {"rows": [{"match_status": "matched"},
                                                                {"match_status": "stmt_deposit_only", "amount": 5}]}
        sheets = types.ModuleType("pearnly_cowork.recon_center.sheets")
        sheets.load = lambda kind, task, lang: {"task": {"name": task, "title": "BBL Feb", "status": "completed",
                                                         "questions": [{"rule": "card_fee"}]}, "sheets": ["big"]}
        bank = types.ModuleType("pearnly_cowork.reconciliation.bank_recon_service")
        bank.export = lambda task, lang: ("BBL Feb.xlsx", b"PK")
        chat = sys.modules.get("pearnly_core.assistant.files") or types.ModuleType("pearnly_core.assistant.files")
        chat.attachment = lambda file_id: (file_id + ".pdf", b"pdf")
        chat.attachments = lambda: []
        chat.add_to_conversation = lambda name, content, mime: self.calls.append(("attach", name)) or "/mnt/x/" + name
        for name, module in {"pearnly_cowork.recon_center.jobs": jobs, "pearnly_cowork.recon_center.api": api,
                             "pearnly_cowork.recon_center.sheets": sheets,
                             "pearnly_cowork.reconciliation.bank_recon_service": bank,
                             "pearnly_cowork.reconciliation.income_recon_service": types.ModuleType("income"),
                             "pearnly_cowork.reconciliation.vat_check_service": types.ModuleType("vat"),
                             "pearnly_core.assistant.files": chat}.items():
            sys.modules[name] = module
            parent, _, child = name.rpartition(".")
            setattr(sys.modules[parent], child, module)
        toolrun = types.ModuleType("pearnly_core.assistant.toolrun")
        toolrun.fresh = lambda: self.calls.append(("fresh",))
        sys.modules["pearnly_core.assistant.toolrun"] = toolrun
        sys.modules["pearnly_core.assistant"].toolrun = toolrun
        self.store = {}
        store = self.store

        class Cache:
            def set(self, key, value, nx=False, ex=None):
                if nx and key in store:
                    return False
                store[key] = value.encode() if isinstance(value, str) else value
                return True

            def get(self, key):
                return store.get(key)

            def delete(self, key):
                store.pop(key, None)

        frappe.cache = Cache()
        frappe.local = types.SimpleNamespace(lang="th", site="s1", pearnly_assistant={"session_id": "sesn_1"})
        sleeper = mock.patch.object(tools.time, "sleep", lambda _s: None)
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def test_the_same_files_twice_start_one_reconciliation(self):
        """模型一次发了两个同样的开始调用（2026-10-10 测试）：第二个接着等第一套，不再开第二套对账。"""
        first = json.loads(tools.recon_start({"kind": "bank", "left_files": ["s"], "right_files": ["gl"]}))
        second = json.loads(tools.recon_start({"kind": "bank", "left_files": ["s"], "right_files": ["gl"]}))
        self.assertEqual(len([c for c in self.calls if c[0] == "start"]), 1)
        self.assertEqual((first["job"], second["job"]), ("J1", "J1"))

    def test_status_reads_afresh_until_the_reconciliation_finishes(self):
        """推送线程里读过一次的状态会停在进程缓存里：每次查都要先清掉，读到后台刚写的「完成」（2026-10-10 测试一直读到排队中）。"""
        states = iter(["queued", "running", "done"])
        sys.modules["pearnly_cowork.recon_center.jobs"].status = lambda job: {"state": next(states), "task": "T1"}
        out = json.loads(tools.recon_status({"job": "J1"}))
        self.assertEqual((out["state"], out["task"]), ("done", "T1"))
        self.assertEqual(len([c for c in self.calls if c[0] == "fresh"]), 3)

    def test_start_hands_both_sides_from_the_chat_and_reports_the_finished_task(self):
        out = json.loads(tools.recon_start({"kind": "bank", "left_files": ["file_s1", "file_s2"], "right_files": ["file_gl"]}))
        kind, sides = self.calls[0][1], self.calls[0][2]
        self.assertEqual((kind, [n for n, _c in sides["left"]], [n for n, _c in sides["right"]]),
                         ("bank", ["file_s1.pdf", "file_s2.pdf"], ["file_gl.pdf"]))
        self.assertEqual((out["state"], out["task"], out["pages"]), ("done", "T1", 3))

    def test_result_gives_the_gist_and_only_unmatched_rows(self):
        out = json.loads(tools.recon_result({"kind": "bank", "task": "T1"}))
        self.assertEqual((out["title"], out["unmatched_total"], out["rows_total"]), ("BBL Feb", 1, 2))
        self.assertNotIn("sheets", out)
        self.assertEqual(out["questions"], [{"rule": "card_fee"}])

    def test_export_goes_into_the_conversation_and_kinds_are_checked(self):
        out = json.loads(tools.recon_export({"kind": "bank", "task": "T1"}))
        self.assertEqual(out["file"], "/mnt/x/BBL Feb.xlsx")
        with self.assertRaises(frappe.ValidationError):
            tools.recon_list({"kind": "payroll"})



class StocktakeAndWorkTests(unittest.TestCase):
    """库存盘点、任务看板：调页面同一个接口；结果只给要点，业务拒绝回原因不抛给模型。"""

    def setUp(self):
        self.calls = []
        for name in ("pearnly_cowork.stocktake", "pearnly_cowork.work"):
            sys.modules.setdefault(name, types.ModuleType(name))
        desk = types.ModuleType("pearnly_cowork.stocktake.desk_api")
        desk.api_stock_count_review = lambda count: {
            "task": {"title": "March", "status": "Active", "item_total": 3, "counted_total": 2, "difference_total": 1},
            "items": [{"code": "A", "product_name": "Apple", "book_qty": 5.0, "counted": 1, "counted_qty": 5.0, "difference": 0.0},
                      {"code": "B", "product_name": "Bean", "book_qty": 5.0, "counted": 1, "counted_qty": 3.0, "difference": -2.0},
                      {"code": "C", "product_name": "Corn", "book_qty": 1.0, "counted": 0, "counted_qty": None, "difference": None}]}
        desk.api_stock_count_lookup = lambda count, code: {"product": {"name": "ITEM-" + code}}
        desk.api_stock_count_recount = lambda count, product: self.calls.append(("recount", product)) or {"cleared": 1}
        desk.api_stock_count_add_entry = lambda count, qty, **kw: self.calls.append(("add", qty, kw["item"])) or {"entry": "E1"}

        def delete(name):
            if name == "closed":
                frappe.throw("stocktake.closed")
            return {"deleted": 1}

        desk.api_stock_count_delete = delete
        work = types.ModuleType("pearnly_cowork.work.api")
        work.board = lambda board: {"board": {"name": "B1", "title": "Team"},
                                    "lists": [{"name": "L1", "title": "To do"}, {"name": "L2", "title": "Done", "is_done_list": 1}],
                                    "cards": [{"name": "C1", "title": "Pay VAT", "board_list": "L1", "assignees": ["a@example.com"]},
                                              {"name": "C2", "title": "Template", "board_list": "L1", "is_template": 1},
                                              {"name": "C3", "title": "File PND", "board_list": "L2", "completed": 1, "assignees": []}]}
        for name, module in {"pearnly_cowork.stocktake.desk_api": desk, "pearnly_cowork.work.api": work}.items():
            sys.modules[name] = module
            parent, _, child = name.rpartition(".")
            setattr(sys.modules[parent], child, module)
        frappe.session = types.SimpleNamespace(user="a@example.com")

    def test_report_lists_only_differences_and_uncounted_items(self):
        out = json.loads(tools.stocktake_report({"count": "S1"}))
        self.assertEqual([r["code"] for r in out["items"]], ["B", "C"])
        self.assertEqual((out["items_total"], out["difference_total"]), (3, 1))
        out = json.loads(tools.stocktake_report({"count": "S1", "all_items": True}))
        self.assertEqual(len(out["items"]), 3)

    def test_recount_clears_then_records_the_new_quantity(self):
        tools.stocktake_count({"count": "S1", "code": "B", "qty": 4, "recount": True})
        self.assertEqual(self.calls, [("recount", "ITEM-B"), ("add", 4, "ITEM-B")])

    def test_delete_reports_each_count_and_refusals_do_not_raise(self):
        out = json.loads(tools.stocktake_delete({"counts": ["S1", "closed"]}))
        self.assertEqual(out["deleted"], ["S1"])
        self.assertIn("closed", out["not_deleted"])

    def test_board_hides_templates_and_mine_keeps_my_cards(self):
        out = json.loads(tools.work_board({}))
        self.assertEqual([c["card"] for c in out["cards"]], ["C1", "C3"])
        self.assertEqual(out["cards"][1]["list"], "Done")
        out = json.loads(tools.work_board({"mine": True}))
        self.assertEqual([c["card"] for c in out["cards"]], ["C1"])


if __name__ == "__main__":
    unittest.main()
