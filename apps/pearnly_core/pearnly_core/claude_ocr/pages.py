"""原件 → 交给 Claude 的页（ADR-022，纯计算）：站点侧客户端与甲骨文服务（按原件地址自己读时）同一份。

每页先转正（与谷歌路线同一个 ``ocr_models.orient``：EXIF + 本地方向模型，免费、约 15 毫秒；转不了就原样送，绝不挡识别），
再缩到长边 2000、JPEG（评测同一口径）；PDF 每页按 170 DPI 渲染。长文件按段读（``segments``），每段只渲染这一段的页。
"""
import io

LONG_EDGE = 2000
PDF_DPI = 170


def upright(data, media_type, skipped=None):
    """转正（EXIF + 方向模型，同 ``pearnly_core.ocr.orient``）→ ``(字节, 类型)``。"""
    from pearnly_core.ocr_models import orient

    return orient.orient(data, media_type, skipped=skipped)



def _save(img):
    img.thumbnail((LONG_EDGE, LONG_EDGE))
    buffer = io.BytesIO()
    img.save(buffer, "JPEG", quality=85)
    return ("image/jpeg", buffer.getvalue())


def count(data, media_type):
    """一个文件有几页：PDF 按页数，图片算一页。"""
    if media_type != "application/pdf":
        return 1
    import pymupdf

    with pymupdf.open(stream=data, filetype="pdf") as document:
        return len(document)


def jpeg(data, media_type, skipped=None, first=1, last=None):
    """一个文件 → 按页的 ``[(media_type, bytes)]``；``first`` / ``last``：只出这几页（从 1 数，含两头，``last`` 为 None 到末页）。"""
    from PIL import Image, ImageOps

    out = []
    if media_type == "application/pdf":
        import pymupdf

        with pymupdf.open(stream=data, filetype="pdf") as document:
            for page in list(document)[first - 1:last]:
                png, _mime = upright(page.get_pixmap(dpi=PDF_DPI).tobytes("png"), "image/png", skipped)
                out.append(_save(Image.open(io.BytesIO(png)).convert("RGB")))
        return out
    if first > 1 or last == 0:
        return out
    data, media_type = upright(data, media_type, skipped)
    return [_save(ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB"))]


def total(files):
    """files：[(bytes, media_type), ...] → 几份合起来一共几页。"""
    return sum(count(data, media_type) for data, media_type in files)


def prepare(files, skipped=None, first=1, last=None):
    """files：[(bytes, media_type), ...]（产品原有的「字节 + 类型」次序）→ 按页的 JPEG；``first`` / ``last``：几份按次序接起来后的
    第几页到第几页（从 1 数，含两头，``last`` 为 None 到末页），只渲染这几页。"""
    pages, offset = [], 0
    for data, media_type in files:
        if last is not None and offset >= last:
            break
        size = count(data, media_type)
        if offset + size >= first:
            pages.extend(jpeg(data, media_type, skipped, max(1, first - offset), None if last is None else last - offset))
        offset += size
    return pages


# 对账文档逐页（与 Cowork ``ocr.inputs_runtime.PageSource.frame`` 同一做法，那边是生产逐页渲染、闸门用的那一份）
FRAME_DPI = 200
MAX_FRAME_PIXELS = 40_000_000


def frame(data, media_type, number, skipped=None):
    """对账文档的一页 → ``(bytes, media_type)``：PDF 这一页按 200 DPI 渲染成 PNG、照片原样，再转正；之后同样经 :func:`prepare`。
    甲骨文按原件地址逐页读对账文档时用，出的图与 ``PageSource.frame`` 逐字节相同（tests/test_claude_ocr_pages.py）。
    页号不对、页面太大抛 ``ValueError``。"""
    import math

    from PIL import Image

    if media_type == "application/pdf":
        import pymupdf

        with pymupdf.open(stream=data, filetype="pdf") as document:
            if type(number) is not int or not 1 <= number <= len(document):
                raise ValueError("bad page")
            page = document.load_page(number - 1)
            scale = FRAME_DPI / 72.0
            if math.ceil(page.rect.width * scale) * math.ceil(page.rect.height * scale) > MAX_FRAME_PIXELS:
                raise ValueError("page too large")
            data = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")
        media_type = "image/png"
    else:
        if number != 1:
            raise ValueError("bad page")
        with Image.open(io.BytesIO(data)) as image:
            media_type = "image/" + (image.format or "png").lower()
    return upright(data, media_type, skipped)


def media_type_of(name):
    """原件文件名 → 类型（甲骨文按原件地址读时用；只认 Cowork 直传收的 PDF 与图片）。"""
    suffix = str(name or "").lower().rsplit(".", 1)[-1]
    return {"pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "webp": "image/webp", "gif": "image/gif", "bmp": "image/bmp", "tif": "image/tiff",
            "tiff": "image/tiff"}.get(suffix, "")
