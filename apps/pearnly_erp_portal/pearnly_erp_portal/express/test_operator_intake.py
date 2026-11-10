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

from pearnly_erp_portal.express import contracts, operator
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
        # 同一张票的判定（`ledger_tickets`）默认都是「没写过」；要测它的用例自己换掉
        self.enterContext(patch("pearnly_core.ledger_tickets.lookup", return_value={}))
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
        # 11-08 生产（Cowork）：代录等着的票被直写写进了真账；ERP 同样按共用层同一张票的判定（`ledger_tickets`，复核 5 批之第 1 批）：
        # 开始前卡片标出它在 Express 里的单号；交单默认跳过，正在写的也跳过；人勾了「再写一次」的照交。交单在按身份的锁里
        from pearnly_core import ledger_tickets

        found = {"I:LI-1": {"doc_id": "P:PO-3", "docnum": "RR-9", "state": "entered", "key": "CO-1:k"}}
        locked = []

        class Lock:
            def __init__(self, key, **kw):
                locked.append(key)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with patch.object(ledger_tickets, "lookup", side_effect=lambda doc_id: found.get(doc_id, {})), \
                patch.object(frappe, "cache", SimpleNamespace(lock=Lock), create=True), \
                patch.object(frappe.local, "site", "t", create=True), \
                patch.object(frappe, "get_doc", return_value=self.doc()):
            row = operator._intake_precheck("CO-1", "I:LI-1")
            self.assertEqual((row["state"], row["docnum"], row["twin"]), ("written", "RR-9", "P:PO-3"))
            with patch.object(operator, "_submit_intake") as sub:
                skipped = operator.submit("CO-1", ["I:LI-1"], {"task": "T1"}, "PC-1")
                self.assertEqual(skipped, [{"doc_id": "I:LI-1", "status": "written", "reason": "RR-9"}])
                sub.assert_not_called()
                found["I:LI-1"]["state"] = "queued"  # 正在写：照样跳过
                self.assertEqual(operator.submit("CO-1", ["I:LI-1"], {"task": "T1"}, "PC-1")[0]["status"], "written")
                sub.return_value = {"doc_id": "I:LI-1", "status": "queued"}
                operator.submit("CO-1", ["I:LI-1"], {"task": "T1", "again": ["I:LI-1"]}, "PC-1")
                sub.assert_called_once()  # 人勾了「再写一次」的照交
        self.assertEqual(locked, ["t:pearnly_ledger_ticket:CO-1:k"] * 3)

    def test_other_peoples_records_are_not_permitted(self):
        self.assertEqual(operator._intake_precheck("CO-1", "I:LI-9")["reason"], "not_permitted")
        self.assertEqual(operator._intake_precheck("CO-2", "I:LI-1")["reason"], "not_permitted")


class SalesNumber(Base):
    """A 线 11-10（ERP 销售票小批，主会话审定）：销售单号建单时才编，开始前预检按收单记录拼表单没有单号，写账护栏判
    ``invoice_no_missing`` → 整张「写不了」、问题也不出。预检按编号规则预看下一个号（不占号）再判；真正建单照常编号，写入与回执
    以实际编出的为准（预看号不落到记录上、不进交单入参）。"""

    def test_a_sales_record_is_judged_with_the_next_number_previewed_not_taken(self):
        doc = SimpleNamespace(name="LI-1", direction="sales", is_paid=1, payment_method="cash",
                              line_choices=json.dumps({"lines": [{"decided": True, "item_type": "service"}]}),
                              result=json.dumps({"doc_no": "IV69/00751", "doc_date": "2026-10-15", "buyer_name": "Big C"}))
        self.rows["LI-1"].direction = "sales"
        found = {"result": {"book": {}}, "items": [], "data": None}
        built = {"company": "CO-1", "doc_date": "2026-10-15"}
        with patch.object(frappe, "get_doc", return_value=doc), \
                patch.object(intake, "build_payload", return_value=built), \
                patch("pearnly_erp_portal.numbering.service.preview", return_value="IV2569-000007") as preview, \
                patch("pearnly_erp_portal.numbering.service.allocate", side_effect=AssertionError("a precheck takes no number")), \
                patch("pearnly_erp_portal.express.precheck.collect_form", return_value=found) as collect:
            operator._intake_precheck("CO-1", "I:LI-1")
        self.assertEqual(collect.call_args.args[0], "ERP Sales Document")
        self.assertEqual(collect.call_args.args[1]["system_no"], "IV2569-000007")
        self.assertEqual(preview.call_args.kwargs, {"on": "2026-10-15"})
        self.assertNotIn("system_no", built)  # 交单时照网页同一入账函数现编（`sales.entry`），写入与回执以那个号为准
        self.db.set_value.assert_not_called()

    def test_a_purchase_record_keeps_its_printed_number(self):
        doc = SimpleNamespace(name="LI-1", direction="purchase", is_paid=1, payment_method="cash",
                              line_choices=json.dumps({"lines": [{"decided": True, "item_type": "service"}]}),
                              result=json.dumps({"doc_no": "IV1", "supplier_name": "ACME"}))
        found = {"result": {"book": {}}, "items": [], "data": None}
        with patch.object(frappe, "get_doc", return_value=doc), \
                patch.object(intake, "build_payload", return_value={"company": "CO-1", "doc_no": "IV1"}), \
                patch("pearnly_erp_portal.numbering.service.preview", side_effect=AssertionError("purchases print their number")), \
                patch("pearnly_erp_portal.express.precheck.collect_form", return_value=found) as collect:
            operator._intake_precheck("CO-1", "I:LI-1")
        self.assertNotIn("system_no", collect.call_args.args[1])


ME, BIGC = "0105561077332", "0107536999991"  # 校验位都对


class OwnParty(Base):
    """A 线 11-10（ERP 销售票小批，主会话审定）：公司没税号时认不出「卖方是本公司」，本公司开出的销售票被当采购建档。开始卡问一次
    「本公司是票上的哪一方」，答案记成公司税号（留修改记录）；卖方是本公司的整份文件改按销售重读（识别费不再扣），这次不交。"""

    def guessed(self, name="LI-1", seller=ME, buyer=BIGC):
        return SimpleNamespace(name=name, direction="purchase", direction_guessed=1, is_paid=1, payment_method="cash",
                               line_choices=json.dumps({"lines": [{"decided": True, "item_type": "service"}]}),
                               result=json.dumps({"supplier_name": "ชินเซียร์ ไอซ์", "supplier_tax_id": seller,
                                                  "buyer_name": "บิ๊กซี", "buyer_tax_id": buyer}))

    def test_a_company_without_a_tax_id_is_asked_once_which_side_it_is(self):
        doc = self.guessed()
        item = operator._own_party_item("CO-1", doc, json.loads(doc.result))
        self.assertEqual((item["kind"], item["subject"]), ("own_party", "CO-1"))
        self.assertEqual([o["code"] for o in item["options"]], [f"{ME}:00000", f"{BIGC}:00000"])
        with patch.object(operator, "_own_tax", return_value=ME):  # 有税号：认得出，不问
            self.assertIsNone(operator._own_party_item("CO-1", doc, json.loads(doc.result)))
        said = SimpleNamespace(**{**vars(doc), "direction_guessed": 0})
        self.assertIsNone(operator._own_party_item("CO-1", said, json.loads(doc.result)))  # 人说了方向：不问

    def test_a_record_held_for_the_missing_company_tax_id_still_asks_which_side(self):
        """主会话 11-10 核两条路：没接 Express 的公司（代录对它也开放）、接 MR.ERP 的公司（不要求税号），没税号时预检停在
        「公司没税号」写不了。这正是这一题要补的信息：照样问（卡片与原来回包的 needed 都有），答了再判。"""
        doc = self.guessed()
        found = {"result": {"hold": contracts.NO_TAX}, "items": [], "data": None}
        with patch.object(frappe, "get_doc", return_value=doc), \
                patch.object(intake, "build_payload", return_value={"company": "CO-1"}), \
                patch("pearnly_erp_portal.express.precheck.collect_form", return_value=found):
            row = operator._intake_precheck("CO-1", "I:LI-1")
        self.assertEqual((row["state"], [n["kind"] for n in row["needed"]][:1]), ("choose", ["own_party"]))
        self.assertEqual((row["precheck"]["state"], [i["kind"] for i in row["precheck"]["items"]][:1]), ("check", ["own_party"]))

    def test_a_batch_asks_one_question_with_the_tax_id_on_every_ticket_preselected(self):
        item = lambda codes: {"kind": "own_party", "subject": "CO-1", "label": "CO", "options": [{"code": c, "name": c} for c in codes]}
        documents = [{"doc_id": "I:LI-1", "precheck": {"items": [item([f"{ME}:00000", f"{BIGC}:00000"])]},
                      "needed": [{**item([f"{ME}:00000", f"{BIGC}:00000"]), "current": ""}]},
                     {"doc_id": "I:LI-2", "precheck": {"items": [item([f"{ME}:00000", "0105559999996:00000"])]},
                      "needed": [{**item([f"{ME}:00000", "0105559999996:00000"]), "current": ""}]}]
        operator._own_party_batch(documents)
        first = documents[0]["precheck"]["items"][0]
        self.assertEqual((first["value"], [o["code"] for o in first["options"]][0]), (f"{ME}:00000", f"{ME}:00000"))
        self.assertEqual(documents[1]["needed"][0]["current"], f"{ME}:00000")
        self.assertNotIn("value", documents[1]["needed"][0])

    def test_the_answer_becomes_the_company_tax_id_and_its_own_sales_file_is_read_again(self):
        rows = {"I:LI-1": self.guessed("LI-1"), "I:LI-2": self.guessed("LI-2", seller="0105559999996", buyer=ME)}
        files = {"LI-1": "h1", "LI-2": "h2"}
        record = lambda doctype, name, fields=None, as_dict=False, **kw: SimpleNamespace(
            result=rows["I:" + name].result, file_hash=files[name], direction_guessed=1)
        self.db.get_value.side_effect = record
        submitted = []
        answer = {"kind": "own_party", "subject": "CO-1", "value": f"{ME}:00000", "label": "CO"}
        with patch.object(operator, "_intake", side_effect=lambda company, doc_id: SimpleNamespace(
                    name=doc_id[2:], direction="purchase", status="ready")), \
                patch.object(operator, "_submitted", return_value=None), \
                patch.object(frappe, "get_all", side_effect=lambda doctype, filters=None, **kw: [
                    SimpleNamespace(result=rows["I:LI-1"].result)] if filters.get("file_hash") == "h1" else []), \
                patch("pearnly_erp_portal.masters.company.set_own_tax") as remembered, \
                patch.object(intake, "reread_as_sales", return_value=True) as reread, \
                patch.object(operator, "_submit_one", side_effect=lambda company, doc_id, index, batch, given, again:
                             submitted.append((doc_id, given)) or {"doc_id": doc_id, "status": "queued", "reason": ""}), \
                patch("pearnly_core.ledger_tickets.one_writer"):
            out = operator.submit("CO-1", ["I:LI-1", "I:LI-2"], {}, "PC-1", {"I:LI-1": [answer], "I:LI-2": [answer]})
        remembered.assert_called_once_with("CO-1", ME, "00000")
        reread.assert_called_once_with("LI-1")
        self.assertEqual(out[0], {"doc_id": "I:LI-1", "status": "failed", "reason": intake.ISSUED_HERE})
        self.assertEqual(submitted, [("I:LI-2", [])])  # 买方是本公司的照交；「本公司」这一题不交给 Express 的记法

    def test_an_answer_for_another_company_is_refused(self):
        answer = {"kind": "own_party", "subject": "CO-2", "value": f"{ME}:00000"}
        with self.assertRaises(frappe.ValidationError):
            operator._own_party_answered("CO-1", ["I:LI-1"], {"I:LI-1": [answer]})


class CompanyOwnTax(Base):
    """`masters.company.set_own_tax`（开始卡「本公司」答案的记法）：没税号时有开单权就能补一次；已有税号要改要管理公司的权限；
    走公司页同一校验；存的时候不跳过修改记录（ERP Company 开着 track_changes）。"""

    def run_it(self, existing="", rights=("sales.write",)):
        from pearnly_erp_portal.masters import company as companies

        doc = Mock(**{"get.side_effect": lambda key, d=None: existing if key == "tax_id" else d})
        with patch.object(companies, "_get", return_value=doc), patch.object(companies, "_", _same), \
                patch.object(companies, "document_company", side_effect=lambda name, user=None: name), \
                patch.object(companies.guard, "has", side_effect=lambda capability, user=None: capability in rights), \
                patch.object(companies.guard, "require", side_effect=lambda capability, user=None: None if capability in rights
                             else (_ for _ in ()).throw(frappe.PermissionError(capability))), \
                patch.object(companies, "_apply") as applied:
            companies.set_own_tax("CO-1", ME, "00000")
        applied.assert_called_once_with(doc, {"tax_id": ME, "branch": "00000"})
        doc.save.assert_called_once_with(ignore_permissions=True)
        self.assertNotEqual(getattr(doc.flags, "ignore_version", None), True)

    def test_filling_a_missing_tax_id_needs_only_the_right_to_issue_documents(self):
        self.run_it()
        with self.assertRaises(frappe.PermissionError):
            self.run_it(rights=())

    def test_changing_an_existing_tax_id_needs_company_management(self):
        self.run_it(existing=BIGC, rights=("company.manage",))
        with self.assertRaises(frappe.PermissionError):
            self.run_it(existing=BIGC, rights=("sales.write",))


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
        # 交之前再判一次（写不了的不交）：默认判得出、能写；要测写不了的用例自己换掉
        self.enterContext(patch.object(operator, "_intake_precheck", return_value={"doc_id": "I:LI-1", "state": "choose"}))

    def record(self, doctype, name, fields=None, as_dict=False, **kw):
        if isinstance(fields, list) and "line_choices" in fields:
            return SimpleNamespace(line_choices=json.dumps(self.choices), direction="sales",
                                   result=json.dumps({"buyer_name": "Big C", "buyer_tax_id": "0107536000633"}))
        return self.get_value(doctype, name, fields, as_dict)

    def test_a_record_the_check_finds_unwritable_is_not_submitted_and_says_why(self):
        """A 线 11-10：预检判「写不了」的票，点开始仍交了出去，再失败成不相干的原因（「部分行未选物料」）。交之前再判一次：
        写不了的不提交、不交电脑，原因照预检写。"""
        self.choices = {"lines": [{"decided": False}], "payment_decided": False}
        blocked = {"doc_id": "I:LI-1", "state": "blocked", "reason": "no_revenue_account", "message": "No revenue account"}
        with patch.object(operator, "_intake_precheck", return_value=blocked) as checked, \
                patch.object(intake, "submit_chat", side_effect=AssertionError("an unwritable record is not submitted")):
            out = operator._submit_intake("CO-1", "I:LI-1", [])
        checked.assert_called_once_with("CO-1", "I:LI-1")
        self.assertEqual(out, {"doc_id": "I:LI-1", "status": "failed", "reason": "No revenue account"})

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
        self.assertIn({"is_paid": 0}, written)  # 记住的「未付」写回记录后才提交

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


if __name__ == "__main__":
    unittest.main()


class TicketTwin(unittest.TestCase):
    """ERP 在共用层「同一张票只写一次」里的声明（`express.tickets`，复核 5 批之第 1 批）：采购单按身份键在同一公司里找另一张写成 /
    正在写的；身份要精确；买方是本公司；只读我们自己的单据（账本状态），不碰 Express 主档。"""

    def test_a_purchase_finds_the_same_ticket_written_by_another_purchase(self):
        from pearnly_erp_portal.express import tickets

        rows = {"PO-1": {"company": "CO-1", "ticket_key": "k" * 40, "ticket_level": "exact"}}
        others = [SimpleNamespace(name="PO-9", ledger_state="written", ledger_docnum="RR-9")]
        seen = []

        def value(doctype, name, fields=None, as_dict=False):
            seen.append(doctype)
            return rows.get(name)

        def listed(doctype, filters=None, **kw):
            seen.append(doctype)
            self.assertEqual((filters["company"], filters["ticket_key"], filters["name"]), ("CO-1", "k" * 40, ["!=", "PO-1"]))
            return list(others)

        with patch.object(frappe, "db", SimpleNamespace(get_value=value), create=True), \
                patch.object(frappe, "get_all", listed, create=True):
            self.assertEqual(tickets.twin("P:PO-1"), {"doc_id": "P:PO-9", "docnum": "RR-9", "state": "entered",
                                                       "key": "CO-1:" + "k" * 40})
            others[0].ledger_state = "queued"
            self.assertEqual((tickets.twin("P:PO-1")["state"], tickets.twin("P:PO-1")["docnum"]), ("queued", ""))
            others.clear()
            self.assertEqual(tickets.twin("P:PO-1"), {"key": "CO-1:" + "k" * 40})
            rows["PO-1"]["ticket_level"] = "near"
            self.assertEqual(tickets.twin("P:PO-1"), {})
            self.assertEqual(tickets.twin("S:SD-1"), {})  # 销售单这一批不判
        self.assertEqual(set(seen), {"Purchase Document"})

    def test_the_identity_reads_the_printed_branch_and_the_own_company(self):
        from pearnly_core.express_rules.contracts import ticket_identity
        from pearnly_erp_portal.express import tickets

        company = {"tax_id": "0315551000297", "branch": "00000"}
        doc = frappe._dict(company="CO-1", doc_no="IV/69/00751", doc_date="2026-10-31", grand_total=1070,
                           supplier_tax_id="0105536000633", supplier_branch_type="branch", supplier_branch_no="2",
                           supplier_name="Big C")
        with patch.object(frappe, "db", SimpleNamespace(get_value=lambda *a, **k: company), create=True):
            key, level = tickets.purchase_identity(doc)
            head = tickets.purchase_identity(frappe._dict(doc, supplier_branch_type="head_office", supplier_branch_no=""))
            none = tickets.purchase_identity(frappe._dict(doc, supplier_branch_type="none", supplier_branch_no=""))
        expected = ticket_identity.identity(ticket_identity.Face(
            direction="purchase", number="IV69-00751", date="2026-10-31", total="1070.00", seller_tax="0105536000633",
            seller_branch="00002", buyer_tax="0315551000297", buyer_branch="00000"))
        self.assertEqual((key, level), (expected.key, "exact"))
        self.assertEqual(len({key, head[0], none[0]}), 3)  # 分店、总公司、没写分店是三张不同的票

