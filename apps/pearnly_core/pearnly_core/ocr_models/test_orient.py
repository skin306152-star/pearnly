"""识别前本地转正（`ocr.orient`，ADR-013）：模型能装载、转角方向对、没把握不动、出错不挡识别。

在候选镜像的离线检查里跑（需要镜像里的 onnxruntime / numpy）；不调任何 API。
"""
import io
import unittest
from unittest.mock import Mock, patch

import frappe
from PIL import Image, ImageDraw

from pearnly_core import ocr


def jpeg(image):
    out = io.BytesIO()
    image.save(out, "JPEG")
    return out.getvalue()


def receipt():
    image = Image.new("RGB", (480, 900), "white")
    draw = ImageDraw.Draw(image)
    for row in range(30):
        draw.text((30, 20 + row * 28), f"ITEM {row:02d}   QTY 1   PRICE {row * 7 + 19}.00", fill="black")
    return image


class Orient(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(frappe, "logger", return_value=Mock(), create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_bundled_model_loads_and_scores_four_directions(self):
        angle, probability = ocr._orientation(receipt())
        self.assertIn(angle, ocr.ORIENT_LABELS)
        self.assertTrue(0 <= probability <= 1)

    def test_a_confident_label_is_turned_back(self):
        data = jpeg(receipt().rotate(90, expand=True))  # 逆时针侧拍
        with patch.object(ocr, "_orientation", return_value=(270, 0.97)):
            fixed, mime = ocr.orient(data, "image/jpeg")
        self.assertEqual((mime, Image.open(io.BytesIO(fixed)).size), ("image/jpeg", (480, 900)))

    def test_not_confident_or_broken_leaves_the_image_alone(self):
        data = jpeg(receipt())
        with patch.object(ocr, "_orientation", return_value=(0, 0.6)):
            self.assertEqual(ocr.orient(data, "image/jpeg"), (data, "image/jpeg"))
        with patch.object(ocr, "_orientation", side_effect=RuntimeError("boom")):
            self.assertEqual(ocr.orient(data, "image/jpeg"), (data, "image/jpeg"))
        self.assertEqual(ocr.orient(b"%PDF-1.7", "application/pdf"), (b"%PDF-1.7", "application/pdf"))


if __name__ == "__main__":
    unittest.main()
