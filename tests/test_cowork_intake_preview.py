"""录入工作台原件预览：手机大照片（2400 万 / 4800 万像素 JPEG）也要能预览（2026-09-30 测试站点 IMG_3134.JPG
5712×4284 原来被 1600 万像素的安全上限拒掉，页面显示「无法加载预览」）。离线：不连站点。"""
import io
import unittest
from types import SimpleNamespace
from unittest import mock

from PIL import Image

from pearnly_cowork.files import service


def encoded(size, image_format):
	stream = io.BytesIO()
	Image.new("RGB", size, (240, 240, 235)).save(stream, format=image_format)
	return stream.getvalue()


class PhotoPreview(unittest.TestCase):
	def page(self, data):
		source = SimpleNamespace(kind="image", page_count=1)
		history = SimpleNamespace(source_file="S1")
		with mock.patch.object(service.scope, "require_read"), \
				mock.patch.object(service.access, "require_page"), \
				mock.patch.object(service.frappe, "get_doc", side_effect=lambda doctype, name: history
					if doctype == service.access.HISTORY else source), \
				mock.patch.object(service, "read_bytes", return_value=data), \
				mock.patch.object(service.frappe, "throw", side_effect=ValueError), \
				mock.patch.object(service, "_", side_effect=lambda text: text):
			return service.page("H1", 1)

	def test_a_large_phone_photo_is_previewed_at_2048(self):
		png = self.page(encoded((5000, 4000), "JPEG"))
		with Image.open(io.BytesIO(png)) as image:
			self.assertEqual(image.format, "PNG")
			self.assertLessEqual(max(image.size), 2048)

	def test_an_oversized_non_jpeg_image_is_still_refused(self):
		with self.assertRaises(ValueError):
			self.page(encoded((5000, 4000), "PNG"))


if __name__ == "__main__":
	unittest.main()
