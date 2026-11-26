# -*- coding: utf-8 -*-
"""库存停放报表的文件：本站私有 File、24 小时签名下载地址、到期真删。

生成物不是原件（ADR-024 第 6 条「生成的单据 PDF」不进原件层），一次约 0.2–0.6 MB、24 小时后删，存本站私有目录。
LINE 服务器取图片、手机点下载按钮都没有登录态，所以下载口是 ``allow_guest``，凭签名放行：签名覆盖 File 名、过期时间与
下载文件名，用本站加密密钥派生的 HMAC，跨站无效；过期、被改、文件已删一律 404。评估第 D 节。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from urllib.parse import quote, urlencode

import frappe

LINK_SECONDS = 24 * 3600
# Flex 按钮 URI 上限 1000 字符，按钮还要加 `&openExternalBrowser=1`；留余量
MAX_URL = 900
_PREFIX = "dms-stock-"
_TYPES = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          "pdf": "application/pdf", "jpg": "image/jpeg"}


def _key() -> bytes:
    from frappe.utils.password import get_encryption_key

    return hashlib.sha256(b"pearnly-dms-stock-report:" + get_encryption_key().encode()).digest()


def _signature(name: str, expires: int, filename: str) -> str:
    message = f"{name}\n{expires}\n{filename}".encode()
    return hmac.new(_key(), message, hashlib.sha256).hexdigest()[:40]


def save(content: bytes, extension: str, filename: str, batch: str) -> str:
    """存一份私有 File，返回 24 小时有效的下载地址。``batch``：同一次报表的几个文件共用的随机前缀。"""
    doc = frappe.get_doc({"doctype": "File", "file_name": f"{_PREFIX}{batch}-{secrets.token_hex(4)}.{extension}",
                          "is_private": 1, "content": content})
    doc.insert(ignore_permissions=True)
    expires = int(time.time()) + LINK_SECONDS
    base = frappe.utils.get_url().rstrip("/") + "/api/method/pearnly_dms.query.stock_files.download?"
    url = base + urlencode({"f": doc.name, "e": expires, "d": filename, "s": _signature(doc.name, expires, filename)})
    if len(url) > MAX_URL:  # 泰文文件名编码后很长：超了就不带下载文件名（下载时用通用名），不让整条回复被 LINE 拒收
        url = base + urlencode({"f": doc.name, "e": expires, "s": _signature(doc.name, expires, "")})
    return url


def new_batch() -> str:
    return secrets.token_hex(8)


def _not_found():
    from werkzeug.wrappers import Response

    return Response("Not found", status=404, mimetype="text/plain")


@frappe.whitelist(allow_guest=True, methods=["GET"])
def download(f: str = "", e: str = "", d: str = "", s: str = ""):
    """LINE 图片与卡片按钮的下载口：只认本模块签过、未过期的地址。"""
    from werkzeug.wrappers import Response

    try:
        expires = int(e)
    except (TypeError, ValueError):
        return _not_found()
    if expires < time.time() or not hmac.compare_digest(str(s or ""), _signature(str(f), expires, str(d))):
        return _not_found()
    row = frappe.db.get_value("File", str(f), ["file_name", "is_private"], as_dict=True)
    if not row or not row.is_private or not str(row.file_name or "").startswith(_PREFIX):
        return _not_found()
    content = frappe.get_doc("File", str(f)).get_content()
    extension = str(row.file_name).rsplit(".", 1)[-1]
    disposition = "inline" if extension == "jpg" else "attachment"
    headers = {"Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(str(d or row.file_name), safe='')}",
               "Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"}
    return Response(content, mimetype=_TYPES.get(extension, "application/octet-stream"), headers=headers)


def purge_expired() -> int:
    """到期真删（挂在 DMS 现有的 */5 定时上，不新增定时项）：24 小时前的报表文件连同磁盘文件一起删。"""
    cutoff = frappe.utils.add_to_date(frappe.utils.now_datetime(), seconds=-(LINK_SECONDS + 600))
    names = frappe.get_all("File", filters={"file_name": ["like", _PREFIX + "%"], "is_private": 1,
                                            "creation": ["<", cutoff]}, pluck="name", limit=500)
    for name in names:
        frappe.delete_doc("File", name, ignore_permissions=True, force=True)
    if names:
        frappe.db.commit()
    return len(names)


__all__ = ["LINK_SECONDS", "download", "new_batch", "purge_expired", "save"]
