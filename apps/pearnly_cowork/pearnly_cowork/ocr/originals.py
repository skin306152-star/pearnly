"""原件检查（纯：不依赖 Frappe、无 IO）：文件名、类型、大小、PDF 能否打开 / 是否加密 / 页数、图片能否解码。

服务器收原件（``files.service.inspect_content``）和 Cloud Run 核对浏览器直传的原件（ADR-016）用同一份检查，
不合格时给出同一个错误代码，服务器再换成原来的提示文字。
"""
import hashlib
import io
from pathlib import PurePath

from pearnly_cowork.ocr.inputs import (IMAGE_EXTENSIONS, MAX_FILE_BYTES, MAX_PDF_PAGES, TABLE_EXTENSIONS,
    OcrInputError, validate_input_metadata)

# 错误代码 → 服务器上的提示文字（``files.service.MESSAGES``）；``OcrInputError`` 的代码原样带出。
CODES = ("invalid_filename", "unsupported_type", "empty", "too_large", "pdf_password", "pdf_unreadable",
         "pdf_pages", "image_unreadable")


class Rejected(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def filename_of(filename):
    """去掉路径，只留文件名；不合规就 ``Rejected("invalid_filename")``。"""
    name = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not name or len(name) > 255 or any(c in name for c in ("\x00", "\r", "\n")):
        raise Rejected("invalid_filename")
    return name


def suffix_of(filename):
    suffix = PurePath(filename).suffix.lower()
    if suffix not in IMAGE_EXTENSIONS | TABLE_EXTENSIONS | {".pdf"}:
        raise Rejected("unsupported_type")
    return suffix


def inspect(filename, content):
    """返回 ``{filename, suffix, kind, page_count, byte_size, sha256}``；不合格抛 ``Rejected`` 或 ``OcrInputError``。"""
    import pymupdf
    from PIL import Image

    filename = filename_of(filename)
    suffix = suffix_of(filename)
    if not isinstance(content, bytes) or not content:
        raise Rejected("empty")
    if len(content) > MAX_FILE_BYTES:
        raise Rejected("too_large")
    count = 1
    kind = "document"
    if suffix == ".pdf":
        kind = "pdf"
        try:
            with pymupdf.open(stream=content, filetype="pdf") as document:
                if document.needs_pass:
                    raise Rejected("pdf_password")
                count = len(document)
        except (ValueError, RuntimeError, pymupdf.FileDataError) as exc:
            if isinstance(exc, Rejected):
                raise
            raise Rejected("pdf_unreadable") from exc
        if not 1 <= count <= MAX_PDF_PAGES:
            raise Rejected("pdf_pages")
    elif suffix in IMAGE_EXTENSIONS:
        kind = "image"
        try:
            with Image.open(io.BytesIO(content)) as image:
                image.verify()
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise Rejected("image_unreadable") from exc
    validate_input_metadata(filename, len(content), pdf_page_count=count if kind == "pdf" else None)
    return {"filename": filename, "suffix": suffix, "kind": kind, "page_count": count,
            "byte_size": len(content), "sha256": hashlib.sha256(content).hexdigest()}


__all__ = ["CODES", "OcrInputError", "Rejected", "filename_of", "inspect", "suffix_of"]
