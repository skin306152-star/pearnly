"""服务品进项税口径（`express_rules.push.service_vat`，10-29）：这本账套的历史定得了的不问，定不了才问一次、按「这本账套 ×
这家供应商」记住，记过的写进载荷 ``srv_vattyp``（载荷 13）。"""
import sys
import unittest
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "apps/pearnly_core")]

from pearnly_core.express_rules.push import choices as C  # noqa: E402
from pearnly_core.express_rules.push import service_vat as V  # noqa: E402

TAX = "0105536080112"
NAME = "BANGCHAK GREENNET CO.,LTD."


def supplier(srv, code="B004", tax=TAX):
    return {"code": code, "name": NAME, "tax_id": tax, "branch": "00000", "srv": srv}


def config(*rows):
    return {"reported_suppliers": list(rows), "reported_accounts": [{"code": "53-05-02-00", "name": "ค่าน้ำมัน"}]}


def purchase(**fields):
    return {"seller_name": NAME, "seller_tax": TAX, "seller_branch": "00000", "document_type": "tax_invoice",
            "vat": "20.48", "total_amount": "313.00", "payment_status": "unpaid",
            "items": [{"name": "ค่าบริการ", "qty": "1", "price": "292.52", "subtotal": "292.52",
                       "posting_kind": "service"}], **fields}


def needed(result):
    return [item for item in result["needed"] if item["kind"] == V.KIND]


def resolve(fields, conf, remembered=None, **kwargs):
    return C.resolve(fields, direction="purchase", posting_kind=None, config=conf, remembered=remembered or {},
                     account_resolved=True, **{"ask_service_vat": True, **kwargs})


class Rule(unittest.TestCase):
    def test_rule(self):
        self.assertEqual(V.basis(has_vat=False, cash=False, service=True, supplier="2"), V.NO_VAT)
        self.assertEqual(V.basis(has_vat=True, cash=True, service=True, supplier="2"), V.ACCRUAL)
        self.assertEqual(V.basis(has_vat=True, cash=False, service=False, supplier="2"), V.ACCRUAL)
        self.assertEqual(V.basis(has_vat=True, cash=False, service=True, supplier="2", book="12"), V.CASH_BASIS)
        # 这家两种都有：不退到整本看，问
        self.assertIsNone(V.basis(has_vat=True, cash=False, service=True, supplier="12", book="1"))
        self.assertEqual(V.basis(has_vat=True, cash=False, service=True, supplier="12", chosen="cash_basis"), "2")
        # 这家没有历史（"0"）：看整本
        self.assertEqual(V.basis(has_vat=True, cash=False, service=True, supplier="0", book="2"), "2")
        self.assertIsNone(V.basis(has_vat=True, cash=False, service=True, supplier="", book="12"))
        self.assertEqual(V.book_verdict(["1", "0", "2", ""]), "12")
        self.assertIn(V.KIND, C.KINDS)  # 账套记忆按 KINDS 存取、列出（KINDS 写成字面量，Cowork 文件夹任务照抄它）


class AskOnlyWhenHistoryCannotDecide(unittest.TestCase):
    def test_a_consistent_supplier_is_not_asked(self):
        for srv in ("1", "2"):
            self.assertEqual(needed(resolve(purchase(), config(supplier(srv), supplier("12", "X", "0")))), [], srv)

    def test_a_mixed_supplier_is_asked_once_then_remembered_into_the_payload(self):
        result = resolve(purchase(), config(supplier("12")))
        [item] = needed(result)
        self.assertEqual([option["code"] for option in item["options"]], ["accrual", "cash_basis"])
        again = resolve(purchase(), config(supplier("12")), remembered={V.KIND: {item["subject"]: "cash_basis"}})
        self.assertEqual(needed(again), [])
        payload = {"direction": "purchase", "payload_version": 5}
        C.apply_to_payload(payload, again["applied"])
        self.assertEqual((payload[V.PAYLOAD_KEY], payload["payload_version"]), ("2", V.PAYLOAD_VERSION))

    def test_a_new_supplier_follows_the_book(self):
        other = supplier("2", "X", "0105500000000")
        self.assertEqual(needed(resolve(purchase(), config(other))), [])
        self.assertEqual(len(needed(resolve(purchase(), config(other, supplier("1", "Y", "0105500000001"))))), 1)

    def test_cash_stock_only_no_vat_and_old_catalogs_are_not_asked(self):
        mixed = config(supplier("12"))
        self.assertEqual(needed(resolve(purchase(payment_status="paid"), mixed)), [])
        stock = purchase(items=[{"name": "น้ำมัน", "qty": "1", "price": "1", "subtotal": "292.52", "posting_kind": "stock"}])
        self.assertEqual(needed(resolve(stock, mixed)), [])
        self.assertEqual(needed(resolve(purchase(vat="0"), mixed)), [])
        old = config({k: v for k, v in supplier("12").items() if k != "srv"})
        self.assertEqual(needed(resolve(purchase(), old)), [])

    def test_a_company_wide_memory_keys_the_book(self):
        """ERP 按公司记：归类键带账套键，同一家供应商在另一本账套要另问。"""
        [item] = needed(resolve(purchase(), config(supplier("12")), book="BOOK-A"))
        self.assertTrue(item["subject"].startswith("BOOK-A|"))
        other = resolve(purchase(), config(supplier("12")), remembered={V.KIND: {item["subject"]: "accrual"}},
                        book="BOOK-B")
        self.assertEqual(len(needed(other)), 1)

    def test_without_asking_a_remembered_choice_is_still_applied(self):
        """Cowork 的选择卡还没有这一项：不问（定不了由 CC 拒写转人工），记过的照用。"""
        [item] = needed(resolve(purchase(), config(supplier("12"))))
        self.assertEqual(needed(resolve(purchase(), config(supplier("12")), ask_service_vat=False)), [])
        quiet = resolve(purchase(), config(supplier("12")), remembered={V.KIND: {item["subject"]: "accrual"}},
                        ask_service_vat=False)
        self.assertEqual(quiet["applied"][V.KIND], "accrual")

    def test_sales_payloads_never_carry_it(self):
        payload = {"direction": "sales", "payload_version": 1}
        C.apply_to_payload(payload, {V.KIND: "cash_basis"})
        self.assertNotIn(V.PAYLOAD_KEY, payload)


if __name__ == "__main__":
    unittest.main()
