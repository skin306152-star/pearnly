"""ERP 采购 / 销售引导的产品函数（``pearnly_erp_portal.express.guide_documents``，documents 出题源点名）：替身走一遍。

断言的是应有状态：
- 交识别：方向按菜单上点的业务（不猜），请求键 = 对话文件编号（同一份不重复识别、不重复收费），带上这段对话；交不进去的回原因；
- 识别到哪：同代录资料区的状态，识别完带出这份文件里每张票（``I:``），还有在读的回 ``wait``；
- 复核：收单记录的识别结果 → 共用复核契约的票面（对方 / 税号 / 分店按方向取，合计按网页同一算法算）；改单号、对方、日期、明细的
  数量 / 单价写回记录；合计、税额、明细金额不让改；已提交的不能改；
- 确定（直写）：在这段对话里提交（排直写带上对话）；有没定的行、付款没点过也没记住的这一张留下、结果行带现成的那句；
  付款按对方记住过的照记住的提交；提交被拒的这一张留下、原因原样，其余照常；
- 结果：提交了的按单据写账状态（已入账带单号 / 在写），没提交的留下带原因。

    python3 tests/test_erp_guide_documents.py
"""
import contextlib
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

frappe = types.ModuleType("frappe")
frappe._ = lambda text: {"Payment not chosen yet": "未选付款方式", "Some lines have no product yet": "有行未定物料"}.get(text, text)
frappe.local = types.SimpleNamespace(pearnly_assistant=None)
frappe.PermissionError, frappe.DoesNotExistError = PermissionError, LookupError
frappe.ValidationError = type("ValidationError", (Exception,), {})
COMMITS = []
frappe.db = types.SimpleNamespace(commit=lambda: COMMITS.append("commit"), rollback=lambda: COMMITS.append("rollback"),
                                  get_value=None)
frappe.clear_messages = lambda: None


def _throw(message, exc=ValueError):
    raise exc(message)


frappe.throw = _throw
sys.modules.setdefault("frappe", frappe)
sys.modules["frappe"] = frappe


def _module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


class Record(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value

    def save(self, ignore_permissions=False):
        STATE["saved"].append(json.loads(self["result"]))


STATE = {"saved": []}
RECEIVED, SUBMITTED, SESSIONS = [], [], []
_module("pearnly_erp_portal")
_module("pearnly_erp_portal.express")
_module("pearnly_erp_portal.integrations")
intake = _module("pearnly_erp_portal.integrations.intake", DOCTYPE="ERP LINE Intake", READY="ready", CONFIRMED="confirmed",
                 FAILED="failed", SOURCE_CC="cc", DIRECTION_CAPABILITY={"purchase": "purchase.write", "sales": "sales.write"})
_module("pearnly_erp_portal.access")
CAPABLE = {"purchase.write", "sales.write"}
sys.modules["pearnly_erp_portal.access"].guard = _module("pearnly_erp_portal.access.guard", has=lambda capability: capability in CAPABLE)
operator = _module("pearnly_erp_portal.express.operator", UNDECIDED_LINES_TEXT="Some lines have no product yet",
                   NO_PAYMENT_TEXT="Payment not chosen yet")
_module("pearnly_core.assistant.files", attachment=lambda file_id: (f"{file_id}.pdf", b"%PDF"))
import pearnly_core.assistant as _assistant  # noqa: E402
_assistant.files = sys.modules["pearnly_core.assistant.files"]


@contextlib.contextmanager
def _for_session(session_id):
    SESSIONS.append(session_id)
    yield


_module("pearnly_erp_portal.express.conversation", for_session=_for_session)
KINDS = {"purchase": "purchase-kind", "sales": "sales-kind"}
OUTCOMES = {}
_module("pearnly_erp_portal.express.cc_tools", KINDS=KINDS, _outcome=lambda kind, name: OUTCOMES[name])
# 这家公司接了哪种账本（`express.push.enabled` / `mrerp.push.handles`）：默认接着 Express
LEDGER = {"express": True, "mrerp": False}
LOOKUPS = []
_module("pearnly_erp_portal.express.push", enabled=lambda company: LOOKUPS.append(company) or LEDGER["express"])
_module("pearnly_erp_portal.mrerp")
_module("pearnly_erp_portal.mrerp.push", handles=lambda company: LEDGER["mrerp"])
sys.modules["pearnly_erp_portal.express"].push = sys.modules["pearnly_erp_portal.express.push"]
sys.modules["pearnly_erp_portal.mrerp"].push = sys.modules["pearnly_erp_portal.mrerp.push"]
sys.modules["pearnly_erp_portal.express"].conversation = sys.modules["pearnly_erp_portal.express.conversation"]
sys.modules["pearnly_erp_portal.express"].cc_tools = sys.modules["pearnly_erp_portal.express.cc_tools"]
sys.modules["pearnly_erp_portal.express"].operator = operator
sys.modules["pearnly_erp_portal.integrations"].intake = intake

_spec = importlib.util.spec_from_file_location(
    "pearnly_erp_portal.express.guide_documents", ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/express/guide_documents.py")
G = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(G)

CTX = {"session": "S1", "user": "u@x", "business": "purchase", "company": "CO-1", "ledger": "B1", "period": "2026-11",
       "answers": {}, "run": "RUN1", "draft": ""}
RESULT = {"doc_no": "IV-1", "doc_date": "2026-11-01", "supplier_name": "ISUZU", "supplier_tax_id": "0105536000000",
          "supplier_branch_no": "00000", "lines": [{"description": "Paper", "qty": "2", "unit_price": "50"}]}


class Base(unittest.TestCase):
    def setUp(self):
        RECEIVED.clear(), SUBMITTED.clear(), SESSIONS.clear(), COMMITS.clear(), STATE["saved"].clear(), OUTCOMES.clear()
        LEDGER.update(express=True, mrerp=False), LOOKUPS.clear()
        frappe.session = types.SimpleNamespace(user="u@x")
        frappe.get_all = self.get_all
        self.records = {"LI-1": Record(name="LI-1", status="ready", direction="purchase", company="CO-1", modified="t1",
                                       source="cc", sent_by="u@x",
                                       result=json.dumps(RESULT), purchase=None, sales_document=None,
                                       line_choices=json.dumps({"lines": [{"decided": True}], "payment_decided": True}))}
        self.settled = True
        frappe.db.get_value = self.get_value
        frappe.get_doc = lambda doctype, name: self.records[name]
        intake._owned = lambda name, lock=False: self.records[name]
        intake.totals = lambda doc, result: {"subtotal": "100.00", "vat_amount": "7.00", "grand_total": "107.00"}
        intake.receive_chat = self.receive
        intake.submit_chat = self.submit
        operator._intake = lambda company, doc_id: self.records.get(doc_id.partition(":")[2]) if company == "CO-1" else None
        operator.settle_payment = lambda company, name, record=None: self.settled
        operator.uploads = lambda company, period, batches: {
            "LI-1": {"state": "done", "code": "", "documents": [{"doc_id": "I:LI-1"}, {"doc_id": "I:LI-2"}]},
            "LI-3": {"state": "reading", "code": ""}}

    def get_all(self, doctype, filters=None, fields=None, limit_page_length=None, **kw):
        names = set(filters["name"][1])
        if "source" not in filters:  # 结果行按单据类型取单号（`guide_documents._numbers`）
            return [row for row in self.records.values() if row.name in names]
        return [row for row in self.records.values() if row.name in names and row.source == filters["source"]
                and row.sent_by == filters["sent_by"] and row.company == filters["company"]]

    def get_value(self, doctype, name, fields=None, as_dict=False, **kw):
        row = self.records[name]
        if isinstance(fields, list):
            return Record({key: row.get(key) for key in fields})
        return row.get(fields)

    def receive(self, **kwargs):
        RECEIVED.append(kwargs)
        return {"name": "LI-9", "status": "failed", "reason": "queue_busy"} if kwargs["request_key"] == "BAD" else \
            {"name": f"LI-{len(RECEIVED)}", "status": "queued"}

    def submit(self, name):
        if name == "LI-BAD":
            raise frappe.ValidationError("Supplier is required")
        SUBMITTED.append((name, list(SESSIONS)))
        return {"name": "PO-7"}


class Intake(Base):
    def test_the_direction_is_the_business_and_the_file_id_is_the_request_key(self):
        found = G.intake(CTX, ["F1", "F1", "BAD"])
        self.assertEqual(found, {"batches": ["LI-1"], "refused": {"BAD": "queue_busy"}})
        self.assertEqual([(r["direction"], r["request_key"], r["session_id"], r["company"]) for r in RECEIVED],
                         [("purchase", "F1", "S1", "CO-1"), ("purchase", "BAD", "S1", "CO-1")])
        self.assertRaises(ValueError, G.intake, {**CTX, "business": "collection"}, ["F1"])

    def test_uploads_bring_the_tickets_of_each_file_and_wait_while_reading(self):
        found = G.uploads(CTX, ["LI-1", "LI-3", "LI-X"])
        self.assertEqual(found["doc_ids"], ["I:LI-1", "I:LI-2"])
        self.assertTrue(found["wait"])
        self.assertEqual([f["state"] for f in found["files"]], ["done", "reading", "failed"])


class Review(Base):
    def test_the_face_comes_from_the_recognized_result_with_web_totals(self):
        [row] = G.review(CTX, ["I:LI-1", "I:NOPE"])["rows"]
        face = row["face"]
        self.assertEqual((face["party_name"], face["party_tax"], face["date"], face["total"]), ("ISUZU", "0105536000000",
                                                                                            "2026-11-01", "107.00"))
        self.assertEqual(face["lines"][0]["amount"], "100")
        self.assertEqual(row["blocking"], [])
        self.assertIn("doc_no", row["editable"])
        self.assertIn("lines.0.qty", row["editable"])
        self.assertNotIn("total", row["editable"])  # 合计按网页算法算，页面画成只读
        self.assertNotIn("lines.0.amount", row["editable"])

    def test_edits_go_back_onto_the_record_and_computed_cells_do_not_change(self):
        G.edit(CTX, "I:LI-1", "doc_no", "IV-9")
        G.edit(CTX, "I:LI-1", "lines.0.qty", "3")
        self.assertEqual((STATE["saved"][-1]["doc_no"], STATE["saved"][-1]["lines"][0]["qty"]), ("IV-9", "3"))
        self.assertEqual(G.edit(CTX, "I:LI-1", "total", "1"), {"refused": G.COMPUTED})
        self.assertEqual(G.edit(CTX, "I:LI-1", "lines.0.amount", "1"), {"refused": G.COMPUTED})
        self.records["LI-1"].status = "confirmed"
        self.assertEqual(G.edit(CTX, "I:LI-1", "doc_no", "x"), {"refused": G.NOT_AVAILABLE})
        self.assertRaises(PermissionError, G.edit, CTX, "I:NOPE", "doc_no", "x")


class Confirm(Base):
    def test_submitted_inside_the_conversation_one_by_one(self):
        found = G.confirm(CTX, ["I:LI-1"], "guide:RUN1:5")
        self.assertEqual(found["docs"], [{"doc_id": "I:LI-1", "status": "queued", "document": "PO-7"}])
        self.assertEqual(SUBMITTED, [("LI-1", ["S1"])])  # 提交时带着这段对话（排直写按它写）

    def test_a_payment_nobody_clicked_holds_that_ticket_with_the_existing_words(self):
        self.settled = False
        self.records["LI-1"].line_choices = json.dumps({"lines": [{"decided": True}]})
        [row] = G.confirm(CTX, ["I:LI-1"], "guide:RUN1:5")["docs"]
        self.assertEqual((row["status"], row["reason"], row["reason_text"]), ("held", G.PAYMENT_UNDECIDED, "未选付款方式"))
        self.assertEqual(SUBMITTED, [])
        [row] = G.result(CTX, ["I:LI-1"])["rows"]
        self.assertEqual((row["status"], row["reason_text"]), ("held", "未选付款方式"))  # 结果行可见

    def test_an_undecided_line_and_a_refused_submission_hold_only_that_ticket(self):
        self.records["LI-2"] = Record({**self.records["LI-1"], "name": "LI-2",
                                       "line_choices": json.dumps({"lines": [{"decided": False}], "payment_decided": True})})
        self.records["LI-BAD"] = Record({**self.records["LI-1"], "name": "LI-BAD"})
        docs = {row["doc_id"]: row for row in G.confirm(CTX, ["I:LI-2", "I:LI-BAD", "I:LI-1"], "guide:RUN1:5")["docs"]}
        self.assertEqual((docs["I:LI-2"]["reason"], docs["I:LI-2"]["reason_text"]), (G.UNDECIDED_LINES, "有行未定物料"))
        self.assertEqual((docs["I:LI-BAD"]["status"], docs["I:LI-BAD"]["reason_text"]), ("held", "Supplier is required"))
        self.assertEqual(docs["I:LI-1"]["status"], "queued")
        self.assertIn("rollback", COMMITS)


class Result(Base):
    def test_submitted_tickets_report_the_ledger_state(self):
        self.records["LI-1"].update(status="confirmed", purchase="PO-7")
        OUTCOMES["PO-7"] = {"status": "entered", "docnum": "RR0001"}
        [row] = G.result(CTX, ["I:LI-1"])["rows"]
        self.assertEqual((row["status"], row["docnum"], row["document"]), ("entered", "RR0001", "PO-7"))
        OUTCOMES["PO-7"] = {"status": "queued"}
        found = G.result(CTX, ["I:LI-1"])
        self.assertEqual((found["rows"][0]["status"], found["wait"]), ("queued", True))
        OUTCOMES["PO-7"] = {"status": "needs_choice", "reason": "写入前要选"}
        self.assertEqual(G.result(CTX, ["I:LI-1"])["rows"][0]["status"], "held")

    def test_a_company_without_any_ledger_is_done_once_the_ticket_is_in_erp(self):
        """主会话 11-24 定：没接任何账本的公司，直写对话里 CC 收单判可写（判定 `precheck.check`，test_operator_intake 守），确定后
        只存进 ERP（同 LINE），不排写账；结果卡这一张是办完了（ERP 单号），不是按写账状态判成的「未完成 · not_for_express」。"""
        self.records["LI-1"].update(status="confirmed", purchase="PO-7")
        self.records["PO-7"] = Record(name="PO-7", ledger_state="", system_no="PE-0007")
        LEDGER.update(express=False, mrerp=False)
        found = G.result(CTX, ["I:LI-1", "I:LI-1"])
        self.assertEqual([(row["status"], row.get("docnum"), row["document"]) for row in found["rows"]],
                         [("entered", "PE-0007", "PO-7")])
        self.assertFalse(found["wait"])
        self.assertEqual(LOOKUPS, ["CO-1"])  # 一次结果按公司只判一次
        # 确定：照网页同一个入账函数提交（提交里接了账本才排写账），这张不因为没账本被留下
        self.records["LI-1"].update(status="ready", purchase=None)
        self.assertEqual([doc["status"] for doc in G.confirm(CTX, ["I:LI-1"], "guide:RUN1:1")["docs"]], ["queued"])
        self.assertEqual([name for name, _ in SUBMITTED], ["LI-1"])
        # 接着 Express 的、账本是 MR.ERP 的照写账状态判（不会因为 ledger_state 还空就说办完）
        self.records["LI-1"].update(status="confirmed", purchase="PO-7")
        OUTCOMES["PO-7"] = {"status": "queued"}
        for express, mrerp in ((True, False), (False, True)):
            LEDGER.update(express=express, mrerp=mrerp)
            self.assertEqual(G.result(CTX, ["I:LI-1"])["rows"][0]["status"], "queued", (express, mrerp))


if __name__ == "__main__":
    unittest.main()
