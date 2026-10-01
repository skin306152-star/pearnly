"""确定性错误触发定向复核（2026-10-02）：税号校验位不过、佛历年份差约 10 年只作内部信号。

离线、无凭据、无付费调用：模型与 Vision 都是替身。场景取自测试机 27 页扫描销项第 3 页：买方税号末位读错、
3/6/2569 读成 2559，复核链走完两处仍错。证明：这类页会带着原因复核一次，复核读对就取对的值；
正确的页不多一次调用；只为年份复核的页复核没读好也不升级到 Vision 原文再读。
"""
import json
import unittest

from pearnly_cowork.ocr.engine import ModelResponse, VisionResponse
from pearnly_cowork.ocr.engine.errors import ContentError
from pearnly_cowork.ocr.schema import Page, validate_invoice
from pearnly_cowork.ocr.validation.accounting import REREAD_PREFIX, adopt_from_reads
from pearnly_cowork.ocr.validation.dates import YEAR_SHIFT_PREFIX, year_shift
from test_cowork_engine import Model, Source, run


def tax_id(base):
    return base + str((11 - sum(int(base[i]) * (13 - i) for i in range(12)) % 11) % 10)


SELLER, BUYER = tax_id("010555516762"), tax_id("010753700105")
BAD_BUYER = BUYER[:12] + str((int(BUYER[12]) + 3) % 10)  # 末位读错 → 校验位不过
RIGHT = {"document_type": "tax_invoice", "invoice_number": "IV69/0300", "subtotal": "100.00", "vat": "7.00",
         "total_amount": "107.00", "date": "2026-06-03", "date_raw": "3/6/2569", "seller_name": "Seller",
         "seller_tax": SELLER, "buyer_name": "Buyer", "buyer_tax": BUYER}
PAGE3 = {**RIGHT, "date": "2016-06-03", "date_raw": "3/6/2559", "buyer_tax": BAD_BUYER}
TEXT = f"ใบกำกับภาษี IV69/0300 {SELLER} 3/6/2569 100.00 7.00 107.00"


def vision(frame, *, stage, page_number):
    return VisionResponse(Page(page_number=page_number, width=1, height=1, blocks=[], full_text=TEXT,
                               avg_confidence=0.97), {"text": TEXT}, 2, f"{stage}:{page_number}")


def read(model, source=None):
    return run(source=source, model=model, vision_call=vision)


class TargetedReread(unittest.TestCase):
    def test_page3_errors_trigger_one_recheck_that_corrects_both_fields(self):
        model = Model([PAGE3, RIGHT])
        page = read(model).pipeline.pages[0]
        self.assertEqual([r.stage for r in model.requests], ["direct", "recheck"])
        reason = model.requests[1].prompt
        self.assertIn("buyer_tax", reason)
        self.assertIn(YEAR_SHIFT_PREFIX, reason)  # 同一次复核同时说明两处
        self.assertEqual(page.layer_chain, ["ID", "RC", "L1"])
        self.assertEqual((page.invoice.buyer_tax, page.invoice.date, page.invoice.date_raw),
                         (BUYER, "2026-06-03", "3/6/2569"))
        self.assertNotEqual(page.confidence_band, "needs_review")

    def test_a_year_only_misread_is_rechecked_once_and_corrected(self):
        model = Model([{**PAGE3, "buyer_tax": BUYER}, RIGHT])
        page = read(model).pipeline.pages[0]
        self.assertEqual([r.stage for r in model.requests], ["direct", "recheck"])
        self.assertEqual(page.invoice.date, "2026-06-03")
        self.assertIn(f"{REREAD_PREFIX} date 2016-06-03→2026-06-03", page.validation_warnings)
        self.assertFalse(any(w.startswith("invoice date") for w in page.validation_warnings))
        self.assertEqual(page.confidence_band, read(Model([RIGHT])).pipeline.pages[0].confidence_band)

    def test_a_year_only_recheck_that_fails_keeps_the_read_without_escalating(self):
        year_only = {**PAGE3, "buyer_tax": BUYER}
        for second in (year_only, "not json", {**year_only, "total_amount": ""}):
            with self.subTest(second=str(second)[:20]):
                model = Model([year_only, second])
                page = read(model).pipeline.pages[0]
                self.assertEqual([r.stage for r in model.requests], ["direct", "recheck"])  # 不升级 Vision 原文再读
                self.assertNotIn("L2", page.layer_chain)
                self.assertEqual((page.invoice.date, page.invoice.total_amount), ("2016-06-03", "107.00"))

    def test_a_correct_read_from_an_earlier_attempt_is_kept_when_the_last_read_is_wrong(self):
        # 直读税号错；复核税号读对了但丢了合计 → 没过闸、走原图 + 原文再读；再读又把税号读错 → 取复核那次过校验的值
        model = Model([{**RIGHT, "buyer_tax": BAD_BUYER}, {**RIGHT, "total_amount": "170.00"},
                       {**RIGHT, "buyer_tax": BAD_BUYER}])
        page = read(model).pipeline.pages[0]
        self.assertEqual([r.stage for r in model.requests], ["direct", "recheck", "ocr_read"])
        self.assertEqual(page.invoice.buyer_tax, BUYER)
        self.assertIn(f"{REREAD_PREFIX} buyer_tax {BAD_BUYER}→{BUYER}", page.validation_warnings)
        self.assertFalse(any("校验位" in w for w in page.validation_warnings))

    def test_still_wrong_after_every_read_keeps_the_best_value(self):
        model = Model([PAGE3, PAGE3, PAGE3])
        page = read(model).pipeline.pages[0]
        self.assertEqual(page.layer_chain, ["ID", "RC", "L1", "L2"])
        self.assertEqual((page.invoice.buyer_tax, page.invoice.date), (BAD_BUYER, "2016-06-03"))

    def test_correct_pages_take_no_extra_call(self):
        pages = [RIGHT, {**RIGHT, "date": "2025-12-30", "date_raw": "30/12/2568"},
                 {**RIGHT, "document_type": "receipt", "date": "2021-01-05", "date_raw": "05/01/2564"}]
        for output in pages:
            with self.subTest(date=output["date"]):
                model = Model([output])
                result = read(model)
                self.assertEqual([r.stage for r in model.requests], ["direct"])
                self.assertEqual(result.pipeline.pages[0].invoice.date, output["date"])
        model = Model([RIGHT] * 5)
        result = read(model, Source(5))
        self.assertEqual([r.stage for r in model.requests], ["direct"] * 5)
        self.assertEqual(len(result.calls), 10)  # 5 次直读 + 5 次完整税票原文核对，没有复核


class Signals(unittest.TestCase):
    def invoice(self, **fields):
        return validate_invoice({**RIGHT, **fields}, anchor_year=2026)

    def test_year_shift_is_about_ten_years_from_the_recognition_year(self):
        flagged = {d: bool(year_shift(self.invoice(date=d, date_raw=""), anchor_year=2026))
                   for d in ("2016-06-03", "2017-01-02", "2015-12-31", "2036-06-03", "2026-06-03",
                             "2021-06-03", "2014-06-03", "1970-01-01")}
        self.assertEqual(flagged, {"2016-06-03": True, "2017-01-02": True, "2015-12-31": True, "2036-06-03": True,
                                   "2026-06-03": False, "2021-06-03": False, "2014-06-03": False,
                                   "1970-01-01": False})
        self.assertIsNone(year_shift(self.invoice(date="2016-06-03", is_not_invoice=True), anchor_year=2026))

    def test_adopt_takes_only_a_unique_valid_value_close_to_the_read(self):
        wrong = self.invoice(buyer_tax=BAD_BUYER, date="2016-06-03", date_raw="3/6/2559")
        other_day = self.invoice(date="2026-07-03", date_raw="3/7/2569")
        self.assertEqual(adopt_from_reads(wrong.model_copy(deep=True), [other_day], anchor_year=2026),
                         [f"{REREAD_PREFIX} buyer_tax {BAD_BUYER}→{BUYER}"])  # 月日不同的日期不取
        other_buyer = tax_id("099999999999")
        self.assertEqual(adopt_from_reads(self.invoice(buyer_tax=BAD_BUYER),
                                          [self.invoice(buyer_tax=other_buyer)], anchor_year=2026), [])
        right = self.invoice(date="2026-06-03", date_raw="3/6/2569")
        fixed = self.invoice(date="2016-06-03", date_raw="3/6/2559")
        self.assertEqual(adopt_from_reads(fixed, [right], anchor_year=2026),
                         [f"{REREAD_PREFIX} date 2016-06-03→2026-06-03"])
        self.assertEqual((fixed.date, fixed.date_raw), ("2026-06-03", "3/6/2569"))
        two = [right, self.invoice(date="2025-06-03", date_raw="")]  # 两个说法不一 → 不取
        self.assertEqual(adopt_from_reads(self.invoice(date="2016-06-03", date_raw="3/6/2559"), two,
                                          anchor_year=2026), [])
        swapped = self.invoice(buyer_tax=BAD_BUYER)
        self.assertEqual(adopt_from_reads(swapped, [self.invoice(buyer_tax=SELLER)], anchor_year=2026), [])


if __name__ == "__main__":
    unittest.main()
