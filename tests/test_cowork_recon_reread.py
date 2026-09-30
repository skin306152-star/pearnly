"""逐页自核与自动再读一次（2026-09-30）：银行页内余额链、税报页合计；税报几份合起来的「无法自核」。

Document AI 与模型都是假的，不连服务商、不付费。
"""
import json
import unittest

from pearnly_cowork.ocr.engine.contracts import ModelResponse
from pearnly_cowork.ocr.engine.errors import ContentError
from pearnly_cowork.ocr.enterprise import pipeline, self_check


def payload(*texts):
	full, page_list, start = "", [], 0
	for number, text in enumerate(texts, 1):
		full += text
		page_list.append({"pageNumber": number, "layout": {"textAnchor": {"textSegments": [
			{"startIndex": str(start), "endIndex": str(start + len(text))}]}}})
		start += len(text)
	return {"document": {"text": full, "pages": page_list}}


def docai(*texts):
	return lambda content, mime, *, expected_pages, page_number=1: (payload(*texts), 1, "key")


class Model:
	"""按调用顺序回答；``None`` 表示这次调用读不出。"""

	def __init__(self, *answers):
		self.answers, self.requests = list(answers), []

	def __call__(self, request):
		self.requests.append(request)
		data = self.answers[min(len(self.requests) - 1, len(self.answers) - 1)]
		if data is None:
			raise ContentError(pipeline.UNREAD_PAGE, page_number=request.page_number)
		return ModelResponse(text=json.dumps(data), raw={}, input_tokens=10, output_tokens=5, model=request.model)


def render(number):
	return b"png-%d" % number, "image/png"


def bank(opening, rows, closing):
	return {"opening_balance": opening, "closing_balance": closing, "entries": [
		{"transaction_date": "2026-06-0%d" % (index + 1), "description": "x", "deposit": deposit, "withdrawal": withdrawal,
		 "balance": balance} for index, (deposit, withdrawal, balance) in enumerate(rows)]}


GOOD = bank("1000", [("", "100", "900"), ("50", "", "950")], "950")
MISSED = bank("1000", [("", "100", "900")], "950")  # 漏读了一笔存入：结转余额对不上
WORSE = bank("1000", [("", "10", "900")], "950")


def vat(entries, subtotal="", tax="", total=""):
	return {"total_subtotal": subtotal, "total_vat": tax, "total_total": total, "entries": [
		{"invoice_no": "IV%03d" % index, "subtotal": base, "vat": vat_amount, "total": ""}
		for index, (base, vat_amount) in enumerate(entries, 1)]}


class BankPage(unittest.TestCase):
	def test_the_running_balance_of_a_page(self):
		self.assertEqual(self_check.bank_breaks(GOOD), 0)
		self.assertEqual(self_check.bank_breaks(MISSED), 1)
		self.assertEqual(self_check.bank_breaks(bank("", [("", "100", "900"), ("50", "", "990")], "")), 1)

	def test_a_broken_page_is_read_again_and_the_better_reading_kept(self):
		model = Model(MISSED, GOOD)
		result = pipeline.run("bank", page_count=1, reads=[(b"img", "image/jpeg", 1)], render=render,
			document_ai_call=docai("no geometry"), model_call=model)
		self.assertEqual([request.stage for request in model.requests], ["enterprise_schema", pipeline.REREAD_STAGE])
		self.assertIn("did not pass its arithmetic check", model.requests[1].prompt)
		self.assertEqual(len(result.pages[0].document.entries), 2)
		audit = result.pages[0].extraction_audit["self_check"]
		self.assertEqual((audit["state"], audit["adopted"], audit["breaks"]), ("passed", "reread", [1, 0]))

	def test_a_reread_that_is_no_better_is_not_used(self):
		result = pipeline.run("bank", page_count=1, reads=[(b"img", "image/jpeg", 1)], render=render,
			document_ai_call=docai("no geometry"), model_call=Model(MISSED, WORSE))
		self.assertEqual(result.pages[0].document.entries[0].withdrawal, "100")
		self.assertEqual(result.pages[0].extraction_audit["self_check"]["state"], "mismatch")

	def test_a_reread_that_cannot_be_read_keeps_the_first_reading(self):
		result = pipeline.run("bank", page_count=1, reads=[(b"img", "image/jpeg", 1)], render=render,
			document_ai_call=docai("no geometry"), model_call=Model(MISSED, None))
		self.assertEqual(len(result.pages[0].document.entries), 1)
		self.assertTrue(result.pages[0].extraction_audit["self_check"]["reread_failed"])

	def test_a_page_that_adds_up_is_not_read_again(self):
		model = Model(GOOD)
		pipeline.run("bank", page_count=1, reads=[(b"img", "image/jpeg", 1)], render=render,
			document_ai_call=docai("no geometry"), model_call=model)
		self.assertEqual(len(model.requests), 1)

	def test_rereads_per_file_are_capped(self):
		pages = pipeline.MAX_REREAD_PAGES + 2
		model = Model(MISSED)
		pipeline.run("bank", page_count=pages, reads=[(b"%PDF", "application/pdf", pages)], render=render,
			document_ai_call=docai(*["page %d" % n for n in range(pages)]), model_call=model)
		rereads = [request for request in model.requests if request.stage == pipeline.REREAD_STAGE]
		self.assertEqual(len(rereads), pipeline.MAX_REREAD_PAGES)

	def test_a_ledger_page_is_not_checked(self):
		model = Model({"opening_balance": "1000", "closing_balance": "5", "entries": [
			{"transaction_date": "2026-06-01", "debit": "100", "credit": "", "balance": "900"}]})
		result = pipeline.run("gl", page_count=1, reads=[(b"img", "image/jpeg", 1)], render=render,
			document_ai_call=docai("x"), model_call=model)
		self.assertEqual(len(model.requests), 1)
		self.assertNotIn("self_check", result.pages[0].extraction_audit)


class VatPage(unittest.TestCase):
	def test_page_totals_and_running_totals(self):
		page = vat([("100", "7"), ("200", "14")], "300", "21")
		self.assertEqual(self_check.vat_state(page)[0], self_check.PASSED)
		carried = vat([("100", "7")], "400", "28")
		self.assertEqual(self_check.vat_state(carried, {"subtotal": 300, "vat": 21, "total": 0})[0], self_check.PASSED)
		self.assertEqual(self_check.vat_state(vat([("100", "7")], "300", "21"))[0], self_check.MISMATCH)
		self.assertEqual(self_check.vat_state(vat([("100", "7")]))[0], self_check.NO_TOTAL)

	def test_a_page_that_does_not_add_up_is_read_again(self):
		wrong, right = vat([("100", "7")], "300", "21"), vat([("100", "7"), ("200", "14")], "300", "21")
		model = Model(wrong, right)
		result = pipeline.run("vat", page_count=1, reads=[(b"img", "image/png", 1)], render=render,
			document_ai_call=docai("x"), model_call=model)
		self.assertEqual(len(result.pages[0].document.entries), 2)
		self.assertEqual(result.pages[0].extraction_audit["self_check"]["adopted"], "reread")

	def test_a_page_without_a_printed_total_is_not_read_again(self):
		model = Model(vat([("100", "7")]))
		result = pipeline.run("vat", page_count=1, reads=[(b"img", "image/png", 1)], render=render,
			document_ai_call=docai("x"), model_call=model)
		self.assertEqual(len(model.requests), 1)
		self.assertEqual(result.pages[0].extraction_audit["self_check"]["state"], self_check.NO_TOTAL)

	def test_a_grand_total_on_the_last_page_is_checked_against_every_page_before_it(self):
		model = Model(vat([("100", "7")]), vat([("200", "14")], "300", "21"))
		pipeline.run("vat", page_count=2, reads=[(b"%PDF", "application/pdf", 2)], render=render,
			document_ai_call=docai("a", "b"), model_call=model)
		self.assertEqual(len(model.requests), 2)


class UnverifiedReportPages(unittest.TestCase):
	def test_a_grand_total_in_a_later_photo_verifies_the_photos_before_it(self):
		reports = [("p1.jpg", [(1, vat([("100", "7")]))]), ("p2.jpg", [(1, vat([("200", "14")], "300", "21"))])]
		self.assertEqual(self_check.unverified_report_pages(reports), [])

	def test_pages_that_cannot_be_checked_are_named(self):
		reports = [("a.jpg", [(1, vat([("100", "7")], "100", "7")), (2, vat([("100", "7")], "150", "7"))]),
			("b.jpg", [(1, vat([("50", "3.5")])), (2, {"entries": []})])]
		self.assertEqual(self_check.unverified_report_pages(reports), [("a.jpg", [2]), ("b.jpg", [1])])


class PhotoPages(unittest.TestCase):
	def test_each_recognised_page_lists_its_invoice_numbers(self):
		from pearnly_cowork.recon_center import vat_check

		pages = [(1, vat([("100", "7"), ("200", "14")])), (2, {"entries": []})]
		self.assertEqual(vat_check.photo_pages([("scan.pdf", pages), ("p.jpg", [(1, vat([("5", "0.35")]))])]),
			[["scan.pdf p.1", ["IV001", "IV002"]], ["p.jpg", ["IV001"]]])

	def test_the_page_list_is_kept_in_the_stored_file_record(self):
		from pearnly_cowork.reconciliation import income_recon_contracts

		stored = income_recon_contracts.parse_info({"vat": [{"file": "p.jpg", "rows": 2,
			"photo_pages": [["p.jpg", ["IV001", "IV002"]]]}]})
		self.assertEqual(stored["vat"][0]["photo_pages"], [["p.jpg", ["IV001", "IV002"]]])
		with self.assertRaises(Exception):
			income_recon_contracts.parse_info({"vat": [{"file": "p.jpg", "photo_pages": [["p.jpg"]]}]})

	def test_a_page_with_several_lines_off_the_ledger_is_named(self):
		from types import SimpleNamespace
		from unittest import mock

		from pearnly_cowork.reconciliation import income_vat, revision

		doc = SimpleNamespace(parse_info_json=json.dumps({"vat": [{"file": "f", "photo_pages": [
			["IMG_1.jpg", ["A-1", "A-2", "A-3"]], ["IMG_2.jpg", ["B-1", "B-2"]]]}]}))
		results = [income_vat.ReconRow("A-1", "", "", 100.0, 90.0, 10.0, ""),
			income_vat.ReconRow("A-2", "", "", 100.0, None, None, ""),
			income_vat.ReconRow("A-3", "", "", 100.0, 100.0, 0.0, ""),
			income_vat.ReconRow("B-1", "", "", 100.0, 80.0, 20.0, ""),
			income_vat.ReconRow("B-2", "", "", 100.0, 100.0, 0.0, "")]
		summary = income_vat.GlVatSummary(0, 0, 0, 0, 0, 0)
		with mock.patch.object(revision, "_", side_effect=lambda text: text + ":{0}"):
			self.assertEqual(revision.income_photo_notes(doc, results, summary),
				["reconciliation.report_page_likely_misread:IMG_1.jpg (2)"])


if __name__ == "__main__":
	unittest.main()
