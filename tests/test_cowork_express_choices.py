"""账套选择（`push/express/choices.py`）：压测里系统猜不准的几类，要人选一次、记住后同类自动套用。"""
import unittest

from pearnly_core.express_rules.push import choices as C

BIGC = "0107536000633"
SUPPLIER = "0105536080112"
CUSTOMERS = [{"code": "BIG C", "name": "บิ๊กซี ซูเปอร์เซ็นเตอร์", "tax_id": BIGC, "branch": "00000"},
             {"code": "BIG C -001", "name": "บิ๊กซี (สาขา-พลับพลาไชย)", "tax_id": BIGC, "branch": "00000"},
             {"code": "BIG C -002", "name": "บิ๊กซี (สาขา-ซอยประชุม)", "tax_id": BIGC, "branch": "00000"},
             {"code": "พ003", "name": "พาวเวอร์วัน", "tax_id": "0105562132302", "branch": "00000"}]
SUPPLIERS = [{"code": "B004", "name": "BANGCHAK GREENNET CO.,LTD.", "tax_id": SUPPLIER, "branch": "00000"}]
PRODUCTS = [{"code": "น้ำแข็งแพ็ค", "name": "น้ำแข็งแพ็ค", "kind": "stock"},
            {"code": "DM-R22", "name": "DM-น้ำยา R22", "kind": "stock"}]
ACCOUNTS = [{"code": "53-05-02-00", "name": "ค่าน้ำมัน"}, {"code": "11-04-02-00", "name": "ซื้อ"}]
CONFIG = {"reported_customers": CUSTOMERS, "reported_suppliers": SUPPLIERS, "reported_products": PRODUCTS,
          "reported_accounts": ACCOUNTS}


def sale(**fields):
    return {"buyer_name": "บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์ (สาขา-ซอยประชุม)", "buyer_tax": BIGC, "buyer_branch": "00000",
            "items": [{"name": "น้ำแข็งหลอด", "qty": "40", "price": "6.7", "subtotal": "268"}], **fields}


def purchase(**fields):
    return {"seller_name": "BANGCHAK GREENNET CO.,LTD.", "seller_tax": SUPPLIER, "seller_branch": "00000",
            "document_type": "tax_invoice", "vat": "20.48", "total_amount": "313.00", **fields}


def kinds(result):
    return [item["kind"] for item in result["needed"]]


class Choices(unittest.TestCase):
    def test_several_customers_with_one_tax_id_ask_once_then_apply(self):
        first = C.resolve(sale(), direction="sales", posting_kind="stock", config=CONFIG, remembered={})
        party = next(item for item in first["needed"] if item["kind"] == "party")
        self.assertEqual([row["code"] for row in party["options"]], ["BIG C", "BIG C -001", "BIG C -002"])
        # 票面品名（客户的叫法）对不上 Express 存货名 → 也问一次
        item = next(item for item in first["needed"] if item["kind"] == "item")
        remembered = {"party": {party["subject"]: "BIG C -002"}, "item": {item["subject"]: "น้ำแข็งแพ็ค"}}
        second = C.resolve(sale(), direction="sales", posting_kind="stock", config=CONFIG, remembered=remembered)
        self.assertEqual(second["needed"], [])
        self.assertEqual(second["applied"]["party"], "BIG C -002")
        payload = {"direction": "sales", "customer": {"code": "", "customer_new": True},
                   "items": [{"name": "น้ำแข็งหลอด", "item_mode": "stock_sale"}], "payload_version": 1}
        C.apply_to_payload(payload, second["applied"])
        self.assertEqual(payload["customer"], {"code": "BIG C -002", "customer_new": False})
        self.assertEqual((payload["items"][0]["code"], payload["payload_version"]), ("น้ำแข็งแพ็ค", 2))

    def test_a_branch_the_record_does_not_have_asks_instead_of_creating_a_duplicate(self):
        # 压测：分店 00113 读成 00013，小助手按分店找不到就新建了 B010
        result = C.resolve(purchase(seller_branch="00013", posting_payment_manual="credit"), direction="purchase",
                           posting_kind="service", config=CONFIG, remembered={})
        self.assertIn("party", kinds(result))
        # 选「新建」：照票面新建，不再拦
        subject = next(item["subject"] for item in result["needed"] if item["kind"] == "party")
        chosen = C.resolve(purchase(seller_branch="00013", posting_payment_manual="credit"), direction="purchase",
                           posting_kind="service", config=CONFIG,
                           remembered={"party": {subject: C.NEW}, "account": {C.supplier_subject(
                               "purchase", SUPPLIER, "BANGCHAK GREENNET CO.,LTD."): "53-05-02-00"}})
        self.assertEqual(chosen["needed"], [])
        payload = {"direction": "purchase", "supplier": {"code": "", "supplier_new": True}, "items": []}
        C.apply_to_payload(payload, chosen["applied"])
        self.assertTrue(payload["supplier"]["confirmed_new"])

    def test_a_single_matching_party_and_known_item_names_do_not_ask(self):
        fields = sale(buyer_name="พาวเวอร์วัน", buyer_tax="0105562132302",
                      items=[{"name": "น้ำแข็งแพ็ค", "qty": "1", "subtotal": "5"}])
        self.assertEqual(C.resolve(fields, direction="sales", posting_kind="stock", config=CONFIG,
                                   remembered={})["needed"], [])

    def test_a_service_purchase_needs_its_expense_account_and_payment_once(self):
        result = C.resolve(purchase(), direction="purchase", posting_kind="service", config=CONFIG, remembered={})
        self.assertEqual(sorted(kinds(result)), ["account", "payment"])
        # 科目映射（分类 → 科目）已定就不问科目
        mapped = C.resolve(purchase(), direction="purchase", posting_kind="service", config=CONFIG,
                           remembered={}, account_resolved=True)
        self.assertEqual(kinds(mapped), ["payment"])
        # 票上会计改过付款方式：不问，并交给调用方按这家记下
        explicit = C.resolve(purchase(posting_payment_manual="credit"), direction="purchase",
                             posting_kind="service", config=CONFIG, remembered={}, account_resolved=True)
        self.assertEqual((explicit["needed"], explicit["applied"]["remember_payment"]), ([], "credit"))

    def test_a_receipt_with_vat_asks_whether_to_claim_it(self):
        result = C.resolve(purchase(document_type="receipt", posting_payment_manual="credit"), direction="purchase",
                           posting_kind="service", config=CONFIG, remembered={}, account_resolved=True)
        self.assertEqual(kinds(result), ["vat"])

    def test_a_remembered_value_that_left_express_is_asked_again(self):
        subject = C.supplier_subject("purchase", SUPPLIER, "BANGCHAK GREENNET CO.,LTD.")
        result = C.resolve(purchase(posting_payment_manual="credit"), direction="purchase", posting_kind="service",
                           config=CONFIG, remembered={"account": {subject: "99-99-99-99"}})
        needed = next(item for item in result["needed"] if item["kind"] == "account")
        self.assertEqual(needed["current"], "99-99-99-99")

    def test_an_applied_account_says_whether_it_was_remembered_or_the_task_default(self):
        # 测试窗口第 2 轮（F19 后续）：科目哪来的一并给出（写进载荷 account_source）
        subject = C.supplier_subject("purchase", SUPPLIER, "BANGCHAK GREENNET CO.,LTD.")
        plain = C.resolve(purchase(posting_payment_manual="credit"), direction="purchase", posting_kind="service",
                          config=CONFIG, remembered={"account": {subject: "53-05-02-00"}})
        ruled = C.resolve(purchase(posting_payment_manual="credit"), direction="purchase", posting_kind="service",
                          config=CONFIG, remembered={"account": {subject: "53-05-02-00"}},
                          fixed={"account": "11-04-02-00"})
        self.assertEqual((plain["applied"]["account"], plain["applied"]["account_from"]), ("53-05-02-00", "remembered"))
        self.assertEqual((ruled["applied"]["account"], ruled["applied"]["account_from"]), ("11-04-02-00", "task_default"))
        mapped = C.resolve(purchase(posting_payment_manual="credit"), direction="purchase", posting_kind="service",
                           config=CONFIG, remembered={}, account_resolved=True)
        self.assertNotIn("account_from", mapped["applied"])  # 科目映射定的由映射器记 category_map

    def test_department_is_not_asked_but_a_remembered_one_is_used(self):
        # 用户 2026-10-08：部门不再问（没选过由小助手取单据簿默认部门或不记）；以前选过 / 自动录入规则设的照用
        config = {**CONFIG, "reported_departments": [{"code": "SUTI", "name": "สาขาที่ 1"},
                                                     {"code": "HQ", "name": "สำนักงานใหญ่"}]}
        result = C.resolve(purchase(posting_payment_manual="credit"), direction="purchase", posting_kind="service",
                           config=config, remembered={}, account_resolved=True)
        self.assertEqual(kinds(result), [])
        self.assertNotIn("department", result["applied"])
        done = C.resolve(purchase(posting_payment_manual="credit"), direction="purchase", posting_kind="service",
                         config=config, remembered={"department": {"*": "SUTI"}}, account_resolved=True)
        payload = C.apply_to_payload({"direction": "purchase", "supplier": {}, "items": []}, done["applied"])
        self.assertEqual((payload["depcod"], payload["payload_version"]), ("SUTI", 2))

    def test_without_reported_masters_nothing_is_asked(self):
        # 旧版小助手没报供应商 / 部门：不拦，照旧由小助手按名匹配
        result = C.resolve(purchase(posting_payment_manual="credit"), direction="purchase", posting_kind="service",
                           config={}, remembered={}, account_resolved=True)
        self.assertEqual(result["needed"], [])

    def test_names_compare_without_legal_forms(self):
        self.assertEqual(C.norm_name("บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์"), C.norm_name("บิ๊กซี\xa0ซูเปอร์เซ็นเตอร์ จำกัด (มหาชน)"))


class FolderRuleDefaults(unittest.TestCase):
    """自动录入规则的默认值只管这条规则的票：当这张票的选择用、优先于账套记过的，不进账套记忆（用户 2026-10-08）。"""

    def test_rule_values_win_for_this_document_only(self):
        subject = C.supplier_subject("purchase", SUPPLIER, "BANGCHAK GREENNET CO.,LTD.")
        remembered = {"payment": {subject: "credit"}}
        plain = C.resolve(purchase(), direction="purchase", posting_kind="service", config=CONFIG,
                          remembered=remembered, account_resolved=True)
        ruled = C.resolve(purchase(), direction="purchase", posting_kind="service", config=CONFIG,
                          remembered=remembered, account_resolved=True, fixed={"payment": "cash", "vat": "claim"})
        self.assertEqual(plain["applied"].get("payment"), "credit")
        self.assertEqual(ruled["applied"].get("payment"), "cash")
        # 哪来的一并给出（写进载荷 doctype_src，CC 推送结果据此说清，验收 F19）
        self.assertEqual(plain["applied"].get("payment_from"), "remembered")
        self.assertEqual(ruled["applied"].get("payment_from"), "task_default")
        self.assertNotIn("payment", {item["kind"] for item in ruled["needed"]})


class PaymentWrittenOnTheInvoice(unittest.TestCase):
    """票面写明现购 / 赊购就照票面；任务默认值与这家记过的只管票面没写的（10-09 实测）。"""

    def test_written_cash_beats_the_task_default_and_the_remembered_choice(self):
        subject = C.supplier_subject("purchase", SUPPLIER, "BANGCHAK GREENNET CO.,LTD.")
        for written in ({"payment_status": "paid"}, {"payment_method": "เงินสด"}, {"payment_status": "credit"}):
            result = C.resolve(purchase(**written), direction="purchase", posting_kind="service", config=CONFIG,
                               remembered={"payment": {subject: "credit"}}, account_resolved=True,
                               fixed={"payment": "cash" if written.get("payment_status") == "credit" else "credit"})
            self.assertNotIn("payment", result["applied"], written)
            self.assertNotIn("payment", {item["kind"] for item in result["needed"]})

    def test_unwritten_payment_takes_the_task_default(self):
        result = C.resolve(purchase(), direction="purchase", posting_kind="service", config=CONFIG,
                           remembered={}, account_resolved=True, fixed={"payment": "cash"})
        self.assertEqual(result["applied"].get("payment"), "cash")


if __name__ == "__main__":
    unittest.main()
