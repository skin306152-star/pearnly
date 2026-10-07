"""上传前在浏览器里把照片缩到识别用得上的大小（所有租户都开，不按租户开关：用户 2026-10-07；
2026-10-01 起先在测试站点对比后，原先是逐站打开的开关，新开户不会写）。

识别本来就把照片缩到长边 ``OCR_IMG_MAX_LONG_EDGE``（2400）再读，手机原图（12–48MP、5–15MB）多出来的像素最后都被丢掉，
只白占上传和服务器解码的内存与 CPU。录入工作台与 ERP 采购 / 销售录入在浏览器里先按 EXIF 转正、长边大于 2400
才缩到 2400（高质量缩放），存成 JPEG（质量 0.92）再上传；PDF、表格、文档不动。代价：「下载原件」拿到的是缩小后的版本。
"""
import frappe

MAX_LONG_EDGE = 2400
JPEG_QUALITY = 0.92


def boot_session(boot):
    # boot 失败会让整个 Desk 打不开：这里绝不往外抛（与站点货币那份同一口径）。
    try:
        boot.pearnly_shrink_photos = {"max_long_edge": MAX_LONG_EDGE, "quality": JPEG_QUALITY}
    except Exception:
        frappe.log_error(title="pearnly_core.desk.upload.boot_session 失败")
