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
         "pdf_pages", "image_unreadable", "preview_too_large", "preview_unsupported")
MAX_PREVIEW_PIXELS = 16_000_000
# 预览图超过这个大小（照片、扫描件）改用 JPEG（质量 85）：同样清楚，体积约十分之一，省出网流量（ADR-016）；
# 文字页的 PNG 本来就小，照旧 PNG。只影响页面上的预览，不影响识别。
PREVIEW_PNG_LIMIT = 400 * 1024


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


def preview_type(picture):
    return "image/jpeg" if picture[:3] == b"\xff\xd8\xff" else "image/png"


def render_page(kind, data, number):
    """原件一页的预览图（PDF 150 dpi；照片按原比例长边不超过 2048）：PNG，超过 ``PREVIEW_PNG_LIMIT`` 时 JPEG。
    服务器与 Cloud Run 同一份（ADR-016）。"""
    import pymupdf
    from PIL import Image, ImageOps

    if kind == "pdf":
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            selected = pdf[number - 1]
            scale = 150 / 72
            if selected.rect.width * selected.rect.height * scale * scale > MAX_PREVIEW_PIXELS:
                raise Rejected("preview_too_large")
            pixmap = selected.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            picture = pixmap.tobytes("png")
            return picture if len(picture) <= PREVIEW_PNG_LIMIT else pixmap.tobytes("jpg", jpg_quality=85)
    if kind == "image" and number == 1:
        with Image.open(io.BytesIO(data)) as image:
            if image.format in ("JPEG", "MPO"):
                # 手机照片（iPhone 2400 万 / 4800 万像素）：JPEG 按 1/2–1/8 直接缩小解码，内存跟着缩小后的尺寸走，
                # 预览本来就只要 2048 边长（2026-09-30 测试站点 IMG_3134.JPG 5712×4284 原来被拒，页面显示无法加载预览）。
                # 按原图比例要（长边 2048）：要正方形的话横拍照片短边不够缩一半，照样按原尺寸解码。
                longest = max(image.width, image.height)
                image.draft("RGB", (max(1, image.width * 2048 // longest), max(1, image.height * 2048 // longest)))
            if image.width * image.height > MAX_PREVIEW_PIXELS:
                raise Rejected("preview_too_large")
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((2048, 2048))
            output = io.BytesIO()
            image.save(output, format="PNG")
            if output.tell() <= PREVIEW_PNG_LIMIT:
                return output.getvalue()
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=85)
            return output.getvalue()
    raise Rejected("preview_unsupported")


__all__ = ["CODES", "OcrInputError", "Rejected", "filename_of", "inspect", "render_page", "suffix_of"]
