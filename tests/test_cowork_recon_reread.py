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

	def test_a_report_page_is_read_from_the_image_first_and_again_with_the_transcript_when_it_has_no_total(self):
		# 税报第一遍只看图；没读到页合计就用原读法（带转写稿、不加提示句）再读，仍没有合计就用第一遍、照旧点名
		model = Model(vat([("100", "7")]))
		result = pipeline.run("vat", page_count=1, reads=[(b"img", "image/png", 1)], render=render,
			document_ai_call=docai("transcript words"), model_call=model)
		self.assertEqual([request.stage for request in model.requests], ["enterprise_schema", pipeline.REREAD_STAGE])
		self.assertNotIn("transcript words", model.requests[0].prompt)
		self.assertIn("transcript words", model.requests[1].prompt)
		self.assertNotIn("did not pass its arithmetic check", model.requests[1].prompt)
		self.assertEqual(result.pages[0].extraction_audit["self_check"]["state"], self_check.NO_TOTAL)

	def test_a_bank_page_is_still_read_with_the_transcript(self):
		model = Model(GOOD)
		pipeline.run("bank", page_count=1, reads=[(b"img", "image/jpeg", 1)], render=render,
			document_ai_call=docai("bank words"), model_call=model)
		self.assertIn("bank words", model.requests[0].prompt)

	def test_a_grand_total_on_the_last_page_is_checked_against_every_page_before_it(self):
		# 第 1 页没印合计（重读一次仍没有），第 2 页的全表总计按累计核得上，不再读
		model = Model(vat([("100", "7")]), vat([("100", "7")]), vat([("200", "14")], "300", "21"))
		pipeline.run("vat", page_count=2, reads=[(b"%PDF", "application/pdf", 2)], render=render,
			document_ai_call=docai("a", "b"), model_call=model)
		self.assertEqual([request.stage for request in model.requests],
			["enterprise_schema", pipeline.REREAD_STAGE, "enterprise_schema"])


class UnverifiedReportPages(unittest.TestCase):
	def test_a_grand_total_in_a_later_photo_verifies_the_photos_before_it(self):
		reports = [("p1.jpg", [(1, vat([("100", "7")]))]), ("p2.jpg", [(1, vat([("200", "14")], "300", "21"))])]
		self.assertEqual(self_check.unverified_report_pages(reports), [])

	def test_pages_that_cannot_be_checked_are_named(self):
		reports = [("a.jpg", [(1, vat([("100", "7")], "100", "7")), (2, vat([("100", "7")], "150", "7"))]),
			("b.jpg", [(1, vat([("50", "3.5")])), (2, {"entries": []})])]
		self.assertEqual(self_check.unverified_report_pages(reports), [("a.jpg", [2]), ("b.jpg", [1])])


class ReportRowChecks(unittest.TestCase):
	@staticmethod
	def page(*entries):
		return {"entries": [dict(zip(("invoice_no", "subtotal", "vat", "transaction_date_raw"), entry)) for entry in entries]}

	def test_lines_that_do_not_agree_with_themselves_are_named(self):
		reports = [("IMG_1.jpg", [(1, self.page(
			("690525-001", "1000.00", "70.00", "25/05/2569"),     # 对
			("690525-001", "500.00", "35.00", "25/05/2569"),      # 单号重复
			("690525-003", "1000.00", "7.00", "25/05/2569"),      # 税额≠税基×7%
			("02000076", "2156.09", "150.91", "25/05/2569"),      # 含税价倒算的尾差 0.02：不算（rM）
			("690523-017", "56.07", "3.92", "22/05/2569"),        # 单号日子差 1 天：先编号后开票，不算（rM）
			("690525-004", "", "70.00", "25/05/2569"),            # 只有税额
			("690526-011", "100.00", "7.00", "11/05/2569"),       # 单号上的日子是 26 号
			("EXP-1", "5000.00", "0.00", "25/05/2569"),           # 零税率不算
		))])]
		self.assertEqual(self_check.suspect_report_rows(reports), [
			("IMG_1.jpg", "690525-001", self_check.ROW_DUPLICATE),
			("IMG_1.jpg", "690525-003", self_check.ROW_VAT_RATE),
			("IMG_1.jpg", "690525-004", self_check.ROW_VAT_ONLY),
			("IMG_1.jpg", "690526-011", self_check.ROW_DATE)])

	def test_the_printed_date_wins_over_the_converted_one(self):
		from pearnly_cowork.reconciliation import vat_parse

		rows = vat_parse.pipeline_rows({"pages": [{"document": {"entries": [
			{"invoice_no": "02000126", "transaction_date": "2026-05-16", "transaction_date_raw": "17/05/2569"},
			{"invoice_no": "02000127", "transaction_date": "2026-05-18", "transaction_date_raw": ""}]}}]},
			"enterprise-2026-09-03-v1")
		self.assertEqual([row["report_date"] for row in rows], ["2026-05-17", "2026-05-18"])


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

	def test_only_lines_that_look_misread_are_counted(self):
		from types import SimpleNamespace
		from unittest import mock

		from pearnly_cowork.reconciliation import income_vat, revision

		row = lambda number, vat, gl: income_vat.ReconRow(number, "2026-05-20", "", vat, gl,
			None if gl is None else round(vat - gl, 2), "")
		doc = SimpleNamespace(parse_info_json=json.dumps({"vat": [{"file": "f", "photo_pages": [
			["SHIFT.jpg", ["690512-011", "690512-012", "690512-013"]],
			["DIGITS.jpg", ["690513-001", "690513-002"]],
			["REAL.jpg", ["690520-004", "690523-017", "SW02000131"]],
			["NEAR.jpg", ["690521-001", "690521-002"]]]}]}))
		results = [
			# 金额在单号之间错开（结果行单号是总账写法，带字母前缀）
			row("SI690512-011", 785.05, 420.56), row("SI690512-012", 420.56, 785.05), row("SI690512-013", 50.0, 50.0),
			# 只差一两位数字
			row("SI690513-001", 785.05, 785.65), row("SI690513-002", 1284.0, 1234.0),
			# 真实差异：总账里没有、也没有近似的；金额差得不像读错（rJ IMG_2545 / 2546）
			row("690520-004", 420.56, None), row("690523-017", 56.07, None), row("SW02000131", 3557.02, 2339.36),
			# 总账里没有，但总账同一天有同金额、单号只差一个字符的 → 像读错；另一行是金额差一位
			row("690521-001", 300.0, None), row("SI690521-002", 500.0, 580.0)]
		summary = income_vat.GlVatSummary(0, 0, 0, 0, 0, 0, gl_only_credit_items=[
			{"doc_no": "SI690527-001", "date": "2026-05-20", "name": "", "amount": 300.0}])
		with mock.patch.object(revision, "_", side_effect=lambda text: text + ":{0}"):
			self.assertEqual(revision.income_photo_notes(doc, results, summary),
				["reconciliation.report_page_shifted:SHIFT.jpg (2)",
				 "reconciliation.report_page_likely_misread:DIGITS.jpg (2)、NEAR.jpg (2)"])


class LongNotes(unittest.TestCase):
	"""提示再长也不让整份对账失败（2026-09-30 压测 rK I01：30 行自检 + 超时文件拼成一条，超长抛错）。"""

	def test_long_and_many_notes_are_folded_not_refused(self):
		from pearnly_cowork.reconciliation import bank_recon_contracts, income_recon_contracts, recon_contracts

		rows = "以下识别出的税报行自己对不上：" + "、".join(f"IMG_{2500 + i}.JPG 69052{i % 10}-0{i:02d}（单号重复）" for i in range(30))
		files = "以下文件没读出来：" + "、".join(f"IMG_{2600 + i}.JPG" for i in range(20))
		pages = "以下税报页没法自核：" + "；".join(f"IMG_{2700 + i}.JPG: 1" for i in range(20))
		stored = income_recon_contracts.parse_info({"vat": [{"file": "f", "rows": 1, "warnings": [rows, files, pages] * 20}]})
		warnings = stored["vat"][0]["warnings"]
		self.assertLessEqual(len(warnings), income_recon_contracts.MAX_WARNINGS)
		self.assertTrue(all(len(note) <= recon_contracts.MAX_TEXT for note in warnings))
		self.assertTrue(warnings[0].startswith("以下识别出的税报行自己对不上：") and "…(+" in warnings[0])
		run = bank_recon_contracts.run_info({"warnings": [rows, files, pages] * 10})
		self.assertLessEqual(len(run["warnings"]), bank_recon_contracts.MAX_WARNINGS)
		self.assertTrue(all(len(note) <= bank_recon_contracts.MAX_WARNING_TEXT for note in run["warnings"]))


if __name__ == "__main__":
	unittest.main()
