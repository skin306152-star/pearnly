"""Differential check of the migrated one-line error text.

CLI: --source <fixed-source-root>, or --source-stdin with the fixed source text as
JSON (used to pipe the source into the bench that has pydantic). The pinned module
imports only the standard library at import time (pydantic is imported inside the
function), so no legacy application, database, file, network, model or export code
runs. Both sides answer the same questions verbatim: the same exceptions, real
pydantic validation errors, and a stand-in whose ``errors()`` raises. The two
module constants and the private field formatter are compared directly as well.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys

from pearnly_cowork.contracts import errors

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
    "services/ocr/error_format.py":
        "8774b6f4fe9fc0d8d629e0b78457bbdf3d35f92d302fd29d02506f39b9611cba",
}
PAYLOAD = "services/ocr/error_format.py"


def load_legacy(root=None, source_text=None):
	data = (source_text[PAYLOAD].encode("utf-8") if source_text is not None
		else (Path(root) / PAYLOAD).read_bytes())
	assert hashlib.sha256(data).hexdigest() == HASHES[PAYLOAD], f"Source hash mismatch: {PAYLOAD}"
	namespace = {"__name__": "fixed_error_format"}
	exec(compile(ast.parse(data.decode("utf-8")), PAYLOAD, "exec"), namespace)
	return namespace


class StandIn:
	"""A non-pydantic object with the ``errors()`` interface the formatter reads."""

	def __init__(self, errs, raise_on_errors=False):
		self._errs = errs
		self._raise = raise_on_errors

	def errors(self):
		if self._raise:
			raise RuntimeError("boom")
		return self._errs


class NoString(Exception):
	def __str__(self):
		raise ValueError("no string")


class UrlOnly(Exception):
	def __str__(self):
		return "see https://errors.pydantic.dev/2.5/v/missing"


class Empty(Exception):
	def __str__(self):
		return ""


def plain_exceptions():
	return [
		lambda: ValueError("bad thing"),
		lambda: ValueError("line1\nline2\nline3"),
		lambda: ValueError("\nsecond line only"),
		lambda: ValueError("line1 \nline2"),
		lambda: ValueError("oops see https://errors.pydantic.dev/2.5/v/missing here"),
		lambda: ValueError("https://errors.pydantic.dev/2.5/v/missing"),
		lambda: ValueError("x" * 159),
		lambda: ValueError("x" * 160),
		lambda: ValueError("x" * 161),
		lambda: ValueError("x" * 500),
		lambda: ValueError("ก" * 200),
		lambda: ValueError("ไทย " * 60),
		lambda: ValueError(""),
		lambda: ValueError(),
		lambda: ValueError({"a": 1}),
		lambda: KeyError("k"),
		lambda: RuntimeError(),
		lambda: SystemExit(2),
		lambda: Empty(),
		lambda: UrlOnly(),
		lambda: NoString(),
		lambda: OSError("read failed\nsecond line"),
		lambda: ValueError("  padded  "),
		lambda: Exception("mixed pydantic https://errors.pydantic.dev/2.5/v/x tail" + "y" * 200),
	]


def stand_in_cases():
	return [
		lambda: StandIn([]),
		lambda: StandIn([], raise_on_errors=True),
		lambda: StandIn([{"loc": ("seller_tax", )}]),
		lambda: StandIn([{"loc": ("seller_tax", )}, {"loc": ("seller_tax", )}]),
		lambda: StandIn([{"loc": ()}]),
		lambda: StandIn([{"loc": None}]),
		lambda: StandIn([{}]),
		lambda: StandIn([{"loc": (0, "lines", 2, "price")}]),
		lambda: StandIn([{"loc": (0, )}]),
		lambda: StandIn([{"loc": (1, )}]),
		lambda: StandIn([{"loc": [name]} for name in ("a", "b", "c")]),
		lambda: StandIn([{"loc": [name]} for name in ("a", "b", "c", "d")]),
		lambda: StandIn([{"loc": [name]} for name in ("a", "b", "c", "d", "e", "f")]),
		lambda: StandIn([{"loc": ("lines", 0, "price")}, {"loc": ("lines", 1, "price")},
			{"loc": ("vat", )}, {"loc": ("subtotal", )}, {"loc": ("vat", )}]),
	]


def pydantic_errors():
	from pydantic import BaseModel, model_validator

	class Line(BaseModel):
		price: float

	class Seller(BaseModel):
		tax: str

	class Invoice(BaseModel):
		seller: Seller
		seller_name: str
		total_amount: float
		subtotal: float
		vat: float
		lines: list[Line] = []
		currency: str = "THB"

		@model_validator(mode="after")
		def _not_negative(self):
			if self.total_amount is not None and self.total_amount < 0:
				raise ValueError("total is negative")
			return self

	payloads = [
		{"seller": {"tax": "x"}, "seller_name": "s", "total_amount": "no", "subtotal": 1,
			"vat": 2},
		{"seller": {"tax": 13}, "seller_name": 5, "total_amount": "no", "subtotal": "no",
			"vat": [], "lines": [{"price": "no"}]},
		{"seller": {}, "seller_name": "s", "total_amount": -1, "subtotal": 1, "vat": 2},
		{"seller": {"tax": "x"}, "seller_name": "s", "total_amount": 1, "subtotal": 2,
			"vat": 3, "currency": 7},
		{"seller": {"tax": "x"}, "seller_name": "s", "total_amount": 1, "subtotal": 2,
			"vat": 3, "lines": [{"price": "no"}, {"price": None}]},
		{"seller": {"tax": "x"}, "seller_name": "s", "total_amount": 1, "subtotal": 2},
	]
	out = []
	for payload in payloads:
		try:
			Invoice(**payload)
			raise AssertionError("the model accepted an invalid payload")
		except ValueError as error:
			out.append(lambda error=error: error)
	try:
		Invoice.model_validate_json("{not json")
		raise AssertionError("the model accepted malformed JSON")
	except ValueError as error:
		out.append(lambda error=error: error)
	return out


def wrapped_pydantic():
	"""The deployed call sites wrapped the error before shortening it."""
	for factory in pydantic_errors():
		error = factory()
		yield (lambda error=error: ValueError(f"pipeline parse failed: {type(error).__name__}: {error}"))
		yield (lambda error=error: ValueError(str(error)))


def outcome(callable_, *arguments):
	"""Success payloads and exception identity, never a partial outcome."""
	try:
		return "ok", callable_(*arguments)
	except BaseException as error:
		return "error", type(error).__name__, str(error)


def compare(legacy, factories, label, counts, seen):
	for factory in factories:
		value = factory()
		expected = outcome(legacy["short_error"], value)
		produced = outcome(errors.short_error, value)
		assert expected == produced, (label, type(value).__name__, expected, produced)
		counts[label] = counts.get(label, 0) + 1
		if expected[0] != "ok":
			seen["propagated"] += 1
			continue
		text = expected[1]
		seen["truncated"] += 1 if text.endswith("…") else 0
		seen["fallback"] += 1 if text == type(value).__name__ else 0
		seen["field_list"] += 1 if text.startswith("字段识别失败 · 共") else 0
		seen["single_line"] += 1 if "\n" not in text else 0


def compare_internals(legacy, counts):
	assert legacy["_PYDANTIC_DOC_RE"].pattern == errors._PYDANTIC_DOC_RE.pattern
	assert legacy["_MAX_LEN"] == errors._MAX_LEN
	counts["internals"] = 2
	for factory in stand_in_cases():
		value = factory()
		expected = outcome(legacy["_fmt_validation"], value)
		produced = outcome(errors._fmt_validation, value)
		assert expected == produced, ("_fmt_validation", expected, produced)
		counts["stand_in"] = counts.get("stand_in", 0) + 1
		counts["no_fields"] = counts.get("no_fields", 0) + (
			1 if expected[1].startswith("字段校验失败") else 0)


def run(root=None, source_text=None, floors=True):
	legacy = load_legacy(root, source_text)
	counts: dict = {}
	seen = {"truncated": 0, "fallback": 0, "field_list": 0, "single_line": 0,
		"propagated": 0}
	compare(legacy, plain_exceptions(), "plain", counts, seen)
	compare(legacy, pydantic_errors(), "pydantic", counts, seen)
	compare(legacy, list(wrapped_pydantic()), "wrapped", counts, seen)
	compare_internals(legacy, counts)
	print({"source": SOURCE_SHA, "cases": counts, "coverage": seen, "differences": 0})
	if not floors:
		return
	if (counts["plain"] < 20 or counts["pydantic"] < 6 or counts["wrapped"] < 8
			or counts["stand_in"] < 14 or counts["no_fields"] < 2 or seen["truncated"] < 3
			or seen["fallback"] < 4 or seen["field_list"] < 6 or seen["propagated"] < 1):
		raise SystemExit(f"fixture coverage below the floor: {counts} {seen}")
	raise SystemExit(0)


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description=__doc__)
	group = parser.add_mutually_exclusive_group(required=True)
	group.add_argument("--source", type=Path)
	group.add_argument("--source-stdin", action="store_true")
	arguments = parser.parse_args()
	try:
		import pydantic  # noqa: F401
	except ImportError:
		raise SystemExit("pydantic is required for this check; run it in the bench")
	if arguments.source_stdin:
		run(None, json.load(sys.stdin))
	else:
		run(arguments.source)
