"""Synthetic local decode/render only; no filesystem originals or provider calls."""
import io
import unittest
from unittest.mock import patch

from PIL import Image
import pymupdf

from pearnly_core.originals.rules import OcrInputError
from pearnly_cowork.ocr.inputs_runtime import InputRuntimeError, PageSource
from pearnly_cowork.ocr.inputs_runtime.source import downscale_image_bytes


def pdf(texts=("", ""), *, size=(72, 144), encryption=False):
    with pymupdf.open() as document:
        for text in texts:
            page = document.new_page(width=size[0], height=size[1])
            if text:
                page.insert_text((8, 16), text, fontsize=3)
        if encryption:
            return document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="synthetic-password")
        return document.tobytes()


def image(size=(30, 20), format="PNG"):
    with Image.new("RGB", size, (100, 130, 200)) as source:
        output = io.BytesIO()
        source.save(output, format=format)
        return output.getvalue()


class RuntimeInputs(unittest.TestCase):
    def test_pdf_is_lazy_single_page_200_dpi_and_repeatable(self):
        content = pdf()
        with patch.object(pymupdf.Page, "get_pixmap", side_effect=AssertionError("eager render")):
            source = PageSource(content, "synthetic.PDF", expected_page_count=2)
        self.assertEqual(source.page_count, 2)
        self.assertIsNone(source.text_pages)
        frame = source.frame(2)
        self.assertEqual(frame.mime_type, "image/png")
        self.assertIsNone(frame.vision_bytes)
        with Image.open(io.BytesIO(frame.image_bytes)) as rendered:
            self.assertEqual(rendered.size, (200, 400))
            self.assertEqual(rendered.mode, "RGB")
        self.assertEqual(source.frame(2), frame)
        self.assertFalse(any(isinstance(value, list) for value in vars(source).values()))
        source.close()
        with self.assertRaisesRegex(InputRuntimeError, "source_closed"):
            source.frame(1)

    def test_text_path_is_whole_document_average_and_does_not_render(self):
        text = "\n".join(["synthetic electronic invoice line with words"] * 12)
        content = pdf((text, ""), size=(200, 250))
        with patch.object(pymupdf.Page, "get_pixmap", side_effect=AssertionError("text path rendered")):
            source = PageSource(content, "electronic.pdf", expected_page_count=2, enable_text_path=True)
        self.assertEqual(len(source.text_pages), 2)
        self.assertGreater(len(source.text_pages[0].full_text), 400)
        self.assertEqual(source.text_pages[1].full_text, "")
        self.assertEqual(source.text_pages[1].avg_confidence, 1)
        self.assertEqual(source.text_pages[0].blocks, [])
        self.assertEqual(source.text_pages[0].width, 0)
        self.assertEqual(source.frame(1).mime_type, "image/png")

    def test_short_or_scanned_pdf_declines_whole_text_override(self):
        for content in (pdf(), pdf(("tiny", "another short line"))):
            with PageSource(content, "short.pdf", expected_page_count=2, enable_text_path=True) as source:
                self.assertIsNone(source.text_pages)

    def test_text_page_extraction_error_still_counts_in_average(self):
        import pypdf
        class Page:
            def __init__(self, value):
                self.value = value
            def extract_text(self):
                if isinstance(self.value, Exception):
                    raise self.value
                return self.value
        for first, accepted in (("x" * 400, True), ("x" * 399, False)):
            with patch.object(pypdf, "PdfReader", return_value=type("Reader", (), {"pages": [Page(first), Page(ValueError())]})()):
                source = PageSource(pdf(), "text.pdf", expected_page_count=2, enable_text_path=True)
            self.assertEqual(source.text_pages is not None, accepted)

    def test_text_count_mismatch_and_parser_error_fall_back_to_physical_source(self):
        import pypdf
        for result in (ValueError("cannot parse"), type("Reader", (), {"pages": []})()):
            with patch.object(pypdf, "PdfReader", side_effect=result if isinstance(result, Exception) else None,
                              return_value=result):
                source = PageSource(pdf(), "text.pdf", expected_page_count=2, enable_text_path=True)
            self.assertIsNone(source.text_pages)
            self.assertEqual(source.page_count, 2)

    def test_images_keep_original_for_direct_and_l3_and_shrink_only_l1(self):
        for format, suffix in (("PNG", "png"), ("JPEG", "jpg")):
            content = image((3000, 800), format=format)
            with PageSource(content, "upload." + suffix, expected_page_count=1) as source:
                frame = source.frame(1)
            self.assertEqual(frame.image_bytes, content)
            self.assertLess(len(frame.vision_bytes), len(content))
            with Image.open(io.BytesIO(frame.vision_bytes)) as reduced:
                self.assertEqual(reduced.size[0], 2400)
                self.assertEqual(reduced.format, format)
            self.assertEqual(frame.mime_type, "image/" + format.lower())

    def test_orient_turns_the_reading_copy_and_shrinks_the_turned_one(self):
        # 识别前转正（ADR-013）：直读 / 复核看转正后的图，Vision 用转正后再缩小的图；PDF 渲染页同样过一遍
        seen = []

        def orient(content, mime):
            seen.append(mime)
            with Image.open(io.BytesIO(content)) as original, original.rotate(90, expand=True) as turned:
                output = io.BytesIO()
                turned.convert("RGB").save(output, "JPEG")
            return output.getvalue(), "image/jpeg"

        with PageSource(image((300, 200)), "wide.png", expected_page_count=1, image_max_long_edge=150,
                        orient=orient) as source:
            frame = source.frame(1)
        self.assertEqual((frame.mime_type, Image.open(io.BytesIO(frame.image_bytes)).size), ("image/jpeg", (200, 300)))
        self.assertEqual(Image.open(io.BytesIO(frame.vision_bytes)).size, (100, 150))
        with PageSource(pdf(("",)), "scan.pdf", expected_page_count=1, orient=orient) as source:
            self.assertEqual(source.frame(1).mime_type, "image/jpeg")
        self.assertEqual(seen, ["image/png", "image/png"])

    def test_without_orient_the_original_bytes_are_sent(self):
        original = image()
        with PageSource(original, "a.png", expected_page_count=1) as source:
            self.assertEqual(source.frame(1).image_bytes, original)

    def test_small_images_disabled_resize_and_resize_errors_preserve_bytes(self):
        content = image()
        self.assertIs(downscale_image_bytes(content, 2400), content)
        self.assertIs(downscale_image_bytes(content, 0), content)
        self.assertEqual(downscale_image_bytes(b"not a real image", 10), b"not a real image")
        with PageSource(content, "upload.png", expected_page_count=1) as source:
            self.assertEqual(source.frame(1).vision_bytes, content)

    def test_invalid_originals_counts_and_unsupported_runtime_types_fail(self):
        for filename, content, count in (("bad.pdf", b"invalid", 1), ("bad.png", b"invalid", 1),
                                        ("encrypted.pdf", pdf(encryption=True), 2),
                                        ("mismatch.pdf", pdf(), 1), ("table.xlsx", b"table", 1)):
            with self.subTest(filename=filename), self.assertRaises((InputRuntimeError, OcrInputError)):
                PageSource(content, filename, expected_page_count=count)
        for count in (True, 0, "2", None):
            with self.assertRaises(InputRuntimeError):
                PageSource(pdf(), "bad.pdf", expected_page_count=count)
        with self.assertRaises(OcrInputError) as caught:
            PageSource(pdf(tuple("" for _ in range(51))), "long.pdf", expected_page_count=50)
        self.assertEqual(caught.exception.code, "ocr.too_many_pages")

    def test_page_reference_and_render_allocation_limit_fail_before_allocation(self):
        with PageSource(pdf(), "oversize.pdf", expected_page_count=2, max_render_pixels=1000) as source:
            for number in (0, 3, True, "1", None):
                with self.assertRaisesRegex(InputRuntimeError, "invalid_page_reference"):
                    source.frame(number)
            with patch.object(pymupdf.Page, "get_pixmap", side_effect=AssertionError("allocation happened")):
                with self.assertRaisesRegex(InputRuntimeError, "render_pixel_limit") as caught:
                    source.frame(1)
            self.assertEqual(caught.exception.page_number, 1)


if __name__ == "__main__":
    unittest.main()
