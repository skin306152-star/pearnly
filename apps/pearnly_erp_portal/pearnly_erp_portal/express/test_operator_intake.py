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
    def doc(self, lines=None, warehouse=None, is_paid=1, method="cash", direction="purchase"):
        return SimpleNamespace(name="LI-1", direction=direction, is_paid=is_paid, payment_method=method,
                               line_choices=json.dumps({"lines": lines if lines is not None else [{"decided": True, "item_type": "service"}],
                                                        "warehouse": warehouse}),
                               result=json.dumps({"doc_no": "IV1", "supplier_name": "ACME", "lines": [{"description": "Paper A4"}]}))

    def test_the_card_asks_payment_undecided_lines_and_the_warehouse(self):
        doc = self.doc(lines=[{"decided": False, "item_type": "goods", "product": None}])
        with patch("pearnly_erp_portal.inventory.warehouses.names_of", return_value=["WH-1", "WH-2"]):
            items = operator.intake_items("CO-1", doc, json.loads(doc.result))
        payment, line, place = items
        self.assertEqual((payment["kind"], payment["subject"], payment["value"], payment["label"]),
                         ("payment", "erp-intake:LI-1", "cash", "ACME"))  # 预选网页默认：已付、现金
        self.assertEqual([o["code"] for o in payment["options"]][-1], operator.UNPAID)
        self.assertEqual((line["kind"], line["subject"], line["label"], line["search"], line["value"]),
                         ("item", "erp-intake:LI-1:0", "Paper A4", "own_products", operator.card_new()))
        self.assertEqual((place["kind"], place["subject"], place["value"]), ("location", "erp-intake:LI-1:warehouse", ""))
        decided = self.doc(lines=[{"decided": True, "item_type": "goods", "product": "P-1"}], warehouse="WH-1", is_paid=0)
        items = operator.intake_items("CO-1", decided, json.loads(decided.result))
        self.assertEqual([(i["kind"], i["value"]) for i in items], [("payment", operator.UNPAID)])  # 定了的行、定了的仓不再问

    def test_ready_records_are_judged_on_the_same_payload_the_web_form_submits(self):
        doc = self.doc()
        found = {"result": {"book": {}}, "items": [{"kind": "account", "subject": "x"}], "data": None}
        with patch.object(frappe, "get_doc", return_value=doc), \
                patch.object(intake, "build_payload", return_value={"company": "CO-1"}) as payload, \
                patch("pearnly_erp_portal.express.precheck.collect_form", return_value=found) as collect:
            row = operator._intake_precheck("CO-1", "I:LI-1")
        payload.assert_called_once_with(doc)
        self.assertEqual(collect.call_args.args[:2], ("Purchase Document", {"company": "CO-1"}))
        self.assertEqual((row["precheck"]["state"], row["precheck"]["ref"], row["precheck"]["party"]), ("check", "IV1", "ACME"))
        self.assertEqual([i["kind"] for i in row["precheck"]["items"]], ["payment", "account"])  # 收单记录的项在前

    def test_other_peoples_records_are_not_permitted(self):
        self.assertEqual(operator._intake_precheck("CO-1", "I:LI-9")["reason"], "not_permitted")
        self.assertEqual(operator._intake_precheck("CO-2", "I:LI-1")["reason"], "not_permitted")


class Answers(Base):
    def setUp(self):
        super().setUp()
        self.saved = {}
        self.db.set_value.side_effect = lambda doctype, name, values: self.saved.update(values)
        self.db.exists.side_effect = lambda doctype, name: name == "P-1"
        self.doc = SimpleNamespace(name="LI-1", direction="purchase", line_choices=json.dumps(
            {"lines": [{"decided": False, "item_type": "goods"}, {"decided": False, "item_type": "goods"}], "warehouse": None}))
        self.enterContext(patch.object(frappe, "get_doc", return_value=self.doc))
        self.enterContext(patch("pearnly_erp_portal.inventory.warehouses.names_of", return_value=["WH-1"]))

    def test_answers_are_written_back_and_express_answers_pass_through(self):
        express = {"kind": "account", "subject": "Paper", "value": "53-01"}
        rest = operator.apply_intake_answers("CO-1", "LI-1", [
            {"kind": "payment", "subject": "erp-intake:LI-1", "value": operator.UNPAID},
            {"kind": "item", "subject": "erp-intake:LI-1:0", "value": "P-1"},
            {"kind": "item", "subject": "erp-intake:LI-1:1", "value": operator.RECORD_ONLY},
            {"kind": "location", "subject": "erp-intake:LI-1:warehouse", "value": "WH-1"}, express])
        self.assertEqual(rest, [express])
        self.assertEqual(self.saved["is_paid"], 0)
        chosen = json.loads(self.saved["line_choices"])
        self.assertEqual([(l["item_type"], l["product"], l["decided"]) for l in chosen["lines"]],
                         [("goods", "P-1", True), ("service", None, True)])
        self.assertEqual(chosen["warehouse"], "WH-1")

    def test_values_not_offered_are_refused(self):
        for answer in ({"kind": "payment", "subject": "erp-intake:LI-1", "value": "bitcoin"},
                       {"kind": "item", "subject": "erp-intake:LI-1:0", "value": "P-unknown"},
                       {"kind": "item", "subject": "erp-intake:LI-1:7", "value": "P-1"},
                       {"kind": "location", "subject": "erp-intake:LI-1:warehouse", "value": "WH-elsewhere"}):
            with self.assertRaises(frappe.ValidationError):
                operator.apply_intake_answers("CO-1", "LI-1", [answer])

    def test_search_lists_own_products_with_new_and_record_only_first(self):
        rows = [{"name": "P-1", "code": "A1", "product_name": "Paper"}]
        with patch("pearnly_erp_portal.products.service.list_products", return_value=rows), \
                patch("pearnly_erp_portal.masters.company.acting") as acting:
            found = operator.intake_search("CO-1", "I:LI-1", "own_products", "", 0, 20)
        acting.assert_called_once_with("CO-1")
        self.assertEqual([o["code"] for o in found["options"]], [operator.card_new(), operator.RECORD_ONLY, "P-1"])
        with self.assertRaises(frappe.PermissionError):
            operator.intake_search("CO-1", "I:LI-9", "own_products")


class Submit(Base):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.object(operator, "apply_intake_answers", side_effect=lambda company, name, answers: list(answers or [])))
        self.db.get_value.side_effect = lambda doctype, name, fields=None, as_dict=False, **kw: (
            json.dumps({"lines": [{"decided": True}]}) if fields == "line_choices" else self.get_value(doctype, name, fields, as_dict))

    def test_unanswered_lines_hold_the_record(self):
        self.db.get_value.side_effect = lambda doctype, name, fields=None, as_dict=False, **kw: (
            json.dumps({"lines": [{"decided": False}]}) if fields == "line_choices" else self.get_value(doctype, name, fields, as_dict))
        with patch.object(intake, "submit_chat") as chat:
            results = operator.submit("CO-1", ["I:LI-1"], {"task": "T1"}, "PC-1")
        self.assertEqual(results, [{"doc_id": "I:LI-1", "status": "failed", "reason": operator.UNDECIDED_LINES_TEXT}])
        chat.assert_not_called()

    def test_submitted_once_then_handed_to_the_operator(self):
        def submit_chat(name):
            self.rows[name].purchase, self.rows[name].status = "PO-7", "confirmed"
            return {"name": "PO-7"}

        with patch.object(intake, "submit_chat", side_effect=submit_chat) as chat, \
                patch.object(operator.push, "release_operator"), \
                patch.object(operator.push, "send", return_value={"status": "queued"}) as send, \
                patch.object(operator, "_remember") as remember:
            express = [{"kind": "account", "subject": "Paper", "value": "53-01"}]
            results = operator.submit("CO-1", ["I:LI-1"], {"task": "T1"}, "PC-1", {"I:LI-1": express})
            operator.submit("CO-1", ["I:LI-1"], {"task": "T1"}, "PC-1")
        remember.assert_called_once_with("CO-1", "Purchase Document", "PO-7", express)  # Express 的项记到提交出的单据上
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
