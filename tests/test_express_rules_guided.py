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

    def test_status_is_worked_out_when_not_given(self):
        """调用方（Cowork 推送评估、ERP 准备）不给对账状态时规则自己按映射器的对账闸算：行合计对不上税前额就问。"""
        bad = purchase(subtotal="100.00", items=[{"name": "A", "qty": "1", "subtotal": "60"}])
        good = purchase(subtotal="100.00", items=[{"name": "A", "qty": "1", "subtotal": "100"}])
        self.assertIn("detail", kinds(resolve(bad, guided=True)))
        self.assertNotIn("detail", kinds(resolve(good, guided=True)))
        self.assertNotIn("detail", kinds(resolve(bad)))

    def test_consistent_detail_is_not_asked(self):
        self.assertNotIn("detail", kinds(resolve(purchase(), items_status="ok", guided=True)))


class A7AmountOnlyInThePayload(unittest.TestCase):
    """A7 答了「只记金额」（``config["choices"]["detail"]``，resolve 读进 applied）：载荷按 Express 现有的表头写法——不带明细行、
    不入库，明细状态改成 empty（写账准备不再停在 items_mismatch / items_incomplete），读到的原状态留作追溯；答「不入账」与没答的照旧。"""
    CONFIG = {"account_set": "DATAT", "revenue_acc": "41-01-01-00", "vat_output_acc": "21-05-01-00",
                               "ar_acc": "11-03-01-00"}
    HISTORY = {"id": "s1", "invoice_date": "2015-12-31", "invoice_no": "IV581231-002", "total_amount": "107.00",
               "fields": {"buyer_name": "Synthetic Customer", "subtotal": "100.00", "vat": "7.00", "invoice_number": "IV581231-002",
                          "items": [{"name": "Paper", "qty": "1", "price": "40", "subtotal": "40"}]}}  # 行合计 40 ≠ 税前 100

    def build(self, detail=None):
        from datetime import date
        from pearnly_core.express_rules.push.sales_mapper import build_express_sales_payload
        config = dict(self.CONFIG, **({"choices": {"detail": detail}} if detail else {}))
        result = build_express_sales_payload(self.HISTORY, config=config, today=date(2026, 9, 21))
        self.assertTrue(result.ok, result.reason)
        return result.payload

    def test_amount_only_writes_the_header_way(self):
        self.assertEqual(self.build()["items_status"], "mismatch")
        payload = self.build("amount_only")
        self.assertEqual((payload["items_status"], payload["items"], payload["items_status_read"], payload["detail_choice"]),
                         ("empty", [], "mismatch", "amount_only"))
        self.assertEqual(payload["base_amount"], self.build()["base_amount"])  # 表头金额不变

    def test_skip_and_unanswered_keep_the_stop(self):
        self.assertEqual(self.build("skip")["items_status"], "mismatch")
        self.assertNotIn("detail_choice", self.build())

    def test_a_consistent_detail_is_not_touched_by_the_answer(self):
        payload = {"items_status": "ok", "items": [{"name": "A"}]}
        choices.apply_to_payload(payload, {"detail": "amount_only"})
        self.assertEqual((payload["items_status"], payload["items"]), ("ok", [{"name": "A"}]))


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


class RememberedEveryBasis(unittest.TestCase):
    """主会话 11-17 定 C：口径只管问不问，记下的所有口径都用——对话里答过的，网页 / LINE / 后台再推（push.poll、再写一次、
    待处理重推、代录重交）也照用；没记过的照旧（网页口径不问、落默认）。改前这三处网页口径一律不读记忆。"""
    CONFIG = {"revenue_acc": "41-01", "reported_accounts": [{"code": "41-01"}, {"code": "41-02"}]}
    CUSTOMER = choices.supplier_subject("sales", "0105550000002", "Synthetic Customer")

    def test_a_remembered_revenue_account_is_used_outside_the_conversation(self):
        result = resolve(sales(), "sales", config=self.CONFIG, remembered={"account": {choices.REVENUE_SUBJECT: "41-02"}})
        self.assertEqual((result["applied"].get("account"), result["applied"].get("account_from")), ("41-02", "remembered"))
        self.assertNotIn("account", kinds(result))

    def test_a_remembered_revenue_account_no_longer_in_the_chart_falls_back_to_the_default(self):
        result = resolve(sales(), "sales", config=self.CONFIG, remembered={"account": {choices.REVENUE_SUBJECT: "41-99"}})
        self.assertNotIn("account", result["applied"])  # 照旧交 mapper 落连接默认 41-01
        self.assertNotIn("account", kinds(result))       # 网页口径不问

    def test_a_remembered_cash_or_credit_sale_is_used_outside_the_conversation(self):
        result = resolve(sales(), "sales", remembered={"payment": {self.CUSTOMER: "cash"}})
        self.assertEqual(result["applied"].get("payment"), "cash")
        self.assertNotIn("payment", kinds(resolve(sales(), "sales")))  # 没记过：网页口径照旧不问

    def test_a_remembered_sales_service_vat_is_used_outside_the_conversation(self):
        fields = sales(vat="7.00", items=[{"name": "Service", "amount": "100"}])
        subject = choices.sales_service_vat_subject("BK1")
        config = {"reported_customers": [{"code": "C1", "srv": "1"}, {"code": "C2", "srv": "2"}]}
        guided = resolve(fields, "sales", config=config, guided=True, book="BK1")
        self.assertIn(choices.service_vat.KIND, kinds(guided))
        remembered = {choices.service_vat.KIND: {subject: "accrual"}}
        web = resolve(fields, "sales", config=config, book="BK1", remembered=remembered)
        self.assertEqual(web["applied"].get(choices.service_vat.KIND), "accrual")
        self.assertNotIn(choices.service_vat.KIND, kinds(resolve(fields, "sales", config=config, book="BK1")))


class A7DetailAnswerOnEveryBasis(unittest.TestCase):
    """A7 明细对不上：答过的存在记忆里（键带票号），所有口径都用，不再出题；「只记金额」交 apply_to_payload 写成表头。
    改前：引导口径只出题、从不读已答的值（答完写账又停在「要人选」），网页口径不读。"""
    FIELDS = sales(invoice_number="IV-9", subtotal="100.00", total_amount="107.00",
                   items=[{"name": "A", "amount": "60"}])  # 明细合计 60 对不上税前 100 → mismatch

    def subject(self):
        found = resolve(self.FIELDS, "sales", guided=True)
        return next(item["subject"] for item in found["needed"] if item["kind"] == "detail")

    def test_an_amount_only_answer_is_used_on_every_basis_and_not_asked_again(self):
        remembered = {"detail": {self.subject(): choices.DETAIL_AMOUNT_ONLY}}
        for guided in (True, False):
            result = resolve(self.FIELDS, "sales", guided=guided, remembered=remembered)
            self.assertNotIn("detail", kinds(result), guided)
            self.assertEqual(result["applied"].get("detail"), choices.DETAIL_AMOUNT_ONLY, guided)

    def test_a_skip_answer_is_kept_and_the_payload_is_left_to_stop(self):
        remembered = {"detail": {self.subject(): choices.DETAIL_SKIP}}
        result = resolve(self.FIELDS, "sales", remembered=remembered)
        self.assertEqual(result["applied"].get("detail"), choices.DETAIL_SKIP)
        payload = {"items": [{"name": "A"}], "items_status": "mismatch"}
        choices.apply_to_payload(payload, result["applied"])
        self.assertEqual(payload["items_status"], "mismatch")  # 不动：准备那一步照旧按 items_mismatch 停下

    def test_the_card_remembers_a_detail_answer_but_it_stays_a_per_document_kind(self):
        from pearnly_core.write_precheck import contracts as card

        item = card.normalize_item({"kind": "detail", "subject": self.subject(), "options": [{"code": "skip"}]})
        self.assertTrue(item["remember"])
        self.assertIn("detail", card.PER_DOCUMENT_KINDS)


class EveryKindAnswerIsUsed(unittest.TestCase):
    """守门（主会话 11-17：「题有出、答案没人用」）：共用规则 ``resolve`` 会问的每一类，造一张会问它的单，答一次（记下第一个选项）
    再判——引导口径、网页口径都不再问。新加一类没在这里造场景，测试失败（不允许只断言出题）。"""
    ACCOUNTS = [{"code": "41-01"}, {"code": "52-01"}, {"code": "11-01"}]
    SCENARIOS = {
        "party": (purchase(), "purchase", {"config": {"reported_suppliers": [
            {"code": "S1", "name": NAME, "tax_id": TAX, "branch": "00000"},
            {"code": "S2", "name": NAME, "tax_id": TAX, "branch": "00000"}]}}),
        "account": (purchase(), "purchase", {"config": {"reported_accounts": ACCOUNTS}}),
        "payment": (sales(), "sales", {}),
        "vat": (purchase(document_type="receipt", vat="7.00", payment_status="paid"), "purchase", {}),
        "receipt": (purchase(), "purchase", {"config": {"reported_accounts": ACCOUNTS}, "receipts": [("promptpay", "PromptPay")]}),
        "service_vat": (sales(vat="7.00", items=[{"name": "Service", "amount": "100"}]), "sales",
                        {"config": {"reported_customers": [{"code": "C1", "srv": "1"}, {"code": "C2", "srv": "2"}]}, "book": "BK1"}),
        "detail": (sales(invoice_number="IV-9", subtotal="100.00", total_amount="107.00", items=[{"name": "A", "amount": "60"}]),
                   "sales", {}),
    }
    # 不经 resolve 出题的类（只读记忆、从不问；或由别的通道问）
    NOT_ASKED = {"department": "整份账套一个，只读不问（用户 10-08）",
                 "item": "存货对照要账套目录，另有 test_express_rules_line_choice 从作答到载荷",
                 "location": "多仓定仓另有 test_express_rules_line_choice / 仓位用例"}

    def answer(self, item):
        options = [o.get("code") for o in item.get("options") or () if o.get("code")]
        return options[0] if options else (item.get("current") or "41-01")

    def test_every_kind_has_a_scenario(self):
        self.assertEqual(set(choices.KINDS) | {choices.DETAIL}, set(self.SCENARIOS) | set(self.NOT_ASKED))

    def test_an_answered_question_is_not_asked_again_on_any_basis(self):
        for kind, (fields, direction, extra) in self.SCENARIOS.items():
            with self.subTest(kind=kind):
                first = resolve(fields, direction, guided=True, **dict(extra))
                asked = [item for item in first["needed"] if item["kind"] == kind]
                self.assertTrue(asked, f"{kind}: the scenario does not ask it")
                remembered = {kind: {item["subject"]: self.answer(item) for item in asked}}
                for guided in (True, False):
                    again = resolve(fields, direction, guided=guided, remembered=remembered, **dict(extra))
                    self.assertNotIn(kind, kinds(again), (kind, guided))


class GuidedOnlyGatesAsking(unittest.TestCase):
    """守门（主会话 11-17 定 C）：``resolve`` 里条件带 ``guided`` 的分支只许决定出不出题，不许在里面读记忆（``chosen(``）——
    读记忆在引导分支里 = 别的口径用不上对话里答过的，后台再推落默认值（记错科目）。"""

    def test_no_memory_read_sits_under_a_guided_condition(self):
        import ast

        source = (ROOT / "apps/pearnly_core/pearnly_core/express_rules/push/choices.py").read_text(encoding="utf-8")
        resolve_fn = next(node for node in ast.parse(source).body
                          if isinstance(node, ast.FunctionDef) and node.name == "resolve")
        found = []
        for node in ast.walk(resolve_fn):
            if isinstance(node, ast.If) and "guided" in ast.unparse(node.test):
                for inner in (n for stmt in node.body for n in ast.walk(stmt)):
                    if isinstance(inner, ast.Call) and ast.unparse(inner.func) == "chosen":
                        found.append(f"line {inner.lineno}: {ast.unparse(node.test)}")
        self.assertEqual(found, [])


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


class ProductsPassGuided(unittest.TestCase):
    """两产品把「在 CC 对话里」交给共用规则：Cowork 推送评估 → ``push/express/decide.decide(guided=)``，ERP ``express/prepare``。"""

    def test_cowork_decide_passes_it_on(self):
        sys.path.insert(0, str(ROOT / "apps/pearnly_cowork"))
        from pearnly_cowork.push.express import decide
        seen = {}
        original = choices.resolve

        def spy(*args, **kw):
            seen.update(kw)
            return original(*args, **kw)

        choices.resolve = spy
        try:
            decide.decide(purchase(payment_status="partial"), direction="purchase", posting_kind=None, config={},
                          remembered={}, guided=True)
        finally:
            choices.resolve = original
        self.assertTrue(seen.get("guided"))

    def test_both_products_read_the_shared_switch(self):
        cowork = (ROOT / "apps/pearnly_cowork/pearnly_cowork/push/application/service.py").read_text(encoding="utf-8")
        erp = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/express/prepare.py").read_text(encoding="utf-8")
        for source in (cowork, erp):
            self.assertIn("guided=assistant_guided.active()", source)


class NotGuidedUnchanged(unittest.TestCase):
    def test_without_guided_nothing_new_is_asked(self):
        for fields, direction in ((purchase(payment_status="partial"), "purchase"), (sales(), "sales")):
            self.assertEqual(resolve(fields, direction), resolve(fields, direction, guided=False))


if __name__ == "__main__":
    unittest.main()
