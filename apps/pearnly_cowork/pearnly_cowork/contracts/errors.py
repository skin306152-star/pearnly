"""One-line user-facing error text for extraction and conversion failures.

Source: ec2e5c957e6b ``services/ocr/error_format.py``, moved with the deployed
wording and quirks intact. The deployed module was a leaf: the bank and GL parse
facades imported it to turn whatever their file-level try caught (usually a
pydantic ``ValidationError`` from the extraction schema) into one sentence an
accountant can read, instead of the multi-line dump with a pydantic.dev URL that
``str(exception)`` produces.

It lives with the Cowork contracts rather than under ``ocr`` because both sides
of the platform need the same sentence: the extraction engine owns the pydantic
schema, the reconciliation converters own the ``error`` field of their result.
Keeping it here leaves those two domains independent of each other, and this
module pure: no Frappe, database, file, clock, HTTP, model or export access.

``pydantic`` is imported inside the function, never at import time, so this
convention is importable in an environment that has no pydantic at all.
"""

from __future__ import annotations

import re
from typing import Any

# The documentation URL pydantic appends is never useful to the reader.
_PYDANTIC_DOC_RE = re.compile(r"https?://errors\.pydantic\.dev/\S+", re.IGNORECASE)
# Deployed cap: a toast line, not a report.
_MAX_LEN = 160


def short_error(e: BaseException) -> str:
	"""Reduce any exception, especially a pydantic one, to ONE short line.

	An empty message falls back to the exception's own class name, because a
	constant linter and a bare ``KeyError()`` both stringify to nothing.
	"""
	try:
		from pydantic import ValidationError  # imported here, never a hard dependency

		if isinstance(e, ValidationError):
			return _fmt_validation(e)
	except Exception:
		pass

	msg = str(e) if e else e.__class__.__name__
	# A wrapped validation error keeps its URL even when the type test above
	# failed, so the URL is stripped from whatever text is left.
	msg = _PYDANTIC_DOC_RE.sub("", msg)
	# Everything after the first line is internals.
	msg = msg.split("\n", 1)[0].strip()
	if len(msg) > _MAX_LEN:
		msg = msg[: _MAX_LEN - 1].rstrip() + "…"
	return msg or e.__class__.__name__


def _fmt_validation(e: Any) -> str:
	"""Name the first few refused fields of a validation error, deduplicated.

	The first tuple element of a ``loc`` is the model position, not a field, so a
	leading ``0`` is dropped before the field names are joined.
	"""
	try:
		errs = list(e.errors())
	except Exception:
		errs = []
	if not errs:
		return "字段校验失败 · 请确认文件格式与表头是否完整"

	fields = []
	for er in errs:
		loc = er.get("loc") or []
		parts = [str(p) for p in loc if not (isinstance(p, int) and p == 0)]
		if parts:
			fields.append(".".join(parts))
	fields = list(dict.fromkeys(fields))  # de-dup, keep order

	n = len(errs)
	if fields:
		head = ", ".join(fields[:3])
		if len(fields) > 3:
			head += f"…(+{len(fields) - 3})"
		return f"字段识别失败 · 共 {n} 项 · 例如 {head} · 请确认文件表头是否完整"
	return f"字段识别失败 · 共 {n} 项 · 请确认文件表头是否完整"
