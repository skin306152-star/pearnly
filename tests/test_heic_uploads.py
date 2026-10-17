"""iPhone 照片（HEIC / HEIF）收文件时转成 JPEG（2026-10-02）。在生产镜像里跑（用镜像里的 frappe 与 pi-heif），不连站点。"""
import base64
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for app in ("pearnly_core", "pearnly_cowork"):
    sys.path.insert(0, str(ROOT / "apps" / app))
for name in [n for n in sys.modules if n.split(".")[0] in ("pearnly_core", "pearnly_cowork")]:
    del sys.modules[name]

import frappe  # noqa: E402

from pearnly_core.desk import heic  # noqa: E402

assert Path(heic.__file__).is_relative_to(ROOT), heic.__file__
SAMPLE = (ROOT / "tests/fixtures/synthetic-64x48.heic").read_bytes()  # 合成纯色图（sips 生成），不含任何真实内容
HAS_DECODER = importlib.util.find_spec("pi_heif") is not None


def _throw(message, exc=None, *args, **kwargs):
    raise (exc or frappe.ValidationError)(message)


class Heic(unittest.TestCase):
    def setUp(self):
        for target, value in (("frappe.throw", _throw), ("frappe._", lambda text, *a, **kw: text),
                              ("pearnly_core.desk.heic._", lambda text, *a, **kw: text)):  # 没有站点，不加载翻译
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)

    def test_recognised_by_content_not_by_name(self):
        self.assertTrue(heic.is_heic(SAMPLE))
        self.assertFalse(heic.is_heic(b"\xff\xd8\xff\xe0" + b"\0" * 20))
        self.assertFalse(heic.is_heic(b""))
        self.assertEqual(heic.jpeg_name("IMG_0001.HEIC"), "IMG_0001.jpg")
        self.assertEqual(heic.jpeg_name("scan.heif"), "scan.jpg")
        self.assertEqual(heic.jpeg_name("renamed"), "renamed.jpg")

    @unittest.skipUnless(HAS_DECODER, "pi-heif 只在新镜像里有")
    def test_photo_becomes_a_jpeg_of_the_same_size(self):
        from PIL import Image

        out = heic.to_jpeg(SAMPLE)
        self.assertEqual(out[:3], b"\xff\xd8\xff")
        with Image.open(io.BytesIO(out)) as image:
            self.assertEqual((image.format, image.size), ("JPEG", (64, 48)))

    def test_damaged_photo_is_refused_in_words(self):
        with self.assertRaises(frappe.ValidationError) as caught:
            heic.to_jpeg(SAMPLE[:120])
        self.assertIn("JPEG", str(caught.exception))

    @unittest.skipUnless(HAS_DECODER, "pi-heif 只在新镜像里有")
    def test_native_uploads_are_converted_before_they_are_stored(self):
        doc = SimpleNamespace(content=SAMPLE, decode=False, file_name="IMG_0001.HEIC")
        heic.convert_file(doc)
        self.assertEqual((doc.content[:3], doc.file_name, doc.decode), (b"\xff\xd8\xff", "IMG_0001.jpg", False))
        encoded = SimpleNamespace(content="data:image/heic;base64," + base64.b64encode(SAMPLE).decode(), decode=True,
                                  file_name="photo.heic")
        heic.convert_file(encoded)
        self.assertEqual((encoded.content[:3], encoded.file_name, encoded.decode), (b"\xff\xd8\xff", "photo.jpg", False))

    def test_other_files_are_left_alone(self):
        jpeg = SimpleNamespace(content=b"\xff\xd8\xff\xe0" + b"\0" * 40, decode=False, file_name="a.heic")
        heic.convert_file(jpeg)
        self.assertEqual(jpeg.file_name, "a.heic")
        by_url = SimpleNamespace(content=None, file_name="b.heic")
        heic.convert_file(by_url)
        self.assertEqual(by_url.file_name, "b.heic")
        text = SimpleNamespace(content="hello", decode=False, file_name="note.txt")
        heic.convert_file(text)
        self.assertEqual(text.content, "hello")

    def test_wired_into_every_product_and_the_image(self):
        hooks = (ROOT / "apps/pearnly_core/pearnly_core/hooks.py").read_text()
        self.assertIn('"before_insert": "pearnly_core.desk.heic.convert_file"', hooks)
        self.assertIn('"pi-heif==1.4.0"', (ROOT / "infrastructure/images/frappe.Dockerfile").read_text())
        service = (ROOT / "apps/pearnly_cowork/pearnly_cowork/files/service.py").read_text()
        self.assertIn("filename, content = _heic_as_jpeg(filename, content)", service)
        self.assertIn('return {"direct": 0}', service.split("def begin_direct", 1)[1].split("_direct_target", 1)[0])
        # ERP 识别前的转 JPEG 在采购 / 销售共用的取原件那一步（统一原件层，ADR-024）；附件新上传时先转好再交原件层
        reading = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/recognition/originals.py").read_text()
        self.assertIn("heic.to_jpeg(content)", reading.split("def contents", 1)[1])
        for product in ("sales", "purchasing"):
            source = (ROOT / f"apps/pearnly_erp_portal/pearnly_erp_portal/{product}/recognition.py").read_text()
            self.assertIn("originals.contents(chosen)", source)
        registration = (ROOT / "apps/pearnly_core/pearnly_core/originals/registered.py").read_text()
        self.assertIn("heic.to_jpeg(content)", registration.split("def put", 1)[1].split("def create", 1)[0])


if __name__ == "__main__":
    unittest.main()
