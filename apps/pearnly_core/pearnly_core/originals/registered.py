"""原件层的登记文件（ADR-024）：产品要一条 Frappe ``File`` 记录、原件却存在原件层（甲骨文 / 谷歌存储）时用——ERP 单据附件与
LINE 收单、Cowork 对账中心的临时文件。多产品共用。

``File`` 只存一条不到 1KB 的登记（固定前缀 + 原文件名 / 原件引用 / sha256 / 页数），文件名 ``<原件 id>.bin``：不按图片 / PDF
处理（Frappe 存图时会去 EXIF、按扩展名猜类型），原件 id 由产品给（Cowork ``CWS-``、ERP ``ERO-`` + 48 位十六进制）。产品照旧按
File 名挂附件、核对归属与权限；读内容一律经这里（:func:`content`、:func:`filename`、:func:`sha256`）。

- 下载：浏览器打开它的 ``file_url`` 时，按 Frappe 自己的私有文件规则核对能不能看（``find_file_by_url`` → ``is_downloadable``），
  通过就转到存放处的签名下载地址（``before_request`` 钩子 :func:`redirect_download`），页面上的链接、图片都不用改。
- 删除：File 删掉时（移除附件、单据删除连带、对账结束、LINE 收单作废）排一个后台任务删存放处那份；同一登记被复制成几条 File
  （复制附件、一张图里几张票，内容一样，Frappe 让它们共用同一个文件）时，最后一条删了才删。
- 这个站点没配原件层（:func:`store.where` 回 ``LOCAL``）：:func:`put` 回 ``None``，产品照旧存本机。
"""
import hashlib
import json
import re

import frappe
from werkzeug.exceptions import HTTPException
from werkzeug.utils import redirect as werkzeug_redirect

from pearnly_core.originals import store

PREFIX = b"PEARNLY-ORIGINAL\x00"
NAME = re.compile(r"(?:CWS|ERO)-[0-9a-f]{48}\.bin")
# Frappe 碰上同名文件会在扩展名前加一段随机字符，这里一并认
URL = re.compile(r"/private/files/(?:CWS|ERO)-[0-9a-f]{48}[0-9a-z]{0,12}\.bin")


def put(original_id, filename, content, **fields):
    """生产手上已有字节的原件（LINE 收单、对账提交的文件）：交原件层存，建登记 File 返回。没配原件层回 ``None``（调用方照旧
    存本机）；原件层的检查不收回 ``{"status": "rejected", "code"}``；存放处一时连不上抛 ``Deferred``。iPhone 照片先转 JPEG。"""
    from pearnly_core.desk import heic
    from pearnly_core.originals.rules import file_extension

    if heic.is_heic(content):
        content, filename = heic.to_jpeg(content), heic.jpeg_name(filename)
    stored = store.put(original_id, file_extension(filename), filename, content)
    if stored is None or stored["status"] != "ok":
        return stored
    return create(original_id, filename, stored, **fields)


def create(original_id, filename, stored, **fields):
    """存放处核对通过（``store.put`` / ``store.finish_upload`` 的 ``ok`` 结论）后建登记 File（私有；``fields`` 照 File 字段）。"""
    meta = stored["meta"]
    entry = {"filename": filename, "ref": stored["ref"], "sha256": meta["sha256"], "pages": int(meta["page_count"]),
             "kind": meta["kind"], "byte_size": int(meta["byte_size"])}
    return frappe.get_doc({"doctype": "File", "file_name": original_id + ".bin", "is_private": 1,
                           "content": PREFIX + json.dumps(entry, ensure_ascii=False).encode("utf-8"),
                           **fields}).insert(ignore_permissions=True)


def copy(file, **fields):
    """再建一条内容相同的 File（复制附件、一张图里几张票各挂各的）：登记就复制登记（同一份原件，不再存一次），本机文件复制字节。"""
    doc = _doc(file)
    content = doc.get_content()
    if not entry(doc):
        content = content if isinstance(content, bytes) else content.encode("utf-8")
    return frappe.get_doc({"doctype": "File", "file_name": doc.file_name, "is_private": 1, "content": content,
                           **fields}).insert(ignore_permissions=True)


def _doc(file):
    return frappe.get_doc("File", file) if isinstance(file, str) else file


def _raw(doc):
    content = doc.get_content()
    return content if isinstance(content, bytes) else content.encode("utf-8")


def entry(file):
    """登记内容（dict），不是登记文件回 ``None``（先看文件名，不读本机整份文件）。``file``：File 名或文档。"""
    file_name = frappe.db.get_value("File", file, "file_name") if isinstance(file, str) else file.file_name
    if not isinstance(file_name, str) or not NAME.fullmatch(file_name):
        return None
    content = _raw(_doc(file))
    return json.loads(content[len(PREFIX):]) if content.startswith(PREFIX) else None


def filename(file):
    """给人看、按扩展名认类型用的原文件名。"""
    doc = _doc(file)
    found = entry(doc)
    return found["filename"] if found else doc.file_name or ""


def content(file):
    """整份原件字节（在这台解析 / 交谷歌识别要用的地方）：登记的从存放处取回，本机的读盘。"""
    doc = _doc(file)
    found = entry(doc)
    return store.read(found["ref"]) if found else _raw(doc)


def sha256(file):
    doc = _doc(file)
    found = entry(doc)
    return found["sha256"] if found else hashlib.sha256(_raw(doc)).hexdigest()


def stored(file):
    """交识别带的引用：甲骨文 ``(对象名, sha256)``、谷歌存储 ``(桶, 对象名, 版本号, sha256)``；本机文件回 ``None``。"""
    found = entry(file)
    parsed = store.parse(found["ref"]) if found else None
    if not parsed:
        return None
    place, value = parsed
    return value if place == store.ORACLE else (*value, found["sha256"])


def pages(file):
    found = entry(file)
    return found["pages"] if found else None


def page_image(file, page):
    """PDF 一页的预览：甲骨文的回签名地址（那边画、存）；谷歌存储的在 Cloud Run 上画、回 ``data:`` 图；本机文件回 ``None``
    （产品照旧自己画）。页号不对抛 ``ValueError``。"""
    import base64

    found = entry(file)
    if not found:
        return None
    if not 1 <= int(page) <= found["pages"]:
        raise ValueError("page")
    url = store.preview_url(found["ref"], found["kind"], int(page))
    if url:
        return url
    drawn = store.render(found["ref"], found["sha256"], found["kind"], int(page))
    if "png" not in drawn:
        raise ValueError(drawn.get("code") or "preview")
    from pearnly_core.originals.checks import preview_type

    return f"data:{preview_type(drawn['png'])};base64," + base64.b64encode(drawn["png"]).decode("ascii")


class _Redirect(HTTPException):
    """``before_request`` 里转走：Frappe 顶层对 werkzeug 的 HTTPException 直接取响应（同 ``login_redirect``）。"""

    def __init__(self, location):
        super().__init__()
        self.location = location

    def get_response(self, environ=None, scope=None):
        response = werkzeug_redirect(self.location, 302)
        response.headers["Cache-Control"] = "private, no-store"
        return response


def redirect_download():
    """打开登记文件的 ``file_url``：按 Frappe 私有文件同样的规则核对能不能看，能看就转到存放处的签名下载地址
    （几分钟有效，带原文件名）。不能看 / 不是登记：不管，Frappe 照常处理（回无权限）。"""
    request = getattr(frappe.local, "request", None)
    if request is None or not URL.fullmatch(request.path or "") or frappe.session.user == "Guest":
        return
    from frappe.core.doctype.access_log.access_log import make_access_log
    from frappe.core.doctype.file.utils import find_file_by_url

    file = find_file_by_url(request.path, name=frappe.form_dict.fid)
    found = entry(file) if file else None
    if not found:
        return
    make_access_log(doctype="File", document=file.name, file_type="bin")
    raise _Redirect(store.download_url(found["ref"], found["filename"]))


class RegisteredFile:
    """File 的扩展（``extend_doctype_class``）：登记的最后一条删掉时，排后台任务删存放处那份（提交后才排；删不掉照任务队列
    重试 / 记失败）。要在 Frappe 删本机那份之前读出登记，所以不能用 ``on_trash`` 文档事件（那时盘上已删）。"""

    def on_trash(self):
        found = entry(self) if NAME.fullmatch(self.file_name or "") else None
        shared = found and frappe.db.count("File", {"content_hash": self.content_hash, "name": ["!=", self.name]})
        result = super().on_trash()
        if found and not shared:
            frappe.enqueue("pearnly_core.originals.registered.purge", queue="short", enqueue_after_commit=True,
                           refs=[found["ref"]])
        return result


def purge(refs):
    store.delete(refs)


__all__ = ["content", "copy", "create", "entry", "filename", "page_image", "pages", "purge", "put",
           "RegisteredFile", "redirect_download", "sha256", "stored"]
