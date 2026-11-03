"""写账前的通用护栏（2026-11-03，``tax_documents.ledger_guard``）：缺总额、金额不闭合、完整税票只读到总额、缺单号 / 日期的单
不写账、进待处理；Express 直写与 MR.ERP 直写都接。每条路都有反例：把护栏拿掉（旧写法），这里的拦截测试就会失败。
纯计算，不连站点、不写账。"""
import sys
import unittest
from copy import deepcopy
from datetime import date
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))
from pearnly_core.tax_documents import ledger_guard  # noqa: E402

# BJC 税票（11-03 真实读法）：第 1 页印「1/2」、第 2 页不在文件里——金额全空；第 7 页同样缺第 2 页——小计、税额有，总额没有。
BJC_PAGE1 = {"document_type": "tax_invoice", "invoice_number": "211067100410", "date": "2026-10-14"}
BJC_PAGE7 = {"document_type": "tax_invoice", "invoice_number": "221067100014", "date": "2026-10-06",
             "subtotal": "38869.00", "vat": "2720.83"}


class Guard(unittest.TestCase):
    def check(self, **fields):
        return ledger_guard.check(document_type=fields.get("document_type"), number=fields.get("invoice_number"),
                                  date=fields.get("date"), subtotal=fields.get("subtotal"), discount=fields.get("discount"),
                                  vat=fields.get("vat"), total=fields.get("total"))

    def test_closed_document_passes(self):
        self.assertEqual(self.check(document_type="tax_invoice", invoice_number="A1", date="2026-10-01", subtotal="1000.00",
                                    vat="70.00", total="1070.00"), [])

    def test_missing_page_documents_are_held(self):
        self.assertEqual(self.check(**BJC_PAGE1), [ledger_guard.TOTAL_MISSING])
        self.assertEqual(self.check(**BJC_PAGE7), [ledger_guard.TOTAL_MISSING])

    def test_amounts_that_do_not_close(self):
        self.assertEqual(self.check(document_type="tax_invoice", invoice_number="A1", date="2026-10-01", subtotal="1000.00",
                                    vat="70.00", total="1170.00"), [ledger_guard.AMOUNTS_NOT_CLOSED])

    def test_legitimate_ways_a_document_closes(self):
        base = {"document_type": "tax_invoice", "invoice_number": "A1", "date": "2026-10-01"}
        for amounts in ({"subtotal": "1100.00", "discount": "100.00", "vat": "70.00", "total": "1070.00"},  # 整单折扣
                        {"subtotal": "1070.00", "vat": "70.00", "total": "1070.00"},  # 小计印的是含税额
                        {"subtotal": "1000.00", "vat": "70.00", "total": "1040.00"}):  # 合计是扣 3% 预扣税后的实付额
            with self.subTest(amounts=amounts):
                self.assertEqual(self.check(**base, **amounts), [])

    def test_total_only(self):
        """只印总额：简式税票、含税收据正常（照账套规则反推）；完整税票只读到总额要人看（用户 11-03 定）。"""
        for kind in ("simplified_tax_invoice", "receipt", None):
            with self.subTest(kind=kind):
                self.assertEqual(self.check(document_type=kind, date="2026-10-01", total="107.00"), [])
        self.assertEqual(self.check(document_type="tax_invoice", invoice_number="A1", date="2026-10-01", total="107.00"),
                         [ledger_guard.TAX_INVOICE_TOTAL_ONLY])

    def test_required_fields(self):
        self.assertEqual(self.check(document_type="tax_invoice", date="2026-10-01", subtotal="100", vat="7", total="107"),
                         [ledger_guard.NUMBER_MISSING])
        self.assertEqual(self.check(document_type="receipt", date="2026-10-01", total="107"), [])  # 收据没印单号不算缺
        self.assertEqual(self.check(document_type="receipt", total="107"), [ledger_guard.DATE_MISSING])
        self.assertEqual(self.check(document_type="receipt", date="2026-10-01", total="0"), [ledger_guard.TOTAL_MISSING])


class ExpressDirect(unittest.TestCase):
    """Cowork → Express 直写：护栏接在预检的「单据」一步。"""

    def setUp(self):
        from pearnly_core.express_rules.push.preflight import ExpressContext
        self.context = ExpressContext(True, date(2026, 10, 21), "0994000333444", "tenant", {})
        self.endpoint = {"id": "ep", "config": {"account_set": "DATAT", "fallback_acc": "A", "vat_input_acc": "V",
                                                 "ap_acc": "P", "revenue_acc": "S", "vat_output_acc": "O", "ar_acc": "R"}}

    def history(self, fields, total):
        fields = {"seller_name": "BJC", "seller_tax": "0107561000013", "buyer_tax": "0994000333444", "buyer_name": "Buyer",
                  **fields}
        return {"id": "h", "tenant_id": "tenant", "direction": "purchase", "invoice_no": fields.get("invoice_number"),
                "invoice_date": fields.get("date"), "total_amount": total, "confidence": "high", "pages": [{"fields": fields}]}

    def preflight(self, fields, total):
        from pearnly_core.express_rules.push.preflight import preflight_express
        return preflight_express(self.endpoint, self.history(fields, total), context=self.context)

    def test_missing_page_document_is_not_written(self):
        for fields in (BJC_PAGE1, BJC_PAGE7):
            with self.subTest(fields=fields):
                result = self.preflight(fields, None)
                self.assertFalse(result.ready)
                self.assertEqual(result.reason, "amounts_not_consistent")

    def test_full_tax_invoice_read_with_total_only_is_held(self):
        """旧写法：只有总额就按 7% 反推照写。"""
        result = self.preflight({"document_type": "tax_invoice", "invoice_number": "A1", "date": "2026-10-01"}, "107.00")
        self.assertFalse(result.ready)
        self.assertEqual((result.reason, [c.key for c in result.checks if c.status == "blocked"]),
                         ("amounts_not_consistent", ["document"]))

    def test_simplified_invoice_with_total_only_is_still_written(self):
        result = self.preflight({"document_type": "simplified_tax_invoice", "invoice_number": "R1", "date": "2026-10-01"},
                                "107.00")
        self.assertTrue(result.ready, result.reason)

    def test_lines_that_do_not_add_up_still_write_the_closed_header(self):
        """表头金额闭合、明细对不上：钱没错，照现有口径只写表头（用户 11-03 定）。"""
        fields = {"document_type": "tax_invoice", "invoice_number": "A1", "date": "2026-10-01", "subtotal": "1000.00",
                  "vat": "70.00", "items": [{"name": "x", "qty": "1", "price": "500", "subtotal": "500.00"}]}
        result = self.preflight(fields, "1070.00")
        self.assertTrue(result.ready, result.reason)
        self.assertEqual(result.payload.get("items_status"), "mismatch")


import importlib.util  # noqa: E402


@unittest.skipUnless(importlib.util.find_spec("requests"), "MR.ERP 传输模块要 requests：在生产镜像的候选离线检查里跑")
class MrErpDirect(unittest.TestCase):
    """Cowork / ERP → MR.ERP 直写：护栏接在 ``MrErpImport._preflight``，赊销、采购、费用三种模块都先过它。"""

    def preflight(self, history, *, sheet_kind="purchase", expense=False):
        if "frappe" not in sys.modules:  # 传输模块顶上导入的会话锁要 ``frappe._``；预检这一步用不到站点
            import types
            sys.modules["frappe"] = types.SimpleNamespace(_=lambda text, *a: text)
        from pearnly_core.mrerp.push.transport import MrErpImport
        importer = MrErpImport.__new__(MrErpImport)
        importer.module = SimpleNamespace(expense=expense, sheet_kind=sheet_kind)
        return importer._preflight([deepcopy(history)], {})

    def history(self, fields, total):
        return {"id": "h1", "invoice_no": fields.get("invoice_number"), "invoice_date": fields.get("date"),
                "total_amount": total, "direction": "purchase", "fields": dict(fields)}

    def test_missing_page_purchase_is_not_written_with_subtotal_plus_vat(self):
        """旧写法：缺总额时用「小计 + 税额」顶上（38869.00 + 2720.83 = 41589.83）照写进账。"""
        for expense in (False, True):
            with self.subTest(expense=expense):
                valid, failed = self.preflight(self.history(BJC_PAGE7, None), expense=expense)
                self.assertEqual(valid, [])
                self.assertEqual(failed[0].reasons, ["ERR_NO_TOTAL_AMOUNT"])

    def test_amounts_that_do_not_close_are_not_written(self):
        fields = {"invoice_number": "A1", "date": "2026-10-01", "subtotal": "1000.00", "vat": "70.00"}
        valid, failed = self.preflight(self.history(fields, "1170.00"))
        self.assertEqual((valid, failed[0].reasons), ([], ["ERR_AMOUNTS_NOT_CLOSED"]))
        from pearnly_core.mrerp.push.user_data import USER_DATA_ERROR_CODES
        self.assertIn("ERR_AMOUNTS_NOT_CLOSED", USER_DATA_ERROR_CODES)  # 重推一样，不自动重试

    def test_sales_credit_goes_through_the_same_guard(self):
        fields = {"invoice_number": "S1", "date": "2026-10-01", "subtotal": "1000.00", "vat": "70.00"}
        valid, failed = self.preflight(self.history(fields, None), sheet_kind="sales_credit")
        self.assertEqual((valid, failed[0].reasons), ([], ["ERR_NO_TOTAL_AMOUNT"]))


if __name__ == "__main__":
    unittest.main()
