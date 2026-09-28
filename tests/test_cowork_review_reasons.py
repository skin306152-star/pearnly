"""需复核原因（`history/review.py`）：纯规则，用 2026-09-28 Express 压测里出现过的几类错误做例子。"""
import unittest

from pearnly_cowork.contracts.line_items import review_status
from pearnly_cowork.history import review

OWN = "0105546015062"          # 本方（压测冰厂）
SUPPLIER = "0105536080112"     # 校验位对的对方税号


def page(**fields):
    base = {"invoice_number": "000094", "date": "2026-06-06", "direction": "purchase", "document_type": "tax_invoice",
            "seller_tax": SUPPLIER, "buyer_tax": OWN, "subtotal": "121.50", "vat": "8.50", "total_amount": "130.00"}
    base.update(fields)
    return {"fields": {k: v for k, v in base.items() if v is not None}, "page_index": 1}


def codes(pages, **context):
    return [reason["code"] for reason in review.reasons(pages, **context)]


class ReviewReasons(unittest.TestCase):
    def test_a_clean_invoice_has_no_reason(self):
        self.assertEqual(codes([page()], own_tax=OWN), [])

    def test_tax_ids_are_checked_by_length_and_check_digit(self):
        found = review.reasons([page(seller_tax="0105536080113")])
        self.assertEqual(found, [{"code": "tax_checksum", "field": "seller_tax"}])
        # 销项买方掉了开头的 0（压测 8 处）：补 0 后校验位对 → 给建议，不自动改
        short = review.reasons([page(direction="sales", buyer_tax="105536080112", seller_tax=OWN)])
        self.assertEqual(short, [{"code": "tax_length", "field": "buyer_tax", "suggest": SUPPLIER}])

    def test_number_history_is_looked_up_by_the_repaired_tax_id(self):
        # 掉了开头 0 的税号按复核建议补上再取这家的票号历史；补了校验位也不对、或长度不对就不取
        self.assertEqual(review.history_tax(OWN[1:]), OWN)
        self.assertEqual(review.history_tax(OWN), OWN)
        self.assertEqual((review.history_tax("105546015063"), review.history_tax("12345")), ("", ""))

    def test_the_own_tax_id_must_be_the_chosen_company(self):
        self.assertEqual(codes([page()], own_tax="0105553045044"), ["own_tax"])
        self.assertEqual(codes([page()], own_tax=None), [])

    def test_amounts_and_line_items_must_add_up(self):
        self.assertEqual(codes([page(total_amount="140.00")]), ["amount_math"])
        # 服务类小票 8 行合计 276、票面 313（压测）：明细对不上
        items = [{"name": "a", "qty": "1", "price": "276", "subtotal": "276"}]
        self.assertEqual(codes([page(subtotal="292.52", vat="20.48", total_amount="313.00", items=items)]),
                         ["items_mismatch"])
        vat_inclusive = [{"name": "a", "qty": "1", "price": "313", "subtotal": "313"}]
        self.assertIsNone(review_status({"items": vat_inclusive, "subtotal": "292.52", "vat": "20.48",
                                         "total_amount": "313.00"}))

    def test_an_express_voucher_stamp_is_not_taken_for_the_invoice_number(self):
        self.assertEqual(codes([page(invoice_number="RR690609-001", date="2026-06-09")]), ["voucher_number"])
        self.assertEqual(codes([page(invoice_number="RR690609-001", date="2026-01-09")]), [])  # 年月对不上不算
        self.assertEqual(codes([page(invoice_number="IV690609-001", date="2026-06-09", direction="sales",
                                     seller_tax=OWN, buyer_tax=SUPPLIER)]), [])  # 销项是本方自己的号

    def test_a_number_written_unlike_this_party_before_is_flagged(self):
        history = ["IV69/06-015", "IV69/06-016", "IV70/01-001"]
        self.assertEqual(codes([page(invoice_number="IU69/06-017")], party_numbers=history), ["number_pattern"])
        self.assertEqual(codes([page(invoice_number="IV70/02-003")], party_numbers=history), [])  # 跨年写法不变
        self.assertEqual(codes([page(invoice_number="IU69/06-017")], party_numbers=history[:1]), [])  # 只有一张不下结论

    def test_the_two_readings_of_a_number_wait_for_a_choice(self):
        pages = [page(invoice_number="IV69/06-017")]
        pages[0]["_validation_warnings"] = ["invoice_number_alternative: IV69/06-017|IU69/06-017"]
        pages[0]["_needs_manual_review"] = True
        self.assertEqual(review.reasons(pages), [{"code": "number_choice", "field": "invoice_number",
                                                  "choices": ["IV69/06-017", "IU69/06-017"]}])
        # 这家以前人核过的票都是 IV99/99-999：模型读法对得上、原文读法对不上 → 不用问
        self.assertEqual(codes(pages, party_numbers=["IV69/06-015", "IV69/06-016"]), [])
        self.assertEqual(codes(pages, party_numbers=["IV69/06-015"]), ["number_choice"])  # 只有一张不下结论
        pages[0]["fields"]["invoice_number"] = "IU69/06-017"      # 选了原文那种
        self.assertEqual(codes(pages, edited=True), [])
        pages[0]["fields"].update(invoice_number="IV69/06-017", review_resolved=["number_choice"])  # 选了模型那种
        self.assertEqual(codes(pages), [])

    def test_a_number_missing_from_the_transcript_is_flagged_until_edited(self):
        pages = [page(invoice_number="38051304940")]
        pages[0]["_validation_warnings"] = ["invoice_number_unseen: 38051304940"]
        self.assertEqual(codes(pages), ["number_unseen"])
        pages[0]["fields"]["invoice_number"] = "138051304940"   # 人改了
        self.assertEqual(codes(pages), [])
        # 同图多票共用一页的说明：只标单号对得上的那一张
        pages[0]["_validation_warnings"] = ["invoice_number_unseen: 000122", "invoice_number_unseen: 000096"]
        pages[0]["fields"]["invoice_number"] = "000096"
        self.assertEqual(codes(pages), ["number_unseen"])
        pages[0]["fields"]["invoice_number"] = "000120"
        self.assertEqual(codes(pages), [])

    def test_uncertain_only_when_nothing_more_specific_and_nobody_edited(self):
        pages = [page()]
        pages[0]["_needs_manual_review"] = True
        self.assertEqual(codes(pages), ["uncertain"])
        self.assertEqual(codes(pages, edited=True), [])
        self.assertEqual(codes(pages, duplicate=True), ["duplicate"])

    def test_a_non_invoice_has_no_reason(self):
        self.assertEqual(codes([page(is_not_invoice=True, seller_tax="1")]), [])


if __name__ == "__main__":
    unittest.main()
