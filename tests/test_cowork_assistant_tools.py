"""Cowork 智能体工具（ADR-019）的离线检查：声明完整、步骤名有译文、推送请求号确定、筛选口径、逐张提交。

不连库：Frappe 与 Cowork 各域服务用最小替身，只看工具层自己的逻辑。
"""
import importlib.util
import json
import pathlib
import sys
import types
import unittest

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

    def test_account_sets_are_capped_and_searchable(self):
        sets = [{"source_id": str(i), "label": f"Company {i}"} for i in range(100)] + [{"source_id": "x", "label": "Acme Trading"}]
        tools.connections.records = lambda *a: [{"name": "C1", "label": "Office", "adapter": "express", "enabled": True}]
        tools.connections.targets = lambda name: {"account_sets": sets, "status": "fresh"}
        data = json.loads(tools.companion_status({}))["connections"][0]
        self.assertEqual(data["account_sets_total"], 101)
        self.assertEqual(len(data["account_sets"]), tools.ACCOUNT_SETS_SHOWN)
        found = json.loads(tools.companion_status({"search": "acme"}))["connections"][0]
        self.assertEqual([a["key"] for a in found["account_sets"]], ["x"])

    def test_already_pushed_documents_are_not_pushed_again_and_each_is_settled(self):
        docs = {"A": types.SimpleNamespace(last_push_status="success"),
                "B": types.SimpleNamespace(last_push_status="", push_connection="")}
        tools.storage.read = lambda name: docs[name]
        result = json.loads(tools.push_documents({"documents": ["A", "B"]}))["results"]
        self.assertEqual(result[0], {"document": "A", "status": "already_success"})
        self.assertEqual(result[1]["status"], "failed")
        self.assertEqual(result[1]["reason"], "no_push_target")



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
            {"source_id": "A", "label": "Alpha Co"}, {"source_id": "B", "label": "Beta Co"},
            {"source_id": "X", "label": "Old Co", "active": False}]}
        tools.destinations.online = lambda adapter, name: True
        frappe.get_list = lambda doctype, **kw: [{"intake_target": "express", "target_connection": "EXP1",
                                                  "target_account_set": "B", "direction": "purchase",
                                                  "posting_kind": "stock", "payment": None}]

    def tearDown(self):
        frappe.get_list = types.MethodType(_Frappe.get_list, frappe)

    def test_targets_states_and_the_last_used_account_set_first(self):
        out = json.loads(tools.intake_options({}))
        express, mrerp, none = out["targets"]
        self.assertEqual((express["connection"], express["state"], express["account_sets_total"]), ("EXP1", "ready", 2))
        self.assertEqual([a["key"] for a in express["account_sets"]], ["B", "A"])
        self.assertEqual(mrerp["state"], "disabled")
        self.assertEqual(none, {"target": "none", "state": "ready"})
        self.assertEqual(out["last_used"], {"target": "express", "connection": "EXP1", "account_set_key": "B",
                                            "direction": "purchase", "posting_kind": "stock"})
        found = json.loads(tools.intake_options({"search": "alp"}))["targets"][0]["account_sets"]
        self.assertEqual(found, [{"key": "A", "label": "Alpha Co"}])

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


if __name__ == "__main__":
    unittest.main()
