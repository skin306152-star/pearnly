"""File authority is the source plus every invoice sharing the requested bytes."""
import json
import os
from urllib.parse import unquote

import frappe
from frappe import _
from frappe.utils import get_url
from pearnly_cowork.access import scope

SOURCE = "Cowork Source File"
HISTORY = "Cowork OCR History"
FILE_FIELDS = ("name", "file_name", "file_url", "thumbnail_url", "is_private", "content_hash",
               "attached_to_doctype", "attached_to_name")
MANIFEST_FIELDS = ("reference_kind", "reference_count", "provenance_json", "coverage_complete")
MANIFEST_SCHEMA = "cowork-document-manifest-v1"
MAX_DOCUMENT_MANIFEST_BYTES = 8 * 1024 * 1024
_MANIFEST_CAPABILITY = object()


def reference_kind(source):
    value = source.get("reference_kind")
    if source.kind in {"pdf", "image"} and value in (None, "", "physical_page"):
        return "physical_page"
    if source.kind == "document" and value in (None, "", "logical_section"):
        return "logical_section"
    return None


def reference_count(source):
    """Old PDF/image rows retain their physical count; documents never use 1."""
    kind = reference_kind(source)
    value = None
    if kind == "physical_page":
        value = source.page_count
    elif (kind == "logical_section" and source.get("reference_kind") == kind
            and source.get("provenance_json")):
        value = source.get("reference_count")
    if type(value) is int and value > 0:
        return value
    return 0


def _manifest_state(source):
    return (source.get("reference_kind") or "", int(source.get("reference_count") or 0),
            source.get("provenance_json") or "", int(source.get("coverage_complete") or 0))


def validate_manifest(doc):
    """Before-validate guard: only the file service may freeze, exactly once.

    Generic original binding has a different capability and cannot change this
    evidence. Legacy empty fields stay empty until a document is explicitly
    parsed; they must not be upgraded from a placeholder page count.
    """
    previous = doc.get_doc_before_save()
    current = _manifest_state(doc)
    prior = _manifest_state(previous) if previous else ("", 0, "", 0)
    if current == prior:
        return
    if (prior != ("", 0, "", 0) or not previous or previous.kind != "document"
            or previous.binding_complete
            or doc.flags.get("cowork_document_manifest") is not _MANIFEST_CAPABILITY):
        frappe.throw(_("The original document manifest cannot be changed."), frappe.PermissionError)
    if (doc.kind != "document" or doc.page_count != previous.page_count or doc.sha256 != previous.sha256
            or doc.reference_kind != "logical_section" or type(doc.reference_count) is not int
            or doc.reference_count < 1 or doc.coverage_complete not in (0, 1)):
        frappe.throw(_("Invalid original document manifest."))
    try:
        body = json.loads(doc.provenance_json)
        if (len(doc.provenance_json.encode()) > MAX_DOCUMENT_MANIFEST_BYTES
                or body["schema"] != MANIFEST_SCHEMA or body["source_sha256"] != doc.sha256
                or body["reference_count"] != doc.reference_count
                or body["provenance"]["reference_kind"] != "logical_section"
                or type(body["provenance"]["coverage_complete"]) is not bool
                or body["provenance"]["coverage_complete"] != bool(doc.coverage_complete)
                or len(body["page_inputs_sha256"]) != doc.reference_count
                or any(not isinstance(value, str) or len(value) != 64
                       or any(char not in "0123456789abcdef" for char in value)
                       for value in body["page_inputs_sha256"])
                or [unit["page_number"] for unit in body["provenance"]["units"]]
                    != list(range(1, doc.reference_count + 1))):
            raise ValueError("Invalid manifest")
    except (ValueError, KeyError, TypeError, AttributeError):
        frappe.throw(_("Invalid original document manifest."))


def readable(user=None):
    return scope.allowed_any(scope.READ_FEATURES, user)


def references(source_name):
    # 每份原件每次核权限都要读：参数化查询，不经查询构建器（与原来的 get_all 同样不套权限、取全部）。
    return frappe.db.sql(f"""select name, workspace, client, source_page_indices from `tab{HISTORY}`
        where source_file=%s order by creation desc""", (source_name,), as_dict=True)


def page_indices(value):
    try:
        result = json.loads(value) if isinstance(value, str) else value
        if not isinstance(result, list) or any(type(p) is not int or p < 1 for p in result):
            return set()
        return set(result)
    except (ValueError, TypeError):
        return set()


def full_allowed(source, user=None):
    user = user or frappe.session.user
    if not readable(user) or not scope.contains(source.workspace, source.client, user):
        return False
    rows = references(source.name)
    # 同一份原件拆出的几张票归属一般都相同：每种（归属，客户）只核一次，与原件本身那一对相同的不再重核。
    pairs = {(row.workspace, row.client or None) for row in rows} - {(source.workspace, source.client or None)}
    if any(not scope.contains(workspace, client, user) for workspace, client in pairs):
        return False
    covered = set().union(*(page_indices(row.source_page_indices) for row in rows))
    count = reference_count(source)
    complete = (reference_kind(source) == "physical_page"
                or (source.get("reference_kind") == "logical_section" and bool(source.get("coverage_complete"))))
    # The uploader had the original bytes; still recheck all current known scopes.
    # Others need complete parser coverage and every physical/logical reference.
    return bool(user == source.uploaded_by or (complete and source.get("binding_complete") and rows and count
                and covered == set(range(1, count + 1))))


def require_full(source, user=None):
    if not full_allowed(source, user):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


def require_page(history, page):
    if not readable() or not scope.contains(history.workspace, history.client):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    if type(page) is not int or page not in page_indices(history.source_page_indices):
        frappe.throw(_("The requested page is not part of this invoice."), frappe.PermissionError)
    source = frappe.get_doc(SOURCE, history.source_file)
    if reference_kind(source) != "physical_page":
        # Logical sections are not PDF/image coordinates, even if their integer
        # index happens to be 1. Reject before granting a byte-read capability.
        frappe.throw(_("Page preview is available only for PDF and image originals."), frappe.PermissionError)
    # A later invoice in the same OCR batch may share this physical page. Wait
    # until the complete manifest was committed before exposing any preview.
    if not source.get("binding_complete") or not 1 <= page <= int(source.page_count or 0):
        frappe.throw(_("The original file is not ready for page preview."), frappe.PermissionError)
    # Several invoices can occupy one physical page. Do not expose a hidden
    # company's other invoice merely because the requested invoice is visible.
    for row in references(history.source_file):
        if page in page_indices(row.source_page_indices) and not scope.contains(row.workspace, row.client):
            frappe.throw(_("Not Permitted"), frappe.PermissionError)


def canonical_storage_url(value, is_private=False, file_name=None):
    """Match local aliases by the path Frappe will read, without opening bytes.

    Source storage URLs are generated by the service. File input may instead be
    an absolute site URL, percent-encoded or contain dot segments/symlinks.
    Remote URLs remain remote; no request to the remote host is made.
    """
    value = str(value or file_name or "")
    if not value or "\x00" in value:
        return None
    value = unquote(value)
    site_url = get_url().rstrip("/")
    if "/files/" in value and value.startswith(site_url):
        value = value[len(site_url):]
    if value.startswith(("http://", "https://", "/api/method/")):
        return None
    if "/" not in value:
        value = ("/private/files/" if is_private else "/files/") + value
    if value.startswith("/private/files/"):
        path = frappe.get_site_path("private", "files", value[len("/private/files/"):])
    elif value.startswith("/files/"):
        path = frappe.get_site_path("public", "files", value[len("/files/"):])
    else:
        # Frappe also resolves absolute paths inside the current Site. This is
        # relevant when an existing File's URL is changed via a native API.
        path = value
    try:
        path = os.path.realpath(path)
        for directory, prefix in (("private", "/private/files/"), ("public", "/files/")):
            base = os.path.realpath(frappe.get_site_path(directory, "files"))
            if os.path.commonpath((base, path)) == base and path != base:
                return prefix + os.path.relpath(path, base).replace(os.sep, "/")
    except (ValueError, OSError):
        pass
    return None


def file_states(file_doc):
    """Use both persisted and submitted properties, never a client flags cache."""
    states = [file_doc]
    if file_doc.get("name"):
        previous = frappe.db.get_value("File", file_doc.name, FILE_FIELDS, as_dict=True)
        if previous:
            states.append(previous)
    return states


def protected_sources(file_doc, *, include_hash=False):
    """Sources whose original this File is, aliases, or (``include_hash``) shares bytes with.

    Each condition is its own indexed exact lookup. They used to be one OR across a
    LEFT JOIN, which scanned and, with ``include_hash``, row-locked every source (and
    its File) on every File write: five times per new File. Under MariaDB 11.8's
    snapshot isolation that made a reconciliation start fail with 1020 whenever the
    same tenant's invoice uploads changed any source (three-tenant stress test
    2026-10-01: 24 of 105 starts). Now every lookup is a plain read and, with
    ``include_hash``, only the source rows found are then locked by primary key (no
    gap locks on the secondary indexes). What counts as protected is unchanged.
    """
    if not frappe.db.table_exists(SOURCE):
        return []
    stored, urls, names, hashes = set(), set(), set(), set()
    for state in file_states(file_doc):
        if state.get("name"):
            stored.add(state.name)
        urls |= {state.get("file_url"), state.get("thumbnail_url"), canonical_storage_url(
            state.get("file_url"), state.get("is_private"), state.get("file_name")),
            canonical_storage_url(state.get("thumbnail_url"))} - {None, ""}
        if state.get("attached_to_doctype") == SOURCE and state.get("attached_to_name"):
            names.add(state.attached_to_name)
        if include_hash and state.get("content_hash"):
            # Native privacy changes update every File with this MD5, even if
            # the files have distinct URLs. SHA-256 remains the integrity key.
            hashes.add(state.content_hash)
    if not (stored or urls or names or hashes):
        return []
    for column, values in (("file_url", urls), ("content_hash", hashes)):
        if values:
            stored.update(frappe.db.sql(f"SELECT name FROM `tabFile` WHERE `{column}` IN %(values)s",
                                        {"values": tuple(sorted(values))}, pluck=True))
    found = set()
    for column, values in (("stored_file", stored), ("storage_url", urls), ("name", names),
                           ("native_content_hash", hashes)):
        if values:
            found.update(frappe.db.sql(f"SELECT name FROM `tabCowork Source File` WHERE `{column}` IN %(values)s",
                                       {"values": tuple(sorted(values))}, pluck=True))
    if include_hash and found:
        # 查到了才按主键锁住这几行原件（只锁这几条记录）。查找本身不加锁：在二级索引上按「没有的值」加锁读会锁住
        # 前后两条记录之间的空档，别的请求往这个空档里插新原件（直传登记）就得等这一整个事务结束——三租户半量压测
        # 2026-10-01：直传登记 12 次等满 50 秒报 1205。
        frappe.db.sql("SELECT name FROM `tabCowork Source File` WHERE name IN %(names)s FOR UPDATE",
                      {"names": tuple(sorted(found))})
    return [frappe.get_doc(SOURCE, name) for name in sorted(found)]


def is_protected(file_doc, *, include_hash=False):
    return any(state.get("attached_to_doctype") == SOURCE for state in file_states(file_doc)) or bool(
        protected_sources(file_doc, include_hash=include_hash))


def source_permission(doc, ptype=None, user=None, **kwargs):
    if ptype not in scope.READ_TYPES:
        frappe.throw(_("Original files can only be changed through the Cowork file service."), frappe.PermissionError)
    require_full(doc, user)
    return True


def file_permission(doc, ptype=None, user=None, **kwargs):
    if is_protected(doc, include_hash=ptype not in scope.READ_TYPES):
        # A False result falls back to DocShare. Generic File metadata and bytes
        # are unavailable even to the uploader; the business API projects them.
        frappe.throw(_("Use the Cowork original-file service to access this file."), frappe.PermissionError)
    # Frappe 16.33 ANDs controller hooks: None also denies. True lets native
    # File/role/user permissions decide and does not independently grant access.
    return True


def reject_share(doc, method=None):
    if doc.share_doctype == SOURCE or (doc.share_doctype == "File"
            and frappe.db.exists("File", doc.share_name)
            and is_protected(frappe.get_doc("File", doc.share_name))):
        frappe.throw(_("Original files cannot be shared separately from their invoices."))


def source_query(user=None):
    # No generic list of original files. Exact metadata is served through an
    # authorized history/source API; this also avoids exposing unassigned PDFs.
    scope._reject_share_fallback(SOURCE, user)
    return "1=0"


def file_query(user=None):
    if not frappe.db.table_exists(SOURCE):
        return ""
    user = user or frappe.session.user
    shared = frappe.get_all("DocShare", filters={"share_doctype": "File"},
                           or_filters={"user": user, "everyone": 1}, pluck="share_name", limit_page_length=0)
    for name in shared:
        if frappe.db.exists("File", name) and is_protected(frappe.get_doc("File", name)):
            frappe.throw(_("Remove legacy document shares before accessing Cowork records."), frappe.PermissionError)
    # Older aliases can have a noncanonical URL even though new writes reject
    # them. Inspect only unusual URLs; ordinary canonical files use the join.
    aliases = []
    for row in frappe.db.sql("""SELECT name, file_name, file_url, thumbnail_url, is_private,
            content_hash, attached_to_doctype, attached_to_name FROM `tabFile`
            WHERE COALESCE(is_folder, 0)=0 AND (COALESCE(file_url, '')=''
                OR (file_url NOT LIKE '/files/%%' AND file_url NOT LIKE '/private/files/%%')
                OR file_url LIKE '%%/./%%' OR file_url LIKE '%%/../%%'
                OR file_url LIKE '%%//%%' OR LOCATE(CHAR(37), file_url)>0
                OR (COALESCE(thumbnail_url, '')!='' AND (
                    (thumbnail_url NOT LIKE '/files/%%' AND thumbnail_url NOT LIKE '/private/files/%%')
                    OR thumbnail_url LIKE '%%/./%%' OR thumbnail_url LIKE '%%/../%%'
                    OR thumbnail_url LIKE '%%//%%' OR LOCATE(CHAR(37), thumbnail_url)>0)))""", as_dict=True):
        url = canonical_storage_url(row.file_url, row.is_private, row.file_name)
        thumbnail = canonical_storage_url(row.thumbnail_url)
        if ((url and url != row.file_url) or (thumbnail and thumbnail != row.thumbnail_url)) and is_protected(row):
            aliases.append(row.name)
    query = """NOT EXISTS (SELECT 1 FROM `tabCowork Source File` s
        LEFT JOIN `tabFile` stored ON stored.name=s.stored_file
        WHERE s.stored_file=`tabFile`.name OR s.storage_url=`tabFile`.file_url
            OR stored.file_url=`tabFile`.file_url OR s.storage_url=`tabFile`.thumbnail_url
            OR stored.file_url=`tabFile`.thumbnail_url)
        AND COALESCE(`tabFile`.attached_to_doctype, '') != 'Cowork Source File'"""
    if aliases:
        query += " AND `tabFile`.name NOT IN (" + ", ".join(frappe.db.escape(name) for name in aliases) + ")"
    return query
