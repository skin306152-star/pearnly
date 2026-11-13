"""CC 引导式录入：共用写账规则在引导模式（``choices.resolve(guided=True)``）下按覆盖表 A 类出题，不再静默默认。

覆盖表 ``docs/work/2026-11-12-cc-guided-entry-express-coverage.md`` 第 5 节：
- A1 现购 / 赊购判不出（付款状态是别的值）一律出题，不落默认 RR；
- A3 服务品进项税口径历史定不了时出题（以前 Cowork 不问，电脑端拒写转人工）；
- A6 多仓账套库存行定不了仓时出题（人在场的入口）；
- A7 明细对不上（mismatch / incomplete）时出「明细」题，不静默只记表头；
- A9 没有同税号档、但有同名档时出「对方」题（疑似重复），以前电脑端转人工；
- A12 销售没有付款信号时出「现销还是赊销」题，不静默默认赊销；
- A14 销售收入科目：记过的照用，没记过出题（连接默认只作预选），不静默落默认只打「待核」。

不给 ``guided``（LINE、网页、定时、自动推）时与以前完全一样。

    python3 tests/test_express_rules_guided.py
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_core.express_rules.push import choices  # noqa: E402

TAX = "0105550000001"
NAME = "Synthetic Supplier"


def purchase(**extra):
    return {"seller_name": NAME, "seller_tax": TAX, **extra}


def sales(**extra):
    return {"buyer_name": "Synthetic Customer", "buyer_tax": "0105550000002", **extra}


def resolve(fields, direction="purchase", config=None, remembered=None, **kw):
    return choices.resolve(fields, direction=direction, posting_kind=kw.pop("posting_kind", None), config=config or {},
                           remembered=remembered or {}, **kw)


def kinds(result):
    return [item["kind"] for item in result["needed"]]


class A1PurchasePayment(unittest.TestCase):
    def test_other_status_is_asked_when_guided(self):
        fields = purchase(payment_status="partial")
        self.assertNotIn("payment", kinds(resolve(fields)))          # 以前：落默认 RR，不问
        self.assertIn("payment", kinds(resolve(fields, guided=True)))

    def test_confident_funnel_is_a_proposal_and_no_signal_is_not(self):
        """主会话 11-12：付款判断有把握（票种：收据 = 已付、完整税票 = 赊）作「怎么录」的预选，要点了才算；没把握不预选。"""
        def item(fields):
            return next(i for i in resolve(fields, guided=True)["needed"] if i["kind"] == "payment")
        self.assertEqual(item(purchase(payment_status="partial", document_type="receipt"))["current"], "cash")
        self.assertEqual(item(purchase(payment_status="partial", document_type="receipt"))["source"], "rule")
        self.assertEqual(item(purchase(payment_status="partial", document_type="tax_invoice"))["current"], "credit")
        self.assertEqual(item(purchase(payment_status="partial"))["current"], "")
        self.assertNotIn("source", item(purchase(payment_status="partial")))

    def test_written_or_remembered_is_not_asked(self):
        self.assertNotIn("payment", kinds(resolve(purchase(payment_status="paid"), guided=True)))
        key = choices.supplier_subject("purchase", TAX, NAME)
        self.assertNotIn("payment", kinds(resolve(purchase(payment_status="partial"), guided=True,
                                                  remembered={"payment": {key: "cash"}})))


class A3ServiceVat(unittest.TestCase):
    def test_guided_turns_on_the_service_vat_question(self):
        import inspect
        source = inspect.getsource(choices.resolve)
        self.assertIn("ask_service_vat or guided", source)


class A6Location(unittest.TestCase):
    CONFIG = {"reported_warehouses": [{"code": "01", "main": "1", "used": "1"}, {"code": "02", "used": "1"}],
              "reported_products": [{"code": "ICE01", "name": "Ice", "kind": "stock"}]}

    def test_multi_warehouse_line_is_asked_when_guided(self):
        fields = purchase(items=[{"name": "Ice", "qty": "1", "subtotal": "10"}])
        self.assertNotIn("location", kinds(resolve(fields, config=self.CONFIG, posting_kind="stock")))
        self.assertIn("location", kinds(resolve(fields, config=self.CONFIG, posting_kind="stock", guided=True)))


class A7Detail(unittest.TestCase):
    def test_mismatched_detail_is_asked_without_preselect(self):
        fields = purchase(invoice_number="INV-1")
        self.assertNotIn("detail", kinds(resolve(fields, items_status="mismatch")))
        result = resolve(fields, items_status="mismatch", guided=True)
        item = next(item for item in result["needed"] if item["kind"] == "detail")
        self.assertEqual(item["current"], "")
        self.assertEqual([o["code"] for o in item["options"]], list(choices.DETAIL_VALUES))

    def test_consistent_detail_is_not_asked(self):
        self.assertNotIn("detail", kinds(resolve(purchase(), items_status="ok", guided=True)))


class A9SameName(unittest.TestCase):
    CONFIG = {"reported_suppliers": [{"code": "S001", "name": NAME, "tax_id": "", "branch": ""}]}

    def test_same_name_without_tax_match_is_asked(self):
        fields = purchase(seller_tax="")
        self.assertNotIn("party", kinds(resolve(fields, config=self.CONFIG)))
        result = resolve(fields, config=self.CONFIG, guided=True)
        item = next(item for item in result["needed"] if item["kind"] == "party")
        self.assertIn("S001", [o["code"] for o in item["options"]])
        self.assertIn(choices.NEW, [o["code"] for o in item["options"]])


class A12SalesPayment(unittest.TestCase):
    def test_no_signal_is_asked_when_guided(self):
        self.assertNotIn("payment", kinds(resolve(sales(), "sales")))   # 以前：默认赊销 IV
        self.assertIn("payment", kinds(resolve(sales(), "sales", guided=True)))

    def test_written_is_not_asked(self):
        self.assertNotIn("payment", kinds(resolve(sales(payment_status="paid"), "sales", guided=True)))

    def test_sales_confident_funnel_is_a_proposal(self):
        found = resolve(sales(document_type="receipt"), "sales", guided=True)["needed"]
        self.assertEqual(next(i for i in found if i["kind"] == "payment")["current"], "cash")

    def test_remembered_is_applied(self):
        key = choices.supplier_subject("sales", "0105550000002", "Synthetic Customer")
        result = resolve(sales(), "sales", guided=True, remembered={"payment": {key: "cash"}})
        self.assertNotIn("payment", kinds(result))
        self.assertEqual(result["applied"].get("payment"), "cash")


class A14Revenue(unittest.TestCase):
    CONFIG = {"revenue_acc": "41-01", "reported_accounts": [{"code": "41-01"}, {"code": "41-02"}]}

    def test_connection_default_is_only_a_proposal(self):
        self.assertNotIn("account", kinds(resolve(sales(), "sales", config=self.CONFIG)))
        result = resolve(sales(), "sales", config=self.CONFIG, guided=True)
        item = next(item for item in result["needed"] if item["kind"] == "account")
        self.assertEqual(item["subject"], choices.REVENUE_SUBJECT)
        self.assertEqual(item["current"], "41-01")

    def test_remembered_revenue_account_is_applied(self):
        result = resolve(sales(), "sales", config=self.CONFIG, guided=True,
                         remembered={"account": {choices.REVENUE_SUBJECT: "41-02"}})
        self.assertNotIn("account", kinds(result))
        self.assertEqual(result["applied"]["account"], "41-02")

    def test_mapped_account_is_not_asked(self):
        self.assertNotIn("account", kinds(resolve(sales(), "sales", config=self.CONFIG, guided=True,
                                                  account_resolved=True)))


class A12SalesMapperHonoursAnswer(unittest.TestCase):
    """销项映射认引导卡上的答案（``config["choices"]["payment"]``），同进项。"""
    CONFIG = {"account_set": "DATAT", "revenue_acc": "41-01-01-00", "vat_output_acc": "21-05-01-00", "ar_acc": "11-03-01-00"}
    HISTORY = {"id": "s1", "invoice_date": "2015-12-31", "invoice_no": "IV581231-001", "total_amount": "107.00",
               "fields": {"buyer_name": "Synthetic Customer", "subtotal": "100.00", "vat": "7.00",
                          "invoice_number": "IV581231-001"}}

    def build(self, payment=None):
        from datetime import date
        from pearnly_core.express_rules.push.sales_mapper import build_express_sales_payload
        config = dict(self.CONFIG, **({"choices": {"payment": payment}} if payment else {}))
        result = build_express_sales_payload(self.HISTORY, config=config, today=date(2026, 9, 21))
        self.assertTrue(result.ok, result.reason)
        return result.payload["doctype"]

    def test_answer_cash_makes_hs(self):
        self.assertEqual(self.build(), "IV")
        self.assertEqual(self.build("cash"), "HS")
        self.assertEqual(self.build("credit"), "IV")


class SalesServiceVat(unittest.TestCase):
    """主会话 11-12：赊销带服务品时销项税口径按这本账套的销项历史定（客户档读回的口径串并集），定不了问一次、按账套记住；
    以前直写写死 1、代录 Express 弹框引擎不认。客户档没带口径串（CC 桌面版还没报）时不问。"""

    def config(self, *srv):
        return {"reported_customers": [{"code": f"C{i}", "name": f"c{i}", "srv": v} for i, v in enumerate(srv)]}

    def fields(self):
        return sales(vat="7", items=[{"name": "Service fee", "qty": "1", "subtotal": "100"}])

    def test_mixed_history_is_asked_once_per_book(self):
        result = resolve(self.fields(), "sales", config=self.config("1", "2"), guided=True, book="T9")
        item = next(i for i in result["needed"] if i["kind"] == "service_vat")
        self.assertEqual(item["subject"], choices.sales_service_vat_subject("T9"))
        self.assertEqual([o["code"] for o in item["options"]], ["accrual", "cash_basis"])

    def test_one_way_history_decides(self):
        self.assertNotIn("service_vat", kinds(resolve(self.fields(), "sales", config=self.config("2", "2"), guided=True)))

    def test_remembered_is_applied(self):
        result = resolve(self.fields(), "sales", config=self.config("1", "2"), guided=True, book="T9",
                         remembered={"service_vat": {choices.sales_service_vat_subject("T9"): "cash_basis"}})
        self.assertEqual(result["applied"]["service_vat"], "cash_basis")

    def test_no_history_reported_is_not_asked(self):
        self.assertNotIn("service_vat", kinds(resolve(self.fields(), "sales", config=self.config(), guided=True)))

    def test_cash_sale_is_not_asked(self):
        self.assertNotIn("service_vat", kinds(resolve(sales(vat="7", payment_status="paid"), "sales",
                                                      config=self.config("1", "2"), guided=True)))

    def test_not_guided_is_not_asked(self):
        self.assertNotIn("service_vat", kinds(resolve(self.fields(), "sales", config=self.config("1", "2"))))


class SalesServiceVatPayload(unittest.TestCase):
    """销项口径的载荷（与 CC 桌面版窗口 11-12 对定）：沿用 ``srv_vattyp``、不进 GUIDED_KEYS；销项单带 ``2`` 才盖 21，``1`` 与旧写法相同不盖。"""

    def payload(self, **extra):
        return {"direction": "sales", "doctype": "IV", "payload_version": 1, "items": [], **extra}

    def test_answer_goes_on_the_sales_payload(self):
        made = choices.apply_to_payload(self.payload(), {"service_vat": "cash_basis"})
        self.assertEqual(made["srv_vattyp"], "2")

    def test_two_is_version_21_and_one_is_not(self):
        from pearnly_core.express_rules.push import common
        self.assertEqual(common.finalize_payload(self.payload(srv_vattyp="2"))["payload_version"], 21)
        self.assertLess(common.finalize_payload(self.payload(srv_vattyp="1"))["payload_version"], 21)
        self.assertNotIn("srv_vattyp", common.GUIDED_KEYS["header"])

    def test_purchase_unchanged(self):
        made = choices.apply_to_payload({"direction": "purchase", "payload_version": 1}, {"service_vat": "cash_basis"})
        self.assertEqual((made["srv_vattyp"], made["payload_version"]), ("2", 13))


class NotGuidedUnchanged(unittest.TestCase):
    def test_without_guided_nothing_new_is_asked(self):
        for fields, direction in ((purchase(payment_status="partial"), "purchase"), (sales(), "sales")):
            self.assertEqual(resolve(fields, direction), resolve(fields, direction, guided=False))


if __name__ == "__main__":
    unittest.main()
