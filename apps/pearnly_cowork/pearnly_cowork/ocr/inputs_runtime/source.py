"""Physical originals for the invoice engine, prepared one image at a time.

The trusted caller must obtain bytes through files.service.read_bytes after
checking the actor's source access. No filesystem path or URL is accepted here.
PDF text extraction remains pypdf, rendering remains PyMuPDF at 200 DPI. Unlike
the source's all-PNG list, frame(n) renders only that physical page on demand.
"""
import io
import math

from PIL import Image
import pymupdf

from pearnly_cowork.ocr.engine.contracts import Frame
from pearnly_cowork.ocr.inputs import IMAGE_EXTENSIONS, file_extension, validate_input_metadata
from pearnly_cowork.ocr.schema.evidence import Page

DPI = 200
MAX_RENDER_PIXELS = 40_000_000


class InputRuntimeError(ValueError):
    def __init__(self, code, *, page_number=None):
        self.code, self.page_number = code, page_number
        super().__init__(code)


def _text_pages(content, page_count):
    # Missing optional runtime dependency is a configuration fault, never an
    # excuse to silently pay Vision for a configured electronic-PDF fast path.
    try:
        import pypdf
    except ImportError as exc:
        raise InputRuntimeError("ocr.pypdf_not_configured") from exc
    try:
        reader = pypdf.PdfReader(io.BytesIO(content))
        if len(reader.pages) != page_count:
            return None
        texts = []
        for source_page in reader.pages:
            try:
                texts.append(source_page.extract_text() or "")
            except Exception:
                texts.append("")  # Source: failed page still counts in average.
    except Exception:
        return None
    if sum(map(len, texts)) < 200 * page_count:
        return None
    return tuple(Page(page_number=index, width=0, height=0, full_text=text,
                      avg_confidence=1.0, blocks=[]) for index, text in enumerate(texts, 1))


def downscale_image_bytes(content, max_long_edge):
    """Source pipeline.downscale_image_bytes: only uploaded image L1 input."""
    if not content or max_long_edge <= 0:
        return content
    try:
        with Image.open(io.BytesIO(content)) as image:
            width, height = image.size
            longest = max(width, height)
            if longest <= max_long_edge:
                return content
            scale = max_long_edge / longest
            target = (max(1, round(width * scale)), max(1, round(height * scale)))
            with image.resize(target, Image.Resampling.LANCZOS) as resized:
                output = io.BytesIO()
                if (image.format or "PNG").upper() in {"JPEG", "JPG"}:
                    with resized.convert("RGB") as rgb:
                        rgb.save(output, format="JPEG", quality=90)
                else:
                    resized.save(output, format="PNG")
                smaller = output.getvalue()
                return smaller if 0 < len(smaller) < len(content) else content
    except Exception:
        return content  # Optimization failure never destroys original evidence.


def _mime(content):
    with Image.open(io.BytesIO(content)) as image:
        return "image/" + (image.format or "png").lower()


class PageSource:
    """Bytes-only, closable source implementing engine.contracts.PageSource.

    expected_page_count is independently read from the stored original. A
    mismatch is fatal before providers run. text_pages is whole-file or None;
    a text hit does not render a PDF until an actual visual stage requests it.
    """
    def __init__(self, content: bytes, filename: str, *, expected_page_count: int,
                 enable_text_path=False, image_max_long_edge=2400,
                 max_render_pixels=MAX_RENDER_PIXELS):
        if not isinstance(content, bytes) or not isinstance(filename, str):
            raise InputRuntimeError("ocr.invalid_input_metadata")
        if type(expected_page_count) is not int or expected_page_count < 1:
            raise InputRuntimeError("ocr.invalid_input_metadata")
        if type(image_max_long_edge) is not int or image_max_long_edge < 0:
            raise InputRuntimeError("ocr.invalid_input_metadata")
        if type(max_render_pixels) is not int or max_render_pixels < 1:
            raise InputRuntimeError("ocr.invalid_input_metadata")
        suffix = file_extension(filename)
        if suffix not in IMAGE_EXTENSIONS | {".pdf"}:
            raise InputRuntimeError("ocr.runtime_format_not_supported")
        # Byte / extension gate runs before decoding potentially expensive data.
        validate_input_metadata(filename, len(content), pdf_page_count=expected_page_count)
        self._content, self._is_pdf = content, suffix == ".pdf"
        self._image_max_long_edge, self._max_render_pixels = image_max_long_edge, max_render_pixels
        self.text_pages = None
        if self._is_pdf:
            try:
                with pymupdf.open(stream=content, filetype="pdf") as document:
                    if document.needs_pass:
                        raise InputRuntimeError("ocr.password_protected_pdf")
                    count = len(document)
            except InputRuntimeError:
                raise
            except (RuntimeError, ValueError) as exc:
                raise InputRuntimeError("ocr.invalid_pdf") from exc
        else:
            try:
                with Image.open(io.BytesIO(content)) as image:
                    image.verify()
            except (OSError, ValueError, Image.DecompressionBombError) as exc:
                raise InputRuntimeError("ocr.invalid_image") from exc
            count = 1  # Source contract counts uploaded images, not GIF/TIFF frames.
        validate_input_metadata(filename, len(content), pdf_page_count=count if self._is_pdf else None)
        if count != expected_page_count:
            raise InputRuntimeError("ocr.source_page_count_changed")
        self.page_count = count
        if self._is_pdf and enable_text_path:
            self.text_pages = _text_pages(content, count)

    def frame(self, page_number):
        if self._content is None:
            raise InputRuntimeError("ocr.source_closed")
        if type(page_number) is not int or not 1 <= page_number <= self.page_count:
            raise InputRuntimeError("ocr.invalid_page_reference")
        if not self._is_pdf:
            vision = downscale_image_bytes(self._content, self._image_max_long_edge)
            return Frame(image_bytes=self._content, mime_type=_mime(self._content),
                         vision_bytes=vision, vision_mime_type=_mime(vision))
        try:
            with pymupdf.open(stream=self._content, filetype="pdf") as document:
                page = document.load_page(page_number - 1)
                scale = DPI / 72.0
                pixels = math.ceil(page.rect.width * scale) * math.ceil(page.rect.height * scale)
                if pixels > self._max_render_pixels:
                    raise InputRuntimeError("ocr.render_pixel_limit", page_number=page_number)
                content = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")
            return Frame(image_bytes=content, mime_type="image/png")
        except InputRuntimeError:
            raise
        except (RuntimeError, ValueError) as exc:
            raise InputRuntimeError("ocr.pdf_render_failed", page_number=page_number) from exc

    def close(self):
        self._content, self.text_pages = None, None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
