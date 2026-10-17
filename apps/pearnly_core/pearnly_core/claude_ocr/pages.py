"""原件 → 交给 Claude 的页（ADR-022，纯计算）：站点侧客户端与甲骨文服务（按原件地址自己读时）同一份。

每页先转正（与谷歌路线同一个 ``ocr_models.orient``：EXIF + 本地方向模型，免费、约 15 毫秒；转不了就原样送，绝不挡识别），
再缩到长边 2000、JPEG（评测同一口径）；PDF 每页按 170 DPI 渲染，最多 ``engine.MAX_PAGES`` 页。
"""
import io

from pearnly_core.claude_ocr import engine

LONG_EDGE = 2000
PDF_DPI = 170


def _upright(data, media_type, skipped=None):
    from pearnly_core.ocr_models import orient

    return orient.orient(data, media_type, skipped=skipped)


def _save(img):
    img.thumbnail((LONG_EDGE, LONG_EDGE))
    buffer = io.BytesIO()
    img.save(buffer, "JPEG", quality=85)
    return ("image/jpeg", buffer.getvalue())


def jpeg(data, media_type, skipped=None):
    """一个文件 → 按页的 ``[(media_type, bytes)]``。"""
    from PIL import Image, ImageOps

    out = []
    if media_type == "application/pdf":
        import pymupdf

        with pymupdf.open(stream=data, filetype="pdf") as document:
            for page in list(document)[:engine.MAX_PAGES]:
                png, _mime = _upright(page.get_pixmap(dpi=PDF_DPI).tobytes("png"), "image/png", skipped)
                out.append(_save(Image.open(io.BytesIO(png)).convert("RGB")))
        return out
    data, media_type = _upright(data, media_type, skipped)
    return [_save(ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB"))]


def prepare(files, skipped=None):
    """files：[(bytes, media_type), ...]（产品原有的「字节 + 类型」次序）→ 按页的 JPEG，最多 ``engine.MAX_PAGES`` 页。"""
    pages = []
    for data, media_type in files:
        pages.extend(jpeg(data, media_type, skipped))
    return pages[:engine.MAX_PAGES]


def media_type_of(name):
    """原件文件名 → 类型（甲骨文按原件地址读时用；只认 Cowork 直传收的 PDF 与图片）。"""
    suffix = str(name or "").lower().rsplit(".", 1)[-1]
    return {"pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "webp": "image/webp", "gif": "image/gif", "bmp": "image/bmp", "tif": "image/tiff",
            "tiff": "image/tiff"}.get(suffix, "")
