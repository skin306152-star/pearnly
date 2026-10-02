"""iPhone 照片（HEIC / HEIF）在收文件那一步转成 JPEG，再进原有流程（2026-10-02，压测窗口转用户口径：「iPhone 默认就是 HEIC，
客户把照片传到电脑再上传就会被拒，这是真实场景」）。

三个产品统一处理：走 Frappe 原生上传的（ERP 附件、DMS、Cowork 工作附件……）由 ``File`` 的 ``before_insert`` 钩子
（:func:`convert_file`）转；Cowork 原件自己的上传口在校验类型之前调 :func:`to_jpeg`。LINE 送来的照片 LINE 已经转成 JPEG，不经这里。
解码用 ``pi-heif``（pillow-heif 的只解码版，BSD-3；自带的 libheif / libde265 是 LGPL，不带编码器），版本钉在镜像里。
按文件头判断，不按扩展名：改了名的 HEIC 也认，扩展名写 .heic 的 JPEG 不动。
"""
import io

import frappe
from frappe import _

# ISO BMFF 的主品牌：HEIC / HEIF 静态图与图片序列（不含 AVIF，那是另一种编码）
BRANDS = frozenset({b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"})
EXTENSIONS = (".heic", ".heif")
QUALITY = 92


def is_heic(content) -> bool:
    return isinstance(content, (bytes, bytearray)) and len(content) > 12 and content[4:8] == b"ftyp" \
        and bytes(content[8:12]) in BRANDS


def _opener():
    from pi_heif import register_heif_opener

    register_heif_opener()


def to_jpeg(content: bytes) -> bytes:
    """HEIC / HEIF → JPEG（按照片里记的方向转正、去掉透明通道）。读不出（损坏、截断、不是 HEIC）报人话，不抛内部异常。"""
    from PIL import Image, ImageOps

    try:
        _opener()
        with Image.open(io.BytesIO(content)) as image:
            image.load()
            picture = ImageOps.exif_transpose(image).convert("RGB")
        out = io.BytesIO()
        picture.save(out, "JPEG", quality=QUALITY)
        return out.getvalue()
    except Exception:
        frappe.throw(_("This photo could not be read. Save it as JPEG and upload it again."))


def jpeg_name(filename: str) -> str:
    """``IMG_0001.HEIC`` → ``IMG_0001.jpg``；没有 HEIC 扩展名的加上 ``.jpg``。"""
    name = filename or "photo"
    stem, dot, suffix = name.rpartition(".")
    if dot and ("." + suffix.lower()) in EXTENSIONS:
        name = stem
    return name + ".jpg"


def convert_file(doc, method=None):
    """``File.before_insert``：带内容上传的 HEIC 转成 JPEG 再存（文件名一并改成 .jpg）。只按网址登记、不带内容的不动。"""
    content = getattr(doc, "content", None)
    if not content:
        return
    if isinstance(content, str):
        if not getattr(doc, "decode", False):
            return  # 文本内容（不是上传的图片）
        import base64

        try:
            raw = base64.b64decode(content.split(",", 1)[-1])
        except Exception:
            return
    else:
        raw = bytes(content)
    if not is_heic(raw):
        return
    doc.content = to_jpeg(raw)
    doc.decode = False
    doc.file_name = jpeg_name(doc.file_name)
