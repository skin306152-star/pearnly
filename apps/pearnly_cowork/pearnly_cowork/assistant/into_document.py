"""补传的文件接到原单后面整份重读（2026-11-03「CC 通用疑难处理」，`cowork_recognize_attachments` 的 ``into_document``）。

缺页、拍漏一页之类：用户在对话里补传那一页，CC 确认是哪张单后调。做法：
1. 原单这几页的原件（同一文件里别的单不带）+ 补传的文件，拼成一个 PDF；
2. 按普通识别交一次（与网页、LINE 同一条路，按页照常扣费，用户「上传识别都扣费」）；请求号按「原单 + 附件」固定，
   同样两样再调一次拿回原来的任务，不重复扣费；
3. 识别完只读出一张单 → 覆盖原单的识别结果（`storage.update_pages`，按原单版本号），原单改指向拼好的原件；改动记一条来源
   （`document_checks.record`，依据写明补传了哪个文件）；识别时临时建出的那张删掉——**不新建单**；
4. 读出不止一张、或一张也没有 → 不合并，照实回结果，CC 问用户（不猜着合并）。

已在推送中 / 已入账的单不动（改不了，`ENTERED_NOTE`）。
"""
from __future__ import annotations

import io
import json
import time

import frappe

from pearnly_cowork.access import scope
from pearnly_cowork.assistant import document_checks
from pearnly_cowork.history import storage

LOCKED = ("pending", "success")  # 推送中 / 已入账
# 原单改指向拼好的原件时一起换的栏（识别时临时那张单上的值）
SOURCE_FIELDS = ("source_file", "source_pdf_id", "source_index", "source_total", "filename", "file_hash", "page_count",
                 "source_page_indices")
IMAGE_TYPES = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "webp", ".tif": "tiff", ".tiff": "tiff",
               ".bmp": "bmp", ".gif": "gif"}


def _suffix(filename) -> str:
    name = str(filename or "").lower()
    return name[name.rfind("."):] if "." in name else ""


def _append(target, content, filename, pages=None):
    """把一份原件（PDF 或图片）接到 ``target``（fitz 文档）后面；``pages``：PDF 只取这几页（1 起）。"""
    import fitz

    suffix = _suffix(filename)
    if suffix in (".heic", ".heif"):
        from pearnly_cowork.files import service as files

        filename, content = files._heic_as_jpeg(filename, content)
        suffix = ".jpg"
    if suffix == ".pdf":
        source = fitz.open(stream=content, filetype="pdf")
        wanted = [number - 1 for number in pages or range(1, source.page_count + 1) if 1 <= number <= source.page_count]
        for index in wanted:
            target.insert_pdf(source, from_page=index, to_page=index)
        return
    if suffix not in IMAGE_TYPES:
        raise ValueError("unsupported_type")
    image = fitz.open(stream=content, filetype=IMAGE_TYPES[suffix])
    target.insert_pdf(fitz.open("pdf", image.convert_to_pdf()))


def combine(parts) -> bytes:
    """``[(内容, 文件名, 页码表或 None)]`` → 一个 PDF 的字节。"""
    import fitz

    target = fitz.open()
    for content, filename, pages in parts:
        _append(target, content, filename, pages)
    out = io.BytesIO()
    target.save(out)
    return out.getvalue()


def _original_pages(doc) -> list | None:
    from pearnly_cowork.files import access as file_access

    found = sorted(file_access.page_indices(doc.get("source_page_indices"))) if doc.get("source_page_indices") else []
    return found or None


def run(name, file_id, tools) -> dict:
    """``tools``：调用方模块（`assistant.tools`），借它的交识别、取结果、等候常量，与普通识别同一条路。"""
    from pearnly_core.assistant import files as chat_files
    from pearnly_cowork.files import service as files

    scope.require("intake")
    doc = storage.project(storage.read(name))
    if doc.get("last_push_status") in LOCKED:
        return {"document": name, "status": "not_merged", "reason": "This document is already pushed or being pushed. "
                + tools.ENTERED_NOTE}
    if not doc.get("source_file"):
        return {"document": name, "status": "not_merged", "reason": "This document has no original file."}
    try:
        filename, content = chat_files.attachment(file_id)
        key = f"assistant:into:{name}:{chat_files.resolve(file_id)}"
    except frappe.ValidationError:
        frappe.clear_messages()
        return {"file": file_id, "status": "not_found", "attachments_in_this_chat": chat_files.attachments()}
    if chat_files.origin(file_id) == "tool":
        return {"file": file_id, "status": "not_started",
                "reason": "made_by_tool: this file was made by a tool, not a document to recognize"}
    source = files.get_source(doc["source_file"])
    try:
        merged = combine([(files.read_bytes(source), source.filename or doc.get("filename") or "original.pdf",
                           _original_pages(doc)), (content, filename, None)])
    except (ValueError, RuntimeError):
        return {"file": file_id, "filename": filename, "status": "unsupported_type",
                "reason": "Only document photos and PDF files can be added to a document."}
    stem = str(doc.get("filename") or name).rsplit(".", 1)[0]
    created = files.create(doc.get("workspace") or scope.default_workspace(), f"{stem}+{filename.rsplit('.', 1)[0]}.pdf",
                           merged, key)
    frappe.db.commit()
    batch, _earlier = tools._submit(created, key, {})
    frappe.db.commit()
    deadline = time.monotonic() + tools.RECOGNIZE_WAIT_SECONDS
    while True:
        frappe.db.rollback()  # 识别在队列里另一进程做：每次重新读
        detail = tools._recognition().detail(batch["name"])
        if detail.get("state") in tools.FINISHED + tools.BROKEN or time.monotonic() >= deadline:
            break
        time.sleep(3)
    state = detail.get("state")
    out = {"document": name, "file": file_id, "filename": filename, "batch": batch["name"], "state": state}
    if state not in tools.FINISHED:
        # 还在识别：同样的 document + 附件再调一次拿回这份（不重复扣费）；识别不出照实说
        return {**out, "status": "recognizing" if state not in tools.BROKEN else "failed",
                **({"reason": detail.get("last_code") or state} if state in tools.BROKEN else {})}
    records = [record for record in detail.get("history_records") or [] if record != name]
    if len(records) != 1:
        # 读出不止一张（或一张也没有）：不猜着合并，交 CC 问用户；临时那几张留着，说清楚
        return {**out, "status": "not_merged", "found": len(records), "documents": records,
                "reason": "The combined file was read as more than one document or none; ask the user which document "
                          "the file belongs to before changing anything."}
    return {**out, **merge(name, records[0], filename)}


def merge(name, temporary, filename) -> dict:
    """临时那张的识别结果覆盖原单（版本号按原单此刻的），原单改指向拼好的原件，记一条来源，删掉临时那张。"""
    from pearnly_cowork.history import removal

    current = storage.project(storage.read(name))
    fresh = storage.project(storage.read(temporary))
    before = (current.get("pages") or [{}])[0].get("fields") or {}
    old_source = current.get("source_file")
    values = {field: fresh.get(field) for field in SOURCE_FIELDS if field in fresh}
    for field in ("source_page_indices",):
        if isinstance(values.get(field), (list, dict)):
            values[field] = json.dumps(values[field])
    frappe.db.set_value(storage.DOCTYPE, name, values, update_modified=False)
    if values.get("source_file"):
        # 推送前核对「原件绑定的单据」（`push.application.provenance`）：拼好的原件识别时绑的是临时那张，换成原单
        bound = storage.parse(frappe.db.get_value("Cowork Source File", values["source_file"], "binding_records") or "[]")
        frappe.db.set_value("Cowork Source File", values["source_file"], "binding_records",
                            storage.encode([name if row == temporary else row for row in bound or [temporary]]),
                            update_modified=False)
    saved = storage.update_pages(name, fresh.get("pages") or [], str(current.get("record_version")))
    after = (saved.get("pages") or [{}])[0].get("fields") or {}
    keys = sorted((set(before) | set(after)) - {"items"})
    document_checks.record(name, before, after, keys, items_changed=before.get("items") != after.get("items"),
                           basis=f"added {filename} to the original and recognized the whole file again")
    removal.delete(json.dumps([temporary]))  # 拼好的原件现在归原单，不会被一起删；原单旧的原件没人用了才删
    if old_source and old_source != values.get("source_file"):
        from pearnly_cowork.files import service as files

        files.purge([old_source])
    frappe.db.commit()
    return {"status": "merged", "version": saved.get("record_version"), "pages": len(saved.get("pages") or [])}
