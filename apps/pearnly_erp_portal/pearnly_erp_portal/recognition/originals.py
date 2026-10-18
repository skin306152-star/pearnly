"""采购 / 销售单据原件的上传（ADR-024 统一原件层）：网页上传附件不再走 Frappe 原生 ``upload_file`` 存本机，按租户路线存原件层
（选了 Claude → 甲骨文、谷歌租户 → 谷歌存储），本机只留登记 File（``pearnly_core.originals.registered``）；之后挂附件、识别、
预览、下载都照旧按 File 名。

- 直传：``begin`` 签一个上传地址，浏览器把原件直接 PUT 到存放处，``finish`` 请存放处读一遍（类型、页数、sha256）后登记。
- 经服务器（iPhone 照片要先转 JPEG、站点没配原件层）：``upload`` 收 multipart，交原件层存；没配原件层照旧建本机私有 File。
- 原件 id ``ERO-`` + 48 位十六进制，由「站点 + 本人 + 采购 / 销售 + 页面给的幂等键」算出：同一份重试不会存两份，别人猜不出。
"""
import hashlib
import re

import frappe
from frappe import _
from pearnly_core.originals import registered, store
from pearnly_core.originals.rules import IMAGE_EXTENSIONS, file_extension

from pearnly_erp_portal.access import guard

CAPABILITY = {"purchase": "purchase.write", "sales": "sales.write"}
KINDS = {**{suffix: "image" for suffix in IMAGE_EXTENSIONS}, ".pdf": "pdf"}
MAX_BYTES = 20 * 1024 * 1024  # 与页面上传框的上限一致
KEY = re.compile(r"[A-Za-z0-9-]{8,64}")
HEIC = (".heic", ".heif")


def _original_id(direction, request_key):
    if direction not in CAPABILITY:
        frappe.throw(_("Invalid value"))
    guard.require(CAPABILITY[direction])
    if not isinstance(request_key, str) or not KEY.fullmatch(request_key):
        frappe.throw(_("Invalid value"))
    seed = f"{frappe.local.site}|{frappe.session.user}|{direction}|{request_key}"
    return "ERO-" + hashlib.sha256(seed.encode()).hexdigest()[:48]


def _reply(file):
    return {"name": file.name, "file_url": file.file_url, "file_name": registered.filename(file)}


def _existing(original_id):
    """这个键已经登记过（页面重试）：本人的那条登记。"""
    name = frappe.db.get_value("File", {"file_name": original_id + ".bin", "owner": frappe.session.user}, "name")
    return frappe.get_doc("File", name) if name else None


def _size(byte_size):
    try:
        size = int(byte_size)
    except (TypeError, ValueError):
        frappe.throw(_("Invalid value"))
    if not 0 < size <= MAX_BYTES:
        frappe.throw(_("This document is too large"))
    return size


def begin(direction, filename, byte_size, request_key):
    """直传第一步：``{"direct": 1, "url", "headers"}``；已登记过回 ``{"direct": 1, "done"}``；不直传（iPhone 照片、站点没配
    原件层）回 ``{"direct": 0}``，页面改用 :func:`upload`。"""
    original_id, size = _original_id(direction, request_key), _size(byte_size)
    suffix = file_extension(filename)
    if suffix not in KINDS:
        return {"direct": 0}
    existing = _existing(original_id)
    if existing:
        return {"direct": 1, "done": _reply(existing)}
    return store.begin_upload(original_id, suffix, size)


def finish(direction, filename, byte_size, request_key):
    """直传第二步：存放处读一遍、核对与声明的一致后登记。存放处一时忙 / 连不上回 ``{"retry": 秒}``（页面过几秒再来）。"""
    from pearnly_core.execution.errors import Deferred

    original_id, size = _original_id(direction, request_key), _size(byte_size)
    suffix = file_extension(filename)
    existing = _existing(original_id)
    if existing:
        return _reply(existing)
    if suffix not in KINDS or store.where() == store.LOCAL:
        frappe.throw(_("Invalid file"))
    try:
        result = store.finish_upload(original_id, suffix, filename)
    except Deferred as error:
        return {"retry": max(1, int(error.delay or 5))}
    if result["status"] != "ok":
        frappe.throw(_("Invalid file"))
    meta = result["meta"]
    if (meta.get("filename"), meta.get("suffix"), meta.get("byte_size"), meta.get("kind")) != (
            filename, suffix, size, KINDS[suffix]):
        store.delete([result["ref"]])  # 传上去的与声明的不一样：不登记，删掉（同一个键才能重传）
        frappe.throw(_("Invalid file"))
    return _reply(registered.create(original_id, filename, result))


def upload(direction, request_key):
    """经服务器上传一份（multipart 字段 ``file``）：交原件层存（iPhone 照片先转 JPEG）；站点没配原件层照旧建本机私有 File。"""
    from pearnly_core.execution.errors import Deferred

    original_id = _original_id(direction, request_key)
    sent = (frappe.request.files or {}).get("file") if frappe.request else None
    if sent is None:
        frappe.throw(_("File not found"))
    filename, content = sent.filename or "document", sent.stream.read(MAX_BYTES + 1)
    _size(len(content))
    if file_extension(filename) not in KINDS and not filename.lower().endswith(HEIC):
        frappe.throw(_("Only an image or a PDF can be recognised"))
    existing = _existing(original_id)
    if existing:
        return _reply(existing)
    try:
        stored = registered.put(original_id, filename, content)
    except Deferred:
        frappe.throw(_("The file could not be saved. Please try again shortly."))
    if stored is None:  # 这个站点没配原件层
        local = frappe.get_doc({"doctype": "File", "file_name": filename, "is_private": 1, "content": content})
        return _reply(local.insert(ignore_permissions=True))
    if isinstance(stored, dict):
        frappe.throw(_("Invalid file"))
    return _reply(stored)


# ── 识别时读原件 ─────────────────────────────────────────────────────────────


def claude_originals(files):
    """选了 Claude、要读的几份都在甲骨文：交识别带的 ``[(对象名, sha256)]``（甲骨文自己读、转正、渲染，不经过这台）；
    否则 ``None``（照旧取回字节在这台转正）。"""
    from pearnly_core.claude_ocr import client

    if not files or not client.uses_claude():
        return None
    refs = [registered.stored(file) for file in files]
    return refs if all(ref and len(ref) == 2 for ref in refs) else None


def contents(chosen):
    """``[(File, 类型)]`` → 识别要的 ``[(字节, 类型)]`` 与各份字节的 sha256（原件在原件层的从那边取回；存本机的旧 iPhone 照片读之前
    转成 JPEG，``pearnly_core.desk.heic``）。"""
    from pearnly_core.desk import heic

    images, hashes = [], []
    for file, mime in chosen:
        content = registered.content(file)
        if heic.is_heic(content):
            content, mime = heic.to_jpeg(content), "image/jpeg"
        images.append((content, mime))
        hashes.append(hashlib.sha256(content).hexdigest())
    return images, hashes
