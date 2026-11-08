"""ERP 代录放文件（A 批 1，ADR-027 代录完整）：`express.operator` 的 intake / uploads / target 与 ``I:<收单记录>``。

在锁定的 Frappe 运行时里跑（候选离线车道）；不连库、不识别、不写 Express：协作者全换成替身。
- 放文件：只给本人可见的公司；交对话收单（方向留空 = 先按采购读）；回包的 ``name`` 当 batch。
- 识别状态：本人、这家公司的记录；完了带出这个文件里的每张票（``I:``）。别家公司的不回。
- 资料区：本人这家公司这个月等着提交的记录以 ``I:`` 列出。
- 开始前：有定不了物料的行先停（原因写明）；其余按提交时同一份入参判 Express 要选的项；已提交的按提交出的单判。
- 交单：先用网页同一个入账函数提交（只一次），再按代录交提交出的 ERP 单据；提交失败原样回。
- 状态：没提交的算取消；提交了按那张单的写账状态。
"""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import frappe

from pearnly_erp_portal.express import operator
from pearnly_erp_portal.integrations import intake


def _same(text, *args, **kwargs):
    return text


ROWS = {
    "LI-1": SimpleNamespace(name="LI-1", source="cc", sent_by="buyer@x", company="CO-1", direction="purchase",
                            status="ready", purchase=None, sales_document=None),
    "LI-9": SimpleNamespace(name="LI-9", source="cc", sent_by="other@x", company="CO-1", direction="purchase",
                            status="ready", purchase=None, sales_document=None),
}


class Base(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(frappe, "_", _same, create=True))
        self.enterContext(patch.object(operator, "_", _same))
        self.enterContext(patch.object(frappe, "session", SimpleNamespace(user="buyer@x"), create=True))
        self.enterContext(patch("pearnly_erp_portal.access.guard.has", return_value=True))
        self.enterContext(patch.object(operator, "_company_ok", side_effect=lambda user, company: company == "CO-1"))
        self.rows = {name: SimpleNamespace(**vars(row)) for name, row in ROWS.items()}
        self.db = Mock(get_value=Mock(side_effect=self.get_value))
        self.enterContext(patch.object(frappe, "db", self.db, create=True))
        self.enterContext(patch.object(frappe, "clear_messages", create=True))
        self.enterContext(patch.object(frappe, "throw", side_effect=lambda message, exc=frappe.ValidationError, **kw: (_ for _ in ()).throw(exc(message))))
        self.enterContext(patch("requests.sessions.Session.request", side_effect=AssertionError("External network forbidden")))

    def get_value(self, doctype, name, fields=None, as_dict=False, **kwargs):
        if doctype == intake.DOCTYPE:
            return self.rows.get(name)
        if doctype == "Pearnly OCR Work":
            return {"status": "succeeded", "last_code": ""}
        return None


class Intake(Base):
    def test_files_go_to_chat_intake_with_no_direction(self):
        with patch.object(intake, "receive_chat", return_value={"name": "LI-1", "status": "queued"}) as receive:
            self.assertEqual(operator.intake("CO-1", "a.pdf", b"%PDF", "file_1"), {"accepted": True, "name": "LI-1", "status": "queued"})
        self.assertEqual(receive.call_args.kwargs["direction"], "")
        with self.assertRaises(frappe.PermissionError):
            operator.intake("CO-2", "a.pdf", b"%PDF", "file_1")
        self.assertEqual(operator.target("CO-1"), "express")

    def test_uploads_maps_states_and_lists_the_documents_of_the_file(self):
        records = [{"record": "LI-1", "status": "ready", "company": "CO-1", "file_hash": "h", "direction": "purchase",
                    "doc_no": "IV1", "doc_date": "2026-11-03", "party": "A"},
                   {"record": "LI-2", "status": "ready", "company": "CO-1", "file_hash": "h", "direction": "purchase",
                    "doc_no": "IV2", "doc_date": "2026-11-03", "party": "A"},
                   {"record": "LI-5", "status": "queued", "company": "CO-2", "file_hash": "x"}]
        with patch.object(intake, "chat_records", return_value=records):
            found = operator.uploads("CO-1", "2026-11", ["LI-1", "LI-5"])
        self.assertEqual(set(found), {"LI-1"})  # 别家公司的不回
        self.assertEqual(found["LI-1"]["state"], "done")
        self.assertEqual([d["doc_id"] for d in found["LI-1"]["documents"]], ["I:LI-1", "I:LI-2"])

    def test_sources_list_pending_records_of_the_month(self):
        rows = [{"record": "LI-1", "status": "ready", "direction": "purchase", "doc_no": "IV1", "doc_date": "2026-11-03",
                 "party": "A", "totals": {"grand_total": "107.00"}},
                {"record": "LI-3", "status": "ready", "direction": "sales", "doc_no": "S1", "doc_date": "2026-10-30"}]
        with patch.object(frappe, "get_all", return_value=["LI-1", "LI-3"]), \
                patch.object(intake, "chat_records", return_value=rows):
            found = operator._intake_rows("CO-1", "2026-11-01", "2026-11-30")
        self.assertEqual([(r["doc_id"], r["total"]) for r in found], [("I:LI-1", "107.00")])


class Precheck(Base):
    def test_undecided_lines_hold_the_record(self):
        doc = SimpleNamespace(line_choices=json.dumps({"lines": [{"decided": False}]}), result=json.dumps({"doc_no": "IV1"}))
        with patch.object(frappe, "get_doc", return_value=doc):
            row = operator._intake_precheck("CO-1", "I:LI-1")
        self.assertEqual((row["state"], row["reason"], row["message"]), ("blocked", operator.UNDECIDED_LINES,
                                                                         operator.UNDECIDED_LINES_TEXT))
        self.assertEqual(row["precheck"]["state"], "not_writable")

    def test_ready_records_are_judged_on_the_same_payload_the_web_form_submits(self):
        doc = SimpleNamespace(line_choices=json.dumps({"lines": [{"decided": True}]}), result=json.dumps({"doc_no": "IV1"}))
        found = {"result": {"book": {}}, "items": [], "data": None}
        with patch.object(frappe, "get_doc", return_value=doc), \
                patch.object(intake, "build_payload", return_value={"company": "CO-1"}) as payload, \
                patch("pearnly_erp_portal.express.precheck.collect_form", return_value=found) as collect:
            row = operator._intake_precheck("CO-1", "I:LI-1")
        payload.assert_called_once_with(doc)
        self.assertEqual(collect.call_args.args[:2], ("Purchase Document", {"company": "CO-1"}))
        self.assertEqual((row["state"], row["precheck"]["state"], row["precheck"]["ref"]), ("ready", "check", "IV1"))

    def test_other_peoples_records_are_not_permitted(self):
        self.assertEqual(operator._intake_precheck("CO-1", "I:LI-9")["reason"], "not_permitted")
        self.assertEqual(operator._intake_precheck("CO-2", "I:LI-1")["reason"], "not_permitted")


class Submit(Base):
    def test_submitted_once_then_handed_to_the_operator(self):
        def submit_chat(name):
            self.rows[name].purchase, self.rows[name].status = "PO-7", "confirmed"
            return {"name": "PO-7"}

        with patch.object(intake, "submit_chat", side_effect=submit_chat) as chat, \
                patch.object(operator.push, "release_operator"), \
                patch.object(operator.push, "send", return_value={"status": "queued"}) as send:
            results = operator.submit("CO-1", ["I:LI-1"], {"task": "T1"}, "PC-1")
            operator.submit("CO-1", ["I:LI-1"], {"task": "T1"}, "PC-1")
        self.assertEqual(results, [{"doc_id": "I:LI-1", "status": "queued", "reason": ""}])
        self.assertEqual(chat.call_count, 1)  # 第二次已提交过，不再提交
        self.assertEqual(send.call_args.args[:2], ("Purchase Document", "PO-7"))

    def test_a_refused_submission_comes_back_as_failed(self):
        with patch.object(intake, "submit_chat", side_effect=frappe.ValidationError("Supplier is required")):
            results = operator.submit("CO-1", ["I:LI-1"], {"task": "T1"}, "PC-1")
        self.assertEqual(results, [{"doc_id": "I:LI-1", "status": "failed", "reason": "Supplier is required"}])

    def test_status_of_records(self):
        self.assertEqual(operator.status("CO-1", ["I:LI-1"])[0]["state"], "cancelled")  # 没提交
        self.rows["LI-1"].purchase = "PO-7"
        self.db.get_value.side_effect = lambda doctype, name, fields=None, as_dict=False, **kw: (
            self.rows.get(name) if doctype == intake.DOCTYPE else
            SimpleNamespace(ledger_state=operator.contracts.WRITTEN, ledger_docnum="RR001", ledger_reason="", ledger_job="J1"))
        row = operator.status("CO-1", ["I:LI-1"])[0]
        self.assertEqual((row["state"], row["docnum"]), ("saved", "RR001"))


if __name__ == "__main__":
    unittest.main()
