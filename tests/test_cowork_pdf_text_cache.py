"""对账读 PDF 文字层的缓存：同一份字节只抽一次，结果与直接抽相同。离线：不连站点。"""
import unittest
from unittest.mock import patch

from pearnly_cowork.reconciliation import bank_intake, pdf_text


class FakeCache:
	def __init__(self):
		self.values, self.ttls = {}, {}

	def get_value(self, key):
		return self.values.get(key)

	def set_value(self, key, value, expires_in_sec=None):
		self.values[key], self.ttls[key] = value, expires_in_sec


class PdfTextCache(unittest.TestCase):
	def setUp(self):
		pdf_text._recent.clear()

	def test_the_same_bytes_are_extracted_once_and_callers_get_their_own_copy(self):
		with patch.object(pdf_text.readers, "extract_pdf_text_pages", return_value=["page one"]) as extract, \
				patch.object(pdf_text, "_cache", return_value=None):
			first = pdf_text.extract_pdf_text_pages(b"%PDF a")
			first.append("changed by a caller")
			self.assertEqual(pdf_text.extract_pdf_text_pages(b"%PDF a"), ["page one"])
			pdf_text.extract_pdf_text_pages(b"%PDF b")
		self.assertEqual(extract.call_count, 2)

	def test_another_process_reads_the_site_cache_instead_of_extracting_again(self):
		cache = FakeCache()
		with patch.object(pdf_text, "_cache", return_value=cache), \
				patch.object(pdf_text.readers, "extract_pdf_text_pages", return_value=["text"]) as extract:
			pdf_text.extract_pdf_text_pages(b"%PDF c")
			pdf_text._recent.clear()  # 唤醒后的另一个任务进程
			self.assertEqual(pdf_text.extract_pdf_text_pages(b"%PDF c"), ["text"])
		self.assertEqual(extract.call_count, 1)
		self.assertEqual(list(cache.ttls.values()), [pdf_text.TTL_SECONDS])

	def test_a_broken_cache_falls_back_to_extracting(self):
		class Broken:
			def get_value(self, key):
				raise ConnectionError

			def set_value(self, *args, **kwargs):
				raise ConnectionError

		with patch.object(pdf_text, "_cache", return_value=Broken()), \
				patch.object(pdf_text.readers, "extract_pdf_text_pages", return_value=["x"]):
			self.assertEqual(pdf_text.extract_pdf_text_pages(b"%PDF d"), ["x"])

	def test_the_recent_list_stays_small(self):
		with patch.object(pdf_text, "_cache", return_value=None), \
				patch.object(pdf_text.readers, "extract_pdf_text_pages", side_effect=lambda data: [data.decode()]):
			for index in range(pdf_text.RECENT + 5):
				pdf_text.extract_pdf_text_pages(str(index).encode())
		self.assertEqual(len(pdf_text._recent), pdf_text.RECENT)

	def test_the_bank_reader_goes_through_the_cache(self):
		source = open(bank_intake.__file__).read()
		self.assertEqual(source.count("pdf_text.extract_pdf_text_pages("), 3)
		self.assertNotIn("readers.extract_pdf_text_pages(", source)


if __name__ == "__main__":
	unittest.main()
