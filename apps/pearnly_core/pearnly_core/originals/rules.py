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
# 人从页面 / CC 对话交来的一份文件最多这么大（CC 附件、代录资料区、ERP 对话收单同一个；50 页扫描 PDF 约 28MB，11-10 压测，
# 主会话 11-10 定 40MB 给密扫描留余量）。再往上的硬限是甲骨文原件服务 60MB 的 JSON 请求体（base64 后约 4/3），文件约 44MB。
# 请求体还多一层 multipart 外壳：Frappe（bench 配置 `max_file_size`，`upload_limits`）与租户站 nginx 按
# UPLOAD_REQUEST_MAX_BYTES 收。零依赖的 CC 契约、前端、宿主机开户脚本各写一份同样的数，tests/test_upload_limits.py 核对。
UPLOAD_MAX_BYTES = 40 * 1024 * 1024
UPLOAD_REQUEST_MAX_BYTES = UPLOAD_MAX_BYTES + 1024 * 1024


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
