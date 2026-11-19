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
        self.written = []  # 已写进 Express 的采购单（同票号的候选）
        self.enterContext(patch.object(frappe, "get_all", create=True, side_effect=lambda doctype, **kw: list(self.written)))
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

    def test_sources_list_every_pending_record_put_in_and_mark_other_months(self):
        # 11-10 线上压测 S5b：11 月的代录放了 25 页 8 月的票，资料只列单据日期在本月的，25 张都没出现，CC 让用户「放进系统再说」。
        # 放进来的照样列出，不是本月的带 other_month（票是几月的），由 CC 在对话里说清再问（主会话 11-10）
        rows = [{"record": "LI-1", "status": "ready", "direction": "purchase", "doc_no": "IV1", "doc_date": "2026-11-03",
                 "party": "A", "totals": {"grand_total": "107.00"}},
                {"record": "LI-3", "status": "ready", "direction": "sales", "doc_no": "S1", "doc_date": "2026-08-30"},
                {"record": "LI-4", "status": "queued", "direction": "purchase", "doc_no": "", "doc_date": ""}]
        with patch.object(frappe, "get_all", return_value=["LI-1", "LI-3", "LI-4"]), \
                patch.object(intake, "chat_records", return_value=rows):
            found = operator._intake_rows("CO-1", "2026-11-01", "2026-11-30")
        self.assertEqual([(r["doc_id"], r["total"], r.get("other_month")) for r in found],
                         [("I:LI-1", "107.00", None), ("I:LI-3", "", "2026-08")])


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
        # 付款按「方向 + 对方」一题（几张同一对方的票合成一题）；采购没记住、票面没有到期日：预选「已付、现金」
        # （ERP 采购单 = 已付 + 到货，用户 10-28 / P0-2 口径）；人可以改
        self.assertEqual((payment["kind"], payment["subject"], payment["value"], payment["label"]),
                         ("payment", "erp-intake-pay:purchase|name:ACME", "cash", "ACME"))
        # 销售读不到付款条件：没记住就不预选，要人定（不再悄悄按「已付、现金」交单）
        sale = SimpleNamespace(**{**vars(doc), "direction": "sales"})
        with patch("pearnly_erp_portal.inventory.warehouses.names_of", return_value=["WH-1", "WH-2"]):
            self.assertEqual(operator.intake_items("CO-1", sale, {"buyer_name": "Big C"})[0]["value"], "")
        self.assertEqual([o["code"] for o in payment["options"]][-1], operator.UNPAID)
        self.assertEqual((line["kind"], line["subject"], line["label"], line["search"], line["value"]),
                         ("item", "erp-intake:LI-1:0", "Paper A4", "own_products", operator.card_new()))
        self.assertEqual((place["kind"], place["subject"], place["value"]), ("location", "erp-intake:LI-1:warehouse", ""))
        decided = self.doc(lines=[{"decided": True, "item_type": "goods", "product": "P-1"}], warehouse="WH-1", is_paid=0)
        face = {**json.loads(decided.result), "doc_date": "2026-10-02", "due_date": "2026-11-01"}  # 票面赊购 30 天
        items = operator.intake_items("CO-1", decided, face)
        self.assertEqual([(i["kind"], i["value"]) for i in items], [("payment", operator.UNPAID)])  # 定了的行、定了的仓不再问
        with patch("pearnly_erp_portal.express.choices.chosen", return_value="transfer"):
            self.assertEqual(operator.intake_items("CO-1", decided, face)[0]["value"], "transfer")  # 这个对方记住的优先

    def test_records_of_one_party_share_one_payment_question(self):
        """11-08：24 张同一买方的票，付款问 24 遍；按「方向 + 对方税号」只问一题。"""
        result = {"buyer_name": "บมจ.บิ๊กซี", "buyer_tax_id": "0-1075-36000-63-3"}
        self.assertEqual(operator.payment_subject("sales", result), "erp-intake-pay:sales|0107536000633")
        self.assertEqual(operator.payment_subject("sales", {**result, "doc_no": "IV2"}), operator.payment_subject("sales", result))

    def test_payment_is_asked_even_when_express_asks_too(self):
        """11-08 复核（管钱）：Express 那边也要人选（例如同税号几个客户）时，原来只问 Express 那几项，付款没问、交单按默认已付现金。"""
        doc = self.doc()
        legacy = {"doc_id": "I:LI-1", "state": "choose", "needed": [{"kind": "party", "subject": "sales|010", "label": "Big C"}]}
        found = {"result": {"book": {}}, "items": [], "data": None}
        with patch.object(frappe, "get_doc", return_value=doc), \
                patch.object(intake, "build_payload", return_value={"company": "CO-1"}), \
                patch("pearnly_erp_portal.express.precheck.collect_form", return_value=found), \
                patch.object(operator, "legacy_row", return_value=legacy):
            row = operator._intake_precheck("CO-1", "I:LI-1")
        self.assertEqual(row["state"], "choose")
        self.assertEqual([n["kind"] for n in row["needed"]], ["payment", "party"])

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

    def test_a_ticket_already_entered_in_express_is_marked_written_and_skipped_unless_entered_again(self):
        # 11-08 生产（Cowork）：代录等着的票被直写写进了真账；ERP 收单记录同样认：同票号、同供应商税号、同金额、已写进 Express
        doc = self.doc()
        self.written = [SimpleNamespace(name="PO-3", supplier_tax_id=" 0105 ", grand_total="107.00", ledger_docnum="RR-9")]
        result = json.loads(doc.result)
        result.update(doc_no="IV1", supplier_tax_id="0105")
        doc.result = json.dumps(result)
        with patch.object(frappe, "get_doc", return_value=doc), \
                patch.object(intake, "totals", return_value={"grand_total": 107}):
            row = operator._intake_precheck("CO-1", "I:LI-1")
            self.assertEqual((row["state"], row["docnum"], row["twin"]), ("written", "RR-9", "PO-3"))
            with patch.object(frappe.db, "get_value", side_effect=lambda doctype, name, field=None, **kw:
                              doc.result if field == "result" else self.get_value(doctype, name, field, **kw)), \
                    patch.object(intake, "submit_chat") as chat, patch.object(operator.push, "release_operator"), \
                    patch.object(operator.push, "send", return_value={"status": "queued"}):
                skipped = operator.submit("CO-1", ["I:LI-1"], {"task": "T1"}, "PC-1")
                self.assertEqual(skipped, [{"doc_id": "I:LI-1", "status": "written", "reason": "RR-9"}])
                chat.assert_not_called()
                with patch.object(operator, "_submit_intake", return_value={"doc_id": "I:LI-1", "status": "queued"}) as sub:
                    operator.submit("CO-1", ["I:LI-1"], {"task": "T1", "again": ["I:LI-1"]}, "PC-1")
                sub.assert_called_once()  # 人勾了「再写一次」的照交
            self.written[0].grand_total = "108.00"  # 金额不同：不是同一张
            self.assertEqual(operator.written_twin("CO-1", "purchase", result, lambda: 107), {})
            self.assertEqual(operator.written_twin("CO-1", "sales", result, lambda: 108), {})  # 销售单不判

    def test_other_peoples_records_are_not_permitted(self):
        self.assertEqual(operator._intake_precheck("CO-1", "I:LI-9")["reason"], "not_permitted")
        self.assertEqual(operator._intake_precheck("CO-2", "I:LI-1")["reason"], "not_permitted")

    def test_line_records_are_judged_but_not_handed_to_the_operator(self):
        """11-19 LINE 出卡前问完：写账判定也认 LINE 收单的记录（本人、这家公司的），代录（预检、交单）仍只认对话来的。"""
        self.rows["LI-7"] = SimpleNamespace(name="LI-7", source="line", sent_by="buyer@x", company="CO-1", direction="purchase",
                                            status="ready", purchase=None, sales_document=None)
        self.rows["LI-8"] = SimpleNamespace(**{**vars(self.rows["LI-7"]), "name": "LI-8", "sent_by": "other@x"})
        self.assertEqual(operator.judged_intake("CO-1", "I:LI-7").name, "LI-7")
        self.assertEqual(operator.judged_intake("CO-1", "I:LI-1").name, "LI-1")
        self.assertIsNone(operator.judged_intake("CO-1", "I:LI-8"))   # 别人发的
        self.assertIsNone(operator.judged_intake("CO-2", "I:LI-7"))   # 别家公司
        self.assertIsNone(operator._intake("CO-1", "I:LI-7"))         # 代录不认
        self.assertEqual(operator._intake_precheck("CO-1", "I:LI-7")["reason"], "not_permitted")

    def test_a_line_record_s_payment_was_chosen_before_upload_and_is_not_asked(self):
        doc = SimpleNamespace(**{**vars(self.doc(is_paid=0, method="")), "source": "line"})
        payment, = operator.intake_items("CO-1", doc, json.loads(doc.result))
        self.assertEqual((payment["value"], payment["source"]), (operator.UNPAID, "remembered"))

    def test_without_express_a_line_record_asks_only_its_own_items(self):
        from pearnly_erp_portal.express import precheck

        row = SimpleNamespace(name="LI-7", source="line", sent_by="buyer@x", company="CO-1", direction="purchase",
                              status="ready", purchase=None, sales_document=None)
        doc = SimpleNamespace(**{**vars(self.doc(lines=[{"decided": False, "item_type": "goods", "product": None}])),
                                 "source": "line"})
        with patch.object(frappe, "get_doc", return_value=doc, create=True), \
                patch("pearnly_erp_portal.inventory.warehouses.names_of", return_value=["WH-1", "WH-2"]):
            view = operator.intake_own_view("CO-1", "I:LI-7", row)
        self.assertEqual((view["state"], view["ref"]), ("check", "IV1"))
        self.assertEqual([(item["kind"], item["source"]) for item in view["items"]],
                         [("payment", "remembered"), ("item", "rule"), ("location", "")])
        row.status = "cancelled"
        self.assertEqual(operator.intake_own_view("CO-1", "I:LI-7", row)["state"], "not_writable")
        # 写账判定：没接 Express 时 LINE 的记录给这一份，CC 代录的照旧写不了
        with patch.object(precheck, "_company_ok", return_value=True), \
                patch.object(precheck.push, "enabled", return_value=False), \
                patch.object(precheck.mrerp_push, "handles", return_value=False), \
                patch.object(precheck, "_collection", return_value=SimpleNamespace(
                    mine=lambda doc_id: False, check=lambda company, ids: {"documents": []})), \
                patch.object(precheck, "target_of", side_effect=lambda company, doc_id: (precheck.INTAKE, row)), \
                patch.object(operator, "intake_own_view", return_value={"doc_id": "I:LI-7", "state": "check", "items": []}):
            self.assertEqual(precheck.check("CO-1", ["I:LI-7"])["documents"], [{"doc_id": "I:LI-7", "state": "check", "items": []}])
            row.source = "cc"
            self.assertEqual(precheck.check("CO-1", ["I:LI-7"])["documents"][0]["reason"], precheck.contracts.DISCONNECTED)


class Answers(Base):
    def setUp(self):
        super().setUp()
        self.saved = {}
        self.db.set_value.side_effect = lambda doctype, name, values: self.saved.update(values)
        self.db.exists.side_effect = lambda doctype, name: name == "P-1"
        self.doc = SimpleNamespace(name="LI-1", direction="purchase", result=json.dumps({"supplier_name": "ACME"}),
                                   line_choices=json.dumps({"lines": [{"decided": False, "item_type": "goods"},
                                                                      {"decided": False, "item_type": "goods"}], "warehouse": None}))
        self.remembered = []
        self.enterContext(patch("pearnly_erp_portal.express.choices.remember",
                                side_effect=lambda *args: self.remembered.append(args)))
        self.enterContext(patch.object(frappe, "get_doc", return_value=self.doc))
        self.enterContext(patch("pearnly_erp_portal.inventory.warehouses.names_of", return_value=["WH-1"]))

    def test_answers_are_written_back_and_express_answers_pass_through(self):
        express = {"kind": "account", "subject": "Paper", "value": "53-01"}
        rest = operator.apply_intake_answers("CO-1", "LI-1", [
            {"kind": "payment", "subject": "erp-intake-pay:purchase|name:ACME", "value": operator.UNPAID, "label": "ACME"},
            {"kind": "item", "subject": "erp-intake:LI-1:0", "value": "P-1"},
            {"kind": "item", "subject": "erp-intake:LI-1:1", "value": operator.RECORD_ONLY},
            {"kind": "location", "subject": "erp-intake:LI-1:warehouse", "value": "WH-1"}, express])
        self.assertEqual(rest, [express])
        self.assertEqual(self.saved["is_paid"], 0)
        self.assertTrue(json.loads(self.saved["line_choices"])["payment_decided"])
        # 按对方记住，同一对方下一张预选它
        self.assertEqual(self.remembered, [("CO-1", operator.PAYMENT_KIND, "erp-intake-pay:purchase|name:ACME", operator.UNPAID, "ACME")])
        chosen = json.loads(self.saved["line_choices"])
        self.assertEqual([(l["item_type"], l["product"], l["decided"]) for l in chosen["lines"]],
                         [("goods", "P-1", True), ("service", None, True)])
        self.assertEqual(chosen["warehouse"], "WH-1")

    def test_values_not_offered_are_refused(self):
        for answer in ({"kind": "payment", "subject": "erp-intake-pay:purchase|name:ACME", "value": "bitcoin"},
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
        self.choices = {"lines": [{"decided": True}], "payment_decided": True}
        self.db.get_value.side_effect = self.record

    def record(self, doctype, name, fields=None, as_dict=False, **kw):
        if isinstance(fields, list) and "line_choices" in fields:
            return SimpleNamespace(line_choices=json.dumps(self.choices), direction="sales",
                                   result=json.dumps({"buyer_name": "Big C", "buyer_tax_id": "0107536000633"}))
        return self.get_value(doctype, name, fields, as_dict)

    def test_unanswered_lines_hold_the_record(self):
        self.choices = {"lines": [{"decided": False}], "payment_decided": True}
        with patch.object(intake, "submit_chat") as chat:
            results = operator.submit("CO-1", ["I:LI-1"], {"task": "T1"}, "PC-1")
        self.assertEqual(results, [{"doc_id": "I:LI-1", "status": "failed", "reason": operator.UNDECIDED_LINES_TEXT}])
        chat.assert_not_called()

    def test_payment_nobody_chose_holds_the_record_instead_of_paid_cash(self):
        """11-08 复核（管钱）：销售付款没人定、这个对方没记过（销售读不到付款条件）：停下要人定，不按默认「已付、现金」提交
        （赊销票会记成已付）。这个对方记住过的照记住的提交。"""
        self.choices = {"lines": [{"decided": True}]}
        with patch.object(intake, "submit_chat") as chat, \
                patch("pearnly_erp_portal.express.choices.chosen", return_value=""):
            results = operator.submit("CO-1", ["I:LI-1"], {"task": "T1"}, "PC-1")
        self.assertEqual(results, [{"doc_id": "I:LI-1", "status": "failed", "reason": operator.NO_PAYMENT_TEXT}])
        chat.assert_not_called()
        written = []
        self.db.set_value.side_effect = lambda doctype, name, values: written.append(values)
        with patch.object(intake, "submit_chat", side_effect=frappe.ValidationError("stop here")), \
                patch("pearnly_erp_portal.express.choices.chosen", return_value=operator.UNPAID):
            operator.submit("CO-1", ["I:LI-1"], {"task": "T1"}, "PC-1")
        # 记住的「未付」写回记录（同时记成已定）后才提交
        self.assertEqual([(values["is_paid"], json.loads(values["line_choices"])["payment_decided"]) for values in written],
                         [(0, True)])

    def test_a_purchase_preselect_nobody_clicked_is_not_written(self):
        """主会话 11-16「卡上没点过的预选不生效」：采购按票面能推出预选（没有到期日 = 已付、现金），但没人点过、这个对方也没记过：
        这一张留下要人定，不再按预选提交（直写、代录同一个判法 `settle_payment`）。"""
        self.choices = {"lines": [{"decided": True}]}
        self.db.get_value.side_effect = lambda doctype, name, fields=None, as_dict=False, **kw: (
            SimpleNamespace(line_choices=json.dumps(self.choices), direction="purchase",
                            result=json.dumps({"supplier_name": "ISUZU", "doc_date": "2026-11-01"}))
            if isinstance(fields, list) and "line_choices" in fields else self.get_value(doctype, name, fields, as_dict))
        with patch.object(intake, "submit_chat") as chat, \
                patch("pearnly_erp_portal.express.choices.chosen", return_value=""):
            results = operator.submit("CO-1", ["I:LI-1"], {"task": "T1"}, "PC-1")
        self.assertEqual(results, [{"doc_id": "I:LI-1", "status": "failed", "reason": operator.NO_PAYMENT_TEXT}])
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
        with patch.object(intake, "chat_records", return_value=[{"record": "LI-1", "doc_no": "IV-9", "party": "ISUZU"}]):
            row = operator.status("CO-1", ["I:LI-1"])[0]
        # 没提交：单号、对方按识别出的（CC 重启后页面写成「单号 · 对方」，同 Cowork）
        self.assertEqual((row["state"], row["ref"], row["party"]), ("cancelled", "IV-9", "ISUZU"))
        self.rows["LI-1"].purchase = "PO-7"
        self.db.get_value.side_effect = lambda doctype, name, fields=None, as_dict=False, **kw: (
            self.rows.get(name) if doctype == intake.DOCTYPE else
            SimpleNamespace(ledger_state=operator.contracts.WRITTEN, ledger_docnum="RR001", ledger_reason="", ledger_job="J1",
                            system_no="PE-7", supplier_name="ISUZU"))
        row = operator.status("CO-1", ["I:LI-1"])[0]
        self.assertEqual((row["state"], row["docnum"], row["ref"], row["party"]), ("saved", "RR001", "PE-7", "ISUZU"))
        # 代录保存途中停住：未完成的单多显示 Express 里留下的半截单号（写账件回执 meta.operator.partial），只加单号
        meta = '{"operator": {"partial": {"docnum": "RR0000045", "missing": ["total"]}}}'
        self.db.get_value.side_effect = lambda doctype, name, fields=None, as_dict=False, **kw: (
            self.rows.get(name) if doctype == intake.DOCTYPE else
            SimpleNamespace(ledger_state=operator.contracts.HELD, ledger_docnum="", ledger_reason="partial_saved",
                            ledger_job="J2", system_no="PE-7", supplier_name="ISUZU"))
        jobs = [SimpleNamespace(name="J2", meta_json=meta)]
        with patch.object(frappe, "get_all", create=True, side_effect=lambda doctype, **kw: jobs):  # 循环外一次取回
            row = operator.status("CO-1", ["I:LI-1"])[0]
        self.assertEqual((row["state"], row["docnum"], row["reason"]), ("not_done", "RR0000045", "partial_saved"))


if __name__ == "__main__":
    unittest.main()
