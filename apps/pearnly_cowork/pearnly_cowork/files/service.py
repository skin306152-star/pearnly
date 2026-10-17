"""Private originals: native File storage, or the Singapore originals bucket for browser direct uploads (ADR-016).

No OCR or legacy blob import here; Cloud Run only checks / previews bucket originals on request."""
import hashlib
import json
import logging
import re
import threading

import frappe
from frappe import _
from frappe.utils import cint

from pearnly_cowork.access import scope
from pearnly_cowork.files import access, cloud, edge, edge_links, guard
from pearnly_cowork.ocr import originals
from pearnly_cowork.ocr.inputs import IMAGE_EXTENSIONS, MAX_FILE_BYTES as MAX_BYTES, OcrInputError

logger = logging.getLogger(__name__)


# 原件检查的错误代码（``ocr.originals``，服务器与 Cloud Run 共用）→ 原来的提示文字。
MESSAGES = {
    "invalid_filename": lambda: _("Invalid original filename."),
    "unsupported_type": lambda: _("This file type is not supported."),
    "empty": lambda: _("The uploaded file is empty."),
    "too_large": lambda: _("The uploaded file exceeds the 100 MiB limit."),
    "pdf_password": lambda: _("Password-protected PDF files cannot be read."),
    "pdf_unreadable": lambda: _("The PDF file cannot be read."),
    "pdf_pages": lambda: _("PDF files must contain between 1 and 50 pages."),
    "image_unreadable": lambda: _("The image file cannot be read."),
    "preview_too_large": lambda: _("This page is too large to preview safely."),
    "preview_unsupported": lambda: _("Page preview is available only for PDF and image originals."),
}


def reject(code):
    """原件检查不通过：``ocr.originals`` 的代码给原来的提示，``OcrInputError`` 的代码原样翻译。"""
    message = MESSAGES.get(code)
    frappe.throw(message() if message else _(code))


def inspect_content(filename, content):
    try:
        return originals.inspect(filename, content)
    except originals.Rejected as error:
        reject(error.code)
    except OcrInputError as error:
        reject(error.code)


def _require_key(request_key):
    if not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 128:
        frappe.throw(_("A request key of 1 to 128 characters is required."))


def _request_key(workspace, request_key):
    return hashlib.sha256(json.dumps([workspace, frappe.session.user, request_key]).encode()).hexdigest()


def _fingerprint(client, meta):
    return hashlib.sha256(json.dumps([client or None, meta], sort_keys=True).encode()).hexdigest()


def _existing(name, fingerprint, *, filename=None, byte_size=None):
    """同一个幂等键已经登记过：内容相同才给出原记录。直传在登记前只知道文件名和大小（``fingerprint`` 为 ``None``），
    就按这两项核对，换了文件照样拒绝。"""
    doc = frappe.get_doc(access.SOURCE, name, for_update=True)
    access.require_full(doc)
    if fingerprint is not None and doc.request_fingerprint != fingerprint:
        frappe.throw(_("This request key has already been used for different content."))
    if fingerprint is None and (doc.filename != filename or int(doc.byte_size or 0) != int(byte_size)):
        frappe.throw(_("This request key has already been used for different content."))
    return doc


def _new_source(name, key, workspace, client, fingerprint, meta, **stored):
    doc = frappe.get_doc({"doctype": access.SOURCE, "name": name, "workspace": workspace, "client": client or "",
        "uploaded_by": frappe.session.user, "request_key": key, "request_fingerprint": fingerprint,
        **{field: value for field, value in meta.items() if field != "suffix"}, **stored})
    doc.flags.cowork_original_write = guard.WRITE_CAPABILITY
    # Permission was checked above. Native generic creation must remain unavailable.
    doc.insert(ignore_permissions=True)
    return doc


def attachments_folder():
    """挂在单据上的 File 放进的那个文件夹（与 Frappe ``File.set_folder_name`` 取的同一个），按站点查一次后缓存。

    Frappe 每插入一个挂单据的 File 都按创建时间倒序查一遍 ``is_attachments_folder``，tabFile 上没有这个索引
    （2026-10-01 生产压测：484 次、平均 0.23 秒）。这个文件夹删不掉（``File.on_trash`` 拒绝），缓存不会过期失效。"""
    return frappe.cache.get_value("cowork:attachments-folder", generator=lambda: frappe.db.get_value(
        "File", {"is_attachments_folder": 1}))


def create(workspace, filename, content, request_key, client=None):
    scope.require("intake")
    scope.require_scope(workspace, client)
    _require_key(request_key)
    filename, content = _heic_as_jpeg(filename, content)
    meta = inspect_content(filename, content)
    key = _request_key(workspace, request_key)
    name = "CWS-" + key[:48]
    fingerprint = _fingerprint(client, meta)
    if frappe.db.exists(access.SOURCE, name):
        return project(_existing(name, fingerprint))
    doc = _new_source(name, key, workspace, client, fingerprint, meta)
    file_doc = frappe.get_doc({"doctype": "File", "file_name": name + meta["suffix"],
        "is_private": 1, "content": content, "owner": "Administrator", "folder": attachments_folder(),
        "attached_to_doctype": access.SOURCE, "attached_to_name": doc.name})
    file_doc.flags.cowork_original_write = guard.WRITE_CAPABILITY
    file_doc.insert(ignore_permissions=True)
    doc.stored_file, doc.storage_url = file_doc.name, file_doc.file_url
    doc.native_content_hash = file_doc.content_hash
    doc.save(ignore_permissions=True)
    read_bytes(doc)
    return project(doc)


def _heic_as_jpeg(filename, content):
    """iPhone 照片（HEIC / HEIF，按文件头认）先转成 JPEG 再进原有校验与识别（`pearnly_core.desk.heic`）。"""
    from pearnly_core.desk import heic

    if heic.is_heic(content):
        return heic.jpeg_name(filename), heic.to_jpeg(content)
    return filename, content


# 浏览器直传（ADR-016）：只收 PDF / 图片（表格、Word 要在服务器上解析，照旧经服务器上传）。
# HEIC 不直传：浏览器原样 PUT 到原件桶就没法先转 JPEG，所以照旧经服务器上传（`create` 里转）。
DIRECT_KINDS = IMAGE_EXTENSIONS | {".pdf"}


def _direct_target(workspace, filename, byte_size, request_key, client):
    """直传前的公共检查：权限、幂等键、文件名 / 类型 / 声明的大小。返回 ``(原件名, 幂等键摘要, 文件名, 扩展名)``。"""
    scope.require("intake")
    scope.require_scope(workspace, client)
    _require_key(request_key)
    try:
        filename = originals.filename_of(filename)
        suffix = originals.suffix_of(filename)
    except originals.Rejected as error:
        reject(error.code)
    if isinstance(byte_size, str) and byte_size.isdigit():
        byte_size = int(byte_size)
    if type(byte_size) is not int or byte_size < 1:
        reject("empty")
    if byte_size > MAX_BYTES:
        reject("too_large")
    key = _request_key(workspace, request_key)
    return "CWS-" + key[:48], key, filename, suffix


def begin_direct(workspace, filename, byte_size, request_key, client=None):
    """浏览器直传第一步：签发上传地址。没开直传或不是 PDF / 图片返回 ``{"direct": 0}``，页面照旧经服务器上传。

    同一个幂等键已经登记过就直接给出原件信息（``done``），页面不用再传。"""
    from pearnly_core.desk import heic

    if str(filename or "").lower().endswith(heic.EXTENSIONS):
        return {"direct": 0}
    name, _key, filename, suffix = _direct_target(workspace, filename, byte_size, request_key, client)
    if suffix not in DIRECT_KINDS:
        return {"direct": 0}
    if _claude_site():
        # 选了 Claude 识别：原件直传甲骨文（ADR-023），不用谷歌存储；没配甲骨文原件存储才照旧经服务器上传。
        if not edge.enabled():
            return {"direct": 0}
        if frappe.db.exists(access.SOURCE, name):
            return {"direct": 1, "done": project(_existing(name, None, filename=filename, byte_size=byte_size))}
        return {"direct": 1, **edge.upload_target(edge_links.object_name(frappe.local.site, name, suffix), int(byte_size))}
    bucket_name = cloud.bucket()
    if not bucket_name or not _remote_ready():
        return {"direct": 0}
    if frappe.db.exists(access.SOURCE, name):
        return {"direct": 1, "done": project(_existing(name, None, filename=filename, byte_size=byte_size))}
    target = cloud.upload_target(bucket_name, cloud.object_name(frappe.local.site, name, suffix), int(byte_size))
    return {"direct": 1, **target}


# 网页进程里同时等 Cloud Run 核对原件 / 现画页图的上限（每进程）：超出的直传确认让页面过几秒再来（页面本来就认 ``retry``），
# 页图稍等片刻、还轮不到就回「稍后再试」（页面会自动再要几次）。不让这两件事占满网页线程（2026-10-01 复查：4 个人同时批量
# 上传就能占满 24 个网页线程，连只做 302 的请求都要排队）。
INSPECT_SLOTS = threading.BoundedSemaphore(4)
RENDER_SLOTS = threading.BoundedSemaphore(2)
RENDER_WAIT_SECONDS = 10


def finish_direct(workspace, filename, byte_size, request_key, client=None):
    """浏览器直传第二步：Cloud Run 读一遍原件（类型、页数、sha256，同 :func:`inspect_content`），通过就登记。"""
    from pearnly_core.execution.errors import Deferred
    from pearnly_cowork.ocr.remote import forward

    name, key, filename, suffix = _direct_target(workspace, filename, byte_size, request_key, client)
    if _claude_site():
        return _finish_edge(name, key, workspace, client, filename, suffix, byte_size)
    bucket_name = cloud.bucket()
    if not bucket_name or suffix not in DIRECT_KINDS or not _remote_ready():
        frappe.throw(_("Direct upload is not available. Please upload again."))
    if frappe.db.exists(access.SOURCE, name):
        return project(_existing(name, None, filename=filename, byte_size=byte_size))
    object_name = cloud.object_name(frappe.local.site, name, suffix)
    if not INSPECT_SLOTS.acquire(blocking=False):
        return {"retry": 2}  # 这个进程已有 4 份在等核对：页面过两秒再来（核对内容一点不放宽）
    try:
        result = forward.inspect_original(object_name, filename)
    except Deferred as error:
        return {"retry": max(1, int(error.delay or 5))}  # Cloud Run 一时连不上 / 忙：页面过几秒再来确认
    finally:
        INSPECT_SLOTS.release()
    if result["status"] != "ok":
        if result.get("code") == "original_missing":
            _not_received(name, "original_missing")
        reject(result.get("code"))
    meta = result["meta"]
    expected = {"filename": filename, "suffix": suffix, "byte_size": int(byte_size)}
    differing = sorted(field for field, value in expected.items() if meta.get(field) != value)
    if meta.get("kind") not in ("pdf", "image"):
        differing.append("kind")
    if differing:
        _not_received(name, "meta_mismatch:" + ",".join(differing))
    fingerprint = _fingerprint(client, meta)
    if frappe.db.exists(access.SOURCE, name):
        return project(_existing(name, fingerprint))
    doc = _new_source(name, key, workspace, client, fingerprint, meta,
                      storage_url=cloud.url_of(bucket_name, object_name, result["generation"]),
                      native_content_hash=result["md5"])
    return project(doc)


def _finish_edge(name, key, workspace, client, filename, suffix, byte_size):
    """直传甲骨文的第二步（ADR-023）：请甲骨文读一遍（类型、页数、sha256，同 :func:`inspect_content`），通过就登记。
    甲骨文一时连不上 / 忙：页面过几秒再来确认（页面本来就认 ``retry``），不退回生产存。"""
    from pearnly_core.execution.errors import Deferred

    if not edge.enabled() or suffix not in DIRECT_KINDS:
        frappe.throw(_("Direct upload is not available. Please upload again."))
    if frappe.db.exists(access.SOURCE, name):
        return project(_existing(name, None, filename=filename, byte_size=byte_size))
    object_name = edge_links.object_name(frappe.local.site, name, suffix)
    if not INSPECT_SLOTS.acquire(blocking=False):
        return {"retry": 2}
    try:
        result = edge.inspect(object_name, filename)
    except Deferred as error:
        return {"retry": max(1, int(error.delay or 5))}
    finally:
        INSPECT_SLOTS.release()
    if result.get("status") != "ok":
        if result.get("code") == "original_missing":
            _not_received(name, "original_missing")
        reject(result.get("code"))
    meta = result["meta"]
    expected = {"filename": filename, "suffix": suffix, "byte_size": int(byte_size)}
    differing = sorted(field for field, value in expected.items() if meta.get(field) != value)
    if meta.get("kind") not in ("pdf", "image"):
        differing.append("kind")
    if differing:
        _not_received(name, "meta_mismatch:" + ",".join(differing))
    fingerprint = _fingerprint(client, meta)
    if frappe.db.exists(access.SOURCE, name):
        return project(_existing(name, fingerprint))
    doc = _new_source(name, key, workspace, client, fingerprint, meta,
                      storage_url=edge_links.url_of(object_name, meta["sha256"]), native_content_hash=result["md5"])
    return project(doc)


def _claude_site():
    from pearnly_core.claude_ocr import client as claude_ocr

    return claude_ocr.uses_claude()


def edge_original(source):
    """原件存在甲骨文（ADR-023）返回 ``(对象名, sha256)``，否则 ``None``。"""
    if getattr(source, "stored_file", None):
        return None
    return edge_links.parse_url(getattr(source, "storage_url", None))


def _not_received(name, reason):
    """两种"没收到"（Cloud Run 读不到对象 / 读到的和声明的对不上）对用户是同一句话，日志里分开记原因与
    对不上的字段名（不记文件内容、文件名），压测里约 1% 的这类失败才查得出是哪一种（2026-10-01）。"""
    logger.warning("direct upload not received: %s %s", name, reason)
    frappe.throw(_("The uploaded file was not received. Please upload again."))


def _remote_ready():
    from pearnly_cowork.ocr.remote import forward

    return forward.settings() is not None


def stored_original(source):
    """原件存在原件桶里（浏览器直传）返回 ``(桶, 对象, 版本号)``，存在服务器上返回 ``None``。"""
    if getattr(source, "stored_file", None):
        return None
    return cloud.parse_url(getattr(source, "storage_url", None))


def verify_stored(source):
    """登记识别结果前核对原件还是那一份。原件桶里的按版本号存（同一版本内容不会变，登记时已由 Cloud Run 核对过
    sha256），不再整份下载；服务器上的照旧读一遍核对。"""
    if stored_original(source) or edge_original(source):
        return
    read_bytes(source)


def project(doc):
    result = {key: doc.get(key) for key in ("name", "workspace", "client", "filename", "kind",
                                          "page_count", "byte_size", "sha256", "uploaded_by")}
    kind = access.reference_kind(doc)
    result.update(reference_kind=kind, reference_count=access.reference_count(doc),
                  coverage_complete=bool(doc.get("coverage_complete")) if kind == "logical_section" else None,
                  reference_unit="page" if kind == "physical_page" else None)
    if kind == "logical_section" and doc.get("provenance_json"):
        result["reference_unit"] = json.loads(doc.provenance_json)["provenance"]["unit_kind"]
    # Provenance contains original cells/paragraphs. Never add it to metadata.
    return result


def _document_manifest(source, document):
    """Bind local parser evidence to exact original bytes before any paid call."""
    try:
        count = document.page_count
        provenance = document.provenance
        if (source.kind != "document" or source.page_count != 1
                or document.source_sha256 != source.sha256
                or type(count) is not int or count < 1
                or [page.page_number for page in document.pages] != list(range(1, count + 1))
                or provenance["version"] != 1 or provenance["reference_kind"] != "logical_section"
                or provenance["unit_kind"] not in {"worksheet", "delimited_text", "word_document", "decoded_text",
                                                   "roundtrip_document", "pos_day"}
                or type(provenance["coverage_complete"]) is not bool
                or not isinstance(provenance["coverage_issues"], list)
                or (provenance["coverage_complete"] and provenance["coverage_issues"])
                or [unit["page_number"] for unit in provenance["units"]] != list(range(1, count + 1))
                or type(document.billing_characters) is not int or document.billing_characters < 0
                or not isinstance(document.engine, str) or not document.engine):
            raise ValueError("Invalid manifest")
        body = {"schema": access.MANIFEST_SCHEMA, "source_sha256": source.sha256,
                "reference_count": count, "engine": document.engine,
                "billing_characters": document.billing_characters, "provenance": provenance,
                # Bind the parsed projection too: identical raw row evidence
                # must not mask a parser upgrade changing model input on replay.
                "page_inputs_sha256": [hashlib.sha256(json.dumps(page.model_dump(mode="json"),
                    ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
                    for page in document.pages]}
        encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, KeyError, AttributeError):
        frappe.throw(_("Invalid original document manifest."))
    if len(encoded.encode("utf-8")) > access.MAX_DOCUMENT_MANIFEST_BYTES:
        frappe.throw(_("The original document manifest exceeds the 8 MiB limit."))
    return encoded


def freeze_doc_manifest(source, document):
    """Internal admission operation; row lock and caller transaction are retained.

    A trusted local parser supplies the complete source provenance. A worker can
    call this again to verify an unchanged parse, but cannot revise the first
    accepted evidence. The ordinary original-write capability is insufficient.
    """
    scope.require("intake")
    source = frappe.get_doc(access.SOURCE, source.name, for_update=True)
    access.require_full(source)
    encoded = _document_manifest(source, document)
    read_bytes(source)
    if any(source.get(field) for field in access.MANIFEST_FIELDS):
        if (source.get("reference_kind") != "logical_section"
                or source.get("reference_count") != document.page_count
                or bool(source.get("coverage_complete")) != document.coverage_complete
                or source.get("provenance_json") != encoded):
            frappe.throw(_("The original document manifest cannot be changed."), frappe.PermissionError)
        return source
    if source.binding_complete:
        frappe.throw(_("The original document manifest cannot be changed."), frappe.PermissionError)
    source.reference_kind = "logical_section"
    source.reference_count = document.page_count
    source.provenance_json = encoded
    source.coverage_complete = int(document.coverage_complete)
    source.flags.cowork_original_write = guard.WRITE_CAPABILITY
    source.flags.cowork_document_manifest = access._MANIFEST_CAPABILITY
    # Source track_changes must not copy the original rows into a native Version.
    source.flags.notify_update = False
    # Frappe _save resets flags.ignore_version from this explicit argument.
    source.save(ignore_permissions=True, ignore_version=True)
    return source


def read_bytes(source):
    """Internal capability, called only after whole-file/page authorization."""
    stored = stored_original(source)
    if stored:
        data = cloud.read(stored)
        if hashlib.sha256(data).hexdigest() != source.sha256:
            frappe.throw(_("The original file checksum does not match."))
        return data
    on_edge = edge_original(source)
    if on_edge:
        # 存在甲骨文（ADR-023）：只有导出打包等少数地方要整份字节；预览、下载、识别都不经过这里。
        data = edge.read(on_edge)
        if hashlib.sha256(data).hexdigest() != source.sha256:
            frappe.throw(_("The original file checksum does not match."))
        return data
    file_doc = frappe.get_doc("File", source.stored_file)
    if (not file_doc.is_private or file_doc.file_url != source.storage_url
            or file_doc.attached_to_doctype != access.SOURCE or file_doc.attached_to_name != source.name):
        frappe.throw(_("The original file reference has changed."), frappe.PermissionError)
    file_doc.flags.cowork_original_read = guard.READ_CAPABILITY
    data = file_doc.get_content(encodings=[])
    if hashlib.sha256(data).hexdigest() != source.sha256:
        frappe.throw(_("The original file checksum does not match."))
    return data


def get_source(name):
    scope.require_read()
    source = frappe.get_doc(access.SOURCE, name)
    access.require_full(source)
    return source


def original(name):
    source = get_source(name)
    return source.filename, read_bytes(source)


def original_link(name):
    """原件在原件桶里：``(文件名, 短时签名下载地址)``；在服务器上返回 ``None``（照旧由接口回字节）。"""
    source = get_source(name)
    on_edge = edge_original(source)
    if on_edge:
        return source.filename, edge.download_url(on_edge, source.filename)
    stored = stored_original(source)
    return (source.filename, cloud.download_url(stored, source.filename)) if stored else None


def page_source(history_name, physical_page):
    """预览一页之前的权限判定（与原来同一套：读权限、这条记录引用这一页、页码在原件范围内）：返回 ``(原件, 页码)``。"""
    scope.require_read()
    if isinstance(physical_page, bool) or not re.fullmatch(r"[1-9][0-9]*", str(physical_page)):
        frappe.throw(_("A positive source page number is required."))
    number = int(physical_page)
    history = frappe.get_doc(access.HISTORY, history_name)
    access.require_page(history, number)
    source = frappe.get_doc(access.SOURCE, history.source_file)
    if not 1 <= number <= source.page_count:
        frappe.throw(_("The requested page is not part of this invoice."), frappe.PermissionError)
    return source, number


# 识别时顺手存好的预览图（Cloud Run 在原件桶里，``ocr.remote.runner.Previews``）。
PREVIEW_KINDS = ("pdf", "image")


def previews_on():
    """回退开关：站点配置 ``pearnly_cowork_stored_previews`` 写 0 就不再存、也不再用存好的图（照旧现画）；默认开。"""
    return bool(cint((frappe.conf or {}).get("pearnly_cowork_stored_previews", 1)))


def preview_spec(source):
    """交给 Cloud Run 识别时带上的预览规格；没配原件桶、不是 PDF / 图片返回 ``None``（不存，预览照旧现画）。"""
    from pearnly_cowork.ocr.remote import contract

    if not previews_on() or not cloud.bucket() or source.kind not in PREVIEW_KINDS:
        return None
    return contract.previews(source=source.name, sha256=source.sha256, kind=source.kind)


def preview_record(source, items):
    """Cloud Run 回来的已存预览 → 记在原件记录上的 JSON（随绑定识别结果那一次保存写入）。核对不过就不记（照旧现画）。"""
    from pearnly_cowork.ocr.remote import contract

    try:
        bucket_name, pages = contract.parse_previews(items, site=frappe.local.site, source=source.name,
                                                     sha256=source.sha256, page_count=int(source.page_count))
    except ValueError:
        logger.warning("stored previews refused: %s", source.name)
        return None
    if bucket_name != cloud.bucket() or not pages:
        return None
    return json.dumps({"version": contract.PREVIEW_VERSION, "bucket": bucket_name, "sha256": source.sha256,
                       "pages": {str(page): value for page, value in sorted(pages.items())}},
                      sort_keys=True, separators=(",", ":"))


def preview_link(source, number):
    """这一页有识别时存好的预览图：返回短时签名地址；没有（旧数据、本机识别的、画法版本变了）返回 ``None``。
    调用方先做完 :func:`page_source` 的权限判定。"""
    from pearnly_cowork.ocr.remote import contract

    on_edge = edge_original(source)
    if on_edge and source.kind in PREVIEW_KINDS:
        return edge.preview_url(on_edge, source.kind, int(number))  # 甲骨文现画并存下（ADR-023），生产不画
    if not previews_on():
        return None
    try:
        record = json.loads(source.get("preview_objects") or "null")
        if (not isinstance(record, dict) or record.get("version") != contract.PREVIEW_VERSION
                or record.get("sha256") != source.sha256 or not isinstance(record.get("bucket"), str)):
            return None
        value = record["pages"].get(str(int(number)))
        if (not isinstance(value, dict) or value.get("type") not in contract.PREVIEW_TYPES
                or value.get("object") != contract.preview_object(frappe.local.site, source.name, source.sha256, number)
                or type(value.get("generation")) is not int):
            return None
    except (ValueError, TypeError, AttributeError, KeyError):
        return None
    return cloud.preview_url(record["bucket"], value["object"], value["generation"], value["type"])


def page(history_name, physical_page):
    """一页预览图的字节（现画）。"""
    return render_page(*page_source(history_name, physical_page))


def render_page(source, number):
    """现画一页预览（调用方先做完 :func:`page_source` 的权限判定）。每进程同时最多现画 ``RENDER_SLOTS`` 页。"""
    if not RENDER_SLOTS.acquire(timeout=RENDER_WAIT_SECONDS):
        frappe.throw(_("The preview is temporarily unavailable. Please try again."))
    try:
        return _render_page(source, number)
    finally:
        RENDER_SLOTS.release()


def _render_page(source, number):
    if edge_original(source):
        frappe.throw(_("The preview is temporarily unavailable. Please try again."))  # 甲骨文的预览走签名地址
    stored = stored_original(source)
    if stored:
        # 原件在原件桶里：在 Cloud Run 上生成这一页的预览，只把生成好的图传回来（不把整份原件下载到服务器，ADR-016）。
        from pearnly_core.execution.errors import Deferred
        from pearnly_cowork.ocr.remote import forward

        try:
            result = forward.preview(stored[1], stored[2], source.sha256, source.kind, number)
        except Deferred:
            frappe.throw(_("The preview is temporarily unavailable. Please try again."))
        if result.get("code"):
            reject(result["code"])
        return result["png"]
    try:
        return originals.render_page(source.kind, read_bytes(source), number)
    except originals.Rejected as error:
        reject(error.code)


# ── 删除：录入工作台删票后，没有别的票再用的原件连同存储一起删（用户 2026-10-08「删就删除所有记录和储存」）──


ACTIVE_BATCH = ("admitted", "recognizing")
WHT_CERTIFICATE = "Cowork WHT Certificate"


def purge(source_names):
    """删掉这些原件：原件记录、它的识别批次与 50 ทวิ 参考记录、服务器上的文件、原件桶里的原件与预览。

    调用方（`history/removal.py`）已核过权限并删掉了引用它的票；这里再核一次：还有票在用、或还有识别没结束的原件不删。
    数据库行当场删；磁盘文件与桶里的对象等事务提交后再删（回滚时不丢文件），桶里的放后台任务，不拖慢删除请求。
    返回删掉的原件数。"""
    paths, prefixes, edge_objects, removed = [], [], [], 0
    bucket_name = cloud.bucket()
    for name in dict.fromkeys(source_names or []):
        source = frappe.db.get_value(access.SOURCE, name, ["name", "stored_file", "storage_url"], as_dict=True,
                                     for_update=True)
        if not source or frappe.db.exists(access.HISTORY, {"source_file": name}) \
                or frappe.db.exists("Cowork OCR Batch", {"source_file": name, "phase": ["in", ACTIVE_BATCH]}):
            continue
        files = frappe.get_all("File", filters={"attached_to_doctype": access.SOURCE, "attached_to_name": name},
                               fields=["name", "file_url"], ignore_permissions=True)
        if source.stored_file and source.stored_file not in {row.name for row in files}:
            row = frappe.db.get_value("File", source.stored_file, ["name", "file_url"], as_dict=True)
            if row:
                files.append(row)
        for row in files:
            frappe.db.delete("File", {"name": row.name})
            # 框架按内容去重：同样内容的别的文件记录还指着这份磁盘文件就留着。
            if row.file_url and not frappe.db.exists("File", {"file_url": row.file_url}):
                parts = [part for part in row.file_url.split("/") if part and part != ".."]
                paths.append(frappe.get_site_path(*(parts if parts[:1] == ["private"] else ["public", *parts])))
        # 50 ทวิ 参考记录（`history.certificates`）随原件与它的识别批次一起删，不再进报税草稿
        batches = frappe.get_all("Cowork OCR Batch", filters={"source_file": name}, pluck="name",
                                 ignore_permissions=True)
        frappe.db.delete(WHT_CERTIFICATE, {"source_file": name})
        if batches:
            frappe.db.delete(WHT_CERTIFICATE, {"batch": ["in", batches]})
        frappe.db.delete("Cowork OCR Batch", {"source_file": name})
        frappe.db.delete(access.SOURCE, {"name": name})
        removed += 1
        on_edge = edge_links.parse_url(source.storage_url) if source.storage_url else None
        if on_edge:
            edge_objects.append(on_edge[0])
        stored = cloud.parse_url(source.storage_url) if source.storage_url else None
        if stored:
            prefixes.append((stored[0], stored[1]))
        if bucket_name:
            prefixes.append((bucket_name, f"{frappe.local.site}/previews/{name}/"))
    if paths:
        frappe.db.after_commit.add(lambda: _remove_paths(paths))
    if prefixes:
        frappe.enqueue("pearnly_cowork.files.service.purge_objects", queue="short", prefixes=prefixes,
                       enqueue_after_commit=True)
    if edge_objects:
        frappe.enqueue("pearnly_cowork.files.service.purge_edge", queue="short", names=edge_objects,
                       enqueue_after_commit=True)
    return removed


def _remove_paths(paths):
    import os

    for path in paths:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("original file not removed: %s", path)


def purge_objects(prefixes):
    """后台：删原件桶里这些原件与预览（失败记一条错误日志，不重试；对象留着只占存储，不影响任何功能）。"""
    for bucket_name, prefix in prefixes or []:
        try:
            cloud.delete_prefix(bucket_name, prefix)
        except Exception:
            frappe.log_error(title="Cowork original objects not deleted", message=frappe.get_traceback()[-1500:])


def purge_edge(names):
    """后台：删甲骨文上这些原件与预览（ADR-023；失败记一条错误日志，不重试，留着只占那台的磁盘）。"""
    try:
        edge.delete(names)
    except Exception:
        frappe.log_error(title="Cowork Oracle originals not deleted", message=frappe.get_traceback()[-1500:])
