"""需复核原因（`history/review.py`）：纯规则，用 2026-09-28 Express 压测里出现过的几类错误做例子。"""
import unittest

from pearnly_core.express_rules.contracts.line_items import review_status
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


class SettledAtIntake(unittest.TestCase):
    """有确定答案的入库时直接改、不问人（用户 2026-10-02）；不确定的不改。"""

    def test_a_tax_id_that_lost_its_leading_zero_is_restored_when_the_check_digit_agrees(self):
        pages = [page(direction="sales", seller_tax=OWN, buyer_tax="105536080112"), page(buyer_tax=OWN[1:])]
        self.assertEqual(review.repair_tax_ids(pages), ["buyer_tax", "buyer_tax"])
        self.assertEqual([p["fields"]["buyer_tax"] for p in pages], [SUPPLIER, OWN])
        self.assertEqual(codes(pages[:1]), [])
        wrong = [page(seller_tax="105536080113"), page(seller_tax="12345")]  # 补了也不对 / 长度不对：不动
        self.assertEqual(review.repair_tax_ids(wrong), [])
        self.assertEqual([p["fields"]["seller_tax"] for p in wrong], ["105536080113", "12345"])

    def two_readings(self, number="IV69/06-017", other="IU69/06-017"):
        pages = [page(invoice_number=number)]
        pages[0]["_validation_warnings"] = [f"invoice_number_alternative: {number}|{other}"]
        return pages

    def test_two_readings_follow_this_partys_earlier_numbers(self):
        pages = self.two_readings(number="IU69/06-017", other="IV69/06-017")
        self.assertEqual(review.settle_number(pages, party_numbers=["IV69/06-015", "IV69/06-016"]), "IV69/06-017")
        self.assertEqual(pages[0]["fields"]["invoice_number"], "IV69/06-017")
        self.assertEqual(codes(pages, party_numbers=["IV69/06-015", "IV69/06-016"]), [])
        for history in (["IU69/06-015", "IU69/06-016"], [], ["IV69/06-015"]):  # 历史认模型读法 / 没有历史 / 只有一张
            with self.subTest(history=history):
                pages = self.two_readings(number="IU69/06-017", other="IV69/06-017")
                self.assertIsNone(review.settle_number(pages, party_numbers=history))
                self.assertEqual(pages[0]["fields"]["invoice_number"], "IU69/06-017")  # 保留识别的主值

    def test_a_voucher_stamp_number_takes_the_other_candidate_only_when_there_is_one(self):
        pages = self.two_readings(number="RR690609-001", other="IV69/06-017")
        pages[0]["fields"]["date"] = "2026-06-09"
        self.assertEqual(review.settle_number(pages), "IV69/06-017")
        alone = [page(invoice_number="RR690609-001", date="2026-06-09")]
        self.assertIsNone(review.settle_number(alone))
        self.assertEqual(alone[0]["fields"]["invoice_number"], "RR690609-001")
        sales = self.two_readings(number="RR690609-001", other="IV69/06-017")  # 销项是本方自己的号：不换
        sales[0]["fields"].update(date="2026-06-09", direction="sales", seller_tax=OWN, buyer_tax=SUPPLIER)
        self.assertIsNone(review.settle_number(sales))

    def test_intake_settles_a_copy_using_this_sellers_history(self):
        # `history.storage.settle` 不依赖站点：只编译这两个函数，数据库换成记下查询参数的替身
        import ast
        import json
        from pathlib import Path
        from types import SimpleNamespace
        from pearnly_core.express_rules.contracts.history import primary_page_fields
        source = Path(review.__file__).with_name("storage.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name in {"party_numbers", "settle", "encode"}]
        queries = []
        db = SimpleNamespace(sql_list=lambda sql, values: queries.append(values) or ["IV69/06-015", "IV69/06-016"])
        env = {"frappe": SimpleNamespace(db=db), "review": review, "primary_page_fields": primary_page_fields,
               "json": json, "DOCTYPE": "Cowork OCR History"}
        exec(compile(ast.Module(body=wanted, type_ignores=[]), "storage.py", "exec"), env)
        pages = self.two_readings(number="IU69/06-017", other="IV69/06-017")
        pages[0]["fields"]["seller_tax"] = SUPPLIER[1:]
        settled = env["settle"]("WS-1", "CWH-1", pages)
        self.assertEqual((settled[0]["fields"]["invoice_number"], settled[0]["fields"]["seller_tax"]),
                         ("IV69/06-017", SUPPLIER))
        self.assertEqual(pages[0]["fields"]["invoice_number"], "IU69/06-017")  # 调用方的页不被改
        self.assertEqual(queries, [("WS-1", "CWH-1", SUPPLIER, SUPPLIER)])  # 补好的税号查这家的历史

    def test_nothing_changes_on_a_clean_or_non_invoice_page(self):
        clean = [page()]
        self.assertIsNone(review.settle_number(clean, party_numbers=["IV69/06-015", "IV69/06-016"]))
        self.assertEqual(clean, [page()])
        self.assertIsNone(review.settle_number([page(is_not_invoice=True)]))


if __name__ == "__main__":
    unittest.main()
