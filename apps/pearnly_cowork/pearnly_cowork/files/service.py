"""Native private originals; no OCR, external upload or legacy blob import."""
import hashlib
import io
import json
from pathlib import PurePath
import re

import frappe
from frappe import _
import pymupdf
from PIL import Image, ImageOps

from pearnly_cowork.access import scope
from pearnly_cowork.files import access, guard
from pearnly_cowork.ocr.inputs import (IMAGE_EXTENSIONS, TABLE_EXTENSIONS as DOCUMENT_EXTENSIONS,
    MAX_FILE_BYTES as MAX_BYTES, MAX_PDF_PAGES as MAX_PAGES, validate_input_metadata, OcrInputError)

MAX_PREVIEW_PIXELS = 16_000_000


def inspect_content(filename, content):
    filename = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not filename or len(filename) > 255 or any(c in filename for c in ("\x00", "\r", "\n")):
        frappe.throw(_("Invalid original filename."))
    suffix = PurePath(filename).suffix.lower()
    if suffix not in IMAGE_EXTENSIONS | DOCUMENT_EXTENSIONS | {".pdf"}:
        frappe.throw(_("This file type is not supported."))
    if not isinstance(content, bytes) or not content:
        frappe.throw(_("The uploaded file is empty."))
    if len(content) > MAX_BYTES:
        frappe.throw(_("The uploaded file exceeds the 100 MiB limit."))
    count = 1
    kind = "document"
    if suffix == ".pdf":
        kind = "pdf"
        try:
            with pymupdf.open(stream=content, filetype="pdf") as document:
                if document.needs_pass:
                    frappe.throw(_("Password-protected PDF files cannot be read."))
                count = len(document)
        except (ValueError, RuntimeError, pymupdf.FileDataError):
            frappe.throw(_("The PDF file cannot be read."))
        if not 1 <= count <= MAX_PAGES:
            frappe.throw(_("PDF files must contain between 1 and 50 pages."))
    elif suffix in IMAGE_EXTENSIONS:
        kind = "image"
        try:
            with Image.open(io.BytesIO(content)) as image:
                image.verify()
        except (OSError, ValueError, Image.DecompressionBombError):
            frappe.throw(_("The image file cannot be read."))
    try:
        validate_input_metadata(filename, len(content), pdf_page_count=count if kind == "pdf" else None)
    except OcrInputError as error:
        frappe.throw(_(error.code))
    return {"filename": filename, "suffix": suffix, "kind": kind, "page_count": count,
            "byte_size": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def create(workspace, filename, content, request_key, client=None):
    scope.require("intake")
    scope.require_scope(workspace, client)
    if not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 128:
        frappe.throw(_("A request key of 1 to 128 characters is required."))
    meta = inspect_content(filename, content)
    key = hashlib.sha256(json.dumps([workspace, frappe.session.user, request_key]).encode()).hexdigest()
    name = "CWS-" + key[:48]
    fingerprint = hashlib.sha256(json.dumps([client or None, meta], sort_keys=True).encode()).hexdigest()
    if frappe.db.exists(access.SOURCE, name):
        doc = frappe.get_doc(access.SOURCE, name, for_update=True)
        access.require_full(doc)
        if doc.request_fingerprint != fingerprint:
            frappe.throw(_("This request key has already been used for different content."))
        return project(doc)
    doc = frappe.get_doc({"doctype": access.SOURCE, "name": name, "workspace": workspace, "client": client or "",
        "uploaded_by": frappe.session.user, "request_key": key, "request_fingerprint": fingerprint,
        **{key: value for key, value in meta.items() if key != "suffix"}})
    doc.flags.cowork_original_write = guard.WRITE_CAPABILITY
    # Permission was checked above. Native generic creation must remain unavailable.
    doc.insert(ignore_permissions=True)
    file_doc = frappe.get_doc({"doctype": "File", "file_name": name + meta["suffix"],
        "is_private": 1, "content": content, "owner": "Administrator",
        "attached_to_doctype": access.SOURCE, "attached_to_name": doc.name})
    file_doc.flags.cowork_original_write = guard.WRITE_CAPABILITY
    file_doc.insert(ignore_permissions=True)
    doc.stored_file, doc.storage_url = file_doc.name, file_doc.file_url
    doc.native_content_hash = file_doc.content_hash
    doc.save(ignore_permissions=True)
    read_bytes(doc)
    return project(doc)


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
                or provenance["unit_kind"] not in {"worksheet", "delimited_text", "word_document", "decoded_text"}
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


def page(history_name, physical_page):
    scope.require_read()
    if isinstance(physical_page, bool) or not re.fullmatch(r"[1-9][0-9]*", str(physical_page)):
        frappe.throw(_("A positive source page number is required."))
    number = int(physical_page)
    history = frappe.get_doc(access.HISTORY, history_name)
    access.require_page(history, number)
    source = frappe.get_doc(access.SOURCE, history.source_file)
    if not 1 <= number <= source.page_count:
        frappe.throw(_("The requested page is not part of this invoice."), frappe.PermissionError)
    data = read_bytes(source)
    if source.kind == "pdf":
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            selected = pdf[number - 1]
            scale = 150 / 72
            if selected.rect.width * selected.rect.height * scale * scale > MAX_PREVIEW_PIXELS:
                frappe.throw(_("This page is too large to preview safely."))
            png = selected.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")
    elif source.kind == "image" and number == 1:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_PREVIEW_PIXELS:
                frappe.throw(_("This page is too large to preview safely."))
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((2048, 2048))
            output = io.BytesIO()
            image.save(output, format="PNG")
            png = output.getvalue()
    else:
        frappe.throw(_("Page preview is available only for PDF and image originals."))
    return png
