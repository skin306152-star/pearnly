"""Offline regressions for the migrated one-line error text."""

import unittest

from pearnly_cowork.contracts.errors import _fmt_validation, short_error

NO_FIELDS = "字段校验失败 · 请确认文件格式与表头是否完整"


def pydantic_or_skip(test):
	try:
		import pydantic  # noqa: F401
	except ImportError:
		test.skipTest("pydantic is only installed where the OCR schema runs")


class _StandIn:
	"""A non-pydantic object with the ``errors()`` interface the formatter reads."""

	def __init__(self, errs, raise_on_errors=False):
		self._errs = errs
		self._raise = raise_on_errors

	def errors(self):
		if self._raise:
			raise RuntimeError("boom")
		return self._errs


class PlainExceptions(unittest.TestCase):
	def test_a_plain_message_is_kept_as_one_line(self):
		self.assertEqual(short_error(ValueError("bad thing")), "bad thing")
		self.assertEqual(short_error(ValueError("line1\nline2\nline3")), "line1")

	def test_an_empty_message_falls_back_to_the_class_name(self):
		class WeirdErr(Exception):
			pass

		self.assertEqual(short_error(WeirdErr("")), "WeirdErr")
		self.assertEqual(short_error(ValueError()), "ValueError")

	def test_a_message_that_is_only_a_documentation_url_falls_back_to_the_class_name(self):
		self.assertEqual(short_error(ValueError("https://errors.pydantic.dev/2.5/v/missing")),
			"ValueError")

	def test_the_pydantic_documentation_url_is_stripped_even_when_wrapped(self):
		out = short_error(ValueError("oops see https://errors.pydantic.dev/2.5/v/missing here"))
		self.assertNotIn("pydantic.dev", out)
		self.assertEqual(out, "oops see  here")

	def test_a_long_message_is_cut_to_the_deployed_cap(self):
		for length in (159, 160):
			with self.subTest(length=length):
				self.assertEqual(short_error(ValueError("x" * length)), "x" * length)
		out = short_error(ValueError("x" * 161))
		self.assertEqual(out, "x" * 159 + "…")
		self.assertEqual(len(out), 160)

	def test_the_cap_counts_characters_not_bytes(self):
		out = short_error(ValueError("ก" * 300))
		self.assertEqual(len(out), 160)
		self.assertTrue(out.endswith("…"))

	def test_other_exception_shapes_are_stringified(self):
		self.assertEqual(short_error(KeyError("k")), "'k'")
		self.assertEqual(short_error(ValueError({"a": 1})), "{'a': 1}")


class ValidationErrors(unittest.TestCase):
	def setUp(self):
		pydantic_or_skip(self)
		from pydantic import BaseModel

		class Line(BaseModel):
			price: float

		class Invoice(BaseModel):
			seller_tax: str
			total_amount: float
			subtotal: float
			vat: float
			lines: list[Line] = []

		self.Invoice = Invoice

	def _errors(self, **fields):
		try:
			self.Invoice(**fields)
		except Exception as error:
			return error
		raise AssertionError("the model accepted an invalid payload")

	def test_the_field_names_are_listed_with_the_leading_index_dropped(self):
		out = short_error(self._errors(seller_tax="x", total_amount="no", subtotal=1, vat=2,
			lines=[{"price": "no"}]))
		self.assertTrue(out.startswith("字段识别失败 · 共 2 项 · 例如 "), out)
		self.assertIn("lines.price", out)
		self.assertNotIn("lines.0.price", out)
		self.assertIn("total_amount", out)
		self.assertEqual(out.count("\n"), 0)
		self.assertNotIn("pydantic.dev", out)

	def test_more_than_three_fields_are_summarised(self):
		out = short_error(self._errors(seller_tax=1, total_amount="no", subtotal="no", vat="no"))
		self.assertIn("共 4 项", out)
		self.assertIn("…(+1)", out)


class FormatterInternals(unittest.TestCase):
	def test_no_errors_asks_the_reader_to_check_the_file(self):
		self.assertEqual(_fmt_validation(_StandIn([])), NO_FIELDS)

	def test_an_errors_call_that_raises_is_answered_the_same_way(self):
		self.assertEqual(_fmt_validation(_StandIn([], raise_on_errors=True)), NO_FIELDS)

	def test_repeated_fields_are_named_once(self):
		out = _fmt_validation(_StandIn([{"loc": ("seller_tax",)}, {"loc": ("seller_tax",)}]))
		self.assertEqual(out, "字段识别失败 · 共 2 项 · 例如 seller_tax · 请确认文件表头是否完整")

	def test_the_leading_model_position_is_dropped_but_other_numbers_are_kept(self):
		out = _fmt_validation(_StandIn([{"loc": (0, "lines", 2, "price")}]))
		self.assertIn("例如 lines.2.price", out)

	def test_an_error_without_a_location_still_reports_the_count(self):
		out = _fmt_validation(_StandIn([{}, {"loc": []}, {"loc": (0, )}]))
		self.assertEqual(out, "字段识别失败 · 共 3 项 · 请确认文件表头是否完整")

	def test_a_fourth_field_becomes_an_overflow_marker(self):
		errs = [{"loc": (name, )} for name in ("a", "b", "c", "d", "e")]
		out = _fmt_validation(_StandIn(errs))
		self.assertEqual(out, "字段识别失败 · 共 5 项 · 例如 a, b, c…(+2) · 请确认文件表头是否完整")
