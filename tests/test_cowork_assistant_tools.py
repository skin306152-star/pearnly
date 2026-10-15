"""Cowork 智能体工具（ADR-019）的离线检查：声明完整、步骤名有译文、推送请求号确定、筛选口径、逐张提交。

不连库：Frappe 与 Cowork 各域服务用最小替身，只看工具层自己的逻辑。
"""
import importlib.util
import json
import pathlib
import sys
import types
import unittest
import uuid
from datetime import date, datetime, timezone
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
        self.rows, self.grouped = [], []
        self.session = types.SimpleNamespace(user="a@example.com")
        self.local = types.SimpleNamespace(lang="en", site="s1", pearnly_assistant=None)
        self.db = types.SimpleNamespace(commit=self._commit, rollback=self._rollback,
                                        get_value=lambda *a, **k: ("express", 3))

    def _commit(self):
        self.commits += 1

    def _rollback(self):
        self.rollbacks += 1

    def get_list(self, doctype, **kwargs):
        """按 limit_start / limit_page_length 从 `rows` 切（每次给新副本：工具会改行）；数总数的查询回 total。
        按一列分组计数的（各账套几张）另记在 `grouped`，`calls` 仍只是列表与总数：分组只认等值筛选，同列表不看别的条件。"""
        if kwargs.get("group_by"):
            self.grouped.append(kwargs)
            field, counts = kwargs["group_by"], {}
            equal = {key: value for key, value in (kwargs.get("filters") or {}).items() if isinstance(value, str)}
            for row in self.rows:
                if all(row.get(key) == value for key, value in equal.items()):
                    counts[row.get(field)] = counts.get(row.get(field), 0) + 1
            return [{field: value, "count": count} for value, count in counts.items()]
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
        "pearnly_cowork.connections.api": dict(request_refresh=lambda *a: {},
                                                ensure_account_masters=lambda *a: {"state": "ready"}),
        "pearnly_cowork.connections.service": dict(records=lambda *a: [], targets=lambda name: {}),
        "pearnly_cowork.folder_intake.destinations": dict(online=lambda *a: True, of=lambda adapter: None),
        "pearnly_cowork.folder_intake.agent": dict(read=lambda reader: {}),
        "pearnly_cowork.folder_intake.rules": dict(listing=lambda: {}, run_now=lambda n: {}, runs=lambda n: {},
                                                   waiting=lambda *a: None, account_label=lambda c, k: k,
                                                   master_names=lambda c, k: {},
                                                   continues_from=lambda reader, root, folder, current=None: None),
        "pearnly_cowork.history.push_state": dict(record_failure=lambda *a: None),
        "pearnly_cowork.history.storage": dict(read=lambda name: None, declare_direction=lambda names, direction: []),
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
    # 报税的纯规则（所得类型码）也用真的：工具的枚举取自它；共用票种规则一并按文件载入，不把整个共用层放上路径。
    # 认 50 ทวิ 的口径规则（`contracts.document_policy`）也用真的：识别结果说「预扣税凭证」与报税草稿同一条规则
    for name in ("pearnly_core", "pearnly_core.tax_documents", "pearnly_cowork.tax", "pearnly_cowork.contracts"):
        sys.modules[name] = types.ModuleType(name)
    for name, path in (("pearnly_core.tax_documents.codes", CORE / "tax_documents/codes.py"),
                       ("pearnly_core.tax_documents.amounts", CORE / "tax_documents/amounts.py"),
                       ("pearnly_core.tax_documents.rules", CORE / "tax_documents/rules.py"),
                       ("pearnly_cowork.contracts.document_policy", COWORK / "contracts/document_policy.py"),
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
        self.assertRegex(hooks, r'pearnly_assistant_tools = \["pearnly_cowork\.assistant\.tools\.TOOLS"[,\]]')

    def test_every_tool_says_what_it_is_doing_while_it_runs(self):
        # 工作状态行（教程第 8 条）：执行中显示这一步的进行时（「正在识别单据」），本 App 自己声明、译文在本 App 术语表
        spec = importlib.util.spec_from_file_location("cowork_step_names", COWORK / "assistant/step_names.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        terms = {}
        for line in (COWORK / "i18n/messages.tsv").read_text(encoding="utf-8").splitlines()[1:]:
            cells = line.split("\t")
            if cells and cells[0]:
                terms[cells[0]] = cells[1:4]
        for tool in tools.TOOLS:
            running = module.STEP_NAMES.get(tool["name"])
            self.assertTrue(running, f"{tool['name']} needs a running step name")
            self.assertNotEqual(running, tool["label"], tool["name"])
            self.assertIn(running, terms, tool["name"])
            self.assertTrue(all(terms[running][:3]), f"{running} needs zh / th / ja")
        hooks = (COWORK / "hooks.py").read_text(encoding="utf-8")
        self.assertIn('pearnly_assistant_step_names = ["pearnly_cowork.assistant.step_names.STEP_NAMES"]', hooks)

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


class PushWhileChartIsReadTests(unittest.TestCase):
    """推送时这份账套的科目表还没读到：不记成「要人选」（没得选），说电脑正在读；要升级 / 读不到的照实记在票上。"""

    def setUp(self):
        self.failures = []
        self.doc = types.SimpleNamespace(last_push_status="", push_connection="EXP1", push_account_set="K",
                                         posting_kind="service", record_version=1, last_push_record="",
                                         last_push_reason="")
        tools.storage.read = lambda name: self.doc
        tools.push_state.record_failure = lambda *a, **k: self.failures.append((*a, k))
        tools.application.submit = lambda *a: self.fail("must not prepare while the chart is missing")

    def _push(self, state):
        tools.application.choices_view = lambda *a: {"needed": [{"kind": "account", "subject": "S"}], "options": {},
                                                     "wait": {"state": state, "message": "msg:" + state}}
        return tools._push_one("H1")

    def test_reading_or_offline_waits_without_recording_a_choice(self):
        for state in ("reading", "offline"):
            out = self._push(state)
            self.assertEqual((out["status"], out["reason"]), ("waiting_for_cc", state))
        self.assertEqual(self.failures, [])
        description = next(t for t in tools.TOOLS if t["name"] == "cowork_push_documents")["description"]
        self.assertIn("waiting_for_cc", description)

    def test_update_needed_is_recorded_on_the_document(self):
        out = self._push("update_needed")
        self.assertEqual(out["status"], "failed")
        # 停下时把这次要推往的连接与账套一并抄到票上（验收 F17：先识别后推的票原本没有去向，待处理页没法当场选）
        self.assertEqual(self.failures, [("H1", "msg:update_needed", {"connection": "EXP1", "account_set": "K"})])


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
        chat.resolve = lambda ref: {"receipt.jpg": "file_abc", "/mnt/session/uploads/file_abc/receipt.jpg": "file_abc"}.get(
            ref, ref)
        originals = types.ModuleType("pearnly_cowork.files.service")
        originals.create = lambda workspace, name, content, key: self.created.append((name, content, key)) or {"name": "CWS-1"}
        ocr = types.ModuleType("pearnly_cowork.ocr.execution.service")
        ocr.submit = self._submit
        ocr.detail = lambda batch: {"state": "saved", "page_count": 1, "billing_state": "pending", "requires_review": 0,
                                    "history_records": ["H1"]}
        ocr.certificates = lambda batch: {"pages": 0, "kept": 0}
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

    def test_the_key_follows_the_file_whatever_the_model_calls_it(self):
        # 接续新上下文后模型按文件名称呼旧附件：请求号仍按本对话里的文件编号，不重复识别，也不与别的对话里的同名文件撞
        for ref in ("file_abc", "receipt.jpg", "/mnt/session/uploads/file_abc/receipt.jpg"):
            out = json.loads(tools.recognize_attachments({"files": [ref]}))["results"][0]
            self.assertEqual(out["file"], ref)
        self.assertEqual({key for _name, _content, key in self.created}, {"assistant:file_abc"})
        self.assertEqual({key for _source, key, _choices in self.submitted}, {"assistant:file_abc"})

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

    def test_a_withholding_certificate_is_named_not_just_not_an_invoice(self):
        # 2026-10-13 测试机：两张 50 ทวิ（选了不录入）只回了「不是发票」，CC 说不清是什么
        ocr = sys.modules["pearnly_cowork.ocr.execution.service"]
        ocr.detail = lambda batch: {"state": "not_invoice", "page_count": 1}
        ocr.certificates = lambda batch: {"pages": 1, "kept": 1}
        out = json.loads(tools.recognition_results({"batches": ["B1"]}))["results"][0]
        self.assertEqual((out["state"], out["document_kind"], out["kept_for_wht_draft"]),
                         ("not_invoice", "withholding_certificate", 1))
        # 别的非发票（空白页、信函）照旧只说不是发票
        ocr.certificates = lambda batch: {"pages": 0, "kept": 0}
        out = json.loads(tools.recognition_results({"batches": ["B1"]}))["results"][0]
        self.assertEqual(out, {"batch": "B1", "state": "not_invoice", "pages": 1})
        # 选了录入（或还没选）：凭证成了记录，documents 里说是预扣税凭证；读数没有标记、只有凭证样子的同样认
        ocr.detail = lambda batch: {"state": "saved", "page_count": 1, "requires_review": 0, "history_records": ["H1"]}
        shapes = ({"document_type": "other", "document_flag": "withholding_certificate", "seller_tax": "0105599210021"},
                  {"document_type": "other", "is_not_invoice": True, "seller_tax": "0105599210021",
                   "buyer_tax": "1990100000029", "wht_amount": "180.00"})
        for fields in shapes:
            tools.storage.project = lambda doc, fields=fields: {"name": doc, "pages": [{"fields": fields}]}
            doc, = json.loads(tools.recognition_results({"batches": ["B1"]}))["results"][0]["documents"]
            self.assertEqual(doc["document_kind"], "withholding_certificate")
        # 和票扫在一起、选了不录入的凭证：documents 只有那张票，另说存了几张给预扣税草稿
        ocr.certificates = lambda batch: {"pages": 1, "kept": 1}
        tools.storage.project = lambda doc: {"name": doc, "pages": [{"fields": {"document_type": "tax_invoice",
                                                                                  "wht_amount": "1250.00"}}]}
        out = json.loads(tools.recognition_results({"batches": ["B1"]}))["results"][0]
        self.assertNotIn("document_kind", out["documents"][0])
        self.assertNotIn("document_kind", out)
        self.assertEqual(out["kept_for_wht_draft"], 1)
        description = {tool["name"]: tool for tool in tools.TOOLS}["cowork_recognize_attachments"]["description"]
        self.assertIn("document_kind withholding_certificate", description)

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
        chat.resolve = lambda ref: {"sales-oct.xlsx": "file_pos"}.get(ref, ref)
        chat.attachment = lambda ref: {"file_pos": ("sales-oct.xlsx", b"x"), "file_xls": ("sales.xls", b"x"),
                                       "file_jpg": ("bill.jpg", b"x")}[chat.resolve(ref)]
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

    def test_a_file_named_by_its_filename_keeps_the_same_key(self):
        out = json.loads(tools.pos_import({"files": ["sales-oct.xlsx"], "connection": "C1", "account_set_key": "K"}))
        self.assertEqual(out["results"][0]["file"], "sales-oct.xlsx")
        self.assertEqual(self.created, [("WS", "sales-oct.xlsx", "assistant:pos:file_pos")])
        self.assertEqual(self.calls[0][2], "assistant:pos:file_pos")
        self.assertNotIn("key", out["results"][0])

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

    def test_the_account_set_entered_into_is_named_by_its_company(self):
        tools.connections.targets = lambda name: {"account_sets": [{"source_id": "K", "label": "T.9 Shop Co., Ltd."}],
                                                  "groups": [{"label": "70EXP", "items": [
                                                      {"value": "K", "label": "T.9 Shop Co., Ltd.", "description": ""}]}]}
        out = self._import()
        self.assertEqual(list(out)[:2], ["account_label", "account_set_key"])
        self.assertEqual(out["account_label"], "70EXP · T.9 Shop Co., Ltd.")
        attached = json.loads(tools.pos_layout({"file": "file_pos", "connection": "C1", "account_set_key": "K"}))
        self.assertEqual(attached["account_label"], "70EXP · T.9 Shop Co., Ltd.")

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
        # 上次用的那份带上清单里的名字、排在键前面（2026-10-13 录屏：只给键，CC 说成文件夹名「demo02」）
        self.assertEqual(out["suggested"], {"target": "express", "connection": "EXP1", "account_label": "Beta Co",
                                            "account_set_key": "b", "direction": "purchase", "posting_kind": "stock"})
        self.assertLess(list(out["suggested"]).index("account_label"), list(out["suggested"]).index("account_set_key"))
        self.assertEqual(list(express["account_sets"][0]), ["label", "key"])
        found = json.loads(tools.intake_options({"search": "alp"}))["targets"][0]["account_sets"]
        self.assertEqual(found, [{"key": "A", "label": "Alpha Co"}])

    def test_a_last_used_account_set_that_is_gone_is_not_suggested(self):
        frappe.get_all = lambda doctype, **kw: [{"intake_target": "express", "target_connection": "EXP1",
                                                 "target_account_set": "c:\\70exp\\gone", "direction": "purchase"}]
        out = json.loads(tools.intake_options({}))
        self.assertIsNone(out["suggested"])
        self.assertEqual(out["last_used_unavailable"]["account_set_key"], "c:\\70exp\\gone")
        self.assertNotIn("account_label", out["last_used_unavailable"])  # 目录里没有了：没有名字可给，只留键
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

    def test_stock_groups_of_an_unread_account_set_are_requested_from_the_computer(self):
        asked = []
        unread = lambda *a: {"account_set_key": a[1], "known": False, "groups": []}
        with mock.patch.object(tools.connections_api, "stock_acc_group", unread, create=True), \
                mock.patch.object(tools.connections_api, "ensure_account_masters",
                                  lambda *a: asked.append(a) or {"state": "reading"}):
            out = json.loads(tools.stock_group({"connection": "EXP1", "account_set_key": "B"}))
            self.assertEqual(asked, [("EXP1", "B")])
            self.assertEqual(out["status"], "reading")
            with mock.patch.object(tools.connections_api, "stock_acc_group",
                                   lambda *a: {"account_set_key": a[1], "known": True, "groups": []}):
                out = json.loads(tools.stock_group({"connection": "EXP1", "account_set_key": "B"}))
        self.assertEqual((len(asked), "status" in out), (1, False))



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

    def test_a_chart_still_being_read_is_waited_for_then_offered(self):
        """2026-10-13 录制：推往电脑没选的账套（DEMO02），科目表从没读过，CC 拿到 0 个科目、95 张票卡在「要人选」。
        现在服务端已请电脑读（`view.wait`），工具等它读完再给可选的科目。"""
        reading = {"needed": [{"kind": "account", "subject": "S"}], "options": {}, "account_set": "K",
                   "wait": {"state": "reading", "message": "reading"}}
        ready = {"needed": [{"kind": "account", "subject": "S"}], "account_set": "K",
                 "options": {"accounts": [{"code": "5300", "name": "Rent"}]}}
        views = [reading, reading, ready]
        tools.application.choices_view = lambda *a: views.pop(0)
        saved_sleep, tools.time.sleep = tools.time.sleep, lambda s: None
        try:
            out = json.loads(tools.push_choices({"document": "H1"}))
        finally:
            tools.time.sleep = saved_sleep
        self.assertEqual(out["options"]["accounts"]["shown"], [{"code": "5300", "name": "Rent"}])
        self.assertNotIn("status", out)

    def test_a_chart_not_read_in_time_says_reading_instead_of_an_empty_list(self):
        tools.application.choices_view = lambda *a: {"needed": [{"kind": "account", "subject": "S"}], "options": {},
                                                     "account_set": "K",
                                                     "wait": {"state": "reading", "message": "reading"}}
        clock = iter(range(0, 1000, 20))
        saved = tools.time.sleep, tools.time.monotonic
        tools.time.sleep, tools.time.monotonic = (lambda s: None), (lambda: next(clock))
        try:
            out = json.loads(tools.push_choices({"document": "H1"}))
        finally:
            tools.time.sleep, tools.time.monotonic = saved
        self.assertEqual(out["status"], "reading")
        self.assertIn("still reading", out["note"])
        self.assertNotIn("options", out)
        for state in ("offline", "update_needed", "failed"):
            tools.application.choices_view = lambda *a, state=state: {
                "needed": [{"kind": "account"}], "options": {}, "account_set": "K",
                "wait": {"state": state, "message": state}}
            out = json.loads(tools.push_choices({"document": "H1"}))
            self.assertEqual(out["status"], state)
            self.assertEqual(out["note"], tools.MASTERS_WAITING[state])

    def test_the_users_expense_note_is_passed_on_and_suggestions_come_back(self):
        """第 4、6 条：会计员在消息里说了「ค่าเครื่องเขียน」，CC 把它当提示交给建议；建议原样交给模型，由它请会计员确认。"""
        seen = []
        suggested = [{"code": "5320-01", "name": "ค่าเครื่องเขียน", "source": "hint", "reason": "Matches"}]

        def view(*a, **k):
            seen.append(k)
            return {"needed": [{"kind": "account", "subject": "S", "suggested": suggested}], "account_set": "K",
                    "options": {"accounts": [{"code": "5320-01", "name": "ค่าเครื่องเขียน"}]}}

        tools.application.choices_view = view
        out = json.loads(tools.push_choices({"document": "H1", "hint": "  ค่าเครื่องเขียน "}))
        self.assertEqual(seen, [{"hint": "ค่าเครื่องเขียน"}])
        self.assertEqual(out["needed"][0]["suggested"], suggested)
        json.loads(tools.push_choices({"document": "H1"}))
        self.assertEqual(seen[-1], {})  # 没说就不传
        description = next(t for t in tools.TOOLS if t["name"] == "cowork_push_choices")
        self.assertIn("hint", description["input_schema"]["properties"])
        self.assertIn("never save a suggestion the user has not confirmed", description["description"])

    def test_the_suppliers_history_still_being_read_is_waited_for_briefly(self):
        reading = {"needed": [{"kind": "account", "subject": "S", "suggested": []}], "options": {},
                   "account_set": "K", "history": "reading"}
        ready = {"needed": [{"kind": "account", "subject": "S", "suggested": [{"code": "5300", "source": "history"}]}],
                 "options": {}, "account_set": "K"}
        views = [reading, reading, ready]
        tools.application.choices_view = lambda *a, **k: views.pop(0)
        saved_sleep, tools.time.sleep = tools.time.sleep, lambda s: None
        try:
            out = json.loads(tools.push_choices({"document": "H1"}))
        finally:
            tools.time.sleep = saved_sleep
        self.assertEqual(out["needed"][0]["suggested"][0]["source"], "history")
        # 一直没读完：最多等 HISTORY_WAIT_SECONDS，照样给按名称的建议，不干等
        tools.application.choices_view = lambda *a, **k: dict(reading)
        clock = iter(range(0, 1000, 5))
        saved = tools.time.sleep, tools.time.monotonic
        tools.time.sleep, tools.time.monotonic = (lambda s: None), (lambda: next(clock))
        try:
            out = json.loads(tools.push_choices({"document": "H1"}))
        finally:
            tools.time.sleep, tools.time.monotonic = saved
        self.assertEqual(out["needed"][0]["kind"], "account")
        self.assertLessEqual(next(clock), 5 * (tools.HISTORY_WAIT_SECONDS // 5 + 3))

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
        self.assertTrue(out["files_added_to_chat"])



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

    def test_accounts_still_being_read_are_reported_as_reading(self):
        tools.rules.options = lambda connection, key: {"readers": [], "accounts": None, "departments": None,
                                                       "stock_group": None, "masters": {"state": "reading"}}
        out = json.loads(tools.folder_task_options({"connection": "EXP1", "account_set_key": "Y1"}))
        self.assertEqual(out["accounts_status"], "reading")
        self.assertNotIn("accounts", out)
        tools.rules.options = lambda connection, key: {"readers": [], "accounts": [{"code": "5300", "name": "R"}],
                                                       "departments": None, "stock_group": None,
                                                       "masters": {"state": "ready"}}
        out = json.loads(tools.folder_task_options({"connection": "EXP1", "account_set_key": "Y1"}))
        self.assertNotIn("accounts_status", out)
        self.assertEqual(out["accounts"]["total"], 1)

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
        self.assertEqual(tool["ask"], "Change this scheduled task?")
        self.assertEqual(tool["ask_when"], {"enabled": "Turn this scheduled task on or off?"})

    def test_the_enable_step_label_does_not_say_on_or_off(self):
        # 2026-10-13 测试机：只改提醒，对话里的步骤却写「启用或停用了文件夹任务」——步骤名用中性的「更新了定时任务」
        tool = next(t for t in tools.TOOLS if t["name"] == "cowork_set_folder_task_enabled")
        self.assertEqual(tool["label"], "Updated a scheduled task")
        root = pathlib.Path(__file__).resolve().parents[1] / "apps/pearnly_cowork/pearnly_cowork"
        rows = [line.split("\t") for line in (root / "i18n/messages.tsv").read_text(encoding="utf-8").splitlines()]
        row, = [row for row in rows if row[0] == "Updated a scheduled task"]
        self.assertEqual(row[1:4], ["更新了定时任务", "อัปเดตงานตามกำหนดเวลาแล้ว", "定期タスクを更新しました"])
        for lang, text in (("zh", "更新了定时任务"), ("th", "อัปเดตงานตามกำหนดเวลาแล้ว"), ("ja", "定期タスクを更新しました")):
            pack = (root / f"translations/{lang}.csv").read_text(encoding="utf-8").splitlines()
            self.assertIn(f"Updated a scheduled task,{text}", pack)
            self.assertFalse([line for line in pack if line.startswith("Turned a folder task on or off,")])

    def test_scheduled_task_steps_and_cards_say_scheduled_task_like_the_page(self):
        # 2026-10-13：页面、授权卡片说定时任务，对话里的步骤名还写「文件夹任务」——四种语言都统一成页面的叫法
        root = pathlib.Path(__file__).resolve().parents[1] / "apps/pearnly_cowork/pearnly_cowork"
        rows = {line.split("\t")[0]: line.split("\t")[1:4]
                for line in (root / "i18n/messages.tsv").read_text(encoding="utf-8").splitlines()}
        self.assertEqual(rows["Scheduled tasks"], ["定时任务", "งานตามกำหนดเวลา", "定期タスク"])  # 页面标题
        shown = [text for t in tools.TOOLS if "folder_task" in t["name"]
                 for text in [t["label"], t.get("ask"), *(t.get("ask_when") or {}).values()] if text]
        # 9 个：含一次启停 / 改提醒几个任务的批量工具（2026-10-15，第 5 条）
        self.assertEqual(len([t for t in tools.TOOLS if "folder_task" in t["name"]]), 9)
        for text in shown:
            self.assertIn("scheduled task", text)
            zh, th, ja = rows[text]
            self.assertIn("定时任务", zh, text)
            self.assertIn("งานตามกำหนดเวลา", th, text)
            self.assertIn("定期タスク", ja, text)
        old = ("folder task", "文件夹任务", "งานโฟลเดอร์", "フォルダータスク")
        for lang in ("en", "zh", "th", "ja"):  # 生成器不删旧行：改名后的旧原文不能留在翻译包里
            pack = (root / f"translations/{lang}.csv").read_text(encoding="utf-8")
            self.assertFalse([line for line in pack.splitlines() if any(word in line for word in old)], lang)

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
        self.assertEqual((out["files_added_to_chat"], out["files_note"]), (True, tools.FILES_ADDED_NOTE))
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
        self.vat_result = lambda: {"status": "readback_needed", "period": "2026-09", "connection": "C",
                                   "account_set_key": "c:\\70exp\\demo02"}
        with mock.patch.object(tools.connections, "targets", lambda name: {"account_sets": [
                {"source_id": "C:\\70EXP\\demo02", "label": "T.9 Example Co., Ltd."}]}):
            out = json.loads(tools.vat_draft({"connection": "C", "account_set_key": "K", "period": "2026-09"}))
        self.assertEqual(out["status"], "readback_needed")
        self.assertIn("cowork_express_readback", out["note"])
        self.assertEqual(out["account_label"], "T.9 Example Co., Ltd.")  # 叫 CC 先回读哪份账套：按名字说
        self.assertLess(list(out).index("account_label"), list(out).index("account_set_key"))
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
        self.assertEqual((out["files_added_to_chat"], out["files_note"]), (True, tools.FILES_ADDED_NOTE))
        # 草稿工作簿是工具产出：对账工具能取，识别工具不收（L3）
        self.assertEqual([call[3] for call in self.calls if call[0] == "attach"], ["tool"])
        self.assertEqual(frappe.commits, 1)
        self.calls.clear()
        out = json.loads(tools.vat_draft({"connection": "C", "account_set_key": "K", "period": "2026-09",
                                          "workbook": False}))
        self.assertNotIn("file", out)
        self.assertNotIn("files_added_to_chat", out)  # 这次没放文件就不说
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
        self.assertEqual((out["files_added_to_chat"], out["files_note"]), (True, tools.FILES_ADDED_NOTE))
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

    def test_a_stocktake_from_the_same_file_gets_the_same_key_whatever_it_is_called(self):
        # 盘点单号按对话 + 文件编号固定：接续新上下文后模型按文件名称呼同一份表，也只得到同一张
        made = []
        upload = types.ModuleType("pearnly_cowork.stocktake.upload")
        upload.create = lambda title, key, content: made.append((title, key)) or {"count": "S-" + key[:4]}
        sys.modules["pearnly_cowork.stocktake.upload"] = sys.modules["pearnly_cowork.stocktake"].upload = upload
        chat = types.ModuleType("pearnly_core.assistant.files")
        chat.resolve = lambda ref: {"books.xlsx": "file_bk", "/mnt/session/uploads/file_bk/books.xlsx": "file_bk"}.get(ref, ref)
        chat.attachment = lambda ref: ("books.xlsx", b"x")
        chat.attachments = lambda: []
        frappe.local.pearnly_assistant = {"session_id": "sesn_1"}
        try:
            with mock.patch.dict(sys.modules, {"pearnly_core.assistant.files": chat}), \
                    mock.patch.object(sys.modules["pearnly_core.assistant"], "files", chat, create=True), \
                    mock.patch.object(frappe, "db", types.SimpleNamespace(commit=lambda: None, rollback=lambda: None),
                                      create=True):
                for ref in ("file_bk", "books.xlsx", "/mnt/session/uploads/file_bk/books.xlsx"):
                    tools.stocktake_create({"file": ref})
        finally:
            frappe.local.pearnly_assistant = None
        self.assertEqual(len({key for _title, key in made}), 1)
        self.assertEqual(made[0], ("books", str(uuid.uuid5(uuid.NAMESPACE_URL, "pearnly-assistant:sesn_1:file_bk"))))

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
        self.assertTrue(out["files_added_to_chat"])


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


class FolderTaskSwitchBatchTests(unittest.TestCase):
    """一句话改几个任务的提醒或启停，只弹一张「允许」卡片（用户 2026-10-13 第 5 条：改 3 个任务的提醒点了 3 次）。
    卡片一个任务一行（任务名 + 改什么）；允许后逐个照单个工具同一套规则执行、逐个回结果，一个不通过只退回它自己。"""

    TIMES = [{"days_before": 2, "time": "09:00"}]

    def setUp(self):
        self.tasks = {"R1": {"name": "R1", "title": "Daonuea purchases", "enabled": 1, "remind": 1,
                             "remind_times": [{"days_before": 1, "time": "08:00"}]},
                      "R2": {"name": "R2", "title": "Daonuea expenses", "enabled": 1, "remind": 0},
                      "R3": {"name": "R3", "title": "Daonuea sales", "enabled": 0, "remind": 1, "remind_times": None}}
        self.calls, self.points, self.rolled_back, self.committed = [], [], [], 0
        self.refuse = {}

        def set_enabled(name, enabled=None, remind=None, remind_times=None, cc_session=None):
            self.calls.append((name, enabled, remind, remind_times, cc_session))
            if name in self.refuse:
                raise self.refuse[name]
            row = dict(self.tasks[name])
            if enabled is not None:
                row["enabled"] = enabled
            if remind_times is not None:
                row.update(remind=1 if remind is None else remind, remind_times=remind_times)
            elif remind is not None:
                row["remind"] = remind
            return row

        def commit():
            self.committed += 1

        for target, name, value in (
                (tools.rules, "set_enabled", set_enabled),
                (tools.rules, "listing", lambda: {"rules": [dict(row) for row in self.tasks.values()]}),
                (frappe.local, "pearnly_assistant", {"session_id": "sesn_7"}),
                (frappe.db, "savepoint", self.points.append),
                (frappe.db, "rollback", lambda save_point=None: self.rolled_back.append(save_point)),
                (frappe.db, "commit", commit)):
            patcher = mock.patch.object(target, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _tool(self, name):
        return next(t for t in tools.TOOLS if t["name"] == name)

    def test_one_card_lists_every_task_with_what_changes(self):
        tool = self._tool("cowork_set_folder_tasks_enabled")
        self.assertTrue(tool["approval"])
        self.assertEqual((tool["label"], tool["ask"]), ("Updated scheduled tasks", "Change these scheduled tasks?"))
        items = tool["input_schema"]["properties"]["tasks"]
        self.assertEqual((items["maxItems"], items["items"]["required"]), (tools.FOLDER_BATCH_MAX, ["task"]))
        # 与单个工具同一份参数说明（提醒时刻的规则只写一处）
        single = self._tool("cowork_set_folder_task_enabled")["input_schema"]["properties"]
        self.assertIs(items["items"]["properties"], single)
        self.assertIn("cowork_set_folder_tasks_enabled", self._tool("cowork_set_folder_task_enabled")["description"])
        with mock.patch.object(tools, "_", _term):
            rows = tools.preview_set_folder_tasks_enabled({"tasks": [
                {"task": "R1", "remind_times": self.TIMES}, {"task": "R2", "remind_times": self.TIMES},
                {"task": "R3", "enabled": True}, {"task": "R9", "remind": False}, "not a task"]})
        self.assertEqual(rows, [
            {"field": "Daonuea purchases", "before": "Reminder: Day before 08:00", "after": "Reminder: 2 days before 09:00"},
            {"field": "Daonuea expenses", "before": "Reminder: Off", "after": "Reminder: 2 days before 09:00"},
            {"field": "Daonuea sales", "before": "cowork.folder.off", "after": "cowork.folder.on"},
            # 读不到的任务：照编号列出，「现在」留空
            {"field": "R9", "before": "", "after": "Reminder: Off"}])
        # 单个工具的卡片照旧（同一套改动行）
        with mock.patch.object(tools, "_", _term):
            single_rows = tools.preview_set_folder_task_enabled({"task": "R3", "enabled": True, "remind": False})
        self.assertEqual([(r["field"], r["before"], r["after"]) for r in single_rows],
                         [("cowork.folder.field.title", "", "Daonuea sales"),
                          ("intake.col.status", "cowork.folder.off", "cowork.folder.on"),
                          ("Reminder", DEFAULT_REMIND, "Off")])

    def test_each_task_is_changed_and_reported_once_after_one_allow(self):
        out = json.loads(tools.set_folder_tasks_enabled({"tasks": [
            {"task": "R1", "remind_times": self.TIMES}, {"task": "R2", "remind_times": self.TIMES},
            {"task": "R3", "enabled": True}]}))
        self.assertEqual(self.calls, [("R1", None, None, self.TIMES, "sesn_7"), ("R2", None, None, self.TIMES, "sesn_7"),
                                      ("R3", 1, None, None, "sesn_7")])
        self.assertEqual(out["status"], "saved")
        self.assertEqual(out["tasks"], [
            {"task": "R1", "title": "Daonuea purchases", "status": "saved", "enabled": 1, "remind": 1,
             "remind_times": self.TIMES},
            {"task": "R2", "title": "Daonuea expenses", "status": "saved", "enabled": 1, "remind": 1,
             "remind_times": self.TIMES},
            # 提醒开着但没选过时刻：任务服务回的就是 None，不硬造
            {"task": "R3", "title": "Daonuea sales", "status": "saved", "enabled": 1, "remind": 1}])
        self.assertEqual((self.committed, self.rolled_back), (1, []))
        self.assertEqual(len(self.points), 3)  # 每个任务一个保存点

    def test_a_refused_task_is_listed_and_the_others_still_change(self):
        self.refuse = {"R2": frappe.ValidationError("A same-day reminder must be earlier than the run time."),
                       "R3": frappe.PermissionError("Not permitted")}
        out = json.loads(tools.set_folder_tasks_enabled({"tasks": [
            {"task": "R1", "enabled": False}, {"task": "R2", "remind_times": [{"days_before": 0, "time": "23:00"}]},
            {"task": "R3", "enabled": True}, {"task": "R1", "remind": False}, {"task": "R4"}, {"enabled": True}]}))
        self.assertEqual(out["status"], "partly_saved")
        shown = [(item["task"], item["status"], item.get("reason")) for item in out["tasks"]]
        self.assertEqual(shown, [
            ("R1", "saved", None),
            ("R2", "failed", "A same-day reminder must be earlier than the run time."),
            ("R3", "failed", "Not permitted"),
            ("R1", "failed", "This task is already in the list."),
            ("R4", "failed", "Give enabled, remind or remind_times."),
            ("", "failed", "Each item needs task.")])
        self.assertEqual(out["tasks"][1]["title"], "Daonuea expenses")
        # 不通过的只退回到它自己的保存点；做成的照常提交
        self.assertEqual(self.rolled_back, [self.points[1], self.points[2]])
        self.assertEqual(self.committed, 1)
        self.assertEqual([call[0] for call in self.calls], ["R1", "R2", "R3"])

    def test_nothing_changed_is_an_error_and_nothing_is_committed(self):
        self.refuse = {"R1": frappe.DoesNotExistError("Not found")}
        out = json.loads(tools.set_folder_tasks_enabled({"tasks": [{"task": "R1", "enabled": True}]}))
        self.assertEqual((out["status"], self.committed), ("not_saved", 0))
        too_many = json.loads(tools.set_folder_tasks_enabled({"tasks": [{"task": f"R{i}", "enabled": True}
                                                                        for i in range(tools.FOLDER_BATCH_MAX + 1)]}))
        self.assertEqual(too_many["status"], "not_saved")
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(frappe.ValidationError):
            tools.set_folder_tasks_enabled({"tasks": []})


class PushWaitTests(unittest.TestCase):
    """推送后等写账结论再回（用户 2026-10-13 第 9 条：服务端已写入 HP691013-001，只晚了几秒，CC 却答「已排队」就收尾）。
    按真实先后：发出 → CC 桌面版取走（leased）→ 回报成功 / 失败；最多等 PUSH_WAIT_SECONDS、每 PUSH_WAIT_STEP 秒读一次
    本人这几条推送记录；只在推送线程里等，网页请求与后台队列里只读一次就回。"""

    def setUp(self):
        self.clock, self.sleeps, self.reads, self.rollbacks = [0.0], [], [], [0]
        self.timeline = []  # 每读一次账本取下一份结论；读完了就停在最后一份
        self.online = True
        self.docs = {name: types.SimpleNamespace(last_push_status="", push_connection="EXP1", push_account_set="K",
                                                 posting_kind="service", record_version=1, last_push_record="",
                                                 last_push_reason="") for name in ("H1", "H2", "H3")}

        def sleep(seconds):
            self.sleeps.append(seconds)
            self.clock[0] += seconds

        def outcomes(records):
            self.reads.append((self.clock[0], sorted(records)))
            step = self.timeline[min(len(self.reads), len(self.timeline)) - 1] if self.timeline else {}
            return {name: dict(step[name]) for name in records if name in step}

        def rollback(save_point=None):
            self.rollbacks[0] += 1

        destination = types.SimpleNamespace(confirm=lambda *a: None)
        for target, name, value in (
                (tools.storage, "read", lambda name: self.docs[name]),
                (tools.application, "choices_view", lambda *a: {"needed": []}),
                (tools.application, "submit",
                 lambda history, *a: {"status": "prepared", "name": "PR-" + history, "revision": 1}),
                (tools.application, "outcomes", outcomes),
                (tools.destinations, "of", lambda adapter: destination),
                (tools.destinations, "online", lambda adapter, connection: self.online),
                (tools.time, "sleep", sleep), (tools.time, "monotonic", lambda: self.clock[0]),
                (frappe.db, "rollback", rollback),
                (frappe.db, "get_value", lambda *a, **k: ("express", 3)),
                (frappe.local, "pearnly_assistant", {"session_id": "sesn_7", "may_wait": True})):
            patcher = mock.patch.object(target, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _push(self, *names):
        return json.loads(tools.push_documents({"documents": list(names)}))

    def test_entered_with_the_express_number_once_cc_desktop_reports(self):
        # 录屏同序：发出时还在排队 → 第 3 秒被 CC 桌面版取走 → 第 6 秒回报成功、带 Express 单号
        self.timeline = [{"PR-H1": {"status": "pending"}}, {"PR-H1": {"status": "leased"}},
                         {"PR-H1": {"status": "leased"}},
                         {"PR-H1": {"status": "success", "docnum": "HP691013-001"}}]
        out = self._push("H1")
        self.assertEqual(out["results"], [{"document": "H1", "status": "entered", "adapter": "express",
                                           "cc_desktop_online": True, "docnum": "HP691013-001"}])
        self.assertNotIn("note", out)
        self.assertEqual(len(self.reads), 4)
        self.assertEqual(self.sleeps, [tools.PUSH_WAIT_STEP] * 3)
        # 每次读之前换一份新快照（结论是别的请求提交的）
        self.assertGreaterEqual(self.rollbacks[0], len(self.reads))

    def test_entered_says_how_it_was_written_so_cc_never_sends_the_user_to_express(self):
        # 验收 F19：用户说「จ่ายเงินสด」、这家记成现购，推进去的是 HP 现购（贷现金），CC 却两次叫用户去 Express 核对
        written = {"payment": "cash", "payment_from": "remembered", "debit_account": "53-05-02-00", "vat": "claimed"}
        self.timeline = [{"PR-H1": {"status": "success", "docnum": "HP690923-001", "written": written}}]
        item, = self._push("H1")["results"]
        self.assertEqual((item["status"], item["docnum"], item["written"]), ("entered", "HP690923-001", written))
        self.assertNotIn("record", item)
        description = next(t for t in tools.TOOLS if t["name"] == "cowork_push_documents")["description"]
        for words in ("payment cash = a cash purchase (HP, paid in cash)", "remembered", "simplified tax invoice = paid",
                      "do not claim it was picked for this one document", "periodic inventory",
                      # 测试窗口第 2 轮：入账一定报 ERP 单号；现 / 赊与科目都说清来源；不叫用户去 Express 核对
                      "always give the user its ERP document number (docnum)",
                      "whether it was entered as cash or credit and which account it went to, each with where that "
                      "came from", "never ask the user to check Express for the number, cash or credit, or the account",
                      "account_from = what decided the account", "express_history", "matched", "never guess a source",
                      "a person picked or changed it after this document came in", "the company page"):
            self.assertIn(words, description)
        for name in ("cowork_push_results", "cowork_document_detail"):
            self.assertIn(tools.WRITTEN_NOTE, next(t for t in tools.TOOLS if t["name"] == name)["description"])

    def test_an_entered_document_pushed_again_still_says_how_it_was_written(self):
        # 复审 L1：已入账的不再推，但回执照样带当时怎么写的；记录名只用来查
        self.docs["H1"].last_push_status = "success"
        self.docs["H1"].last_push_record = "PR-H1"
        written = {"payment": "cash", "payment_from": "remembered", "debit_account": "53-05-02-00"}
        asked = []
        with mock.patch.object(tools.application, "written", create=True,
                               new=lambda records: asked.append(list(records)) or {"PR-H1": written}):
            item, = self._push("H1")["results"]
        self.assertEqual(item, {"document": "H1", "status": "already_success", "written": written})
        self.assertEqual(asked, [["PR-H1"]])
        self.assertEqual((self.reads, self.sleeps), ([], []))

    def test_a_refusal_comes_back_with_its_reason_and_an_unknown_result_is_not_called_failed(self):
        self.timeline = [{"PR-H1": {"status": "pending"}, "PR-H2": {"status": "pending"}},
                         {"PR-H1": {"status": "manual", "reason": "[ACCOUNT_COMPANY_MISMATCH] wrong company"},
                          "PR-H2": {"status": "manual", "reason": "ERP_CONFIRMED_PUSH_RESULT_UNKNOWN",
                                    "result_unknown": True}}]
        results = self._push("H1", "H2")["results"]
        self.assertEqual((results[0]["status"], results[0]["reason"]),
                         ("failed", "said:[ACCOUNT_COMPANY_MISMATCH] wrong company"))  # 按用户语言的一句话
        self.assertEqual((results[1]["status"], results[1]["reason"]),
                         ("unconfirmed", "said:ERP_CONFIRMED_PUSH_RESULT_UNKNOWN"))
        self.assertEqual(len(self.reads), 2)
        description = next(t for t in tools.TOOLS if t["name"] == "cowork_push_documents")["description"]
        for word in ("entered", "docnum", "queued", "unconfirmed", "cowork_resolve_push_result", "cowork_push_results",
                     "do not wait"):
            self.assertIn(word, description)

    def test_no_result_within_the_limit_is_queued_with_a_hint(self):
        # 一直没回报（例如 CC 桌面版在写别的账套）：到点照实说已排队，上次没写成的原因带上，提示之后用 cowork_push_results 查
        self.timeline = [{"PR-H1": {"status": "pending"}},
                         {"PR-H1": {"status": "pending", "reason": "[EXPRESS_BUSY] locked"}}]
        out = self._push("H1")
        item, = out["results"]
        self.assertEqual((item["status"], item["last_attempt_failed"]), ("queued", "said:[EXPRESS_BUSY] locked"))
        self.assertIn("cowork_push_results", out["note"])
        self.assertIn("Do not wait or sleep", out["note"])
        self.assertLessEqual(self.clock[0], tools.PUSH_WAIT_SECONDS)
        self.assertGreaterEqual(self.clock[0], tools.PUSH_WAIT_SECONDS - tools.PUSH_WAIT_STEP)
        self.assertLessEqual(len(self.reads), tools.PUSH_WAIT_SECONDS // tools.PUSH_WAIT_STEP + 2)
        self.assertNotIn("record", item)

    def test_several_documents_share_one_limit_and_only_unsettled_ones_are_read_again(self):
        self.timeline = [{"PR-H1": {"status": "pending"}, "PR-H2": {"status": "pending"}, "PR-H3": {"status": "pending"}},
                         {"PR-H1": {"status": "success", "docnum": "RR691013-001"}, "PR-H2": {"status": "leased"},
                          "PR-H3": {"status": "pending"}}]
        results = self._push("H1", "H2", "H3")["results"]
        self.assertEqual([item["status"] for item in results], ["entered", "queued", "queued"])
        self.assertEqual(self.reads[1][1], ["PR-H1", "PR-H2", "PR-H3"])
        self.assertEqual(self.reads[2][1], ["PR-H2", "PR-H3"])  # 有了结论的不再读
        self.assertLessEqual(self.clock[0], tools.PUSH_WAIT_SECONDS)  # 三张共用一个时限

    def test_outside_the_relay_thread_it_reads_once_and_does_not_wait(self):
        # 页面兜底（网页请求线程）、引擎回调（long 队列）执行时：只读一次，不占共用名额干等
        frappe.local.pearnly_assistant = {"session_id": "sesn_7", "may_wait": False}
        self.timeline = [{"PR-H1": {"status": "pending"}}]
        self.assertEqual(self._push("H1")["results"][0]["status"], "queued")
        self.assertEqual((len(self.reads), self.sleeps), (1, []))
        # 已经有结论的（例如同一请求号重放）照样当场给出
        self.reads.clear()
        self.timeline = [{"PR-H1": {"status": "success", "docnum": "HP691013-001"}}]
        self.assertEqual(self._push("H1")["results"][0]["docnum"], "HP691013-001")

    def test_cc_desktop_offline_is_not_waited_for(self):
        self.online = False
        self.timeline = [{"PR-H1": {"status": "pending"}}]
        item, = self._push("H1")["results"]
        self.assertEqual((item["status"], item["cc_desktop_online"]), ("queued", False))
        self.assertEqual((len(self.reads), self.sleeps), (1, []))

    def test_documents_not_sent_are_not_waited_for(self):
        self.docs["H1"].last_push_status = "success"
        self.docs["H2"].push_connection = ""
        out = self._push("H1", "H2")
        self.assertEqual([item["status"] for item in out["results"]], ["already_success", "failed"])
        self.assertEqual((self.reads, self.sleeps), ([], []))
        self.assertNotIn("note", out)


def _pushed_module():
    spec = importlib.util.spec_from_file_location("cowork_express_pushed", COWORK / "push/express/pushed.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# 验收 F19 那张：这家记过现购 → HP，借费用科目、贷（小助手换成账套的现金科目）
CASH_PAYLOAD = {"direction": "purchase", "doctype": "HP", "doctype_src": "remembered", "items_account": "53-05-02-00",
                "doc_lane": "goods", "vat_amount": "70.00", "base_amount": "1000.00", "total_amount": "1070.00",
                "lines": [{"acc": "53-05-02-00", "side": "D", "amount": "1000.00"}], "supplier": {"tax_id": "0105562012340"}}


def _push_service(rows):
    """真的 `push.application.service`（其余各域用替身），SQL 交给 ``rows`` 回答，记下每次查询。"""
    queries = []
    fake = types.ModuleType("frappe")
    fake._ = lambda text, *args, **kwargs: text
    fake.session = types.SimpleNamespace(user="a@example.com")

    def sql(query, values=None, as_dict=False):
        queries.append((" ".join(query.split()), values))
        if len(values) == 1:  # `written`：不按人筛，只要入账成功的
            names, = values
            return [types.SimpleNamespace(**row) for row in rows if row["status"] == "success" and row["name"] in names]
        actor, names = values
        return [types.SimpleNamespace(**row) for row in rows if row["actor"] == actor and row["name"] in names]

    fake.db = types.SimpleNamespace(sql=sql)
    modules = {"frappe": fake, "pearnly_cowork": types.ModuleType("pearnly_cowork")}
    for name in ("access", "connections", "targets", "contracts", "contracts.history", "contracts.push", "history",
                 "push", "push.application", "push.application.provenance", "push.express", "push.express.common",
                 "push.express.prepare", "push.express.preflight", "push.express.history",
                 "push.mrerp", "push.native", "push.receipts", "targets.choice", "targets.contracts",
                 "targets.freeze", "targets.selection", "targets.identity"):
        modules["pearnly_cowork." + name] = mock.MagicMock()
    pushed = modules["pearnly_cowork.push.express.pushed"] = _pushed_module()  # 纯函数：用真的
    modules["pearnly_cowork.push.express"].pushed = pushed
    with mock.patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location("push_service_outcomes", COWORK / "push/application/service.py")
        service = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(service)
    return service, queries


class PushOutcomeServiceTests(unittest.TestCase):
    """推送的人看自己这几次推送此刻的结论：只认本人发起的记录，只读一条主键查询，单号只在入账成功时给。"""

    ROWS = [{"name": "PR1", "actor": "a@example.com", "status": "success", "reason": "",
             "external_docnum": "HP691013-001", "result_unknown": 0, "payload_json": json.dumps(CASH_PAYLOAD)},
            {"name": "PR2", "actor": "a@example.com", "status": "manual", "reason": "[X] no",
             "external_docnum": "HP-OLD", "result_unknown": 1, "payload_json": json.dumps(CASH_PAYLOAD)},
            {"name": "PR3", "actor": "b@example.com", "status": "success", "reason": "",
             "external_docnum": "HP691013-002", "result_unknown": 0, "payload_json": json.dumps(CASH_PAYLOAD)},
            {"name": "PR4", "actor": "a@example.com", "status": "leased", "reason": None,
             "external_docnum": None, "result_unknown": 0, "payload_json": "{}"},
            # 赊购、没有可判的信号（账套默认）；MR.ERP 的记录（载荷不是 Express 进 / 销项）；改版前的旧记录（没有 doctype_src）
            {"name": "PR5", "actor": "a@example.com", "status": "success", "reason": "",
             "external_docnum": "RR691013-001", "result_unknown": 0,
             "payload_json": json.dumps({**CASH_PAYLOAD, "doctype": "RR", "doctype_src": "config_default"})},
            {"name": "PR6", "actor": "a@example.com", "status": "success", "reason": "",
             "external_docnum": "PO-1", "result_unknown": 0, "payload_json": json.dumps({"menu": "purchase"})},
            {"name": "PR7", "actor": "a@example.com", "status": "success", "reason": "",
             "external_docnum": "HP690901-001", "result_unknown": 0,
             "payload_json": json.dumps({key: value for key, value in CASH_PAYLOAD.items() if key != "doctype_src"})}]
    CASH = {"payment": "cash", "payment_from": "remembered", "debit_account": "53-05-02-00", "vat": "claimed"}

    def test_only_my_own_pushes_and_the_number_only_when_entered(self):
        service, queries = _push_service(self.ROWS)
        found = service.outcomes(["PR1", "PR2", "PR3", "PR4", "PR1", ""])
        self.assertEqual(found, {
            "PR1": {"status": "success", "reason": "", "docnum": "HP691013-001", "result_unknown": False,
                    "written": self.CASH},
            "PR2": {"status": "manual", "reason": "[X] no", "docnum": "", "result_unknown": True, "written": None},
            "PR4": {"status": "leased", "reason": "", "docnum": "", "result_unknown": False, "written": None}})
        (query, values), = queries
        self.assertIn("WHERE actor=%s AND name IN %s", query)
        self.assertNotIn("FOR UPDATE", query)
        self.assertEqual(values, ("a@example.com", ("PR1", "PR2", "PR3", "PR4")))
        service.scope.require.assert_called_with("intake")

    def test_nothing_asked_reads_nothing_and_the_list_is_capped(self):
        service, queries = _push_service(self.ROWS)
        self.assertEqual(service.outcomes([]), {})
        self.assertEqual(queries, [])
        service.outcomes([f"PR{i}" for i in range(50)])
        self.assertEqual(len(queries[0][1][1]), service.OUTCOMES_MAX)
        self.assertEqual(service.OUTCOMES_MAX, tools.PUSH_MAX)

    def test_credit_default_and_records_that_are_not_express_or_older(self):
        service, _queries = _push_service(self.ROWS)
        found = service.outcomes(["PR5", "PR6", "PR7"])
        self.assertEqual(found["PR5"]["written"], {**self.CASH, "payment": "credit", "payment_from": "default"})
        self.assertIsNone(found["PR6"]["written"])  # MR.ERP：不是 Express 载荷，不猜
        # 改版前的记录没有 doctype_src：照实给现 / 赊，不编来源
        self.assertEqual(found["PR7"]["written"], {key: value for key, value in self.CASH.items() if key != "payment_from"})

    def test_results_list_reads_what_was_written_for_entered_records_only(self):
        # cowork_push_results 那条路：记录名来自已按权限取到的单据，这里不按人筛；没成功的、不是 Express 的不回
        service, queries = _push_service(self.ROWS)
        self.assertEqual(service.written(["PR3", "PR2", "PR6", "PR1", "PR3", None]),
                         {"PR3": self.CASH, "PR1": self.CASH})
        (query, values), = queries
        self.assertIn("WHERE status='success' AND name IN %s", query)
        self.assertEqual(values, (("PR3", "PR2", "PR6", "PR1"),))
        service.scope.require_read.assert_called_with()
        self.assertEqual(service.written([]), {})
        self.assertEqual(len(queries), 1)


class WrittenFactsTests(unittest.TestCase):
    """推送记录冻结的 Express 载荷 → 怎么写进去的（验收 F19）：现 / 赊按单据类型，来源照 doctype_src 一一对应。"""

    def setUp(self):
        self.pushed = _pushed_module()

    def test_each_source_maps_to_one_word_and_unknown_ones_are_left_out(self):
        expected = {"manual": "chosen", "remembered": "remembered", "task_default": "task_default",
                    "explicit": "document", "doc_type": "document_type", "profile": "supplier_profile",
                    "bank": "bank", "config_default": "default"}
        for source, word in expected.items():
            self.assertEqual(self.pushed.written({**CASH_PAYLOAD, "doctype_src": source})["payment_from"], word)
        self.assertNotIn("payment_from", self.pushed.written({**CASH_PAYLOAD, "doctype_src": "something_new"}))

    def test_the_account_says_where_it_came_from_and_older_records_leave_it_out(self):
        # 测试窗口第 2 轮：科目也要说清哪来的（载荷 account_source），一一对应；旧记录（笼统的 choice、没有这项）不给
        expected = {"chosen": "chosen", "remembered": "remembered", "task_default": "task_default",
                    "express_history": "express_history", "category_map": "matched", "config_default": "default"}
        for source, word in expected.items():
            out = self.pushed.written({**CASH_PAYLOAD, "account_source": source})
            self.assertEqual((out["debit_account"], out["account_from"]), ("53-05-02-00", word))
        for source in ("choice", "", None, "something_new"):
            self.assertNotIn("account_from", self.pushed.written({**CASH_PAYLOAD, "account_source": source}))
        self.assertNotIn("account_from", self.pushed.written(CASH_PAYLOAD))
        sale = {"direction": "sales", "doctype": "IV", "items_account": "41-01-00-00", "account_source": "category_map"}
        self.assertEqual(self.pushed.written(sale), {"payment": "credit", "revenue_account": "41-01-00-00",
                                                     "account_from": "matched"})
        # 没有科目就不说科目的来源
        self.assertNotIn("account_from", self.pushed.written({**CASH_PAYLOAD, "items_account": "",
                                                              "account_source": "config_default"}))

    def test_sales_vat_handling_and_what_is_not_express(self):
        sale = {"direction": "sales", "doctype": "HS", "doctype_src": "bank", "items_account": "41-01-00-00"}
        self.assertEqual(self.pushed.written(sale),
                         {"payment": "cash", "payment_from": "bank", "revenue_account": "41-01-00-00"})
        self.assertEqual(self.pushed.written({**sale, "doctype": "IV", "doctype_src": "config_default"})["payment"],
                         "credit")
        self.assertEqual(self.pushed.written({**sale, "pos": {"transfers": []}, "doctype_src": "manual"})["payment_from"],
                         "pos_summary")
        expense = {**CASH_PAYLOAD, "doc_lane": "expense", "vat_amount": "0.00", "vat_capitalized": "7.00"}
        self.assertEqual(self.pushed.written(expense)["vat"], "added_to_cost")
        self.assertEqual(self.pushed.written({**CASH_PAYLOAD, "vat_amount": "0.00"})["vat"], "none")
        for payload in (None, {}, {"direction": "purchase", "doctype": "IV"}, {"direction": "x", "doctype": "HP"}):
            self.assertIsNone(self.pushed.written(payload))


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

    def test_entered_rows_say_how_they_were_written_without_the_record_name(self):
        # cowork_push_results（push_state success）：每张入账的票带 written，推送记录名只用来查、不给模型（验收 F19）
        frappe.rows[0].update(last_push_status="success", last_push_record="PR9")
        frappe.rows[1].update(last_push_status="failed", last_push_record="PR8")
        frappe.rows[2].update(last_push_status="success", last_push_record="PR7")  # 旧记录 / MR.ERP：查不到就不给
        asked = []
        cash = {"payment": "cash", "payment_from": "remembered", "debit_account": "53-05-02-00"}

        def written(records):
            asked.append(sorted(records))
            return {"PR9": cash}

        with mock.patch.object(tools.application, "written", written, create=True):
            docs = json.loads(tools.list_documents({"push_state": "success", "limit": 5}))["documents"]
        self.assertEqual(asked, [["PR7", "PR9"]])
        self.assertIn("last_push_record", frappe.calls[0]["fields"])
        self.assertEqual([d.get("written") for d in docs], [cash, None, None, None, None])
        self.assertFalse(any("last_push_record" in d for d in docs))
        description = next(t for t in tools.TOOLS if t["name"] == "cowork_push_results")["description"]
        self.assertIn("never ask the user to check Express", description)

    def test_the_detail_of_an_entered_document_says_how_it_was_written(self):
        # 复审 M1：用户问「这张是不是现金」CC 最常查详情；入账的带 written，没入账的不查，记录名不给
        cash = {"payment": "cash", "payment_from": "remembered", "debit_account": "53-05-02-00", "vat": "claimed"}
        asked = []

        def written(records):
            asked.append(list(records))
            return {"PR9": cash}

        with mock.patch.object(tools.application, "written", written, create=True):
            entered = tools._detail({"name": "D1", "last_push_status": "success", "last_push_record": "PR9",
                                     "push_connection": "EXP1", "pages": []})
            failed = tools._detail({"name": "D2", "last_push_status": "manual", "last_push_record": "PR8",
                                    "push_connection": "EXP1", "pages": []})
            older = tools._detail({"name": "D3", "last_push_status": "success", "last_push_record": "PR7",
                                   "push_connection": "EXP1", "pages": []})
        self.assertEqual(entered["written"], cash)
        self.assertNotIn("written", failed)
        self.assertNotIn("written", older)  # 旧记录 / MR.ERP：查不到就不给
        self.assertEqual(asked, [["PR9"], ["PR7"]])
        self.assertFalse(any("last_push_record" in out or "PR9" in json.dumps(out) for out in (entered, failed, older)))
        description = next(t for t in tools.TOOLS if t["name"] == "cowork_document_detail")["description"]
        self.assertIn("never ask the user to check Express", description)

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

    def test_reminder_times_are_only_given_when_the_reminder_is_on(self):
        """2026-10-13 测试机：任务服务关着提醒也回生效的时刻（默认前一天 08:00、14:00），CC 把 remind=0 的任务说成
        提醒开着。列表、保存（单个 / 批量）、开关的结果都只在提醒开着时给时刻。"""
        times = [{"days_before": 1, "time": "08:00"}, {"days_before": 1, "time": "14:00"}]
        off = {"name": "R2", "title": "Bills", "enabled": 1, "remind": 0, "remind_times": times, "defaults": {}}
        on = {**off, "name": "R3", "remind": 1}
        with mock.patch.object(tools.rules, "listing", lambda: {"rules": [off, on]}):
            listed = json.loads(tools.folder_tasks({}))["tasks"]
        self.assertEqual([(row["remind"], "remind_times" in row) for row in listed], [(0, False), (1, True)])
        with mock.patch.object(tools.rules, "save", lambda values, cc_session=None: dict(off), create=True):
            saved = json.loads(tools.save_folder_task({"name": "R2", "title": "Bills"}))
        self.assertEqual((saved["remind"], "remind_times" in saved), (0, False))
        with mock.patch.object(tools.rules, "save_many", lambda values, cc_session=None: {"saved": [dict(off), dict(on)]},
                               create=True), mock.patch.object(tools, "_task_rows", lambda items: {}):
            batch = json.loads(tools.save_folder_tasks({"tasks": [{"name": "R2"}, {"name": "R3"}]}))["tasks"]
        self.assertEqual([(row["remind"], row.get("remind_times")) for row in batch], [(0, None), (1, times)])
        with mock.patch.object(tools.rules, "set_enabled", lambda name, enabled=None, remind=None, cc_session=None:
                               dict(off), create=True):
            switched = json.loads(tools.set_folder_task_enabled({"task": "R2", "remind": False}))
        self.assertEqual(switched, {"status": "saved", "task": "R2", "enabled": 1, "remind": 0})


class WaitingDocumentTests(unittest.TestCase):
    """2026-10-13 录屏（testcowork，泰文界面）：问「卡住的票，给我看第一张」，CC 先挑了已推送成功的 ABB6909-00007（ดาวเหนือ）。
    原因：「等复核」按 DocType 的草稿标记 staged 筛，可录入没有「确认」这一步，推送成功的票也一直是草稿；列表行还带着
    staged=1 与识别完整度 pending。现在「等着录入」按推送状态：没推过或推送失败，推送成功、在途的都不算。"""

    def setUp(self):
        frappe.calls.clear()

    def test_waiting_means_not_entered_yet_and_never_pushed_successfully(self):
        tools.list_documents({"push_state": "waiting"})
        call = frappe.calls[-1]
        self.assertEqual(call["filters"]["last_push_status"], ["not in", ["pending", "success"]])
        self.assertNotIn("staged", call["filters"])
        self.assertIsNone(call["or_filters"])
        # 与搜索一起用：搜索是 or 那组，「等着录入」仍是并列的条件，推送成功的照样排除
        tools.list_documents({"push_state": "waiting", "search": "ดาวเหนือ"})
        call = frappe.calls[-1]
        self.assertEqual(call["filters"]["last_push_status"], ["not in", ["pending", "success"]])
        self.assertIn(["seller_name", "like", "%ดาวเหนือ%"], call["or_filters"])

    def test_the_old_waiting_review_flag_from_open_chats_means_the_same(self):
        tools.list_documents({"waiting_review": True})
        self.assertEqual(frappe.calls[-1]["filters"], {"last_push_status": ["not in", ["pending", "success"]]})
        tools.list_documents({"waiting_review": False})
        self.assertEqual(frappe.calls[-1]["filters"], {})  # 旧的「已完成（非草稿）」没有对应的意思：不再按草稿筛

    def test_rows_do_not_carry_flags_that_read_as_waiting(self):
        tools.list_documents({})
        fields = frappe.calls[-1]["fields"]
        for hidden in ("staged", "quality_status"):
            self.assertNotIn(hidden, fields)
        for shown in ("last_push_status", "last_push_reason", "last_pushed_at"):
            self.assertIn(shown, fields)

    def test_descriptions_say_what_waiting_means(self):
        declared = {tool["name"]: tool for tool in tools.TOOLS}
        listing = declared["cowork_list_documents"]
        properties = listing["input_schema"]["properties"]
        self.assertIn("waiting", properties["push_state"]["enum"])
        self.assertNotIn("waiting_review", properties)
        self.assertIn("waiting = not in the ERP yet: never pushed or the push failed", listing["description"])
        self.assertIn("pushed successfully or on its way is never waiting", listing["description"])
        self.assertNotIn("waiting for review", listing["description"])
        self.assertIn("push_state waiting in cowork_list_documents", declared["cowork_push_results"]["description"])


def _filed_row(name, connection, account_set):
    return {"name": name, "push_connection": connection, "push_account_set": account_set,
            "last_push_status": "success", "pages": json.dumps([{"fields": {"direction": "purchase"}}])}


class CompanyScopeTests(unittest.TestCase):
    """2026-10-15 测试机：在公司主档「T.8 ซินเซียร์ไอซ์」的 9 月对话里问「这个月哪些单进了 Express」，CC 调 cowork_list_documents
    没带账套，答出整个工作空间 9 月推送的 128 张（别家三份账套的）。公司对话里不带账套 / 连接 / 公司的调用，服务端默认只取
    这家的账套（`company_tools.default_scope` 给），all_companies 放开；没挂公司、点了账套的照旧。"""

    SETS = [{"connection": "EXP1", "key": "s:\\69exp\\69sincer", "branch": "00000"},
            {"connection": "EXP1", "key": "s:\\68exp\\68sincer", "branch": "00000"},
            {"connection": "EXP1", "key": "s:\\69exp\\69sincer01", "branch": "00001"}]
    SCOPE = {"company": "T.8", "company_id": "CO-1", "conversation_company": True, "account_sets": 3}

    def setUp(self):
        history = types.ModuleType("pearnly_cowork.contracts.history")
        history.primary_page_fields = lambda pages: (pages[0].get("fields") or {}) if pages else {}
        push = types.ModuleType("pearnly_cowork.contracts.push")
        push.normalize_direction = lambda value: value if value in ("purchase", "sales") else None
        contracts = sys.modules.setdefault("pearnly_cowork.contracts", types.ModuleType("pearnly_cowork.contracts"))
        self.asked = []
        self.answer = None
        company_tools = types.ModuleType("pearnly_cowork.assistant.company_tools")
        company_tools.default_scope = lambda tool_input: self.asked.append(dict(tool_input)) or self.answer
        package = types.ModuleType("pearnly_cowork.assistant")
        package.company_tools = company_tools
        patcher = mock.patch.dict(sys.modules, {"pearnly_cowork.contracts.history": history,
                                                "pearnly_cowork.contracts.push": push,
                                                "pearnly_cowork.assistant": package,
                                                "pearnly_cowork.assistant.company_tools": company_tools})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name, module in (("history", history), ("push", push)):
            patcher = mock.patch.object(contracts, name, module, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        frappe.rows = [_filed_row("D1", "EXP1", "S:\\69EXP\\69SINCER"), _filed_row("D2", "EXP1", "S:\\69EXP\\DEMO01"),
                       _filed_row("D3", "EXP1", "S:\\69EXP\\69SINCER01")]
        self.addCleanup(setattr, frappe, "rows", [])
        frappe.calls.clear()
        frappe.grouped.clear()
        frappe.local.pearnly_assistant = {"session_id": "sesn_1"}
        self.addCleanup(setattr, frappe.local, "pearnly_assistant", None)

    def test_a_company_conversation_only_sees_that_companys_account_sets(self):
        self.answer = {"scope": dict(self.SCOPE), "sets": [dict(item) for item in self.SETS]}
        out = json.loads(tools.list_documents({"push_state": "success", "invoice_date_from": "2026-09-01",
                                               "invoice_date_to": "2026-09-30"}))
        filters = frappe.calls[0]["filters"]
        self.assertEqual(filters["push_connection"], ["in", ["EXP1"]])
        # 两个年度、两个分店的账套都在
        self.assertEqual(filters["push_account_set"], ["in", sorted(item["key"] for item in self.SETS)])
        self.assertEqual(filters["invoice_date"], ["between", ["2026-09-01", "2026-09-30"]])
        self.assertEqual(out["scope"], self.SCOPE)
        # 各账套几张也只在这家的账套里数
        self.assertTrue(frappe.grouped)
        for call in frappe.grouped:
            self.assertEqual(call["filters"]["push_account_set"], ["in", sorted(item["key"] for item in self.SETS)])
        # 推送结果是同一个处理函数，同样带上范围
        frappe.calls.clear()
        json.loads(tools.list_documents({"push_state": "failed"}))
        self.assertEqual(frappe.calls[0]["filters"]["push_connection"], ["in", ["EXP1"]])

    def test_a_work_branch_narrows_to_its_account_sets(self):
        branch = [item for item in self.SETS if item["branch"] == "00001"]
        self.answer = {"scope": {**self.SCOPE, "account_sets": 1, "branch": "00001"}, "sets": branch}
        out = json.loads(tools.list_documents({}))
        self.assertEqual(frappe.calls[0]["filters"]["push_account_set"], ["in", ["s:\\69exp\\69sincer01"]])
        self.assertEqual(out["scope"]["branch"], "00001")

    def test_all_companies_lifts_the_default_and_answers_company_by_company(self):
        self.answer = {"scope": {"all_companies": True}, "sets": None}
        out = json.loads(tools.list_documents({"all_companies": True, "push_state": "success"}))
        filters = frappe.calls[0]["filters"]
        self.assertNotIn("push_connection", filters)
        self.assertNotIn("push_account_set", filters)
        self.assertEqual(out["scope"], {"all_companies": True})
        self.assertEqual({row["account_set_key"] for row in out["by_account_set"]},
                         {row["push_account_set"] for row in frappe.rows})  # 逐家列

    def test_an_account_set_the_call_names_is_not_overridden(self):
        self.answer = {"scope": dict(self.SCOPE), "sets": [dict(item) for item in self.SETS]}
        out = json.loads(tools.list_documents({"connection": "EXP1", "account_set_key": "S:\\69EXP\\DEMO01"}))
        self.assertEqual(self.asked, [])
        self.assertEqual((frappe.calls[0]["filters"]["push_connection"], frappe.calls[0]["filters"]["push_account_set"]),
                         ("EXP1", "S:\\69EXP\\DEMO01"))
        self.assertNotIn("scope", out)

    def test_a_connection_alone_does_not_lift_the_company_scope(self):
        # 2026-10-15 测试机：模型把说明里的连接照抄进来，旧代码因此不按公司筛，列出了别家 128 张
        self.answer = {"scope": dict(self.SCOPE), "sets": [{"connection": "EXP1", "key": "s:\\69exp\\69sincer"},
                                                           {"connection": "EXP2", "key": "s:\\69exp\\demo01"}]}
        out = json.loads(tools.list_documents({"connection": "EXP1", "push_state": "success"}))
        self.assertEqual(len(self.asked), 1)
        filters = frappe.calls[0]["filters"]
        self.assertEqual((filters["push_connection"], filters["push_account_set"]), (["in", ["EXP1"]], ["in", ["s:\\69exp\\69sincer"]]))
        self.assertEqual(out["scope"], self.SCOPE)
        # 这家在点的连接上没有账套：回空，不放开
        frappe.calls.clear()
        out = json.loads(tools.list_documents({"connection": "EXP9", "push_state": "success"}))
        self.assertEqual((out["count"], frappe.calls), (0, []))

    def test_outside_a_company_conversation_nothing_changes(self):
        frappe.local.pearnly_assistant = None
        out = json.loads(tools.list_documents({"push_state": "success"}))
        self.assertEqual(self.asked, [])  # 不在 CC 对话里：不碰主档
        self.assertEqual(frappe.calls[0]["filters"], {"last_push_status": "success"})
        self.assertNotIn("scope", out)
        frappe.local.pearnly_assistant = {"session_id": "sesn_2"}
        self.answer = None  # 对话没挂公司
        frappe.calls.clear()
        out = json.loads(tools.list_documents({"push_state": "success"}))
        self.assertEqual(frappe.calls[0]["filters"], {"last_push_status": "success"})
        self.assertNotIn("scope", out)
        self.assertEqual(len(out["by_account_set"]), 3)

    def test_sets_on_two_connections_are_matched_pair_by_pair(self):
        # 两条连接上的账套不一样：两列分别筛会多圈进 EXP1 上的 DEMO01（它的键在 EXP2 上属于这家），取回后逐对核对
        frappe.rows.append(_filed_row("D4", "EXP2", "S:\\69EXP\\DEMO01"))
        self.answer = {"scope": dict(self.SCOPE), "sets": [{"connection": "EXP1", "key": "s:\\69exp\\69sincer"},
                                                           {"connection": "EXP2", "key": "s:\\69exp\\demo01"}]}
        out = json.loads(tools.list_documents({}))
        self.assertEqual([row["name"] for row in out["documents"]], ["D1", "D4"])
        self.assertEqual(out["total"], 2)
        self.assertEqual({(row["connection"], row["account_set_key"]) for row in out["by_account_set"]},
                         {("EXP1", "S:\\69EXP\\69SINCER"), ("EXP2", "S:\\69EXP\\DEMO01")})

    def test_a_company_without_account_sets_lists_nothing_and_a_wrong_company_fails(self):
        self.answer = {"scope": {**self.SCOPE, "account_sets": 0}, "sets": []}
        out = json.loads(tools.list_documents({}))
        self.assertEqual((out["documents"], out["total"], out["scope"]["account_sets"]), ([], 0, 0))
        self.assertEqual(frappe.calls, [])
        self.answer = {"failed": "unknown company: pass an id from cowork_companies"}
        self.assertEqual(json.loads(tools.list_documents({"company": "nope"}))["status"], "failed")
        frappe.local.pearnly_assistant = None
        self.asked.clear()
        tools.list_documents({"company": "CO-2"})  # 没挂公司也能点名查一家
        self.assertEqual(self.asked, [{"company": "CO-2"}])

    def test_scheduled_tasks_follow_the_same_scope(self):
        rules = [{"name": "R1", "connection": "EXP1", "account_set_key": "S:\\69EXP\\69SINCER", "remind": 0},
                 {"name": "R2", "connection": "EXP1", "account_set_key": "S:\\69EXP\\DEMO01", "remind": 0}]
        self.answer = {"scope": dict(self.SCOPE), "sets": [dict(item) for item in self.SETS]}
        with mock.patch.object(tools.rules, "listing", lambda: {"rules": [dict(row) for row in rules]}):
            out = json.loads(tools.folder_tasks({}))
            self.assertEqual(([task["name"] for task in out["tasks"]], out["scope"]), (["R1"], self.SCOPE))
            self.answer = {"scope": {"all_companies": True}, "sets": None}
            out = json.loads(tools.folder_tasks({"all_companies": True}))
            self.assertEqual([task["name"] for task in out["tasks"]], ["R1", "R2"])
            frappe.local.pearnly_assistant = None
            out = json.loads(tools.folder_tasks({}))
            self.assertEqual(([task["name"] for task in out["tasks"]], "scope" in out), (["R1", "R2"], False))

    def test_the_scope_tools_offer_the_lift_and_the_guidance_says_when(self):
        declared = {tool["name"]: tool for tool in tools.TOOLS}
        for name in ("cowork_list_documents", "cowork_push_results", "cowork_folder_tasks"):
            properties = declared[name]["input_schema"]["properties"]
            self.assertEqual(properties["all_companies"]["type"], "boolean", name)
            self.assertIn("company", properties, name)
        self.assertTrue(any("all_companies true" in line for line in tools.GUIDANCE))


class LocalTimeTests(unittest.TestCase):
    """2026-10-13 录屏（testcowork，泰文界面、曼谷用户）：推送时间按 UTC 原样交给模型，CC 把 15:40 推送成功说成「今天 08:40」。
    工具结果里的时间点一律是站点时区的 YYYY-MM-DD HH:MM：存 UTC 的字段由工具点名、换算一次；存站点时区的只换格式（不再偏）；
    只有日期的照旧；当版本号原样交回的字符串不动。"""

    def setUp(self):
        ListAndRunsTests.setUp(self)
        for name, value in (("utils", types.SimpleNamespace(get_system_timezone=lambda: "Asia/Bangkok")),
                            ("db", types.SimpleNamespace(commit=lambda: None, rollback=lambda: None,
                                                         get_value=lambda *a, **k: ("express", 3)))):
            patcher = mock.patch.object(frappe, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _module(self, name, **attrs):
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        parent, _, child = name.rpartition(".")
        package = sys.modules.setdefault(parent, types.ModuleType(parent))
        for patcher in (mock.patch.dict(sys.modules, {name: module}),
                        mock.patch.object(package, child, module, create=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        return module

    def test_one_rule_for_every_time(self):
        self.assertEqual(tools._local_time(datetime(2026, 10, 13, 8, 40, 12, 345678), utc=True), "2026-10-13 15:40")
        self.assertEqual(tools._local_time(datetime(2026, 10, 13, 15, 40, 12)), "2026-10-13 15:40")  # 站点时区：不再偏
        self.assertEqual(tools._local_time(datetime(2026, 10, 13, 20, 30, tzinfo=timezone.utc)), "2026-10-14 03:30")
        self.assertEqual(tools._local_time("2026-10-13T08:40:12Z"), "2026-10-13 15:40")  # 带 Z 的本身就说了是 UTC
        self.assertEqual(tools._local_time("2026-10-13T08:40:12.5", utc=True), "2026-10-13 15:40")
        self.assertEqual(tools._local_time("2026-10-13 15:40:12.123456"), "2026-10-13 15:40")
        for kept in ("2026-10-13", "", "soon", None, date(2026, 10, 13)):
            self.assertEqual(tools._local_time(kept, utc=True), kept)
        with mock.patch.object(frappe, "utils", types.SimpleNamespace(get_system_timezone=lambda: "Asia/Tokyo")):
            self.assertEqual(tools._local_time(datetime(2026, 10, 13, 8, 40), utc=True), "2026-10-13 17:40")

    def test_the_push_time_from_the_recording_is_said_in_bangkok_time(self):
        frappe.rows[0].update(last_push_status="success", last_pushed_at=datetime(2026, 10, 13, 8, 40, 5),
                              creation=datetime(2026, 10, 13, 15, 30, 9, 120000), invoice_date=date(2026, 10, 1))
        doc = json.loads(tools.list_documents({"limit": 1}))["documents"][0]
        self.assertEqual((doc["last_pushed_at"], doc["creation"], doc["invoice_date"]),
                         ("2026-10-13 15:40", "2026-10-13 15:30", "2026-10-01"))
        pushed = json.loads(tools.list_documents({"push_state": "success", "totals": True, "limit": 1}))["documents"]
        self.assertEqual(pushed[0]["last_pushed_at"], "2026-10-13 15:40")  # 按页看、算合计的那条路也一样

    def test_only_named_strings_change_and_version_tokens_stay(self):
        out = json.loads(tools._json({
            "card": {"modified": "2026-10-13 15:40:12.123456", "creation": datetime(2026, 10, 13, 15, 41, 2),
                     "activity": [{"creation": datetime(2026, 10, 13, 15, 42)}], "due_date": date(2026, 10, 20)},
            "archived_at": datetime(2026, 10, 13, 8, 43), "presets": [{"set_at": "2026-10-13 15:44:59.5"}],
            "remind_times": [{"days_before": 1, "time": "08:00"}], "read_at": "2026-10-13 15:45"},
            utc=("archived_at",), local=("set_at",)))
        self.assertEqual(out["card"]["modified"], "2026-10-13 15:40:12.123456")  # 改卡片要原样交回
        self.assertEqual((out["card"]["creation"], out["card"]["activity"][0]["creation"], out["card"]["due_date"]),
                         ("2026-10-13 15:41", "2026-10-13 15:42", "2026-10-20"))
        self.assertEqual((out["archived_at"], out["presets"][0]["set_at"]), ("2026-10-13 15:43", "2026-10-13 15:44"))
        self.assertEqual((out["remind_times"], out["read_at"]), ([{"days_before": 1, "time": "08:00"}], "2026-10-13 15:45"))

    def test_tools_name_the_fields_stored_in_utc(self):
        self._module("pearnly_cowork.stocktake.desk_api", api_list_stock_counts=lambda start, limit: [
            {"name": "S1", "title": "March", "creation": datetime(2026, 10, 13, 2, 0)}])
        self._module("pearnly_cowork.work.api", boards_list=lambda: [
            {"name": "B1", "archived_at": datetime(2026, 10, 12, 18, 30)}])
        self.assertEqual(json.loads(tools.stocktake_list({}))["result"][0]["creation"], "2026-10-13 09:00")
        self.assertEqual(json.loads(tools.work_boards({}))["result"][0]["archived_at"], "2026-10-13 01:30")
        menu = {"account_set_key": "K", "observed_at": "2026-10-13T08:40:00.123456", "error_at": None, "kinds": {},
                "defaults": {"observed_at": "2026-10-13T01:00:00", "error_at": "2026-10-13T02:00:00", "fields": {}}}
        with mock.patch.object(tools.connections_api, "mrerp_menu", lambda name, key: json.loads(json.dumps(menu)),
                               create=True):
            out = json.loads(tools.mrerp_setup({"connection": "M1", "account_set_key": "K"}))
        self.assertEqual((out["observed_at"], out["error_at"]), ("2026-10-13 15:40", None))
        self.assertEqual((out["defaults"]["observed_at"], out["defaults"]["error_at"]),
                         ("2026-10-13 08:00", "2026-10-13 09:00"))

    def test_vat_draft_says_when_the_books_were_read_in_local_time(self):
        self._module("pearnly_cowork.tax.service", vat_draft=lambda *args: {
            "status": "drafted", "draft": "CTD-1", "period": "2026-09", "boxes": {},
            "readback": {"ref": "RB1", "read_at": "2026-10-13T08:40:12Z"}, "deadlines": {"paper": "2026-10-15"}})
        out = json.loads(tools.vat_draft({"connection": "C", "account_set_key": "K", "period": "2026-09",
                                          "workbook": False}))
        self.assertEqual(out["readback"], {"ref": "RB1", "read_at": "2026-10-13 15:40"})
        self.assertEqual(out["deadlines"], {"paper": "2026-10-15"})

    def test_times_stored_in_site_time_are_only_reformatted(self):
        run = {"name": "RUN1", "trigger": "schedule", "started_at": datetime(2026, 10, 13, 9, 0, 4, 5),
               "scanned_at": datetime(2026, 10, 13, 9, 1, 30)}
        rule = {"name": "R1", "active_since": datetime(2026, 10, 1, 8, 15, 59)}
        task = {"name": "R1", "last_run": {"started_at": datetime(2026, 10, 13, 9, 0, 4), "found": 3}}
        with mock.patch.object(tools.rules, "runs", lambda name: {"rule": dict(rule), "runs": [run]}), \
                mock.patch.object(tools.rules, "listing", lambda: {"rules": [task]}):
            runs = json.loads(tools.folder_task_runs({"task": "R1"}))
            listed = json.loads(tools.folder_tasks({}))["tasks"][0]
        self.assertEqual((runs["runs"][0]["started_at"], runs["runs"][0]["scanned_at"], runs["task"]["active_since"]),
                         ("2026-10-13 09:00", "2026-10-13 09:01", "2026-10-01 08:15"))
        self.assertEqual(listed["last_run"]["started_at"], "2026-10-13 09:00")
        self._module("pearnly_cowork.recon_center.presets", listing=lambda kind: [
            {"scope": "bank:1", "rule": "card_fee", "value": "no", "set_at": "2026-10-13 15:40:12.654321"}])
        presets = json.loads(tools.recon_presets({"kind": "bank"}))["presets"]
        self.assertEqual(presets[0]["set_at"], "2026-10-13 15:40")

    def test_cc_is_told_the_times_are_local(self):
        self.assertIn("Times in tool results are already the user's local time", " ".join(tools.GUIDANCE))


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
        self.assertEqual((out["files_added_to_chat"], out["files_note"]), (True, tools.FILES_ADDED_NOTE))

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
        # 明说放进了这段对话、不在电脑上的哪个文件夹（2026-10-13 测试机：CC 说放进了「结果文件夹」）
        self.assertEqual((out["files_added_to_chat"], out["files_note"]), (True, tools.FILES_ADDED_NOTE))
        self.assertIn("not saved to any folder", tools.FILES_ADDED_NOTE)
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
        self.assertNotIn("files_added_to_chat", out)
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


class AccountSetNameTests(unittest.TestCase):
    """2026-10-13 录屏（泰文界面）：CC 对会计说账套「demo02」——Express 数据文件夹名。会计认公司，不认文件夹代码：
    点了某份账套的结果都在键前面带上给人看的名字（同录入下拉），Cowork 的说明里也写明按公司名说、键只用在调工具。"""

    KEY = "C:\\ExpressI\\70EXP\\demo02"
    LABEL = "70EXP · T.9 บริษัท ตัวอย่าง จำกัด"
    CATALOG = {"account_sets": [
        {"source_id": KEY, "label": "T.9 บริษัท ตัวอย่าง จำกัด", "attributes": {"root_label": "70EXP"}},
        {"source_id": "C:\\ExpressI\\70EXP\\audit", "label": "Audit copy", "attributes": {"root_label": "70EXP"}},
        {"source_id": "C:\\ExpressI\\69EXP\\old", "label": "Old Co", "active": False}],
        "groups": [{"value": "c:\\expressi\\70exp", "label": "70EXP", "items": [
            {"value": KEY, "label": "T.9 บริษัท ตัวอย่าง จำกัด", "description": ""},
            {"value": "C:\\ExpressI\\70EXP\\audit", "label": "Audit copy", "description": "บริษัท ตัวอย่าง จำกัด"}]}]}

    def setUp(self):
        ListAndRunsTests.setUp(self)
        self.reads = []
        patcher = mock.patch.object(tools.connections, "targets",
                                    lambda name: self.reads.append(name) or json.loads(json.dumps(self.CATALOG)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def assertNamedFirst(self, out, key_field, label=None):
        self.assertEqual(out["account_label"], label or self.LABEL)
        self.assertLess(list(out).index("account_label"), list(out).index(key_field))

    def test_cowork_tells_cc_to_name_account_sets_by_company(self):
        self.assertEqual(tools.GUIDANCE[0],
            "When you talk to the user, name an account set by its company name (the label or account_label in tool "
            "results), never by its folder code, path or key; use keys only in tool calls.")
        hooks = (COWORK / "hooks.py").read_text(encoding="utf-8")
        self.assertRegex(hooks, r'pearnly_assistant_guidance = \["pearnly_cowork\.assistant\.tools\.GUIDANCE"[,\]]')

    def test_names_follow_the_intake_list(self):
        names = tools._catalog_names(self.CATALOG)
        self.assertEqual(names[tools._account_key("c:/expressi/70exp/DEMO02/")], self.LABEL)  # 大小写、分隔符不计
        self.assertEqual(names[tools._account_key("C:\\ExpressI\\70EXP\\audit")],
                         "70EXP · Audit copy（บริษัท ตัวอย่าง จำกัด）")
        self.assertEqual(names[tools._account_key("C:\\ExpressI\\69EXP\\old")], "Old Co")  # 下拉里没有的用目录里的名字

    def test_document_lists_and_details_name_the_account_set_first(self):
        for row in frappe.rows:
            row["push_account_set"] = self.KEY.lower()
        docs = json.loads(tools.list_documents({"limit": 5}))["documents"]
        for doc in docs:
            self.assertNamedFirst(doc, "push_account_set")
        self.assertEqual(self.reads, ["EXP1"])  # 一次调用每条连接只读一次目录
        detail = tools._detail({"name": "D1", "push_connection": "EXP1", "push_account_set": self.KEY, "pages": []})
        self.assertNamedFirst(detail, "push_account_set")
        with mock.patch.object(tools.storage, "read", lambda name: name), \
                mock.patch.object(tools.storage, "project", create=True,
                                  new=lambda name: {"name": name, "push_connection": "EXP1",
                                                    "push_account_set": self.KEY, "pages": [{"fields": {}}]}):
            self.assertNamedFirst(tools._document("H1"), "push_account_set")

    def test_push_choices_stock_group_and_task_runs_name_the_account_set(self):
        doc = types.SimpleNamespace(push_connection="EXP1", push_account_set=self.KEY, posting_kind="stock")
        with mock.patch.object(tools.storage, "read", lambda name: doc), \
                mock.patch.object(tools.application, "choices_view", create=True,
                                  new=lambda *a: {"needed": [], "options": {}, "account_set": self.KEY.lower()}):
            self.assertNamedFirst(json.loads(tools.push_choices({"document": "H1"})), "account_set")
        with mock.patch.object(tools.connections_api, "stock_acc_group", create=True,
                               new=lambda name, key: {"account_set_key": key.lower(), "known": True, "groups": []}):
            self.assertNamedFirst(json.loads(tools.stock_group({"connection": "EXP1", "account_set_key": self.KEY})),
                                  "account_set_key")
        # 任务上存着的名字挪到键前面，不再读目录
        self.reads.clear()
        rule = {"name": "R1", "connection": "EXP1", "account_set_key": self.KEY, "account_label": "70EXP · Saved name"}
        with mock.patch.object(tools.rules, "runs", lambda name: {"rule": dict(rule), "runs": []}):
            task = json.loads(tools.folder_task_runs({"task": "R1"}))["task"]
        self.assertNamedFirst(task, "account_set_key", "70EXP · Saved name")
        self.assertEqual(self.reads, [])

    def test_an_unreadable_catalogue_leaves_the_key_alone(self):
        def refuse(name):
            raise frappe.PermissionError("no access")

        tools.connections.targets = refuse
        for row in frappe.rows:
            row["push_account_set"] = self.KEY
        doc = json.loads(tools.list_documents({"limit": 1}))["documents"][0]
        self.assertEqual(doc["push_account_set"], self.KEY)
        self.assertNotIn("account_label", doc)


def _matches(row, filters, or_filters) -> bool:
    """替身库的筛选（只认工具用到的几种写法）：等值（不分大小写）、not in、between、>=、is set / not set、like。"""
    def holds(field, condition):
        value = row.get(field)
        if not isinstance(condition, list):
            return str(value or "").casefold() == str(condition).casefold()
        op, arg = condition
        if op == "not in":
            return (value or "") not in arg
        if op == "between":
            return value is not None and arg[0] <= value <= arg[1]
        if op == ">=":
            return value is not None and value >= arg
        if op == "=":
            return value == arg
        if op == "is":
            return bool(value) == (arg == "set")
        if op == "like":
            return arg.strip("%").casefold() in str(value or "").casefold()
        raise AssertionError(op)

    return (all(holds(field, condition) for field, condition in (filters or {}).items())
            and (not or_filters or any(holds(field, [op, arg]) for field, op, arg in or_filters)))


class AllCompaniesTests(unittest.TestCase):
    """第 2 条（2026-10-13 录屏 L8，testcowork，泰文界面）：对话一直在谈 แก้วกานดา 9 月的税，会计问「เดือนนี้ยังมีบิลไหนค้าง」，
    CC 按 10 月、带着 แก้วกานดา 的账套查，答「没有」；会计补「เดือน 9 ทุกเจ้าเลย」，CC 回了 3 张——全是 แก้วกานดา 的
    （它的销项贷项单 CN6909-0001、进项贷项单 MK-CN-6909-007、ทองหล่อ 的订金收据 4/0091），也没说只查了一家。
    早上推送后另两家同样卡着的单据（ดาวเหนือ、ภูผาแก้ว 各自的 CN6909-0001、ดาวเหนือ 的美元票）都没出现。

    复现：只查一家的是 cowork_list_documents 的 account_set_key（连同 connection）沿用了上文那家公司；工具照参数筛，
    结果里看不出范围。修法：说明里点明「所有公司」不带公司筛选；没限定公司时结果另给 by_account_set，同一时间范围里
    有单据的账套都列、各几张（0 张也列），按全部符合的单据数，不只这一页。"""

    KEAW, DAO, PHU, SUAN, OLD = (r"c:\account\70exp\dem001", r"c:\account\70exp\dem003", r"c:\account\70exp\dem002",
                                 r"c:\account\70exp\dem004", r"c:\account\70exp\dem005")
    CATALOG = {"account_sets": [
        {"source_id": KEAW, "label": "70EXP · D.1 บริษัท แก้วกานดา บิวตี้ สโตร์ จำกัด"},
        {"source_id": DAO, "label": "70EXP · D.3 บริษัท ดาวเหนือ ไอที ซัพพลาย จำกัด"},
        {"source_id": PHU, "label": "70EXP · D.2 บริษัท ภูผาแก้ว จำกัด"},
        {"source_id": SUAN, "label": "70EXP · D.4 Suan Co"},
        {"source_id": OLD, "label": "70EXP · D.5 Old Co"}]}

    def setUp(self):
        ListAndRunsTests.setUp(self)

        def doc(name, account, day, state, direction, kind="tax_invoice", connection="EXP1"):
            fields = {"direction": direction, "document_type": kind, "subtotal": "100", "vat": "7"}
            return {"name": name, "invoice_no": name, "invoice_date": day, "push_connection": connection if account else "",
                    "push_account_set": account, "last_push_status": state,
                    "last_push_reason": "document.credit_note" if state == "manual" else "", "total_amount": "107",
                    "pages": json.dumps([{"fields": fields}])}

        self.rows = [
            doc("CN6909-0001/KEAW", self.KEAW, "2026-09-22", "manual", "sales", "credit_note"),
            doc("MK-CN-6909-007", self.KEAW, "2026-09-23", "manual", "purchase", "credit_note"),
            doc("4/0091", self.KEAW, "2026-09-06", "", "purchase", "receipt"),
            doc("INV6909-0010", self.KEAW, "2026-09-10", "success", "sales"),
            doc("CN6909-0001/DAO", self.DAO, "2026-09-20", "manual", "sales", "credit_note"),
            doc("NF-2026-091744", self.DAO, "2026-09-17", "manual", "purchase"),
            doc("IV6909-0005", self.DAO, "2026-09-05", "success", "sales"),
            doc("CN6909-0001/PHU", self.PHU, "2026-09-25", "manual", "sales", "credit_note"),
            doc("TT-6909-0087", self.PHU, "2026-09-12", "pending", "purchase"),
            doc("SC6909-0001", self.SUAN, "2026-09-03", "success", "sales"),
            doc("OLD6908-0001", self.OLD, "2026-08-15", "", "purchase"),
            doc("SK-6910-01284", "", "2026-09-30", "", "purchase", "simplified_tax_invoice"),
        ]
        self.grouped = []
        patcher = mock.patch.object(frappe, "get_list", self._get_list)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(tools.connections, "targets", lambda name: json.loads(json.dumps(self.CATALOG)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _get_list(self, doctype, **kwargs):
        found = [dict(row) for row in self.rows if _matches(row, kwargs.get("filters"), kwargs.get("or_filters"))]
        if kwargs.get("group_by"):
            self.grouped.append(kwargs)
            field, counts = kwargs["group_by"], {}
            for row in found:
                counts[row.get(field)] = counts.get(row.get(field), 0) + 1
            return [{field: value, "count": count} for value, count in counts.items()]
        if kwargs.get("fields") and isinstance(kwargs["fields"][0], dict):
            return [{"total": len(found)}]
        start, size = kwargs.get("limit_start") or 0, kwargs.get("limit_page_length")
        return found[start:start + size if size else None]

    SEPTEMBER = {"push_state": "waiting", "invoice_date_from": "2026-09-01", "invoice_date_to": "2026-09-30"}

    def counts(self, out):
        return [(item.get("account_label", item.get("push_target")), item["count"]) for item in out["by_account_set"]]

    def test_the_recorded_call_kept_the_one_company_from_earlier_in_the_chat(self):
        # 录屏里 CC 的那一步：9 月、等着录入，连接与账套还是上文的 แก้วกานดา —— 只回这一家的 3 张
        out = json.loads(tools.list_documents({**self.SEPTEMBER, "connection": "EXP1", "account_set_key": self.KEAW}))
        self.assertEqual(sorted(d["invoice_no"] for d in out["documents"]),
                         ["4/0091", "CN6909-0001/KEAW", "MK-CN-6909-007"])
        self.assertEqual({d["account_label"] for d in out["documents"]},
                         {"70EXP · D.1 บริษัท แก้วกานดา บิวตี้ สโตร์ จำกัด"})
        self.assertNotIn("by_account_set", out)  # 限定了一家：不另列各家
        # 不带公司筛选：三家卡着的都在，另外没选账套的那张手机拍的简易税票也在
        everyone = json.loads(tools.list_documents(self.SEPTEMBER))
        self.assertEqual({d["invoice_no"] for d in everyone["documents"]},
                         {"CN6909-0001/KEAW", "MK-CN-6909-007", "4/0091", "CN6909-0001/DAO", "NF-2026-091744",
                          "CN6909-0001/PHU", "SK-6910-01284"})

    def test_all_companies_are_counted_company_by_company_including_those_with_none(self):
        out = json.loads(tools.list_documents(self.SEPTEMBER))
        self.assertEqual(self.counts(out), [
            ("70EXP · D.1 บริษัท แก้วกานดา บิวตี้ สโตร์ จำกัด", 3),
            ("70EXP · D.3 บริษัท ดาวเหนือ ไอที ซัพพลาย จำกัด", 2),
            ("70EXP · D.2 บริษัท ภูผาแก้ว จำกัด", 1),
            ("not chosen yet", 1),          # 没选账套的另算一行
            ("70EXP · D.4 Suan Co", 0)])    # 9 月有单据、都录好了：说得出「这家没有」
        # 只有 8 月单据的那家不在 9 月的清单里；键仍给，模型调工具要用
        self.assertNotIn(self.OLD, [item.get("account_set_key") for item in out["by_account_set"]])
        self.assertEqual(out["by_account_set"][0]["account_set_key"], self.KEAW)
        self.assertEqual(out["by_account_set"][0]["connection"], "EXP1")
        self.assertEqual(sum(item["count"] for item in out["by_account_set"]), out["total"])
        # 按连接再按账套分组计数（都是单列分组），用的是这次的条件与同一时间范围
        self.assertEqual({call["group_by"] for call in self.grouped}, {"push_connection", "push_account_set"})
        self.assertIn(["not in", ["pending", "success"]],
                      [call["filters"].get("last_push_status") for call in self.grouped])
        self.assertTrue(all(call["filters"]["invoice_date"] == ["between", ["2026-09-01", "2026-09-30"]]
                            for call in self.grouped))

    def test_a_plain_date_range_is_counted_once(self):
        # 只有时间范围、没有别的条件：在这段时间有单据的各家就是答案，分组计数只查一遍（按连接一次、连接里按账套一次）
        out = json.loads(tools.list_documents({"invoice_date_from": "2026-09-01", "invoice_date_to": "2026-09-30"}))
        self.assertEqual(self.counts(out), [
            ("70EXP · D.1 บริษัท แก้วกานดา บิวตี้ สโตร์ จำกัด", 4),
            ("70EXP · D.3 บริษัท ดาวเหนือ ไอที ซัพพลาย จำกัด", 3),
            ("70EXP · D.2 บริษัท ภูผาแก้ว จำกัด", 2),
            ("70EXP · D.4 Suan Co", 1),
            ("not chosen yet", 1)])
        self.assertEqual([call["group_by"] for call in self.grouped], ["push_connection", "push_account_set"])

    def test_counts_cover_every_matching_document_not_only_this_page(self):
        out = json.loads(tools.list_documents({**self.SEPTEMBER, "limit": 2}))
        self.assertEqual((out["count"], out["total"], out["next_offset"]), (2, 7, 2))
        self.assertEqual(sum(item["count"] for item in out["by_account_set"]), 7)

    def test_filters_read_after_fetching_count_the_rows_that_matched(self):
        # 按方向筛走「取回后再筛」那条路：各家按筛完的行数；ภูผาแก้ว 9 月等着录入的只有销项，进项记 0
        out = json.loads(tools.list_documents({**self.SEPTEMBER, "direction": "purchase", "totals": True}))
        self.assertEqual(self.counts(out), [
            ("70EXP · D.1 บริษัท แก้วกานดา บิวตี้ สโตร์ จำกัด", 2),
            ("70EXP · D.3 บริษัท ดาวเหนือ ไอที ซัพพลาย จำกัด", 1),
            ("not chosen yet", 1),
            ("70EXP · D.2 บริษัท ภูผาแก้ว จำกัด", 0),
            ("70EXP · D.4 Suan Co", 0)])

    def test_a_truncated_scan_never_counts_an_unreached_company_as_none(self):
        """审查 2026-10-15：取回后再筛只看最新的 LIST_SCAN_MAX 张时，没看到的账套原来记 0，模型会答「这家没有」。"""
        # 只看了最新的两张，都是 แก้วกานดา 的（一张销项、一张进项）；ดาวเหนือ 与没选账套的进项排在后面、没看到
        with mock.patch.object(tools, "LIST_SCAN_MAX", 2), mock.patch.object(tools, "LIST_SCAN_PAGE", 2):
            out = json.loads(tools.list_documents({**self.SEPTEMBER, "direction": "purchase"}))
        self.assertIs(out["complete"], False)
        self.assertIs(out["by_account_set_complete"], False)
        rows = {item.get("account_label", item.get("push_target")): item for item in out["by_account_set"]}
        self.assertEqual(rows["70EXP · D.1 บริษัท แก้วกานดา บิวตี้ สโตร์ จำกัด"]["count"], 1)  # 看到的张数：下限
        self.assertNotIn("not chosen yet", rows)
        for name in ("70EXP · D.3 บริษัท ดาวเหนือ ไอที ซัพพลาย จำกัด", "70EXP · D.2 บริษัท ภูผาแก้ว จำกัด",
                     "70EXP · D.4 Suan Co"):
            self.assertIsNone(rows[name]["count"], name)  # 没看到：不给 0
        self.assertTrue(all(item["partial"] for item in out["by_account_set"]))
        self.assertNotIn(0, [item["count"] for item in out["by_account_set"]])
        self.assertIn("never say a company has none", out["note"])
        # 看完了的照旧：0 张也列、不标 partial
        whole = json.loads(tools.list_documents({**self.SEPTEMBER, "direction": "purchase"}))
        self.assertNotIn("by_account_set_complete", whole)
        self.assertFalse(any("partial" in item for item in whole["by_account_set"]))
        self.assertIn(0, [item["count"] for item in whole["by_account_set"]])
        listing = {tool["name"]: tool for tool in tools.TOOLS}["cowork_list_documents"]["description"]
        self.assertIn("When complete is false", listing)
        self.assertIn("never say it has none", listing)

    def test_a_search_or_one_account_set_gives_no_company_list(self):
        self.assertNotIn("by_account_set", json.loads(tools.list_documents({**self.SEPTEMBER, "search": "CN6909"})))
        self.rows = [row for row in self.rows if not row["push_account_set"]]
        self.assertNotIn("by_account_set", json.loads(tools.list_documents(self.SEPTEMBER)))  # 全没选账套：没有可分的

    def test_cc_is_told_what_all_companies_means(self):
        guidance = " ".join(tools.GUIDANCE)
        self.assertIn("When the user asks about all companies (ทุกเจ้า, all clients), leave out connection, "
                      "account_set_key and search even if the conversation was about one company", guidance)
        self.assertIn("by_account_set", guidance)
        listing = {tool["name"]: tool for tool in tools.TOOLS}["cowork_list_documents"]["description"]
        self.assertIn("Without account_set_key and search, by_account_set gives every account set (company)", listing)


class ThisMonthTests(unittest.TestCase):
    """第 2 条之一（2026-10-13 录屏 L8）：CC 自己刚说「这个月的报表在对话里」（9 月），会计接着问「这个月还有哪些票卡着」，
    CC 按 10 月查、答「没有」；之前「แก้วกานดาเดือนนี้ภาษีขายภาษีซื้อเท่าไหร่」也先答了 10 月的 0。会计说的这个月是在做的那期。"""

    def test_this_month_follows_the_month_being_filed_and_is_confirmed_when_unsure(self):
        line = next(line for line in tools.GUIDANCE if line.startswith("'This month'"))
        self.assertIn("a month already being discussed in this conversation is the one meant", line)
        self.assertIn("early in a month it is usually last month's filing", line)
        self.assertIn("confirm the month in one short question before looking anything up", line)


class CompanyNamedForADocumentTests(unittest.TestCase):
    """第 4 条（2026-10-13 录屏 phone-09）：手机拍了简易税票（ศรีกรุง เครื่องเขียน SK-6910-01284，642.00），同一句写
    「อันนี้ของดาวเหนือ ค่าเครื่องเขียน」。CC 只识别了（一步），答「票上没有 ดาวเหนือ 的名字，所以还没认定是他的费用；要送进
    Express 请告诉公司名」。

    查清拦在哪一层：推送的服务端规则（`targets.account_match`）对没有买方的票不拦，按用户选的账套推；识别不做公司归属；
    拦住的是 CC 的说法（只在「要录进 ERP 且没说去哪」时才去选账套，把会计员说的公司当成待核实）。修法：说明写口径
    （会计员说了是哪家公司的就录进那家的账套、简易税票没有买方不必有公司名、票面买方是别家才提），规则不变并钉住；
    照片先「仅识别」、会计员随后才说公司时，再交同一个附件拿回原来那份并指向按账套推送（推送指定账套见 PushTargetTests）。"""

    OWN_TAX, OTHER_TAX = "0105551234567", "0105559999996"  # 校验位都对
    # 对话附件与识别服务的替身同 RecognizeAttachmentTests（不继承：免得把那边的测试再跑一遍）
    setUp, _attachment, _submit = (RecognizeAttachmentTests.setUp, RecognizeAttachmentTests._attachment,
                                   RecognizeAttachmentTests._submit)

    def _rule(self):
        spec = importlib.util.spec_from_file_location("cowork_account_match", COWORK / "targets/account_match.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_push_rule_takes_a_document_with_no_buyer_to_the_company_named(self):
        rule = self._rule()
        account = {"source_id": r"c:\account\70exp\dem003", "attributes": {
            "tax_id": self.OWN_TAX, "period_start": "2025-11-01", "period_end": "2026-10-31", "next_end": "2027-10-31"}}
        abb = {"document_type": "simplified_tax_invoice", "seller_name": "บริษัท ศรีกรุง เครื่องเขียน จำกัด",
               "seller_tax": "0105599220311", "date": "2026-10-13", "total_amount": "642.00"}
        self.assertEqual(rule.conflict("express", account, direction="purchase", fields=abb), "")
        # 有买方的照旧核：票面买方税号是别家就拦（推送回「本方税号与所选账套不符」），是这家就过
        self.assertEqual(rule.conflict("express", account, direction="purchase",
                                       fields={**abb, "buyer_tax": self.OTHER_TAX}), rule.TAX_MISMATCH)
        self.assertEqual(rule.conflict("express", account, direction="purchase",
                                       fields={**abb, "buyer_tax": self.OWN_TAX}), "")
        # 2026-10-15 testcowork：方向没判出（工作空间税号不是这家账套的）时，不拿卖方（快递公司）的税号当本方去比
        courier = {"document_type": "simplified_tax_invoice", "seller_name": "บริษัท ด่วนจี่ เอ็กซ์เพรส",
                   "seller_tax": "0105599220248", "buyer_name": "", "buyer_tax": "", "date": "2026-10-14",
                   "total_amount": "285.00", "vat": "18.64"}
        self.assertEqual(rule.conflict("express", account, direction=None, fields=courier), "")
        self.assertEqual(rule.conflict("express", account, direction=None,
                                       fields={**courier, "buyer_tax": self.OTHER_TAX}), rule.TAX_MISMATCH)

    def test_cc_is_told_the_users_word_names_the_company(self):
        line = next(line for line in tools.GUIDANCE if line.startswith("When the user says which company"))
        self.assertIn("enter it into that company's account set (find it with cowork_intake_options search) without "
                      "asking for the company again", line)
        self.assertIn("Abbreviated tax invoices and receipts often name no buyer: the user's word is enough", line)
        self.assertIn("Point out a conflict only when the document names a different buyer", line)
        recognize = {tool["name"]: tool for tool in tools.TOOLS}["cowork_recognize_attachments"]["description"]
        self.assertIn("a company the user names is where: its account set, found there with search", recognize)

    def _refuse_other_choices(self, earlier="saved", taken=()):
        """识别服务的口径：同一份原件第一次交的参数定了，再交不同参数就拒（`ocr.execution.service._admitted`）。
        ``taken``：这些请求号早先已按别的口径交过；``earlier``：早先那份的状态（None = 没交过）。"""
        first = {key: {"earlier": True} for key in taken}

        def submit(source, key, **choices):
            self.submitted.append((source, key, choices))
            if first.setdefault(key, choices) != choices:
                raise frappe.ValidationError("This original already has a recognition task with different parameters.")
            return {"name": "B-" + key, "state": "saved"}

        ocr = sys.modules["pearnly_cowork.ocr.execution.service"]
        ocr.submit = submit
        ocr.recognized = lambda source: {"name": "B-earlier", "state": earlier} if earlier else None
        return ocr

    def test_naming_the_company_after_recognizing_gives_back_the_earlier_result_to_push(self):
        self._refuse_other_choices()
        first = json.loads(tools.recognize_attachments({"files": ["file_abc"], "direction": "purchase"}))["results"][0]
        self.assertNotIn("recognized_before", first)
        again = json.loads(tools.recognize_attachments({
            "files": ["file_abc"], "direction": "purchase", "target": "express", "connection": "C1",
            "account_set_key": "K"}))["results"][0]
        self.assertEqual((again["batch"], again["state"], again["recognized_before"]), ("B-earlier", "saved", True))
        self.assertIn("cowork_push_documents with connection and account_set_key", again["note"])
        self.assertIn("not recognized or charged again", again["note"])
        self.assertEqual(again["documents"][0]["invoice_no"], "R308")
        self.assertNotIn("status", again)
        self.assertEqual([key for _name, _content, key in self.created], ["assistant:file_abc"] * 2)

    def _declared(self):
        declared = []
        tools.storage.declare_direction = lambda names, direction: declared.append((list(names), direction)) or names
        self.addCleanup(setattr, tools.storage, "declare_direction", lambda names, direction: [])
        return declared

    def test_the_direction_said_after_recognizing_lands_on_the_recognized_documents(self):
        """2026-10-15 testcowork：先「仅识别」，会计员说「ค่าขนส่ง ส่งเข้า Express」后 CC 带 direction=purchase 再交，
        识别服务按第一次的口径拿回原来那份，票上方向空着。现在把这次说明的方向写到识别出的票上（已有方向的由存储层保留）。"""
        self._refuse_other_choices()
        declared = self._declared()
        commits = frappe.commits
        json.loads(tools.recognize_attachments({"files": ["file_abc"], "target": "none"}))
        self.assertEqual(declared, [])  # 第一次照常按批次口径识别，没有要补的
        again = json.loads(tools.recognize_attachments({
            "files": ["file_abc"], "direction": "purchase", "target": "express", "connection": "C1",
            "account_set_key": "K"}))["results"][0]
        self.assertTrue(again["recognized_before"])
        self.assertEqual(declared, [(["H1"], "purchase")])
        self.assertGreater(frappe.commits, commits)

    def test_no_direction_or_a_fresh_recognition_writes_nothing(self):
        self._refuse_other_choices()
        declared = self._declared()
        tools.recognize_attachments({"files": ["file_abc"], "direction": "purchase"})  # 第一次：方向随批次落到票上
        tools.recognize_attachments({"files": ["file_abc"], "target": "express", "connection": "C1",
                                     "account_set_key": "K"})  # 再交没说方向
        self.assertEqual(declared, [])

    def test_a_direction_that_cannot_be_written_does_not_fail_the_recognition(self):
        self._refuse_other_choices()
        tools.recognize_attachments({"files": ["file_abc"]})

        def refuse(names, direction):
            raise frappe.ValidationError("The record changed. Reload it before saving.")

        tools.storage.declare_direction = refuse
        self.addCleanup(setattr, tools.storage, "declare_direction", lambda names, direction: [])
        rollbacks = frappe.rollbacks
        out = json.loads(tools.recognize_attachments({"files": ["file_abc"], "direction": "sales", "target": "express",
                                                      "connection": "C1", "account_set_key": "K"}))["results"][0]
        self.assertEqual((out["state"], out["recognized_before"]), ("saved", True))
        self.assertGreater(frappe.rollbacks, rollbacks)

    def test_an_earlier_failed_recognition_is_tried_again_with_the_new_choices(self):
        self._refuse_other_choices("failed", taken=("assistant:file_f",))
        out = json.loads(tools.recognize_attachments({"files": ["file_f"], "target": "express", "connection": "C1",
                                                      "account_set_key": "K"}))["results"][0]
        self.assertEqual((out["batch"], out["attempt"]), ("B-assistant:file_f:2", 2))
        self.assertNotIn("recognized_before", out)
        self.assertEqual(self.submitted[-1][2]["account_set_key"], "K")

    def test_a_refusal_without_an_earlier_recognition_is_still_reported(self):
        ocr = self._refuse_other_choices(None)
        ocr.submit = lambda source, key, **choices: (_ for _ in ()).throw(frappe.ValidationError("Invalid push target."))
        out = json.loads(tools.recognize_attachments({"files": ["file_abc"]}))["results"][0]
        self.assertEqual(out, {"file": "file_abc", "status": "not_started", "reason": "Invalid push target."})


if __name__ == "__main__":
    unittest.main()
