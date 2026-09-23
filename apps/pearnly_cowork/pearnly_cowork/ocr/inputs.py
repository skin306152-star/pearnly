"""Pure upload metadata gate from deployed legacy ec2e5c957e6b.

Sources: recognize/core.py:101-136, pipeline.py:117-131,
table_path.py:36-45, core/route_helpers.py:177-191. PDF parsing belongs to
the file adapter: pdf_page_count must come from the bytes, never the client.
This preserves the legacy extension/size gate, not a new image decoder check.
"""

from __future__ import annotations

PDF_EXTENSIONS = frozenset({".pdf"})
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif", ".bmp", ".gif"})
TABLE_EXTENSIONS = frozenset({".xlsx", ".xls", ".xlsm", ".csv", ".tsv", ".docx", ".doc", ".txt"})
SUPPORTED_EXTENSIONS = PDF_EXTENSIONS | IMAGE_EXTENSIONS | TABLE_EXTENSIONS
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_PDF_PAGES = 50


class OcrInputError(ValueError):
	def __init__(self, code, **details):
		self.code = code
		self.details = {"code": code, **details} if details else code
		super().__init__(code)


def file_extension(filename: str | None) -> str:
	name = (filename or "").lower()
	return "." + name.rsplit(".", 1)[-1] if "." in name else ""


def validate_input_metadata(filename, size_bytes, *, pdf_page_count=None,
							max_bytes=MAX_FILE_BYTES, max_pages=MAX_PDF_PAGES) -> dict:
	"""Validate trusted byte length and parsed count without reading a file.

	Images count as one page even if their container has multiple frames.
	Table/text input uses legacy `excel` character billing; its provisional
	page_count=1 is not a billable page count or a parsed worksheet count.
	"""
	if not isinstance(filename, str):
		raise OcrInputError("ocr.unsupported_format")
	extension = file_extension(filename)
	if not filename or extension not in SUPPORTED_EXTENSIONS:
		raise OcrInputError("ocr.unsupported_format")
	if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0:
		raise OcrInputError("ocr.invalid_input_metadata")
	if not size_bytes:
		raise OcrInputError("ocr.empty_file")
	if any(isinstance(value, bool) or not isinstance(value, int) or value < 1
		   for value in (max_bytes, max_pages)):
		raise OcrInputError("ocr.invalid_input_metadata")
	if size_bytes > max_bytes:
		raise OcrInputError("ocr.file_too_large", mb=max_bytes / (1024 * 1024))
	if extension in PDF_EXTENSIONS:
		if isinstance(pdf_page_count, bool) or not isinstance(pdf_page_count, int) or pdf_page_count < 1:
			raise OcrInputError("ocr.invalid_pdf")
		if pdf_page_count > max_pages:
			raise OcrInputError("ocr.too_many_pages", max=max_pages, actual=pdf_page_count)
		family, page_count = "pdf", pdf_page_count
	else:
		family, page_count = ("image" if extension in IMAGE_EXTENSIONS else "table"), 1
	return {"extension": extension, "family": family, "page_count": page_count,
			"billing_kind": "excel" if family == "table" else "pdf"}
