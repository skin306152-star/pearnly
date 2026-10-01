"""识别前本地转正（ADR-013）：EXIF + PaddleOCR 方向分类模型（`doc_ori.onnx`，见 README.md）。

不依赖 Frappe：站点里的识别（`pearnly_core.ocr.orient` 包了一层日志）和 Cloud Run 上的识别执行（ADR-0015）
用同一份实现，同一张图转出来的结果一样。
"""
import io
import os

ORIENT_LABELS = (0, 90, 180, 270)
ORIENT_MIN_PROBABILITY = 0.9
_session = None


def upright(data: bytes, mime: str) -> tuple[bytes, str]:
    """按 EXIF 方向把照片转正；PDF、方向本来就对、没有方向信息或打不开的图原样返回。"""
    if not mime.startswith("image/"):
        return data, mime
    try:
        from PIL import Image, ImageOps

        image = Image.open(io.BytesIO(data))
        if (image.getexif().get(0x0112) or 1) == 1:
            return data, mime
        out = io.BytesIO()
        ImageOps.exif_transpose(image).convert("RGB").save(out, "JPEG", quality=92)
        return out.getvalue(), "image/jpeg"
    except Exception:
        return data, mime


def orientation(image):
    """PaddleOCR 方向分类：短边缩到 224，沿长边取上 / 中 / 下三块，三块都判成同一方向、
    且平均概率 ≥ 0.9 才算数。返回 (要逆时针转的角度, 概率)；不确定返回 (0, 概率)。"""
    import numpy
    import onnxruntime
    from PIL import Image

    global _session
    if _session is None:
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 1  # 每个 worker 一个线程，不和别的任务抢 CPU
        options.inter_op_num_threads = 1
        _session = onnxruntime.InferenceSession(os.path.join(os.path.dirname(__file__), "doc_ori.onnx"),
                                                options, providers=["CPUExecutionProvider"])
    width, height = image.size
    scale = 224 / min(width, height)
    image = image.resize((max(224, round(width * scale)), max(224, round(height * scale))), Image.BILINEAR)
    width, height = image.size
    mean = numpy.array([0.485, 0.456, 0.406], numpy.float32)
    std = numpy.array([0.229, 0.224, 0.225], numpy.float32)
    crops = []
    for fraction in (0.2, 0.5, 0.8):
        left = int((width - 224) * (fraction if width > height else 0.5))
        top = int((height - 224) * (fraction if height >= width else 0.5))
        crop = numpy.asarray(image.crop((left, top, left + 224, top + 224)), numpy.float32) / 255.0
        crops.append(((crop - mean) / std).transpose(2, 0, 1))
    session = _session
    probabilities = session.run(None, {session.get_inputs()[0].name: numpy.stack(crops)})[0]
    agreed = {int(row.argmax()) for row in probabilities}
    average = probabilities.mean(axis=0)
    best = int(average.argmax())
    probability = float(average[best])
    if len(agreed) != 1 or probability < ORIENT_MIN_PROBABILITY:
        return 0, probability
    return ORIENT_LABELS[best], probability


def orient(data: bytes, mime: str, *, classify=None, skipped=None) -> tuple[bytes, str]:
    """先按 EXIF，再按方向模型转正。``classify`` 默认 :func:`orientation`；``skipped`` 在转正出错时调用（记日志）。
    模型或 onnxruntime 不可用、图打不开时只做 EXIF 转正 —— 转正失败绝不挡识别。"""
    data, mime = upright(data, mime)
    if not mime.startswith("image/"):
        return data, mime
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(data)).convert("RGB")
        angle, _probability = (classify or orientation)(image)
        if not angle:
            return data, mime
        out = io.BytesIO()
        image.rotate(angle, expand=True).save(out, "JPEG", quality=92)
        return out.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001
        if skipped:
            skipped()
        return data, mime
