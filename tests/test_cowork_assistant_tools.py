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
CORE = ROOT / "apps/pearnly_core/pearnly_core"


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
        self.calls, self.commits, self.rollbacks = [], 0, 0
        self.rows = []
        self.session = types.SimpleNamespace(user="a@example.com")
        self.local = types.SimpleNamespace(lang="en", site="s1", pearnly_assistant=None)
        self.db = types.SimpleNamespace(commit=self._commit, rollback=self._rollback,
                                        get_value=lambda *a, **k: ("express", 3))

    def _commit(self):
        self.commits += 1

    def _rollback(self):
        self.rollbacks += 1

    def get_list(self, doctype, **kwargs):
        """按 limit_start / limit_page_length 从 `rows` 切（每次给新副本：工具会改行）；数总数的查询回 total。"""
        self.calls.append(kwargs)
        if kwargs.get("fields") and isinstance(kwargs["fields"][0], dict):
            return [{"total": len(self.rows)}]
        start, size = kwargs.get("limit_start") or 0, kwargs.get("limit_page_length")
        return [dict(row) for row in self.rows[start:start + size if size else None]]

    def clear_messages(self):
        pass

    def throw(self, message, exc=None):
        raise (exc or self.ValidationError)(message)

    @staticmethod
    def _(text, lang=None):
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
        "pearnly_cowork.folder_intake.agent": dict(read=lambda reader: {}),
        "pearnly_cowork.folder_intake.rules": dict(listing=lambda: {}, run_now=lambda n: {}, runs=lambda n: {},
                                                   waiting=lambda *a: None, account_label=lambda c, k: k,
                                                   master_names=lambda c, k: {},
                                                   continues_from=lambda reader, root, folder, current=None: None),
        "pearnly_cowork.history.push_state": dict(record_failure=lambda *a: None),
        "pearnly_cowork.history.storage": dict(read=lambda name: None),
        "pearnly_cowork.push.application.service": dict(),
        "pearnly_cowork.push.application.api": dict(),
        # 推送原因按用户语言给模型（真的 reason_text 要查翻译表，这里标出「翻过了」）
        "pearnly_cowork.push.export_facts": dict(reason_text=lambda reason: "said:" + reason),
    }
    for name, attrs in leaf.items():
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        sys.modules[name] = module
        parent, _, child = name.rpartition(".")
        setattr(sys.modules[parent], child, module)
    # 定时任务的纯规则（默认值各类、哪类适用）用真的：工具的说明与校验要与它一致
    spec = importlib.util.spec_from_file_location("pearnly_cowork.folder_intake.contracts",
                                                  COWORK / "folder_intake/contracts.py")
    contracts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contracts)
    sys.modules[spec.name] = sys.modules["pearnly_cowork.folder_intake"].contracts = contracts
    # 报税的纯规则（所得类型码）也用真的：工具的枚举取自它；共用票种规则一并按文件载入，不把整个共用层放上路径
    for name in ("pearnly_core", "pearnly_core.tax_documents", "pearnly_cowork.tax"):
        sys.modules[name] = types.ModuleType(name)
    for name, path in (("pearnly_core.tax_documents.codes", CORE / "tax_documents/codes.py"),
                       ("pearnly_core.tax_documents.amounts", CORE / "tax_documents/amounts.py"),
                       ("pearnly_core.tax_documents.rules", CORE / "tax_documents/rules.py"),
                       ("pearnly_cowork.tax.periods", COWORK / "tax/periods.py"),
                       ("pearnly_cowork.tax.thai_words", COWORK / "tax/thai_words.py"),
                       ("pearnly_cowork.tax.wht", COWORK / "tax/wht.py")):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
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
            # 等「允许」时卡片上的问句也要有译文（`ask`，以及随参数变的 `ask_when`）
            for ask in [tool.get("ask")] + list((tool.get("ask_when") or {}).values()):
                if ask:
                    self.assertIn(ask, terms, tool["name"])
            for key in tool.get("ask_when") or {}:
                self.assertIn(key, tool["input_schema"]["properties"], tool["name"])
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
        self.assertEqual(frappe.calls[-1]["limit_page_length"], tools.LIST_SCAN_PAGE)  # 失败条件改在取回后筛

    def test_companion_status_leaves_account_set_lists_to_intake_options(self):
        tools.connections.records = lambda *a: [{"name": "C1", "label": "Office", "adapter": "express", "enabled": True}]
        tools.connections.targets = lambda name: {"account_sets": [{"source_id": "x", "label": "Acme"}], "status": "fresh"}
        data = json.loads(tools.companion_status({}))["connections"][0]
        self.assertEqual(data["account_sets_status"], "fresh")
        self.assertNotIn("account_sets", data)
        self.assertIn("cc_desktop_online", data)  # 模型读到的键也不带旧称

    def test_the_model_is_told_cc_desktop_names_once(self):
        # 2026-10-13：模型从没被告知 Windows 端叫什么，又读到 companion，就转述成「小助手 / ผู้ช่วย / コンパニオン」
        status = next(t for t in tools.TOOLS if t["name"] == "cowork_companion_status")
        for name in ("CC Desktop", "CC 桌面版", "CC เดสก์ท็อป", "CC デスクトップ"):
            self.assertIn(name, status["description"])
        self.assertEqual(sum(tools.CC_DESKTOP in t["description"] for t in tools.TOOLS), 1)
        run = json.loads(tools.run_folder_task({"task": "R1"}))
        self.assertTrue(run["note"].startswith("CC Desktop "))

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
        chat.origin = lambda file_id: {"file_page": "tool", "file_folder": "folder"}.get(file_id, "user")
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

    def test_files_made_by_tools_are_not_recognized_but_files_from_a_folder_are(self):
        # L3：工具产出也留在对话里能再当输入；原件页图、回读的账不是票据，送识别还要扣费（2026-10 CC 闭环，评审三-12）
        made = json.loads(tools.recognize_attachments({"files": ["file_page"]}))["results"][0]
        self.assertEqual(made["status"], "not_started")
        self.assertTrue(made["reason"].startswith("made_by_tool"))
        self.assertEqual(self.created, [])
        fetched = json.loads(tools.recognize_attachments({"files": ["file_folder"]}))["results"][0]
        self.assertEqual(fetched["batch"], "B1")
        self.assertEqual(self.created, [("receipt.jpg", b"img-file_folder", "assistant:file_folder")])

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

    def test_a_failed_recognition_is_tried_again_with_a_new_key_and_finished_ones_are_kept(self):
        # 2026-10-13 测试机：上一次谷歌报错 / 回放库里没有，再调永远拿回那份失败的
        states = {"assistant:file_f": "failed", "assistant:file_f:2": "uncertain"}

        def submit(source, key, **choices):
            self.submitted.append((source, key, choices))
            return {"name": "B-" + key, "state": states.get(key, "queued")}

        sys.modules["pearnly_cowork.ocr.execution.service"].submit = submit
        out = json.loads(tools.recognize_attachments({"files": ["file_f"]}))["results"][0]
        self.assertEqual([key for _source, key, _choices in self.submitted],
                         ["assistant:file_f", "assistant:file_f:2", "assistant:file_f:3"])
        self.assertEqual([item[2] for item in self.created],
                         ["assistant:file_f", "assistant:file_f:2", "assistant:file_f:3"])
        self.assertEqual((out["batch"], out["attempt"]), ("B-assistant:file_f:3", 3))
        # 已出结果（含「不是发票」）、还在跑、被取消的：照旧拿回原来那份，不重识别、不重扣
        for state in ("saved", "not_invoice", "running", "queued", "deferred", "cancelled"):
            self.submitted.clear()
            states["assistant:file_k"] = state
            out = json.loads(tools.recognize_attachments({"files": ["file_k"]}))["results"][0]
            self.assertEqual([key for _source, key, _choices in self.submitted], ["assistant:file_k"], state)
            self.assertNotIn("attempt", out)
        # 一直失败：最多识别 RECOGNIZE_ATTEMPTS 次，最后那份失败的照实回
        self.submitted.clear()
        sys.modules["pearnly_cowork.ocr.execution.service"].submit = lambda source, key, **choices: (
            self.submitted.append(key) or {"name": "B-" + key, "state": "failed"})
        sys.modules["pearnly_cowork.ocr.execution.service"].detail = lambda batch: {
            "state": "failed", "page_count": 1, "last_code": "ocr.replay_missing"}
        out = json.loads(tools.recognize_attachments({"files": ["file_x"]}))["results"][0]
        self.assertEqual(len(self.submitted), tools.RECOGNIZE_ATTEMPTS)
        self.assertEqual((out["state"], out["reason"], out["attempt"]), ("failed", "ocr.replay_missing", 5))
        description = {tool["name"]: tool for tool in tools.TOOLS}["cowork_recognize_attachments"]["description"]
        self.assertIn("only when its last recognition failed", description)

    def test_not_enough_balance_is_reported_and_nothing_waits(self):
        def refuse(source, key, **choices):
            raise self.Deferred("insufficient_balance")

        sys.modules["pearnly_cowork.ocr.execution.service"].submit = refuse
        out = json.loads(tools.recognize_attachments({"files": ["file_c"]}))["results"][0]
        self.assertEqual(out, {"file": "file_c", "status": "not_started", "reason": "insufficient_balance"})


class PosImportToolTests(unittest.TestCase):
    """L9：对话里的 POS 导出按天建票（不识别、不扣费）；认列问一次（附件或文件夹任务里停着的那份），旧版 .xls 说清楚。"""

    def setUp(self):
        self.created, self.calls = [], []
        chat = types.ModuleType("pearnly_core.assistant.files")
        chat.attachment = lambda file_id: {"file_pos": ("sales-oct.xlsx", b"x"), "file_xls": ("sales.xls", b"x"),
                                           "file_jpg": ("bill.jpg", b"x")}[file_id]
        chat.attachments = lambda: []
        originals = types.ModuleType("pearnly_cowork.files.service")
        originals.create = lambda workspace, name, content, key: self.created.append((workspace, name, key)) or {
            "name": "CWS-" + key[-8:]}
        tables = types.ModuleType("pearnly_cowork.pos_import.tables")
        tables.kind_of = lambda name: {"xlsx": "workbook", "csv": "delimited", "xls": "legacy"}.get(
            name.rsplit(".", 1)[-1], "")
        service = types.ModuleType("pearnly_cowork.pos_import.service")
        service.preview = lambda source, **kw: self.calls.append(("preview", source, kw)) or dict(self.preview)
        service.admit = lambda source, key, **kw: self.calls.append(("admit", source, key, kw)) or dict(self.admitted)
        package = types.ModuleType("pearnly_cowork.pos_import")
        package.service, package.tables = service, tables
        for name, module in {"pearnly_core.assistant.files": chat, "pearnly_cowork.files.service": originals,
                             "pearnly_cowork.pos_import": package, "pearnly_cowork.pos_import.service": service,
                             "pearnly_cowork.pos_import.tables": tables}.items():
            sys.modules[name] = module
        for parent in ("pearnly_core", "pearnly_core.assistant", "pearnly_cowork.files"):
            sys.modules.setdefault(parent, types.ModuleType(parent))
        sys.modules["pearnly_core.assistant"].files = chat
        sys.modules["pearnly_cowork.files"].service = originals
        search = types.ModuleType("pearnly_cowork.connections.account_search")
        search.usable = lambda adapter, rows: rows
        sys.modules["pearnly_cowork.connections.account_search"] = search
        tools.connections.targets = lambda name: {"account_sets": [{"source_id": "K"}]}
        tools.scope.default_workspace = lambda: "WS"
        self.preview = {"status": "ready", "days": []}
        self.admitted = {"status": "ok", "histories": ["H1", "H2"], "days": [{"document": "H1", "date": "2026-10-13"}]}

    def _import(self, **extra):
        return json.loads(tools.pos_import({"files": ["file_pos"], "connection": "C1", "account_set_key": "K", **extra}))

    def test_one_fixed_key_per_attachment_and_no_recognition(self):
        out = self._import(posting_kind="stock")["results"][0]
        self.assertEqual(self.created, [("WS", "sales-oct.xlsx", "assistant:pos:file_pos")])
        _kind, source, key, kw = self.calls[0]
        self.assertEqual((source, key), ("CWS-file_pos", "assistant:pos:file_pos"))
        self.assertEqual((kw["connection"], kw["account_set_key"], kw["posting_kind"], kw["workspace"]),
                         ("C1", "K", "stock", "WS"))
        self.assertEqual((out["status"], out["histories"]), ("ok", ["H1", "H2"]))

    def test_layout_questions_come_back_and_answers_are_remembered_before_entering(self):
        self.admitted = {"status": "layout_needed", "headers": ["Date"], "questions": [{"kind": "date_order"}]}
        out = self._import()["results"][0]
        self.assertEqual((out["status"], out["questions"]), ("layout_needed", [{"kind": "date_order"}]))
        self.calls.clear()
        self.admitted = {"status": "ok", "histories": ["H1"], "days": []}
        self._import(answers={"date_order": "dmy", "junk": 1})
        self.assertEqual([call[0] for call in self.calls], ["preview", "admit"])
        self.assertEqual(self.calls[0][2]["answers"], {"date_order": "dmy"})

    def test_old_excel_and_other_files_are_explained_without_a_source(self):
        out = json.loads(tools.pos_import({"files": ["file_xls", "file_jpg"], "connection": "C1",
                                           "account_set_key": "K"}))["results"]
        self.assertEqual([item["code"] for item in out], ["pos.xls_not_supported", "pos.type_unsupported"])
        self.assertEqual((self.created, self.calls), ([], []))

    def test_an_account_set_no_longer_in_the_list_is_refused(self):
        out = json.loads(tools.pos_import({"files": ["file_pos"], "connection": "C1", "account_set_key": "gone"}))
        self.assertIn("account_set_unavailable", out["results"][0]["reason"])
        self.assertEqual(self.created, [])

    def test_a_held_folder_file_is_answered_with_its_tasks_target_and_resumed(self):
        held = {"file": "F1", "filename": "pos.csv", "source_file": "CWS-9", "task": "R1", "title": "Shop sales",
                "workspace": "WS", "client": None, "connection": "C2", "account_set_key": "K2", "posting_kind": None}
        resumed = []
        with mock.patch.object(tools.rules, "pos_file", lambda name: held, create=True), \
                mock.patch.object(tools.rules, "resume_pos", lambda name: resumed.append(name) or True, create=True):
            out = json.loads(tools.pos_layout({"folder_file": "F1", "answers": {"amount_basis": "inclusive"}}))
        self.assertEqual((out["status"], out["resumed"], resumed), ("ready", True, ["F1"]))
        _kind, source, kw = self.calls[0]
        self.assertEqual((source, kw["connection"], kw["account_set_key"], kw["answers"]),
                         ("CWS-9", "C2", "K2", {"amount_basis": "inclusive"}))
        self.preview = {"status": "layout_needed", "questions": [{"kind": "column", "field": "amount"}]}
        resumed.clear()
        with mock.patch.object(tools.rules, "pos_file", lambda name: held, create=True), \
                mock.patch.object(tools.rules, "resume_pos", lambda name: resumed.append(name) or True, create=True):
            out = json.loads(tools.pos_layout({"folder_file": "F1"}))
        self.assertEqual((out["status"], out["resumed"], resumed), ("layout_needed", False, []))


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

    def test_offline_cc_desktop_is_not_ready(self):
        tools.destinations.online = lambda adapter, name: False
        self.assertEqual(json.loads(tools.intake_options({}))["targets"][0]["state"], "cc_desktop_offline")

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
        import datetime

        site_now = datetime.datetime(2026, 10, 11, 2, 2, 8)  # 站点时区（曼谷）的时间，不是容器的 UTC
        with mock.patch.object(tools.frappe, "utils", types.SimpleNamespace(now_datetime=lambda: site_now), create=True):
            out = json.loads(tools.export_review_workbook({"documents": ["H1", "H1", "H2"]}))
        self.assertEqual(self.calls[1][1], "cowork-review-20261011-020208.xlsx")
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
        tools.rules.save = lambda values, cc_session=None: self.calls.append(("save", values)) or {
            "name": "R1", "title": values["title"], "enabled": 1, "defaults": {}, "remind": 0}
        tools.rules.set_enabled = lambda name, enabled=None, remind=None, cc_session=None: self.calls.append(
            ("enabled", name, enabled)) or {"name": name, "enabled": enabled}
        tools.rules.delete = lambda names: self.calls.append(("delete", json.loads(names))) or {"deleted": 1}

    def test_options_list_the_open_folders_trimmed_and_searchable(self):
        out = json.loads(tools.folder_task_options({"connection": "EXP1"}))
        root = out["computers"][0]["roots"][0]
        self.assertEqual((out["computers"][0]["computer"], root["root_id"], root["folders_total"], len(root["folders"])),
                         ("DESKTOP-1", "r1", 79, tools.FOLDERS_SHOWN))
        found = json.loads(tools.folder_task_options({"connection": "EXP1", "search": "2026-07"}))
        self.assertEqual(found["computers"][0]["roots"][0]["folders"], ["2026-07"])

    def test_options_give_the_reported_file_counts(self):
        """小助手报了各文件夹现有几份就带上（CC 建任务前能说出来）：数过的（有 counts 键，哪怕是空的）没文件的文件夹给 0；
        没报过的（没有 counts 键）不给，不拿 0 冒充（interfaces-2）。"""
        tools.rules.options = lambda connection, key: {"readers": [{"connection": "EXP1", "label": "PC", "fresh": True, "roots": [
            {"id": "r1", "label": "C:\\Bills", "folders": ["Ice", "Mint"], "counts": {"": 9, "Ice": 7}, "partial": True},
            {"id": "r2", "label": "D:\\Scan", "folders": ["A"]},
            {"id": "r3", "label": "E:\\Empty", "folders": ["2026-11"], "counts": {}}]}]}
        roots = json.loads(tools.folder_task_options({"connection": "EXP1"}))["computers"][0]["roots"]
        self.assertEqual((roots[0]["files"], roots[0]["files_partial"]), ({"": 9, "Ice": 7, "Mint": 0}, True))
        self.assertNotIn("files", roots[1])
        self.assertEqual(roots[2]["files"], {"": 0, "2026-11": 0})
        self.assertNotIn("files_partial", roots[2])
        found = json.loads(tools.folder_task_options({"connection": "EXP1", "search": "mint"}))["computers"][0]["roots"]
        self.assertEqual(found[0]["files"], {"": 9, "Mint": 0})  # 只给列出来的文件夹（与根目录）
        description = next(t for t in tools.TOOLS if t["name"] == "cowork_folder_task_options")["description"]
        self.assertIn("without files means that computer has not reported counts", description)

    def test_the_enable_card_asks_what_actually_changes(self):
        # 2026-10-13：只改提醒时刻，卡片却问「要启用或停用这个定时任务吗？」——带了 enabled 才问启停，否则问「要更改吗」
        tool = next(t for t in tools.TOOLS if t["name"] == "cowork_set_folder_task_enabled")
        self.assertEqual(tool["ask"], "Change this folder task?")
        self.assertEqual(tool["ask_when"], {"enabled": "Turn this folder task on or off?"})

    def test_folder_task_tools_change_only_what_the_user_asked(self):
        # 2026-10-13：用户只让改提醒时刻，CC 却顺手把运行时间也改了
        described = {t["name"]: t["description"] for t in tools.TOOLS}
        for name in ("cowork_save_folder_task", "cowork_save_folder_tasks"):
            self.assertIn("change only the fields the user asked to change; any other value you pass must be the "
                          "task's current one", described[name].lower())
        self.assertIn("give only what the user asked to change", described["cowork_set_folder_task_enabled"])

    def test_save_enable_delete_go_through_the_task_service(self):
        values = {"title": "Ice", "connection": "EXP1", "direction": "purchase", "posting_kind": "stock",
                  "root_id": "r1", "folder": "2026-10", "run_day": 0, "run_time": "22:00", "ignored": 1}
        out = json.loads(tools.save_folder_task(values))
        self.assertEqual(out, {"status": "saved", "task": "R1", "title": "Ice", "enabled": 1, "defaults": {}, "remind": 0})
        self.assertNotIn("ignored", self.calls[0][1])
        tools.set_folder_task_enabled({"task": "R1", "enabled": False})
        tools.delete_folder_tasks({"tasks": ["R1"]})
        self.assertEqual(self.calls[1:], [("enabled", "R1", 0), ("delete", ["R1"])])

    def test_refusals_from_the_service_come_back_as_not_saved(self):
        def overlap(values, cc_session=None):
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
        rows = ([{"match_status": "matched", "record_id": "r", "match_layer": 1, "stmt_deposit": 10, "gl_debit": 10}] * 247
                # L6：两笔刷卡毛额 − 总账实际手续费 = 银行到账（同一组）
                + [{"match_status": "matched", "match_layer": 6, "group_no": 7, "stmt_deposit": "14782.29"},
                   {"match_status": "matched", "match_layer": 6, "group_no": 7, "gl_debit": "15093.58"},
                   {"match_status": "matched", "match_layer": 6, "group_no": 7, "gl_credit": "311.29"}]
                + [{"match_status": "stmt_deposit_only", "amount": 5}])
        api.detail = lambda kind, task, start, limit: {"rows": rows[start:start + limit], "total": len(rows),
                                                       "summary": {"matched_count": 250}}
        sheets = types.ModuleType("pearnly_cowork.recon_center.sheets")
        sheets.load = lambda kind, task, lang: {"task": {"name": task, "title": "BBL Feb", "status": "completed",
                                                         "questions": [{"rule": "card_fee"}]}, "sheets": ["big"]}
        labels = types.ModuleType("pearnly_cowork.reconciliation.export.labels")
        labels.layer_label = lambda layer, lang: {1: "L1-วันที่ตรงกัน", 6: "L6-card net (ledger)"}.get(layer, "")
        export = types.ModuleType("pearnly_cowork.reconciliation.export")
        export.labels = labels
        sys.modules["pearnly_cowork.reconciliation.export"] = export
        sys.modules["pearnly_cowork.reconciliation.export.labels"] = labels
        mods["pearnly_cowork.reconciliation"].export = export
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
        # 翻完全部明细（不只第一页 200 行），汇总用对账自己存的数
        self.assertEqual((out["title"], out["unmatched_total"], out["rows_total"]), ("BBL Feb", 1, 251))
        self.assertEqual(out["summary"], {"matched_count": 250})
        self.assertEqual(out["unmatched"], [{"match_status": "stmt_deposit_only", "amount": 5}])
        self.assertNotIn("sheets", out)
        self.assertEqual(out["questions"], [{"rule": "card_fee"}])

    def test_bank_result_sums_matched_lines_by_how_they_matched(self):
        """两边合计的差额要能从配上的组里讲清楚（2026-10-11 TTB：总账贷方多出的是 L6 组里总账记的刷卡手续费）。"""
        out = json.loads(tools.recon_result({"kind": "bank", "task": "T1"}))
        self.assertEqual(out["matched_by_kind"], [
            {"kind": "L1-วันที่ตรงกัน", "groups": 0, "lines": 247, "bank_deposits": "2470.00", "gl_debits": "2470.00"},
            {"kind": "L6-card net (ledger)", "groups": 1, "lines": 3, "bank_deposits": "14782.29",
             "gl_debits": "15093.58", "gl_credits": "311.29"}])

    def test_list_reads_the_tasks_the_page_lists(self):
        """列表接口回 {"tasks", "total"}（2026-10-10 测试：读错字段，三种对账都列成空的）。"""
        sys.modules["pearnly_cowork.recon_center.api"].tasks = lambda kind, start, limit: {
            "tasks": [{"name": "CVC-1", "title": "SINCERE 7"}], "total": 1}
        out = json.loads(tools.recon_list({"kind": "vat"}))
        self.assertEqual((out["total"], out["tasks"]), (1, [{"name": "CVC-1", "title": "SINCERE 7"}]))

    def test_vat_result_lists_the_lines_with_problems(self):
        """销项核查也给有问题的行（2026-10-10 测试：只有状态，智能体只好导出再读）。"""
        vat = types.ModuleType("pearnly_cowork.reconciliation.vat_check_service")
        vat.task = lambda task: types.SimpleNamespace(name=task)
        vat.stored_inputs = lambda doc: {}
        vat.compute = lambda inputs, lang: (b"", {"n_total": 3, "n_ok": 1, "rows": [
            {"status_key": "st_ok", "invoice_no": "A"},
            {"status_key": "st_no_inv", "status": "Report only", "invoice_no": "B", "dims": {"tax_id": ""}},
            {"status_key": "st_void", "invoice_no": "C"}]})
        sys.modules["pearnly_cowork.reconciliation.vat_check_service"] = vat
        sys.modules["pearnly_cowork.reconciliation"].vat_check_service = vat
        out = json.loads(tools.recon_result({"kind": "vat", "task": "CVC-1"}))
        self.assertEqual((out["rows_total"], out["unmatched_total"]), (3, 1))
        self.assertEqual(out["unmatched"], [{"status": "Report only", "invoice_no": "B"}])

    def test_export_goes_into_the_conversation_and_kinds_are_checked(self):
        out = json.loads(tools.recon_export({"kind": "bank", "task": "T1"}))
        self.assertEqual(out["file"], "/mnt/x/BBL Feb.xlsx")
        with self.assertRaises(frappe.ValidationError):
            tools.recon_list({"kind": "payroll"})



class TaxDraftTests(unittest.TestCase):
    """报税草稿：没有回读就叫 CC 先回读；回给模型的是要点（问题最多 30 条、按严重程度），工作簿放进对话；
    业务拒绝回 not_drafted；所得类型的回答 / 列出 / 删除走同一个工具。工具层只委托 `tax.service`。"""

    VAT = {"status": "drafted", "draft": "CTD-1", "kind": "vat_return", "period": "2026-09",
           "company": {"tax_id": "0105556000009"}, "boxes": {"5": "700.00", "13": ""}, "outcome": "payable",
           "issues_by_code": {"input_no_document": 40, "input_over_six_months": 1},
           "issues": [{"code": "input_no_document", "level": "info", "index": i} for i in range(40)]
           + [{"code": "input_over_six_months", "level": "error", "index": 99, "refnum": "T9",
               "detail": {"invoice_month": "2026-01"}}],
           "unreadable_rows": [], "sales_rows": [{"big": "x"}], "purchase_rows": [], "excluded_rows": []}

    def setUp(self):
        self.calls = []
        tax_service = types.ModuleType("pearnly_cowork.tax.service")
        tax_service.vat_draft = lambda *args: self.calls.append(("vat",) + args) or self.vat_result()
        tax_service.wht_draft = lambda *args: self.calls.append(("wht",) + args) or self.wht_result()
        tax_service.answer = lambda answers, draft: self.calls.append(("answer", answers, draft)) or {
            "status": "saved", "saved": ["payee:1"]}
        tax_service.forget = lambda payee, category: self.calls.append(("forget", payee, category)) or {
            "status": "removed"}
        tax_service.remembered = lambda: [{"scope": "payee:1", "income_type": "rent"}]
        tax_service.delete = lambda drafts: self.calls.append(("delete", drafts)) or {
            "status": "deleted", "deleted": drafts, "missing": []}
        tax_service.workbook = lambda summary, lang: (f"PP30-{summary['period']}.xlsx", b"PK")
        chat = types.ModuleType("pearnly_core.assistant.files")
        chat.add_to_conversation = lambda name, content, mime, origin="user": self.calls.append(
            ("attach", name, mime, origin)) or "/mnt/x/" + name
        for name, module in {"pearnly_cowork.tax.service": tax_service, "pearnly_core.assistant.files": chat}.items():
            sys.modules[name] = module
            parent, _, child = name.rpartition(".")
            setattr(sys.modules.setdefault(parent, types.ModuleType(parent)), child, module)
        self.vat_result = lambda: dict(self.VAT)
        self.wht_result = lambda: {
            "status": "drafted", "draft": "CTD-2", "period": "2026-09", "company": {"tax_id": "0105556000009"},
            "forms": {"pnd53": {"payees": 50, "sheets": 9, "base": "1.00", "tax": "0.03",
                                "lines": [{"row": f"H{i}", "payee_tax_id": "0105546015062", "rate": "3", "base": "1.00",
                                           "tax": "0.03", "issues": [{"code": "payment_date_assumed", "level": "info"}],
                                           "refs": [f"H{i}"], "include": True} for i in range(50)]},
                      "pnd3": {"payees": 0, "sheets": 0, "base": "0.00", "tax": "0.00", "lines": []}},
            "questions": [{"payee_tax_id": "0105546015062", "suggested": "service"}], "review": [], "suggestions": [],
            "notes": [], "totals": {"tax": "1.50"}, "issues_by_code": {}, "edits_rejected": []}
        frappe.commits = frappe.rollbacks = 0

    def test_the_three_tools_are_declared_and_delegate(self):
        declared = {tool["name"]: tool for tool in tools.TOOLS}
        for name in ("cowork_vat_draft", "cowork_wht_draft", "cowork_wht_answer"):
            self.assertIn(name, declared)
            self.assertNotIn("approval", declared[name])
        edits = declared["cowork_wht_draft"]["input_schema"]["properties"]["edits"]["items"]["properties"]
        answers = declared["cowork_wht_answer"]["input_schema"]["properties"]["answers"]["items"]["properties"]
        wht_rules = sys.modules["pearnly_cowork.tax.wht"]
        self.assertEqual(edits["income_type"]["enum"], list(wht_rules.INCOME_CODES))
        self.assertEqual(answers["income_type"]["enum"], list(wht_rules.INCOME_CODES))
        self.assertEqual(set(edits) - {"row", "reset"}, set(wht_rules.EDIT_KEYS))
        self.assertIn("cowork_express_readback", declared["cowork_vat_draft"]["description"])
        self.assertIn("never say it was filed", declared["cowork_wht_draft"]["description"])

    def test_box_labels_are_the_forms_own_labels(self):
        """2026-10-13 测试机：CC 把第 9 栏（ภาษีที่ชำระเกินเดือนนี้，多缴）说成了应纳税额。栏名取工作簿用的同一组词条。"""
        rows = {}
        tsv = pathlib.Path(__file__).resolve().parents[1] / "apps/pearnly_cowork/pearnly_cowork/i18n/messages.tsv"
        for line in tsv.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) == 5 and parts[4].startswith("cowork.tax.pp30.box."):
                rows[parts[4]] = {"en": parts[0], "th": parts[2]}
        real = tools._
        tools._ = lambda text, lang=None: rows[text][lang] if lang else text
        self.addCleanup(setattr, tools, "_", real)
        boxes = {str(number): "1.00" for number in range(1, 13)} | {str(number): "" for number in range(13, 17)}
        shown = {row["box"]: row for row in tools._vat_boxes(boxes)}
        self.assertEqual(list(shown), [str(number) for number in range(1, 13)])
        self.assertTrue(shown["9"]["th"].startswith("ภาษีที่ชำระเกินเดือนนี้"))
        self.assertTrue(shown["9"]["en"].startswith("Tax overpaid this month"))
        self.assertTrue(shown["8"]["th"].startswith("ภาษีที่ต้องชำระเดือนนี้"))
        self.assertTrue(shown["11"]["en"].startswith("Net tax payable"))

    def test_vat_draft_without_readback_asks_for_one(self):
        self.vat_result = lambda: {"status": "readback_needed", "period": "2026-09"}
        out = json.loads(tools.vat_draft({"connection": "C", "account_set_key": "K", "period": "2026-09"}))
        self.assertEqual(out["status"], "readback_needed")
        self.assertIn("cowork_express_readback", out["note"])
        self.assertFalse([call for call in self.calls if call[0] == "attach"])

    def test_vat_draft_gives_the_points_and_the_workbook(self):
        real = tools._
        tools._ = lambda text, lang=None: f"{lang}:{text}" if lang else text
        self.addCleanup(setattr, tools, "_", real)
        out = json.loads(tools.vat_draft({"connection": "C", "account_set_key": "K", "period": "2026-09",
                                          "excess_brought_forward": "100", "zero_vat_as": ""}))
        self.assertEqual(self.calls[0], ("vat", "C", "K", "2026-09", {"excess_brought_forward": "100"}))
        # 每栏带表上的栏名（泰文 + 英文）；空着的第 13 栏不给
        self.assertEqual(out["boxes"], [{"box": "5", "th": "th:cowork.tax.pp30.box.5", "en": "en:cowork.tax.pp30.box.5",
                                         "amount": "700.00"}])
        self.assertEqual(out["issues_total"], 41)
        self.assertEqual(len(out["issues"]), tools.TAX_ISSUES_SHOWN)
        self.assertEqual(out["issues"][0]["code"], "input_over_six_months")
        self.assertNotIn("index", out["issues"][0])
        self.assertNotIn("sales_rows", out)
        self.assertEqual(out["file"], "/mnt/x/PP30-2026-09.xlsx")
        # 草稿工作簿是工具产出：对账工具能取，识别工具不收（L3）
        self.assertEqual([call[3] for call in self.calls if call[0] == "attach"], ["tool"])
        self.assertEqual(frappe.commits, 1)
        self.calls.clear()
        out = json.loads(tools.vat_draft({"connection": "C", "account_set_key": "K", "period": "2026-09",
                                          "workbook": False}))
        self.assertNotIn("file", out)
        self.assertFalse([call for call in self.calls if call[0] == "attach"])

    def test_refusals_come_back_as_not_drafted(self):
        def refuse(*args):
            raise frappe.ValidationError("The tax month is not valid.")

        sys.modules["pearnly_cowork.tax.service"].vat_draft = refuse
        out = json.loads(tools.vat_draft({"connection": "C", "account_set_key": "K", "period": "x"}))
        self.assertEqual(out, {"status": "not_drafted", "reason": "The tax month is not valid."})
        self.assertEqual(frappe.rollbacks, 1)

    def test_wht_draft_lists_forms_questions_and_trims_lines(self):
        edits = [{"row": "H1", "include": False}]
        out = json.loads(tools.wht_draft({"company_tax_id": "0105556000009", "period": "2026-09", "edits": edits}))
        self.assertEqual(self.calls[0], ("wht", "0105556000009", "2026-09", edits, "", ""))
        pnd53 = out["forms"]["pnd53"]
        self.assertEqual((pnd53["lines_total"], len(pnd53["lines"])), (50, tools.TAX_LINES_SHOWN))
        self.assertEqual(pnd53["lines"][0], {"row": "H0", "payee_tax_id": "0105546015062", "rate": "3", "base": "1.00",
                                             "tax": "0.03", "issues": ["payment_date_assumed"]})
        self.assertEqual(out["questions"][0]["suggested"], "service")
        self.assertTrue(out["file"].endswith(".xlsx"))
        self.assertNotIn("excluded", out)

    def test_wht_draft_says_why_documents_read_were_not_counted(self):
        excluded = [{"ref": f"S{i}", "reason": "sales_document", "date": "2026-09-09"} for i in range(45)]
        excluded.insert(0, {"ref": "R1", "reason": "nothing_withheld", "date": "2026-09-02", "seller_name": "Mali"})
        base = self.wht_result
        self.wht_result = lambda: {**base(), "excluded": excluded}
        out = json.loads(tools.wht_draft({"company_tax_id": "0105556000009", "period": "2026-09"}))
        self.assertEqual((out["excluded_total"], out["excluded_by_reason"]),
                         (46, {"nothing_withheld": 1, "sales_document": 45}))
        self.assertEqual(len(out["excluded"]), tools.TAX_LINES_SHOWN)
        self.assertEqual(out["excluded"][0], excluded[0])
        description = {tool["name"]: tool for tool in tools.TOOLS}["cowork_wht_draft"]["description"]
        self.assertIn("excluded", description)

    def test_answers_list_and_remove(self):
        answers = [{"payee_tax_id": "0105546015062", "income_type": "transport"}]
        out = json.loads(tools.wht_answer({"answers": answers, "draft": "CTD-2"}))
        self.assertEqual((out["status"], self.calls[-1]), ("saved", ("answer", answers, "CTD-2")))
        self.assertIn("cowork_wht_draft", out["note"])
        self.assertEqual(json.loads(tools.wht_answer({"action": "list"})),
                         {"remembered": [{"scope": "payee:1", "income_type": "rent"}]})
        json.loads(tools.wht_answer({"action": "remove", "category": "ค่าเช่า"}))
        self.assertEqual(self.calls[-1], ("forget", "", "ค่าเช่า"))

    def test_rows_that_may_be_the_same_payment_name_each_other(self):
        line = {"row": "C1", "issues": [{"code": "pairing_unclear", "level": "warn", "candidates": ["I1", "I2"]},
                                        {"code": "income_type_missing", "level": "warn"}]}
        self.assertEqual(tools._tax_line(line), {"row": "C1", "issues": ["pairing_unclear", "income_type_missing"],
                                                 "check_with": ["I1", "I2"]})
        description = {tool["name"]: tool for tool in tools.TOOLS}["cowork_wht_draft"]["description"]
        for code in ("pairing_unclear", "buyer_unconfirmed", "check_with", "include true"):
            self.assertIn(code, description)

    def test_drafts_are_deleted_through_the_service(self):
        declared = {tool["name"]: tool for tool in tools.TOOLS}["cowork_tax_draft_delete"]
        self.assertEqual(declared["input_schema"]["required"], ["drafts"])
        out = json.loads(tools.tax_draft_delete({"drafts": ["CTD-1", "CTD-2"]}))
        self.assertEqual((out["deleted"], self.calls[-1]), (["CTD-1", "CTD-2"], ("delete", ["CTD-1", "CTD-2"])))
        self.assertEqual(frappe.commits, 1)

        def refuse(drafts):
            raise frappe.PermissionError("Not Permitted")

        sys.modules["pearnly_cowork.tax.service"].delete = refuse
        self.assertEqual(json.loads(tools.tax_draft_delete({"drafts": ["CTD-1"]})),
                         {"status": "not_deleted", "reason": "Not Permitted"})
        self.assertEqual(frappe.rollbacks, 1)


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
        desk.api_stock_count_lookup = lambda count, code: {"product": {"name": "ITEM-" + code},
                                                           "rows": [{"warehouse": "WH1", "location": "A-02"}]}
        desk.api_stock_count_recount = lambda count, product: self.calls.append(("recount", product)) or {"cleared": 1}
        desk.api_stock_count_add_entry = lambda count, qty, **kw: self.calls.append(
            ("add", qty, kw["item"], kw["warehouse"], kw["location"])) or {"entry": "E1"}

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
        self.assertEqual(self.calls, [("recount", "ITEM-B"), ("add", 4, "ITEM-B", "WH1", "A-02")])

    def test_count_uses_the_book_position_unless_told_otherwise(self):
        """仓库必填：没说就用账面位置（2026-10-10 测试 P002 / P004 记不进去）；说了在别处就按说的记。"""
        tools.stocktake_count({"count": "S1", "code": "P002", "qty": 9})
        tools.stocktake_count({"count": "S1", "code": "P004", "qty": 38, "warehouse": "WH2", "location": "C-03"})
        self.assertEqual(self.calls, [("add", 9, "ITEM-P002", "WH1", "A-02"), ("add", 38, "ITEM-P004", "WH2", "C-03")])

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



class ApprovalPreviewTests(unittest.TestCase):
    """要「允许」的那一步：卡片上先列改什么（2026-10-10 测试：只有卡片，点之前不知道要改什么）。"""

    def test_folder_task_change_lists_only_what_changes(self):
        current = {"name": "R1", "title": "QA 月度进项（T9SINCER）", "connection": "EXP1", "account_label": "70EXP · T.7 SINCER",
                   "direction": "purchase", "run_day": 1, "run_time": "22:00:00", "root_label": "Docs", "folder": "CC-QA-empty"}
        with mock.patch.object(tools.rules, "listing", return_value={"rules": [current]}, create=True), \
                mock.patch.object(tools.rules, "account_label", return_value="70EXP · T.9 SINCER", create=True):
            rows = tools.preview_save_folder_task({"name": "R1", "account_set_key": "c:\\70exp\\t9sincer",
                                                   "run_time": "22:00", "direction": "purchase"})
        self.assertEqual([(r["before"], r["after"]) for r in rows],
                         [("", "QA 月度进项（T9SINCER）"), ("70EXP · T.7 SINCER", "70EXP · T.9 SINCER")])


    def test_changing_a_task_keeps_what_was_not_mentioned(self):
        """改已有任务只说了账套：其余照库里现值（2026-10-10 测试缺入账方式被拒）。"""
        current = {"name": "R1", "title": "QA", "connection": "EXP1", "account_set_key": "k7", "direction": "purchase",
                   "posting_kind": "service", "reader": "EXP1", "root_id": "r", "folder": "f", "run_day": 1,
                   "run_time": "22:00:00", "defaults": {}}
        saved = []
        with mock.patch.object(tools.rules, "listing", return_value={"rules": [current]}, create=True), \
                mock.patch.object(tools.rules, "save", lambda values, cc_session=None: saved.append(values) or {"name": "R1"},
                                  create=True):
            tools.save_folder_task({"name": "R1", "account_set_key": "k9"})
        self.assertEqual(saved[0]["account_set_key"], "k9")
        self.assertEqual((saved[0]["posting_kind"], saved[0]["folder"], saved[0]["run_time"]), ("service", "f", "22:00"))


class OriginalTests(unittest.TestCase):
    """复核页右侧的原件：页图放进对话（同网页入口判权限、记访问日志）。"""

    def test_original_pages_go_into_the_conversation(self):
        added, logged = [], []
        files_mod = types.ModuleType("pearnly_cowork.files")
        access = types.ModuleType("pearnly_cowork.files.access")
        access.page_indices = lambda value: {1, 2}
        service = types.ModuleType("pearnly_cowork.files.service")
        service.page_source = lambda name, number: ("SRC", number)
        service.preview_link = lambda source, number: None
        service.render_page = lambda source, number: b"\xff\xd8\xff-page%d" % number
        originals = types.ModuleType("pearnly_cowork.ocr.originals")
        originals.preview_type = lambda data: "image/jpeg"
        chat = sys.modules.get("pearnly_core.assistant.files") or types.ModuleType("pearnly_core.assistant.files")
        chat.add_to_conversation = lambda name, content, mime: added.append((name, mime)) or "/mnt/x/" + name
        access_log = types.ModuleType("frappe.core.doctype.access_log.access_log")
        access_log.make_access_log = lambda **kw: logged.append(kw)
        requests = types.ModuleType("requests")
        requests.RequestException = type("RequestException", (Exception,), {})
        mods = {"requests": requests, "pearnly_cowork.files": files_mod, "pearnly_cowork.files.access": access,
                "pearnly_cowork.files.service": service, "pearnly_cowork.ocr": types.ModuleType("pearnly_cowork.ocr"),
                "pearnly_cowork.ocr.originals": originals, "pearnly_core.assistant.files": chat,
                "frappe.core": types.ModuleType("frappe.core"), "frappe.core.doctype": types.ModuleType("x"),
                "frappe.core.doctype.access_log": types.ModuleType("y"),
                "frappe.core.doctype.access_log.access_log": access_log}
        doc = types.SimpleNamespace(source_page_indices="[1, 2]", invoice_no="IV69/00095")
        with mock.patch.dict(sys.modules, mods), mock.patch.object(tools.storage, "read", return_value=doc, create=True):
            files_mod.access, files_mod.service = access, service
            mods["pearnly_cowork.ocr"].originals = originals
            out = json.loads(tools.document_original({"document": "H1"}))
        self.assertEqual(added, [("IV69-00095-p1.jpg", "image/jpeg"), ("IV69-00095-p2.jpg", "image/jpeg")])
        self.assertEqual((out["pages_total"], len(logged)), (2, 2))


# 卡片上的叫法：替身 `_` 原样返回词条键，这里给几条带占位符的，看拼出来的句子
TERMS = {"cowork.folder.pair": "{0}: {1}", "cowork.folder.waiting": "{0} files in the folder now",
         "cowork.folder.day": "Day {0}", "cowork.folder.lastDay": "Last day", "cowork.folder.remind.on": "On",
         "cowork.folder.remind.off": "Off", "cowork.folder.field.remind": "Reminder", "intake.dir.purchase": "Purchase",
         "cowork.folder.default.payment": "Payment", "cowork.folder.default.vat": "VAT",
         "cowork.folder.default.account": "Account", "cowork.folder.vat.claim": "Claim",
         "cowork.folder.vat.expense": "Expense", "cowork.folder.new": "New scheduled task",
         "cowork.folder.remind.sameDay": "Same day", "cowork.folder.remind.dayBefore": "Day before",
         "cowork.folder.remind.daysBefore": "{0} days before"}
DEFAULT_REMIND = "Day before 08:00 / Day before 14:00"


def _term(text):
    return TERMS.get(text, text)


class FolderTaskBatchTests(unittest.TestCase):
    """一次存几个定时任务（一张允许卡片）：任务列表只读一次、改任务以现值为底、默认值按类合并、提醒只在说了时带、
    来源对话取自当前对话（不从表单取）；全有或全无，不通过的照实回；卡片一个任务一行、最多 20 行。"""

    def setUp(self):
        self.current = {"name": "R1", "title": "Ice purchases", "connection": "EXP1", "adapter": "express",
                        "account_set_key": "c:\\70exp\\t9", "account_label": "70EXP · T.9", "direction": "purchase",
                        "posting_kind": "service", "payment": "", "reader": "EXP1", "root_id": "r1",
                        "root_label": "C:\\Bills", "folder": "Ice\\purchase", "run_day": 0, "run_time": "22:00:00",
                        "defaults": {"payment": "cash", "vat": "claim"}, "remind": 0}
        self.listed, self.calls = 0, []
        self.result = {"saved": [], "failed": []}

        def listing():
            self.listed += 1
            return {"rules": [dict(self.current)]}

        def save_many(items, cc_session=None):
            self.calls.append((items, cc_session))
            return self.result

        for name, value in {"listing": listing, "save_many": save_many,
                            "account_label": lambda connection, key: "70EXP · T.8",
                            "waiting": lambda reader, root, folder, direction=None, connection=None:
                                {"files": 7, "partial": True},
                            "master_names": lambda connection, key: {"accounts": {"5100": "Office supplies"}}}.items():
            patcher = mock.patch.object(tools.rules, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, name, value in ((tools.agent, "read", lambda reader: {"roots": [{"id": "r1", "label": "C:\\Bills"}]}),
                                    (frappe.local, "pearnly_assistant", {"session_id": "sesn_7"}),
                                    (frappe.db, "get_value", lambda *a, **k: "express")):
            patcher = mock.patch.object(target, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.new = {"title": "Ice expenses", "connection": "EXP1", "account_set_key": "c:\\70exp\\t8",
                    "direction": "purchase", "posting_kind": "service", "root_id": "r1", "folder": "Ice/expenses",
                    "run_day": 0, "run_time": "22:00", "defaults": {"payment": "credit", "account": "5100",
                                                                   "item": "__new__"}, "remind": True}

    def test_the_list_is_read_once_and_each_task_is_merged(self):
        self.result = {"saved": [{"name": "R1", "title": "Ice purchases", "enabled": 1, "remind": 1,
                                  "defaults": {"payment": "cash", "account": "5100"}},
                                 {"name": "R2", "title": "Ice sales", "enabled": 1, "remind": 0, "defaults": {}}],
                       "failed": []}
        commits, rollbacks = frappe.commits, frappe.rollbacks
        out = json.loads(tools.save_folder_tasks({"tasks": [
            {"name": "R1", "defaults": {"vat": "", "account": "5100"}, "remind": True, "cc_session": "sesn_other"},
            {"title": "Ice sales", "connection": "EXP1", "direction": "sales", "posting_kind": "service",
             "root_id": "r1", "folder": "Ice\\sales", "run_day": 5, "run_time": "09:30"}]}))
        self.assertEqual(self.listed, 1)
        (items, conversation), = self.calls
        self.assertEqual(conversation, "sesn_7")  # 来源对话取自当前对话，模型给的不算
        changed, created = items
        self.assertEqual((changed["posting_kind"], changed["folder"], changed["run_time"], changed["run_day"]),
                         ("service", "Ice\\purchase", "22:00", 0))
        self.assertEqual(changed["defaults"], {"payment": "cash", "account": "5100"})  # 按类合并，空串清掉 vat
        self.assertEqual(changed["remind"], 1)
        self.assertNotIn("cc_session", changed)
        self.assertNotIn("remind", created)  # 没说提醒就不带，由任务服务保留原值
        self.assertNotIn("defaults", created)
        self.assertEqual(out["status"], "saved")
        self.assertEqual(out["tasks"][0], {"task": "R1", "title": "Ice purchases", "enabled": 1,
                                           "defaults": {"payment": "cash", "account": "5100"}, "remind": 1})
        self.assertEqual((frappe.commits - commits, frappe.rollbacks - rollbacks), (1, 0))

    def test_a_refused_task_keeps_nothing_and_says_which(self):
        self.result = {"saved": [], "failed": [{"task": 2, "title": "Ice sales", "reason": "overlaps «Ice purchases»"}]}
        commits, rollbacks = frappe.commits, frappe.rollbacks
        out = json.loads(tools.save_folder_tasks({"tasks": [self.new, {"title": "Ice sales"}]}))
        self.assertEqual(out["status"], "not_saved")
        self.assertEqual(out["failed"], self.result["failed"])
        self.assertIn("whole list", out["reason"])
        self.assertEqual((frappe.commits - commits, frappe.rollbacks - rollbacks), (0, 1))

    def test_service_errors_come_back_as_not_saved(self):
        def refuse(items, cc_session=None):
            raise frappe.PermissionError("No access to this connection")

        with mock.patch.object(tools.rules, "save_many", refuse):
            out = json.loads(tools.save_folder_tasks({"tasks": [self.new]}))
        self.assertEqual((out["status"], out["reason"]), ("not_saved", "No access to this connection"))

    def test_too_many_tasks_or_misspelled_default_kinds_are_refused_before_saving(self):
        out = json.loads(tools.save_folder_tasks({"tasks": [dict(self.new) for _i in range(21)]}))
        self.assertEqual(out["status"], "not_saved")
        self.assertIn("20", out["reason"])
        out = json.loads(tools.save_folder_tasks({"tasks": [self.new, {"name": "R1", "defaults": {"acount": "5100"}}]}))
        self.assertEqual(out["status"], "not_saved")
        self.assertEqual((out["failed"][0]["task"], out["failed"][0]["title"]), (2, "R1"))
        self.assertIn("acount", out["failed"][0]["reason"])
        with mock.patch.object(tools.rules, "save", lambda *a, **k: self.fail("must not save"), create=True):
            single = json.loads(tools.save_folder_task({"name": "R1", "defaults": {"acount": "5100"}}))
        self.assertEqual(single["status"], "not_saved")
        self.assertEqual(self.calls, [])
        with self.assertRaises(frappe.ValidationError):
            tools.save_folder_tasks({"tasks": []})

    def test_the_card_has_one_row_per_task(self):
        with mock.patch.object(tools, "_", _term):
            rows = tools.preview_save_folder_tasks({"tasks": [self.new, {"name": "R1", "run_time": "23:00",
                                                                         "defaults": {"vat": "expense"}}]})
        self.assertEqual(self.listed, 1)
        new, changed = rows
        self.assertEqual((new["field"], new["before"]), ("Ice expenses", ""))
        self.assertEqual(new["after"].split(" · "), [
            "70EXP", "T.8", "Purchase", "Service", "C:\\Bills\\Ice\\expenses", "Last day", "22:00", "Payment: Credit",
            "Account: 5100", "Office supplies", "Reminder: Day before 08:00 / Day before 14:00",
            "7+ files in the folder now"])
        # 改已有任务只写变了的；没换文件夹不说份数；不适用的默认值（服务类不用 item）不上卡片
        self.assertEqual(changed, {"field": "Ice purchases", "before": "22:00 · VAT: Claim", "after": "23:00 · VAT: Expense"})
        many = tools.preview_save_folder_tasks({"tasks": [dict(self.new) for _i in range(25)]})
        self.assertEqual(len(many), tools.FOLDER_BATCH_MAX)

    def test_single_card_lists_defaults_month_end_reminder_and_files(self):
        with mock.patch.object(tools, "_", _term):
            rows = tools.preview_save_folder_task(self.new)
            shown = {row["field"]: (row["before"], row["after"]) for row in rows}
            self.assertEqual(shown["cowork.folder.field.day"], ("", "Last day"))
            self.assertEqual(shown["Payment"], ("", "Credit"))
            self.assertEqual(shown["Account"], ("", "5100 · Office supplies"))
            self.assertEqual(shown["Reminder"], ("Off", DEFAULT_REMIND))
            self.assertEqual(rows[-1], {"field": "", "before": "", "after": "7+ files in the folder now"})
            # 小助手没报过份数：不说，不拿 0 冒充；MR.ERP 任务没有默认处理
            with mock.patch.object(tools.rules, "waiting", lambda *a: None), \
                    mock.patch.object(frappe.db, "get_value", lambda *a, **k: "mrerp"):
                rows = tools.preview_save_folder_task(self.new)
            self.assertNotIn("", [row["field"] for row in rows])
            self.assertFalse({"Payment", "Account"} & {row["field"] for row in rows})
            with mock.patch.object(tools.rules, "waiting", lambda *a: {"files": 0, "partial": False}):
                rows = tools.preview_save_folder_task(self.new)
            self.assertEqual(rows[-1]["after"], "0 files in the folder now")

    def test_a_task_that_continues_a_paused_one_says_so_instead_of_the_file_count(self):
        """business-logic-1：新任务的文件夹与停用的任务重叠（任务服务会置成只收此后放进来的），卡片说接续哪个任务，
        不说「现有 N 份，到点一起处理」；单个、批量两种卡片同一句。"""
        asked = []

        def continues_from(reader, root, folder, current=None):
            asked.append((reader, root, folder, current))
            return "Old purchases"

        with mock.patch.object(tools, "_", lambda text: {"cowork.folder.continues": "Continues «{0}»"}.get(text, _term(text))), \
                mock.patch.object(tools.rules, "continues_from", continues_from):
            single = tools.preview_save_folder_task(self.new)
            batch = tools.preview_save_folder_tasks({"tasks": [self.new, {"name": "R1", "folder": "Ice\\old"}]})
        self.assertEqual(single[-1], {"field": "", "before": "", "after": "Continues «Old purchases»"})
        self.assertNotIn("7+ files in the folder now", json.dumps(single))
        self.assertEqual(batch[0]["after"].split(" · ")[-1], "Continues «Old purchases»")
        self.assertEqual(batch[1]["after"].split(" · ")[-1], "Continues «Old purchases»")
        # 改已有任务换文件夹：不拿它自己比
        self.assertEqual(asked, [("EXP1", "r1", "Ice/expenses", None), ("EXP1", "r1", "Ice/expenses", None),
                                 ("EXP1", "r1", "Ice\\old", "R1")])

    def test_single_save_passes_the_conversation_and_reports_defaults_not_kept(self):
        saved = []
        with mock.patch.object(tools.rules, "save", lambda values, cc_session=None: saved.append((values, cc_session))
                               or {"name": "R3", "title": "Ice expenses", "enabled": 1, "remind": 1,
                                   "defaults": {"payment": "credit", "account": "5100"}}, create=True):
            out = json.loads(tools.save_folder_task({**self.new, "cc_session": "sesn_other"}))
            values, conversation = saved[0]
            self.assertEqual((conversation, values["remind"]), ("sesn_7", 1))
            self.assertNotIn("cc_session", values)
            self.assertEqual(out["defaults_not_applicable"], ["item"])
            tools.save_folder_task({key: value for key, value in self.new.items() if key != "remind"})
            self.assertNotIn("remind", saved[1][0])

    def test_reminder_alone_can_be_turned_on(self):
        calls = []
        with mock.patch.object(tools.rules, "set_enabled", lambda name, enabled=None, remind=None, cc_session=None:
                               calls.append((name, enabled, remind, cc_session)) or {"name": name, "enabled": 1,
                                                                                      "remind": remind}, create=True):
            out = json.loads(tools.set_folder_task_enabled({"task": "R1", "remind": True}))
            self.assertEqual(calls, [("R1", None, 1, "sesn_7")])
            self.assertEqual((out["status"], out["remind"]), ("saved", 1))
            refused = json.loads(tools.set_folder_task_enabled({"task": "R1"}))
            self.assertEqual((refused["status"], len(calls)), ("not_saved", 1))
        with mock.patch.object(tools, "_", _term):
            rows = tools.preview_set_folder_task_enabled({"task": "R1", "remind": True})
        self.assertEqual([(r["field"], r["before"], r["after"]) for r in rows],
                         [("cowork.folder.field.title", "", "Ice purchases"), ("Reminder", "Off", DEFAULT_REMIND)])

    def test_chosen_reminder_times_go_to_the_service_and_show_on_the_card(self):
        """2026-10-13：提醒时刻只在说了时带给任务服务；卡片写「当天 / 前 N 天 + 时间」，只给提醒时刻也是打开提醒。"""
        times = [{"days_before": 0, "time": "07:05"}, {"days_before": 3, "time": "16:45"}]
        saved = []
        with mock.patch.object(tools.rules, "save", lambda values, cc_session=None: saved.append(values)
                               or {"name": "R1", "title": "Ice purchases", "enabled": 1, "remind": 1,
                                   "remind_times": times, "defaults": {}}, create=True):
            out = json.loads(tools.save_folder_task({"name": "R1", "remind_times": times}))
        self.assertEqual((saved[0]["remind_times"], "remind" in saved[0]), (times, False))
        self.assertEqual((out["remind"], out["remind_times"]), (1, times))
        with mock.patch.object(tools, "_", _term):
            single = {r["field"]: (r["before"], r["after"]) for r in
                      tools.preview_save_folder_task({"name": "R1", "remind_times": times})}
            self.assertEqual(single["Reminder"], ("Off", "3 days before 16:45 / Same day 07:05"))
            # 提醒开着、只换时刻：现在 → 改后；说了关就写关
            self.current.update(remind=1, remind_times=[{"days_before": 1, "time": "08:00"}])
            batch = tools.preview_save_folder_tasks({"tasks": [
                {"name": "R1", "remind_times": [{"days_before": 1, "time": "08:00"}, {"days_before": 2, "time": "09:00"}]}]})
            self.assertEqual((batch[0]["before"], batch[0]["after"]),
                             ("Reminder: Day before 08:00", "Reminder: 2 days before 09:00 / Day before 08:00"))
            off = {r["field"]: r["after"] for r in tools.preview_save_folder_task({"name": "R1", "remind": False,
                                                                                    "remind_times": times})}
            self.assertEqual(off["Reminder"], "Off")
            # 形状不对：卡片照原样给人看（保存时任务服务会拒）
            odd = {r["field"]: r["after"] for r in tools.preview_save_folder_task(
                {"name": "R1", "remind_times": [{"days_before": 9, "time": "08:00"}]})}
            self.assertIn('"days_before": 9', odd["Reminder"])
            rows = tools.preview_set_folder_task_enabled({"task": "R1", "remind_times": [{"days_before": 0, "time": "06:00"}]})
        self.assertEqual(rows[-1], {"field": "Reminder", "before": "Day before 08:00", "after": "Same day 06:00"})
        calls = []
        with mock.patch.object(tools.rules, "set_enabled", lambda name, enabled=None, remind=None, remind_times=None,
                               cc_session=None: calls.append((name, enabled, remind, remind_times)) or
                               {"name": name, "enabled": 1, "remind": 1, "remind_times": remind_times}, create=True):
            out = json.loads(tools.set_folder_task_enabled({"task": "R1", "remind_times": times}))
        self.assertEqual(calls, [("R1", None, None, times)])
        self.assertEqual((out["status"], out["remind_times"]), ("saved", times))


def _history_row(name, direction, kind, subtotal, vat, total):
    fields = {"direction": direction, "document_type": kind, "subtotal": subtotal, "vat": vat}
    return {"name": name, "total_amount": total, "push_connection": "EXP1", "pages": json.dumps([{"fields": fields}])}


class ListAndRunsTests(unittest.TestCase):
    """单据列表：翻页、按连接 / 账套筛、每行带识别出的税前额与税额、按方向筛与 Decimal 合计；运行记录带推送统计。"""

    def setUp(self):
        history = types.ModuleType("pearnly_cowork.contracts.history")
        history.primary_page_fields = lambda pages: (pages[0].get("fields") or {}) if pages else {}
        push = types.ModuleType("pearnly_cowork.contracts.push")
        push.normalize_direction = lambda value: value if value in ("purchase", "sales") else None
        contracts = sys.modules.setdefault("pearnly_cowork.contracts", types.ModuleType("pearnly_cowork.contracts"))
        patcher = mock.patch.dict(sys.modules, {"pearnly_cowork.contracts.history": history,
                                                "pearnly_cowork.contracts.push": push})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name, module in (("history", history), ("push", push)):
            patcher = mock.patch.object(contracts, name, module, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        frappe.rows = [_history_row("D1", "purchase", "tax_invoice", "1,000.10", "70.00", "1,070.10"),
                       _history_row("D2", "sales", "tax_invoice", "500", "35", "535"),
                       _history_row("D3", "purchase", "credit_note", "100", "7", "107"),
                       _history_row("D4", "", "receipt", "10", "", "10"),
                       _history_row("D5", "purchase", "receipt", "200.20", None, "abc")]
        self.addCleanup(setattr, frappe, "rows", [])
        frappe.calls.clear()

    def test_pages_and_target_filters(self):
        out = json.loads(tools.list_documents({"offset": 2, "limit": 2, "connection": "EXP1",
                                               "account_set_key": "c:\\70exp\\t9"}))
        listed = frappe.calls[0]
        self.assertEqual((listed["limit_start"], listed["limit_page_length"]), (2, 2))
        self.assertEqual((listed["filters"]["push_connection"], listed["filters"]["push_account_set"]),
                         ("EXP1", "c:\\70exp\\t9"))
        self.assertEqual([d["name"] for d in out["documents"]], ["D3", "D4"])
        self.assertEqual((out["total"], out["next_offset"]), (5, 4))  # 这一页装满了才数总数
        self.assertEqual(frappe.calls[1]["fields"], [{"COUNT": "name", "as": "total"}])
        third = out["documents"][0]
        self.assertEqual((third["document_type"], third["direction"], third["subtotal"], third["vat"]),
                         ("credit_note", "purchase", "100", "7"))
        self.assertNotIn("pages", third)
        last = json.loads(tools.list_documents({"offset": 4, "limit": 2}))
        self.assertEqual(([d["name"] for d in last["documents"]], last["total"], last["next_offset"]), (["D5"], 5, None))

    def test_direction_and_totals_are_summed_with_decimal(self):
        out = json.loads(tools.list_documents({"direction": "purchase", "totals": True, "limit": 1}))
        self.assertEqual(([d["name"] for d in out["documents"]], out["total"], out["next_offset"]), (["D1"], 3, 1))
        self.assertEqual(out["without_direction"], 1)
        self.assertEqual(out["totals"]["documents"], {"count": 2, "subtotal": "1200.30", "vat": "70.00",
                                                      "total_amount": "1070.10",
                                                      "without_value": {"vat": 1, "total_amount": 1}})
        self.assertEqual(out["totals"]["credit_notes"], {"count": 1, "subtotal": "100.00", "vat": "7.00",
                                                         "total_amount": "107.00"})
        self.assertNotIn("complete", out)

    def test_only_the_newest_documents_are_scanned(self):
        with mock.patch.object(tools, "LIST_SCAN_MAX", 4), mock.patch.object(tools, "LIST_SCAN_PAGE", 2):
            out = json.loads(tools.list_documents({"totals": True}))
        self.assertEqual((out["total"], out["complete"]), (4, False))
        self.assertIn("newest 4", out["note"])

    def test_failed_and_search_are_filtered_after_reading(self):
        frappe.rows[1]["last_push_reason"] = "choice_needed:account"
        out = json.loads(tools.list_documents({"search": "Ice", "push_state": "failed"}))
        self.assertEqual([d["name"] for d in out["documents"]], ["D2"])
        # 筛选按库里的原值，交给模型的是按用户语言翻好的那句（同网页与结果表格）
        self.assertEqual(out["documents"][0]["last_push_reason"], "said:choice_needed:account")

    def test_push_reasons_reach_the_model_in_the_users_words(self):
        # 存的是码或 10-12 前的旧英文原句（里面还是旧称）；交给模型前翻成用户语言，模型就不会照着说旧称
        frappe.rows[0]["last_push_reason"] = "push.companion_account_unreported"
        docs = json.loads(tools.list_documents({"limit": 2}))["documents"]
        self.assertEqual([d["last_push_reason"] for d in docs], ["said:push.companion_account_unreported", None])
        detail = tools._detail({"name": "D1", "last_push_reason": "document_policy_needed_transfer_slip",
                                "push_connection": "EXP1", "pages": []})
        self.assertEqual(detail["push_reason"], "said:document_policy_needed_transfer_slip")
        self.assertEqual(detail["handling_choice_needed"], "transfer_slip")  # 认码仍按原值

    def test_runs_and_tasks_carry_results(self):
        run = {"name": "RUN1", "trigger": "schedule", "found": 3, "results": {"entered": 2, "attention": 1, "moving": 0},
               "states": {"done": 3}}
        task = {"name": "R1", "defaults": {"vat": "claim"}, "remind": 1, "include_existing": 1, "waiting": 5,
                "remind_times": [{"days_before": 1, "time": "08:00"}],
                "waiting_partial": True, "rule_owner": "x@example.com"}
        with mock.patch.object(tools.rules, "runs", lambda name: {"rule": {"name": name}, "runs": [run]}), \
                mock.patch.object(tools.rules, "listing", lambda: {"rules": [task]}):
            runs = json.loads(tools.folder_task_runs({"task": "R1"}))["runs"][0]
            listed = json.loads(tools.folder_tasks({}))["tasks"][0]
        self.assertEqual((runs["results"], runs["states"]), (run["results"], run["states"]))
        self.assertNotIn("name", runs)
        keys = ("defaults", "remind", "remind_times", "include_existing", "waiting", "waiting_partial")
        self.assertEqual({key: listed[key] for key in keys}, {key: task[key] for key in keys})
        self.assertNotIn("rule_owner", listed)


class FolderTaskDeclarationTests(unittest.TestCase):
    """工具说明与参数：默认值各类与任务规则同一组、批量上限不超过卡片行数、改字段说明与推送行为一致。"""

    def _tool(self, name):
        return next(t for t in tools.TOOLS if t["name"] == name)

    def test_default_kinds_match_the_task_rules_on_both_save_tools(self):
        single = self._tool("cowork_save_folder_task")["input_schema"]["properties"]["defaults"]
        batch = self._tool("cowork_save_folder_tasks")["input_schema"]["properties"]["tasks"]["items"]["properties"]
        for defaults in (single, batch["defaults"]):
            self.assertEqual(set(defaults["properties"]), set(tools.folder_contracts.KINDS))
            self.assertFalse(defaults["additionalProperties"])
        self.assertIn("remind", batch)

    def test_the_batch_fits_on_one_card(self):
        import re

        core = (ROOT / "apps/pearnly_core/pearnly_core/assistant/tools.py").read_text(encoding="utf-8")
        rows = int(re.search(r"^PREVIEW_ROWS = (\d+)", core, re.M).group(1))
        tool = self._tool("cowork_save_folder_tasks")
        self.assertEqual(tool["input_schema"]["properties"]["tasks"]["maxItems"], tools.FOLDER_BATCH_MAX)
        self.assertLessEqual(tools.FOLDER_BATCH_MAX, rows)
        self.assertTrue(tool["approval"])
        self.assertTrue(callable(getattr(tools, tool["preview"].rpartition(".")[2])))
        switch = self._tool("cowork_set_folder_task_enabled")["input_schema"]
        self.assertEqual((switch["required"], "remind" in switch["properties"]), (["task"], True))

    def test_reminder_times_are_declared_on_every_task_tool(self):
        """2026-10-13：最多 3 个「运行日前 0–7 天 + HH:MM」，单个、批量保存与启停三个工具同一份说明。"""
        single = self._tool("cowork_save_folder_task")["input_schema"]["properties"]["remind_times"]
        batch = self._tool("cowork_save_folder_tasks")["input_schema"]["properties"]["tasks"]["items"]["properties"]
        switch = self._tool("cowork_set_folder_task_enabled")["input_schema"]["properties"]["remind_times"]
        self.assertIs(single, batch["remind_times"])
        self.assertIs(single, switch)
        self.assertEqual((single["type"], single["maxItems"]), ("array", tools.folder_contracts.REMIND_MAX))
        item = single["items"]
        self.assertEqual((item["required"], item["additionalProperties"]), (["days_before", "time"], False))
        self.assertEqual((item["properties"]["days_before"]["minimum"], item["properties"]["days_before"]["maximum"]),
                         (0, tools.folder_contracts.REMIND_DAYS))
        for text in ("Bangkok", "turns the reminder on", "keeps the current times", "earlier than run_time"):
            self.assertIn(text, single["description"])
        self.assertNotIn("08:00 and 14:00 Bangkok time, to put", batch["remind"]["description"])
        self.assertIn("remind_times", self._tool("cowork_set_folder_task_enabled")["description"])

    def test_descriptions_match_what_the_tools_do(self):
        edit = self._tool("cowork_edit_document")["description"]
        self.assertNotIn("must be pushed again", edit)
        self.assertIn("not pushed again", edit)
        self.assertIn("already_success", self._tool("cowork_push_documents")["description"])
        self.assertIn("task_defaults", self._tool("cowork_push_choices")["description"])
        # 新任务处理文件夹里还没处理过的，包括建任务前已有的（不再说只收新放入的）
        self.assertNotIn("new photos", self._tool("cowork_save_folder_task")["description"])
        self.assertNotIn("only handles files put in the folder after now",
                         self._tool("cowork_set_folder_task_enabled")["description"])

    def test_task_defaults_come_back_with_push_choices(self):
        doc = types.SimpleNamespace(push_connection="EXP1", push_account_set="K", posting_kind="service")
        fixed = {"needed": [], "options": {}, "fixed": {"account": "5100"}}
        with mock.patch.object(tools.storage, "read", lambda name: doc, create=True), \
                mock.patch.object(tools.application, "choices_view", lambda *a: fixed, create=True):
            out = json.loads(tools.push_choices({"document": "H1"}))
            self.assertEqual(out["task_defaults"], {"account": "5100"})
            fixed.pop("fixed")
            self.assertNotIn("task_defaults", json.loads(tools.push_choices({"document": "H1"})))


class FolderFilesTests(unittest.TestCase):
    """L2：CC 从电脑上已授权的文件夹列文件、取文件进对话；只走 `folder_intake.fetch` 的公开入口，取来的文件标「从文件夹」，
    电脑离线或版本太旧时照实说，还在读时不干等。"""

    def setUp(self):
        self.calls, self.cache = [], {}
        self.states, self.listings, self.contents = {}, {}, {}
        self.made = {}
        fetch = types.ModuleType("pearnly_cowork.folder_intake.fetch")
        fetch.files = lambda reader, root, folder="", **kw: self.calls.append(("files", reader, root, folder, kw)) \
            or self.made.get("files", {"request": "L1", "status": "requested"})
        fetch.fetch = lambda reader, root, path, **kw: self.calls.append(("fetch", reader, root, path, kw)) \
            or self.made.get(path, {"request": "F-" + path, "status": "requested"})
        fetch.state = lambda request: self.states.get(request, {"request": request, "status": "done", "code": ""})
        fetch.listing = lambda request: self.calls.append(("listing", request)) or self.listings[request]
        fetch.content = lambda request: self.calls.append(("content", request)) or self.contents[request]
        sys.modules["pearnly_cowork.folder_intake.fetch"] = fetch
        sys.modules["pearnly_cowork.folder_intake"].fetch = fetch
        chat = sys.modules.setdefault("pearnly_core.assistant.files", types.ModuleType("pearnly_core.assistant.files"))
        self.chat = chat
        chat.add_to_conversation = lambda name, content, mime, origin="tool": self.calls.append(
            ("attach", name, content, mime, origin)) or f"/mnt/session/uploads/file_{len(self.calls)}/{name}"
        sys.modules.setdefault("pearnly_core", types.ModuleType("pearnly_core"))
        sys.modules.setdefault("pearnly_core.assistant", types.ModuleType("pearnly_core.assistant")).files = chat
        toolrun = types.ModuleType("pearnly_core.assistant.toolrun")
        toolrun.fresh = lambda: self.calls.append(("fresh",))
        sys.modules["pearnly_core.assistant.toolrun"] = toolrun
        sys.modules["pearnly_core.assistant"].toolrun = toolrun
        frappe.cache = types.SimpleNamespace(get_value=self.cache.get,
                                             set_value=lambda key, value, expires_in_sec=None: self.cache.__setitem__(key, value),
                                             delete_value=lambda key: self.cache.pop(key, None))
        frappe.local.pearnly_assistant = {"session_id": "sesn_1"}

    def tearDown(self):
        frappe.local.pearnly_assistant = None

    def _listing(self, count):
        return {"files": [{"path": f"ACME\\f{n}.pdf", "name": f"f{n}.pdf", "size": n, "modified": "2026-10-13 09:00",
                           "result_folder": n == 0} for n in range(count)], "truncated": True, "scanned": count}

    def test_files_are_listed_once_and_paged_from_the_same_listing(self):
        self.listings["L1"] = self._listing(150)
        out = json.loads(tools.folder_files({"reader": "C1", "root_id": "r1", "folder": "ACME", "search": "f",
                                             "extensions": [".pdf"]}))
        self.assertEqual(self.calls[0], ("files", "C1", "r1", "ACME", {"search": "f", "extensions": [".pdf"],
                                                                         "scope": "sesn_1"}))
        self.assertEqual((len(out["files"]), out["total"], out["next_offset"], out["truncated"]), (100, 150, 100, True))
        self.assertEqual(out["files"][0], {"path": "ACME\\f0.pdf", "name": "f0.pdf", "size_bytes": 0,
                                           "modified": "2026-10-13 09:00", "result_folder": True})
        self.calls.clear()
        page = json.loads(tools.folder_files({"reader": "C1", "root_id": "r1", "folder": "ACME", "search": "f",
                                              "extensions": [".pdf"], "offset": 100}))
        self.assertEqual(self.calls, [])  # 翻页不再请电脑重列
        self.assertEqual((len(page["files"]), page["next_offset"]), (50, None))

    def test_an_offline_computer_or_a_slow_listing_is_reported_without_waiting_forever(self):
        self.made["files"] = {"status": "waiting_for_cc", "reason": "offline"}
        out = json.loads(tools.folder_files({"reader": "C1", "root_id": "r1"}))
        self.assertEqual((out["status"], out["reason"]), ("waiting_for_cc", "offline"))
        self.assertIn("offline", out["note"])
        self.made.pop("files")
        self.states["L1"] = {"request": "L1", "status": "leased", "code": ""}
        with mock.patch.object(tools, "FOLDER_LIST_WAIT_SECONDS", 0), mock.patch.object(tools.time, "sleep"):
            out = json.loads(tools.folder_files({"reader": "C1", "root_id": "r1"}))
        self.assertEqual(out["status"], "reading")
        self.assertIn("same input", out["note"])
        self.states["L1"] = {"request": "L1", "status": "failed", "code": "folder_missing"}
        self.assertEqual(json.loads(tools.folder_files({"reader": "C1", "root_id": "r1"})),
                         {"status": "failed", "reason": "folder_missing"})

    def test_fetched_files_join_the_chat_as_folder_files(self):
        self.contents["F-ACME\\kbank.pdf"] = ("kbank.pdf", b"%PDF")
        self.contents["F-ACME\\gl.xlsx"] = ("gl.xlsx", b"PK")
        self.states["F-ACME\\gone.pdf"] = {"request": "F-ACME\\gone.pdf", "status": "failed", "code": "not_found"}
        out = json.loads(tools.folder_fetch({"reader": "C1", "root_id": "r1",
                                             "paths": ["ACME\\kbank.pdf", "ACME\\gl.xlsx", "ACME\\gone.pdf",
                                                       "ACME\\kbank.pdf"]}))
        attached = [call for call in self.calls if call[0] == "attach"]
        self.assertEqual([call[1:] for call in attached], [
            ("kbank.pdf", b"%PDF", "application/pdf", "folder"),
            ("gl.xlsx", b"PK", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "folder")])
        files = out["files"]
        self.assertEqual([row["status"] for row in files], ["added", "added", "failed"])
        self.assertTrue(files[0]["file"].startswith("/mnt/session/uploads/") and files[0]["filename"] == "kbank.pdf")
        self.assertEqual((files[0]["size_bytes"], files[2]["reason"]), (4, "not_found"))
        self.assertEqual([call[4]["scope"] for call in self.calls if call[0] == "fetch"], ["sesn_1"] * 3)

    def test_two_spellings_of_one_file_are_fetched_once(self):
        for spelling in ("ACME/x.pdf", "ACME\\x.pdf"):
            self.made[spelling] = {"request": "F-X", "status": "requested"}
        self.contents["F-X"] = ("x.pdf", b"%PDF")
        out = json.loads(tools.folder_fetch({"reader": "C1", "root_id": "r1", "paths": ["ACME/x.pdf", "ACME\\x.pdf"]}))
        self.assertEqual([row["status"] for row in out["files"]], ["added", "added"])
        self.assertEqual([call[0] for call in self.calls if call[0] in ("content", "attach")], ["content", "attach"])

    def test_calling_again_while_some_are_still_reading_does_not_bring_the_added_ones_twice(self):
        """一次取两份，一份已取进对话、一份还在传：按提示原样重调时，已取进的那份直接回原来的编号，电脑不再读、对话里
        不多一份（识别按编号扣费，两份编号会扣两次）；都读完以后再要是重新取（文件可能改过）。"""
        self.contents["F-ACME\\a.pdf"] = ("a.pdf", b"%PDF-a")
        self.states["F-ACME\\b.pdf"] = {"request": "F-ACME\\b.pdf", "status": "leased", "code": ""}
        ask = {"reader": "C1", "root_id": "r1", "paths": ["ACME\\a.pdf", "ACME\\b.pdf"]}
        with mock.patch.object(tools, "FOLDER_FETCH_WAIT_SECONDS", 0), mock.patch.object(tools.time, "sleep"):
            first = json.loads(tools.folder_fetch(ask))["files"]
        self.assertEqual([row["status"] for row in first], ["added", "reading"])
        self.assertIn("same input", tools.FOLDER_STILL_READING)
        self.calls.clear()
        self.states.pop("F-ACME\\b.pdf")
        self.contents["F-ACME\\b.pdf"] = ("b.pdf", b"%PDF-b")
        again = json.loads(tools.folder_fetch({**ask, "paths": ["ACME/a.pdf", "ACME\\b.pdf"]}))["files"]
        self.assertEqual([row["status"] for row in again], ["added", "added"])
        self.assertEqual({k: v for k, v in again[0].items() if k != "path"},
                         {k: v for k, v in first[0].items() if k != "path"})  # 原来那份的编号
        self.assertEqual([call[3] for call in self.calls if call[0] == "fetch"], ["ACME\\b.pdf"])
        self.assertEqual([call[1] for call in self.calls if call[0] == "attach"], ["b.pdf"])
        self.assertEqual(self.cache, {})  # 都读完了：不再记
        self.calls.clear()
        json.loads(tools.folder_fetch(ask))
        self.assertEqual([call[3] for call in self.calls if call[0] == "fetch"], ["ACME\\a.pdf", "ACME\\b.pdf"])

    def test_fetch_stops_when_the_computer_cannot_take_it(self):
        self.made["ACME\\a.pdf"] = {"status": "waiting_for_cc", "reason": "update_needed", "version": "1.4.4",
                                    "minimum": "1.5.0"}
        out = json.loads(tools.folder_fetch({"reader": "C1", "root_id": "r1", "paths": ["ACME\\a.pdf", "ACME\\b.pdf"]}))
        self.assertEqual((out["status"], out["reason"], out["version"]), ("waiting_for_cc", "update_needed", "1.4.4"))
        self.assertEqual([call[0] for call in self.calls], ["fetch"])
        with self.assertRaises(frappe.ValidationError):
            tools.folder_fetch({"reader": "C1", "root_id": "r1", "paths": []})

    def test_declarations_point_the_agent_to_the_next_tools(self):
        by_name = {tool["name"]: tool for tool in tools.TOOLS}
        files, fetch = by_name["cowork_folder_files"], by_name["cowork_folder_fetch"]
        self.assertEqual(files["input_schema"]["required"], ["reader", "root_id"])
        self.assertEqual(fetch["input_schema"]["properties"]["paths"]["maxItems"], tools.FOLDER_FETCH_MAX)
        for name in ("cowork_recon_start", "cowork_recognize_attachments", "cowork_stocktake_create"):
            self.assertIn(name, fetch["description"])
        self.assertIn("cowork_folder_fetch", by_name["cowork_recon_start"]["description"])
        self.assertIn("not charged", fetch["description"])
        self.assertNotIn("approval", files)
        self.assertNotIn("approval", fetch)  # 只读、不扣费：不弹「允许」卡
        names = [tool["name"] for tool in tools.TOOLS]
        self.assertEqual(names.index("cowork_folder_files"), names.index("cowork_folder_task_options") + 1)
        # 每种能取的类型都有自己的 mime（容器里没有系统 mime 表时 xlsx / docx 也不变成 octet-stream）
        self.assertEqual(set(tools.FOLDER_FILE_TYPES), set(tools.folder_contracts.FETCH_EXTENSIONS))


class ReadbackTests(unittest.TestCase):
    """L1：回读 Express 账套只走 `readback.service` 的公开入口；读完把四份工作簿以「工具生成」放进对话（同一段对话、同一份
    回读只放一次），还在读时不干等，电脑离线 / 版本太旧照实说；推送成功的单只能由人在 Express 里改、改完回读（L7）。"""

    def setUp(self):
        self.calls, self.cache, self.states = [], {}, []
        service = types.ModuleType("pearnly_cowork.readback.service")
        service.request = lambda *args: self.calls.append(("request", args)) or dict(self.made)
        service.status = lambda name: self.calls.append(("status", name)) or (
            self.states.pop(0) if len(self.states) > 1 else dict(self.states[0]))
        service.file = lambda name, kind: self.calls.append(("file", name, kind)) or (f"T9-{kind}-2026-05.xlsx", b"PK")
        service.delete = lambda name: self.calls.append(("delete", name)) or {"readback": name, "deleted": True}
        package = sys.modules.setdefault("pearnly_cowork.readback", types.ModuleType("pearnly_cowork.readback"))
        package.service = service
        sys.modules["pearnly_cowork.readback.service"] = service
        chat = sys.modules.setdefault("pearnly_core.assistant.files", types.ModuleType("pearnly_core.assistant.files"))
        chat.add_to_conversation = lambda name, content, mime, origin="tool": self.calls.append(
            ("attach", name, mime, origin)) or f"/mnt/session/uploads/file_{len(self.calls)}/{name}"
        sys.modules.setdefault("pearnly_core", types.ModuleType("pearnly_core"))
        sys.modules.setdefault("pearnly_core.assistant", types.ModuleType("pearnly_core.assistant")).files = chat
        toolrun = types.ModuleType("pearnly_core.assistant.toolrun")
        toolrun.fresh = lambda: self.calls.append(("fresh",))
        sys.modules["pearnly_core.assistant.toolrun"] = toolrun
        sys.modules["pearnly_core.assistant"].toolrun = toolrun
        frappe.cache = types.SimpleNamespace(
            get_value=self.cache.get,
            set_value=lambda key, value, expires_in_sec=None: self.cache.__setitem__(key, dict(value)))
        frappe.local.pearnly_assistant = {"session_id": "sesn_1"}
        self.made = {"readback": "r" * 32, "status": "reading", "period": "2026-05", "account_set": "T9SINCER"}
        sleeper = mock.patch.object(tools.time, "sleep", lambda _s: None)
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def tearDown(self):
        frappe.local.pearnly_assistant = None

    def _ready(self, changes=1):
        return {"readback": "r" * 32, "status": "ready", "period": "2026-05", "account_set": "T9SINCER",
                "summary": {"sales_vat": {"count": 3}}, "files": ["changes", "general_ledger", "purchase_vat",
                                                                   "sales_vat"],
                "changes": [{"docnum": f"RR690502-{n:03d}", "change": "changed", "field": "total"}
                            for n in range(changes)], "direct": []}

    def test_a_finished_readback_puts_the_four_workbooks_in_the_chat_once(self):
        self.states = [{**self.made}, self._ready(changes=25)]
        out = json.loads(tools.express_readback({"connection": "EXP1", "account_set_key": "S:\\EXP69\\T9",
                                                 "period": "2026-05", "accounts": ["11-01"]}))
        self.assertEqual(self.calls[0], ("request", ("EXP1", "S:\\EXP69\\T9", "2026-05", ["11-01"])))
        attached = [call for call in self.calls if call[0] == "attach"]
        self.assertEqual(sorted(call[1] for call in attached), [
            "T9-changes-2026-05.xlsx", "T9-general_ledger-2026-05.xlsx", "T9-purchase_vat-2026-05.xlsx",
            "T9-sales_vat-2026-05.xlsx"])
        self.assertEqual({call[3] for call in attached}, {"tool"})  # 工具生成：识别工具不收、对账工具能取
        self.assertEqual({call[2] for call in attached}, {tools.XLSX})
        self.assertEqual(set(out["files"]), {"general_ledger", "sales_vat", "purchase_vat", "changes"})
        self.assertTrue(out["files"]["general_ledger"].startswith("/mnt/session/uploads/"))
        self.assertEqual((out["status"], len(out["changes"]), out["changes_not_shown"]), ("ready", 20, 5))
        self.calls.clear()
        again = json.loads(tools.express_readback_status({"readback": "r" * 32}))
        self.assertEqual([call for call in self.calls if call[0] in ("attach", "file")], [])
        self.assertEqual(again["files"], out["files"])

    def test_still_reading_or_waiting_for_the_computer_is_said_without_waiting_forever(self):
        self.states = [dict(self.made)]
        with mock.patch.object(tools, "READBACK_WAIT_SECONDS", 0):
            out = json.loads(tools.express_readback({"connection": "EXP1", "account_set_key": "K", "period": "2026-05"}))
        self.assertEqual(out["status"], "reading")
        self.assertIn("cowork_express_readback_status", out["note"])
        self.made = {"status": "waiting_for_cc", "reason": "update_needed", "version": "1.4.4", "minimum": "1.5.0"}
        self.calls.clear()
        out = json.loads(tools.express_readback({"connection": "EXP1", "account_set_key": "K", "period": "2026-05"}))
        self.assertEqual((out["status"], out["version"]), ("waiting_for_cc", "1.4.4"))
        self.assertIn("updated", out["note"])
        self.assertEqual([call[0] for call in self.calls], ["request"])  # 电脑领不了：不等、不查

    def test_refusals_come_back_as_reasons_and_delete_goes_through_the_service(self):
        def refuse(*args):
            raise frappe.ValidationError("cowork.readback.account_missing")

        sys.modules["pearnly_cowork.readback.service"].request = refuse
        out = json.loads(tools.express_readback({"connection": "EXP1", "account_set_key": "K", "period": "2026-5"}))
        self.assertEqual(out, {"status": "not_started", "reason": "cowork.readback.account_missing"})
        self.assertEqual(json.loads(tools.express_readback_delete({"readback": "r" * 32})),
                         {"readback": "r" * 32, "deleted": True})

    def test_declarations_say_which_file_feeds_which_reconciliation(self):
        by_name = {tool["name"]: tool for tool in tools.TOOLS}
        readback = by_name["cowork_express_readback"]
        self.assertEqual(readback["input_schema"]["required"], ["connection", "account_set_key", "period"])
        for words in ("general_ledger (bank reconciliation right_files; income reconciliation left_files)",
                      "sales_vat (income reconciliation right_files; output VAT check left_files)", "Read-only",
                      "nothing is recognized or charged", "waiting_for_cc", "cowork_express_readback_status"):
            self.assertIn(words, readback["description"])
        for name in ("cowork_express_readback", "cowork_express_readback_status", "cowork_express_readback_delete"):
            self.assertNotIn("approval", by_name[name])  # 只读、不扣费：不弹「允许」卡
        names = [tool["name"] for tool in tools.TOOLS]
        self.assertEqual(names.index("cowork_express_readback"), names.index("cowork_folder_fetch") + 1)
        # L7：推送成功的单只能由人在 Express 里改，改完用回读看
        self.assertIn("cowork_express_readback", by_name["cowork_edit_document"]["description"])
        self.assertIn("only a person can change it", by_name["cowork_edit_document"]["description"])
        self.assertIn("cowork_express_readback", by_name["cowork_push_documents"]["description"])
        self.assertIn("cowork_express_readback", by_name["cowork_recon_start"]["description"])


if __name__ == "__main__":
    unittest.main()
