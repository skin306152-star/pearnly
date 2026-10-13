"""认不出银行的 PDF 账单按表头上标着的账号记对账预设（L8 复查 2026-10-13）。

教程三家公司的电子 PDF 账单（虚构的 ธนาคารสาธิต / SATHIT DEMO BANK）读成通用版式，原来没有尾号：不问总账科目、什么都不记。
现在只读文字层第一页、流水表之前、紧跟在「เลขที่บัญชี / Account No.」这类标签后面的完整号码；遮挡的、隔着别的字的不算，
认得出银行的照旧不记（预设键不变）。

``HeaderLabel`` / ``PdfIntake`` 只调纯函数、把文字层换成给定文字，不需要 PDF 库，本地可跑；``RealStatements`` 读
tests/fixtures/bank-statements 里的三份真 PDF，要 pdfminer 与 frappe，在候选镜像里跑（scripts/ci-candidate-checks.sh）。
"""
import os
import unittest
from unittest import mock

from pearnly_cowork.reconciliation import bank_intake, bank_text, presets

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "bank-statements")

# pdfminer 抽出的教程 D2 账单第一页开头（原样：声调符号抽成 \x00 并断行，「เลขที่บัญชี」成了「เลขที\n\n\x00บัญชี」）。
MINER_HEADER = ("ธนาคารสาธิต\n\nSAT H I T   D E M O   BA N K\n\nรายการเดินบัญชี / Statement of Account\n"
	"บัญชีกระแสรายวัน (Current Account)\n\nชื\n\n\x00อบัญชี: บริษัท แก้วกานดา บิวตี\n\n\x00 สโตร์ จํากัด\n\n"
	"สาขาเจ้าของบัญชี: สาขารามคําแหง 53\n\nสกุลเงิน: THB\n\nเลขที\n\n\x00บัญชี: 305-1-77420-9\n\n"
	"รอบรายการ: 01/09/2569 - 30/09/2569\n\nหน้า: 1/4\n\nวันที\n\nเวลา\n\nรายการ\n\nถอนเงิน\n\nฝากเงิน\n\n"
	"ยอดคงเหลือ  ช่องทาง\n\n")
MINER_ROWS = ("                ยอดยกมา                       180,000.00\n\n"
	"01/09/2569  14:55    รับโอนพร้อมเพย์ xxx-xxx-2019          590.00              180,590.00  PROMPTPAY\n\n"
	"01/09/2569  15:58    รับโอนพร้อมเพย์ xxx-xxx-5598          900.00              181,490.00  PROMPTPAY\n\n"
	"02/09/2569  09:46    หักบัญชีอัตโนมัติ KIDNGERNWAI SOFTWARE  738.30          180,751.70  AUTO\n\n"
	"02/09/2569  16:12    โอนเงินออก ไปยัง SAISUK PLAZA          25,500.00          155,251.70  MOBILE\n\n")
# pypdf 抽同一页：声调符号成了空格，标签和号码在一行。
PYPDF_HEADER = ("ธนาคารสาธิต\nSATHIT DEMO BANK\nรายการเดินบัญชี / Statement of Account\n"
	"บัญชีกระแสรายวัน (Current Account)\nชื อบัญชี: บริษัท แก้วกานดา บิวตี  สโตร์ จํากัด เลขที บัญชี: 305-1-77420-9\n"
	"สาขาเจ้าของบัญชี: สาขารามคําแหง 53 รอบรายการ: 01/09/2569 - 30/09/2569\nสกุลเงิน: THB หน้า: 1/4\n"
	"วันที  เวลา รายการ ถอนเงิน ฝากเงิน ยอดคงเหลือ  ช่องทาง\n")


def header(*lines):
	"""一页账单：给定的表头行 + 两行流水。"""
	return "\n".join(["DEMO BANK", "Statement of Account", *lines, "วันที่ รายการ ถอนเงิน ฝากเงิน ยอดคงเหลือ",
		"01/09/2569 รับโอน 590.00 180,590.00", "02/09/2569 โอนออก 100.00 180,490.00"])


class HeaderLabel(unittest.TestCase):
	def test_the_tutorial_header_is_read_as_pdfminer_and_pypdf_give_it(self):
		self.assertEqual(bank_text.labelled_account(MINER_HEADER + MINER_ROWS), "305-1-77420-9")
		self.assertEqual(bank_text.labelled_account(PYPDF_HEADER + MINER_ROWS), "305-1-77420-9")

	def test_each_account_label_counts(self):
		for line in ("เลขที่บัญชี: 201-3-48817-6", "หมายเลขบัญชี 201-3-48817-6", "บัญชีเลขที่ 201-3-48817-6",
				"เลขบัญชี : 201-3-48817-6", "Account No. : 201-3-48817-6", "ACCOUNT NUMBER 2013488176",
				"A/C No. 201-3-48817-6", "เลขที่บัญชี / Account No. 201-3-48817-6", "เลขที่บัญชี (Account No.) : 201-3-48817-6",
				"เลขที่บัญชี\n201-3-48817-6", "เลขที่บัญชี ๒๐๑-๓-๔๘๘๑๗-๖"):
			with self.subTest(line=line):
				self.assertEqual(presets.account_tail([bank_text.labelled_account(header(line))]), "8176")

	def test_a_masked_number_does_not_count(self):
		for line in ("เลขที่บัญชี: xxx-x-x4209-x", "Account No. XXX-X-X4209-X", "A/C No. ******4209", "เลขที่บัญชี ...4209",
				"เลขที่บัญชี 305-1-7742x-x", "Account No. •••• 4209"):
			with self.subTest(line=line):
				self.assertEqual(bank_text.labelled_account(header(line)), "")

	def test_numbers_that_are_not_next_to_an_account_label_do_not_count(self):
		# 电话、参考号、税号、隔着别的字的号码；号码位数不像账号的（页码、日期）。
		for lines in (["โทร. 02-123-4567", "Tel 081-234-5678"], ["Ref No. 3051774209", "เลขที่อ้างอิง 3051774209"],
				["เลขประจำตัวผู้เสียภาษี 0105599210021"], ["เลขที่บัญชี กระแสรายวัน 305-1-77420-9"],
				["เลขที่บัญชี:", "ชื่อบัญชี: บริษัท แก้วกานดา บิวตี้ สโตร์ จำกัด", "305-1-77420-9"],
				["เลขที่บัญชี 1/4"], ["Account No. 01/09/2569"]):
			with self.subTest(lines=lines):
				self.assertEqual(bank_text.labelled_account(header(*lines)), "")

	def test_only_the_header_of_the_first_page_counts(self):
		# 流水行里的号码（遮挡的 PromptPay 号、转账写的对方账号）、流水表开始后才出现的、第二页才有的都不算。
		self.assertEqual(bank_text.labelled_account(header("ชื่อบัญชี: บริษัท ทดสอบ จำกัด") + "\n"
			"03/09/2569 โอนไปบัญชีเลขที่ 123-4-56789-0 50.00 180,440.00\nเลขที่บัญชี: 305-1-77420-9"), "")
		self.assertEqual(bank_text.labelled_account("ยอดยกมา 180,000.00\nเลขที่บัญชี: 305-1-77420-9"), "")
		self.assertEqual(bank_text.labelled_account(MINER_ROWS + MINER_HEADER), "")
		self.assertEqual(bank_text.labelled_account(header("สกุลเงิน: THB") + "\f" + MINER_HEADER), "")

	def test_two_different_labelled_numbers_are_not_guessed_between(self):
		self.assertEqual(bank_text.labelled_account(header("เลขที่บัญชี 305-1-77420-9", "Account No. 118-7-30554-2")), "")
		self.assertEqual(bank_text.labelled_account(header("เลขที่บัญชี 305-1-77420-9", "Account No. 3051774209")),
			"305-1-77420-9")


class PdfIntake(unittest.TestCase):
	"""文字层换成给定文字（真接缝 ``pdf_text.extract_pdf_text_pages``），看读出的结果带不带 ``printed_account``。"""

	def read(self, text, filename="Statement_202609.pdf"):
		with mock.patch.object(bank_intake.pdf_text, "extract_pdf_text_pages", return_value=[text]):
			return bank_intake.read_statement_file(b"%PDF-1.4 fake", filename)

	def test_an_unknown_bank_records_the_labelled_account(self):
		result = self.read(MINER_HEADER + MINER_ROWS)
		self.assertEqual((result["ok"], result["bank_code"], result["source"], result["row_count"]),
			(True, "generic", "text_lines", 4))
		self.assertEqual(result["printed_account"], "305-1-77420-9")

	def test_a_masked_header_records_nothing(self):
		result = self.read(MINER_HEADER.replace("305-1-77420-9", "xxx-x-x4209-x") + MINER_ROWS)
		self.assertEqual((result["ok"], result["bank_code"]), (True, "generic"))
		self.assertNotIn("printed_account", result)

	def test_a_named_bank_is_unchanged(self):
		# 认得出银行（文字里或文件名里）的不记：它们的预设键照旧。「กรุงเทพ」照旧认成盘谷银行。
		for text, filename, bank in ((header("BANGKOK BANK", "Account No. 123-4-56789-0"), "Statement.pdf", "bbl"),
				(header("สาขากรุงเทพ", "เลขที่บัญชี 123-4-56789-0"), "Statement.pdf", "bbl"),
				(header("เลขที่บัญชี 123-4-56789-0"), "SCB_202609.pdf", "scb")):
			with self.subTest(bank=bank, filename=filename):
				result = self.read(text, filename)
				self.assertEqual((result["ok"], result["bank_code"]), (True, bank))
				self.assertNotIn("printed_account", result)


class RealStatements(unittest.TestCase):
	"""教程三家公司的真 PDF（data/README.md：D1 201-3-48817-6、D2 305-1-77420-9、D3 118-7-30554-2）。要 pdfminer 与 frappe。"""

	EXPECTED = {"Statement_2013488176_202609.pdf": ("201-3-48817-6", "8176", 33),
		"Statement_3051774209_202609.pdf": ("305-1-77420-9", "4209", 174),
		"Statement_1187305542_202609.pdf": ("118-7-30554-2", "5542", 26)}

	def test_each_statement_is_known_by_its_printed_account(self):
		from pearnly_cowork.reconciliation import bank_recon_service

		for name, (account, tail, rows) in self.EXPECTED.items():
			with self.subTest(name=name):
				with open(os.path.join(FIXTURES, name), "rb") as handle:
					result = bank_intake.read_statement_file(handle.read(), name)
				self.assertEqual((result["ok"], result["bank_code"], result["source"], result["row_count"]),
					(True, "generic", "text_lines", rows))
				self.assertEqual(result["printed_account"], account)
				# 记到 parse_info.stmt_files 的就是这一条：尾号 + 读自账单（通用版式据此按「GENERIC + 尾号」记预设）。
				recorded = bank_recon_service._account_of(result)
				self.assertEqual(recorded, {"account": tail, "account_read": True})
				self.assertEqual(presets.account_scope("generic", presets.statement_tail("generic", [{"ok": True, **recorded}])),
					f"bank|GENERIC|{tail}|-")


if __name__ == "__main__":
	unittest.main()
