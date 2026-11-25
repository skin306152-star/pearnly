"""Cowork 采购 / 销售引导的产品函数（``pearnly_cowork.assistant.guide_documents``，documents 出题源点名）：替身走一遍。

与 ERP 同一组签名、同一条路；断言的是应有状态：
- 交识别：代录资料区同一个入口（请求键 = 对话文件编号），回识别批次；交不进去的回原因；
- 识别到哪：同代录资料区，识别完带出识别出的单，还有在读的回 ``wait``；
- 复核：单据主页 → 共用复核契约的票面（采购的对方是卖方、销售的是买方）；改一格写回主页（带版本号）、记一条改动（依据 = 复核卡）；
  别人刚改过（版本对不上）不覆盖；契约之外的格拒；
- 确定（直写）：CC 推送工具同一个服务函数，在这段对话里调（写入计划的门照样过）；
- 结果：推送状态与账本单号一次取回（已入账带单号、在写、没成带原因）。

    python3 tests/test_cowork_guide_documents.py
"""
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

frappe = types.ModuleType("frappe")
frappe._ = lambda text: text
frappe.local = types.SimpleNamespace(pearnly_assistant=None)
frappe.PermissionError, frappe.DoesNotExistError = PermissionError, LookupError
frappe.ValidationError = type("ValidationError", (Exception,), {})
frappe.db = types.SimpleNamespace(commit=lambda: None, rollback=lambda: None)
frappe.clear_messages = lambda: None


def _throw(message, exc=ValueError):
    raise exc(message)


frappe.throw = _throw
sys.modules["frappe"] = frappe


def _module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


CALLS = {"intake": [], "push": [], "record": [], "update": []}
for package in ("pearnly_cowork", "pearnly_cowork.assistant", "pearnly_cowork.history", "pearnly_cowork.push"):
    _module(package)
# 推送结果不明的判法（同 `push.lifecycle.result_unknown`：manual + 结果不明原因）
_module("pearnly_cowork.push.lifecycle", result_unknown=lambda status, reason: status == "manual" and "UNKNOWN" in str(reason))
sys.modules["pearnly_cowork.push"].lifecycle = sys.modules["pearnly_cowork.push.lifecycle"]
operator_source = _module("pearnly_cowork.assistant.operator_source")
tools = _module("pearnly_cowork.assistant.tools", _primary=lambda pages: 0, _reason=lambda code: f"why:{code}")
document_checks = _module("pearnly_cowork.assistant.document_checks",
                          record=lambda *a, **kw: CALLS["record"].append((a, kw)))
storage = _module("pearnly_cowork.history.storage")
_module("pearnly_core.assistant.files", attachment=lambda file_id: (f"{file_id}.pdf", b"%PDF"))
TASKS = {}  # 会话 → 挂着的代录任务（代录对话）
UPLOADED = []
_module("pearnly_core.assistant.sessions", owned=lambda session_id: {"operator_task": TASKS.get(session_id, "")})
_module("pearnly_core.express_operator")
_module("pearnly_core.express_operator.uploads",
        record=lambda task, key, filename, size, result: UPLOADED.append((task, key, filename, size, result.get("name"))))
import pearnly_core.assistant as _assistant  # noqa: E402
_assistant.files = sys.modules["pearnly_core.assistant.files"]
_assistant.sessions = sys.modules["pearnly_core.assistant.sessions"]
sys.modules["pearnly_core.express_operator"].uploads = sys.modules["pearnly_core.express_operator.uploads"]
sys.modules["pearnly_cowork.assistant"].operator_source = operator_source
sys.modules["pearnly_cowork.assistant"].tools = tools
sys.modules["pearnly_cowork.assistant"].document_checks = document_checks
sys.modules["pearnly_cowork.history"].storage = storage

_spec = importlib.util.spec_from_file_location(
    "pearnly_cowork.assistant.guide_documents", ROOT / "apps/pearnly_cowork/pearnly_cowork/assistant/guide_documents.py")
G = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(G)

CTX = {"session": "S1", "business": "purchase", "company": "CC-1", "run": "RUN1"}
FIELDS = {"direction": "purchase", "invoice_number": "IV-1", "date": "2026-11-01", "seller_name": "ISUZU",
          "seller_tax": "0105536000000", "seller_branch": "00000", "buyer_name": "Us", "subtotal": "100.00", "vat": "7.00",
          "total_amount": "107.00", "items": [{"name": "Paper", "qty": "2", "price": "50", "subtotal": "100", "posting_kind": "stock"}]}


class Row(dict):
    __getattr__ = dict.get


class Base(unittest.TestCase):
    def setUp(self):
        for calls in CALLS.values():
            calls.clear()
        self.docs = {"H1": {"name": "H1", "record_version": 3, "pages": [{"fields": json.loads(json.dumps(FIELDS))}]}}
        self.history = {"H1": Row(name="H1", invoice_no="IV-1", last_push_status="success", last_push_reason="", last_push_record="PR1"),
                        "H2": Row(name="H2", last_push_status="failed", last_push_reason="choice_needed", last_push_record=""),
                        "H4": Row(name="H4", last_push_status="manual", last_push_reason="ERP_CONFIRMED_PUSH_RESULT_UNKNOWN",
                                  last_push_record="PR4")}
        storage.read = lambda name, for_update=False: self.docs[name] if name in self.docs else (_ for _ in ()).throw(LookupError())
        storage.project = lambda doc: doc
        storage.update_pages = self.update_pages
        operator_source.intake = self.intake
        operator_source.uploads = lambda company, period, batches: {
            "P1": {"state": "done", "code": "", "documents": [{"doc_id": "H1"}, {"doc_id": "H2"}]}, "P2": {"state": "reading", "code": ""}}
        tools.push_documents = self.push
        frappe.get_all = self.get_all

    def intake(self, company, filename, content, request_key):
        CALLS["intake"].append((company, filename, request_key))
        if request_key == "BAD":
            raise frappe.ValidationError("cowork.company.pending.no_target")
        return {"accepted": True, "source": "SRC", "name": f"P-{request_key}"}

    def update_pages(self, name, pages, version):
        if str(version) != str(self.docs[name]["record_version"]):
            raise frappe.ValidationError("changed")
        CALLS["update"].append((name, pages[0]["fields"]))
        self.docs[name] = {**self.docs[name], "pages": pages, "record_version": self.docs[name]["record_version"] + 1}
        return self.docs[name]

    def push(self, tool_input):
        CALLS["push"].append((tool_input, (frappe.local.pearnly_assistant or {}).get("session_id")))
        return json.dumps({"results": [{"document": "H1", "status": "queued"},
                                       {"document": "H2", "status": "needs_choice", "reason": "写入前要选"}]})

    def get_all(self, doctype, filters=None, fields=None, limit_page_length=None, **kw):
        names = filters["name"][1]
        if doctype == G.HISTORY:
            return [self.history[name] for name in names if name in self.history]
        return [Row(name="PR1", external_docnum="RR0009")] if "PR1" in names else []


class Walk(Base):
    def test_files_go_through_the_operator_upload_entry_with_the_file_id_as_key(self):
        found = G.intake(CTX, ["F1", "F1", "BAD"])
        self.assertEqual((found["batches"], list(found["refused"])), (["P-F1"], ["BAD"]))
        self.assertEqual(CALLS["intake"][0], ("CC-1", "F1.pdf", "F1"))
        found = G.uploads(CTX, ["P1", "P2", "PX"])
        self.assertEqual((found["doc_ids"], found["wait"]), (["H1", "H2"], True))

    def test_files_given_in_an_operator_conversation_are_recorded_on_its_task(self):
        """P4 11-24：代录对话里经引导交的文件同资料区上传一样记进代录任务（别月的票才交得进这次代录）；直写对话不记。"""
        UPLOADED.clear()
        G.intake(CTX, ["F1"])
        self.assertEqual(UPLOADED, [])
        TASKS["S1"] = "T1"
        self.addCleanup(TASKS.clear)
        G.intake(CTX, ["F2", "BAD"])
        self.assertEqual([(task, key, name) for task, key, name, _size, _batch in UPLOADED], [("T1", "F2", "F2.pdf")])

    def test_the_face_uses_the_seller_on_purchases_and_the_buyer_on_sales(self):
        [row] = G.review(CTX, ["H1", "NOPE"])["rows"]
        self.assertEqual((row["face"]["party_name"], row["face"]["total"], row["face"]["lines"][0]["unit_price"]),
                         ("ISUZU", "107.00", "50"))
        self.docs["H1"]["pages"][0]["fields"]["direction"] = "sales"
        self.assertEqual(G.review(CTX, ["H1"])["rows"][0]["face"]["party_name"], "Us")

    def test_an_edit_goes_back_onto_the_main_page_with_a_change_record(self):
        found = G.edit(CTX, "H1", "party_name", "ISUZU CO")
        self.assertEqual(CALLS["update"][-1][1]["seller_name"], "ISUZU CO")
        self.assertEqual(CALLS["record"][-1][1]["basis"], G.BASIS)
        self.assertEqual(found["face"]["party_name"], "ISUZU CO")
        G.edit(CTX, "H1", "lines.0.qty", "3")
        self.assertEqual(CALLS["update"][-1][1]["items"][0]["qty"], "3")
        self.assertEqual(CALLS["update"][-1][1]["items"][0]["posting_kind"], "stock")  # 别的列不动
        self.assertRaises(Exception, G.edit, CTX, "H1", "notes", "x")  # 契约之外的格

    def test_a_document_changed_meanwhile_is_not_overwritten(self):
        original = storage.update_pages
        storage.update_pages = lambda name, pages, version: original(name, pages, "1")
        self.assertEqual(G.edit(CTX, "H1", "doc_no", "IV-9"), {"refused": "changed"})

    def test_confirm_uses_the_push_tool_service_inside_the_conversation(self):
        found = G.confirm(CTX, ["H1", "H2"], "guide:RUN1:4")
        self.assertEqual(CALLS["push"], [({"documents": ["H1", "H2"]}, "S1")])
        self.assertEqual([(d["doc_id"], d["status"]) for d in found["docs"]], [("H1", "queued"), ("H2", "held")])
        self.assertIsNone(frappe.local.pearnly_assistant)  # 出来还原

    def test_results_read_the_push_state_and_the_ledger_number_at_once(self):
        found = G.result(CTX, ["H1", "H2", "H3"])
        rows = {row["doc_id"]: row for row in found["rows"]}
        self.assertEqual((rows["H1"]["status"], rows["H1"]["docnum"]), ("entered", "RR0009"))
        self.assertEqual(rows["H1"]["doc_no"], "IV-1")   # 结果行写票面号，不写内部编号（主会话 11-25）
        self.assertEqual((rows["H2"]["status"], rows["H2"]["reason_text"]), ("failed", "why:choice_needed"))
        self.assertEqual(rows["H3"]["status"], "failed")
        self.assertFalse(found["wait"])

    def test_a_result_unknown_push_is_its_own_kind_not_failed(self):
        # 结果不明（账本里可能有）单独一类：不算失败、也不算跳过（主会话 11-25，算失败会让人重录）
        rows = {row["doc_id"]: row for row in G.result(CTX, ["H4"])["rows"]}
        self.assertEqual(rows["H4"]["status"], "unconfirmed")


if __name__ == "__main__":
    unittest.main()
