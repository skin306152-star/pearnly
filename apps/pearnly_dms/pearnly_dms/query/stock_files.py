# -*- coding: utf-8 -*-
"""库存停放报表的文件：本站私有 File、24 小时签名下载地址、到期真删。

生成物不是原件（ADR-024 第 6 条「生成的单据 PDF」不进原件层），一次约 0.2–0.6 MB、24 小时后删，存本站私有目录。
LINE 服务器取图片、手机打开下载页与下载文件都没有登录态，所以下载口、下载页都是 ``allow_guest``，凭签名放行：签名覆盖 File 名、过期时间与
下载文件名，用本站加密密钥派生的 HMAC，跨站无效；过期、被改、文件已删一律 404。评估第 D 节。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import quote, urlencode

import frappe

LINK_SECONDS = 24 * 3600
# Flex 按钮 URI 上限 1000 字符，按钮还要加 `&openExternalBrowser=1`；留余量
MAX_URL = 900
_PREFIX = "dms-stock-"
_MANIFEST = "-page.json"  # 下载页清单的文件名后缀
_PAGE = "\x00page"  # 下载页签名里的标记：与文件下载的签名互不通用
_TYPES = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          "pdf": "application/pdf", "jpg": "image/jpeg"}


def _key() -> bytes:
    from frappe.utils.password import get_encryption_key

    return hashlib.sha256(b"pearnly-dms-stock-report:" + get_encryption_key().encode()).digest()


def _signature(name: str, expires: int, filename: str) -> str:
    message = f"{name}\n{expires}\n{filename}".encode()
    return hmac.new(_key(), message, hashlib.sha256).hexdigest()[:40]


def save_named(content: bytes, extension: str, filename: str, batch: str) -> dict:
    """存一份私有 File，返回 ``{"file": File 名, "url": 24 小时有效的下载地址, "size": 字节数}``。
    ``batch``：同一次报表的几个文件共用的随机前缀。"""
    doc = frappe.get_doc({"doctype": "File", "file_name": f"{_PREFIX}{batch}-{secrets.token_hex(4)}.{extension}",
                          "is_private": 1, "content": content})
    doc.insert(ignore_permissions=True)
    expires = int(time.time()) + LINK_SECONDS
    base = frappe.utils.get_url().rstrip("/") + "/api/method/pearnly_dms.query.stock_files.download?"
    url = base + urlencode({"f": doc.name, "e": expires, "d": filename, "s": _signature(doc.name, expires, filename)})
    if len(url) > MAX_URL:  # 泰文文件名编码后很长：超了就不带下载文件名（下载时用通用名），不让整条回复被 LINE 拒收
        url = base + urlencode({"f": doc.name, "e": expires, "s": _signature(doc.name, expires, "")})
    return {"file": doc.name, "url": url, "size": len(content)}


def save(content: bytes, extension: str, filename: str, batch: str) -> str:
    """存一份私有 File，返回 24 小时有效的下载地址。"""
    return save_named(content, extension, filename, batch)["url"]


def save_page(manifest: dict, batch: str) -> str:
    """存这次报表的下载页清单（标题、按钮文字、每个文件的名称 / 地址 / 大小），返回 24 小时有效的下载页地址。
    页面签名与文件签名不通用（签的是 ``_PAGE``），拿文件地址换不出页面，拿页面地址也下不了别的文件。"""
    doc = frappe.get_doc({"doctype": "File", "file_name": f"{_PREFIX}{batch}{_MANIFEST}", "is_private": 1,
                          "content": json.dumps(manifest, ensure_ascii=False).encode()})
    doc.insert(ignore_permissions=True)
    expires = int(time.time()) + LINK_SECONDS
    return (frappe.utils.get_url().rstrip("/") + "/api/method/pearnly_dms.query.stock_files.page?"
            + urlencode({"f": doc.name, "e": expires, "s": _signature(doc.name, expires, _PAGE)}))


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
    if str(row.file_name).endswith(_MANIFEST):
        return _not_found()
    content = frappe.get_doc("File", str(f)).get_content()
    extension = str(row.file_name).rsplit(".", 1)[-1]
    disposition = "inline" if extension == "jpg" else "attachment"
    headers = {"Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(str(d or row.file_name), safe='')}",
               "Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"}
    return Response(content, mimetype=_TYPES.get(extension, "application/octet-stream"), headers=headers)


def _manifest(f: str, e: str, s: str):
    """下载页：签名有效、未过期、是本模块存的清单 → (清单, 过期时间)，否则 None。"""
    try:
        expires = int(e)
    except (TypeError, ValueError):
        return None
    if expires < time.time() or not hmac.compare_digest(str(s or ""), _signature(str(f), expires, _PAGE)):
        return None
    row = frappe.db.get_value("File", str(f), ["file_name", "is_private"], as_dict=True)
    name = str((row or {}).get("file_name") or "")
    if not row or not row.is_private or not name.startswith(_PREFIX) or not name.endswith(_MANIFEST):
        return None
    content = frappe.get_doc("File", str(f)).get_content()
    try:
        return json.loads(content.decode() if isinstance(content, bytes) else content), expires
    except ValueError:
        return None


def _picked(k: str, count: int):
    """``k``：清单序号（逗号分隔）→ 排好序的序号；空 / 越界 / 不是数字 → None。"""
    try:
        picked = sorted({int(part) for part in str(k).split(",") if part.strip()})
    except ValueError:
        return None
    if not picked or picked[0] < 0 or picked[-1] >= count:
        return None
    return picked


@frappe.whitelist(allow_guest=True, methods=["GET"])
def page(f: str = "", e: str = "", s: str = "", k: str = ""):
    """卡片「下载文件」打开的页面：勾选文件后分享（手机系统分享面板）或下载；只认本模块签过、未过期的地址。
    ``k``：只列这几个序号的文件（页面里「分享」出去的地址带着当时勾选的那几个）；不带就是全部。"""
    from frappe.utils.jinja_globals import bundled_asset
    from werkzeug.wrappers import Response

    from pearnly_dms.query.stock_page import html_page

    found = _manifest(f, e, s)
    if not found:
        return _not_found()
    manifest, _expires = found
    only = None
    if str(k or "").strip():
        only = _picked(k, len(manifest.get("files") or []))
        if only is None:
            return _not_found()
    base = frappe.utils.get_url().rstrip("/") + "/api/method/pearnly_dms.query.stock_files."
    signed = urlencode({"f": f, "e": e, "s": s})
    html = html_page(manifest, bundled_asset("website.bundle.css"), base + "page?" + signed, only)
    return Response(html, mimetype="text/html",
                    headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                             "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"})


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


__all__ = ["LINK_SECONDS", "download", "new_batch", "page", "purge_expired", "save", "save_named", "save_page"]
