"""Frappe v16 File extensions for Cowork originals only.

Private downloads call File.is_downloadable, and ZIP calls get_content after a
different owner/share check. Guard both; a DocType permission hook is not enough.
"""
import mimetypes

import frappe
from frappe import _
from frappe.core.doctype.file.exceptions import MaxFileSizeReachedError
from frappe.core.doctype.file.utils import decode_file_content, generate_file_name, get_content_hash
from frappe.utils import call_hook_method, get_hook_method
from pearnly_cowork.files import access
from pearnly_cowork.ocr.inputs import MAX_FILE_BYTES

WRITE_CAPABILITY = object()
READ_CAPABILITY = object()
MAX_ORIGINAL_BYTES = MAX_FILE_BYTES


def _write_capability(doc):
    return doc.flags.get("cowork_original_write") is WRITE_CAPABILITY


def _original_write(doc):
    return (_write_capability(doc) and doc.get("attached_to_doctype") == access.SOURCE
            and bool(doc.get("attached_to_name")))


def mutation(doc):
    if not _write_capability(doc) and access.is_protected(doc, include_hash=True):
        frappe.throw(_("Original files cannot be changed."), frappe.PermissionError)


def _content_mutation(doc, content):
    if content and not _write_capability(doc):
        snapshot = frappe._dict({key: doc.get(key) for key in access.FILE_FIELDS})
        snapshot.content_hash = get_content_hash(content)
        snapshot.flags = frappe._dict()
        mutation(snapshot)


def validate_source(doc):
    if not _write_capability(doc):
        frappe.throw(_("Original files can only be changed through the Cowork file service."), frappe.PermissionError)


def deny_delete(doc, method=None):
    frappe.throw(_("Original files cannot be deleted while invoice references may exist."), frappe.PermissionError)


class CoworkFileGuard:
    def is_downloadable(self):
        if access.is_protected(self):
            # Direct /private/files requests bypass get_content and can be
            # cached by nginx. Originals are served only by the no-store API.
            return False
        return super().is_downloadable()

    def get_content(self, encodings=None):
        if access.is_protected(self):
            if self.flags.get("cowork_original_read") is not READ_CAPABILITY and not _write_capability(self):
                frappe.throw(_("Use the Cowork original-file service to access this file."), frappe.PermissionError)
            encodings = []  # Native text detection must not change original bytes.
        return super().get_content(encodings=encodings)

    def before_insert(self):
        mutation(self)
        result = super().before_insert()
        mutation(self)  # Native URL normalization/deduplication can create an alias.
        return result

    def validate(self):
        mutation(self)  # Before native validate can move private files to /files.
        return super().validate()

    def before_validate(self):
        mutation(self)  # Frappe still calls this with flags.ignore_validate=True.
        inherited = getattr(super(), "before_validate", None)
        if inherited:
            return inherited()

    def on_trash(self):
        mutation(self)
        return super().on_trash()

    def save_file(self, content=None, decode=False, ignore_existing_file_check=False, overwrite=False):
        mutation(self)
        if _original_write(self):
            return self._save_original(content, decode=decode, overwrite=overwrite)
        # A fresh File has neither URL nor native hash yet. Detect copied source
        # bytes before native deduplication assigns it a protected URL.
        candidate = content or self.get("content") or self.get("_content")
        if candidate:
            if decode or self.get("decode"):
                candidate = decode_file_content(candidate)
            _content_mutation(self, candidate)
        result = super().save_file(content=content, decode=decode,
            ignore_existing_file_check=ignore_existing_file_check, overwrite=overwrite)
        mutation(self)
        return result

    def _save_original(self, content=None, *, decode=False, overwrite=False):
        """Use native storage primitives, omitting transformations and dedup only.

        Frappe's ordinary save_file may strip JPEG EXIF and reuse another File's
        physical path. An uploaded original must retain its bytes and own path.
        This branch is reachable only with an in-process capability, for a new
        Source attachment. All ordinary files retain native save_file behavior.
        """
        if (not self.is_new() or overwrite or not self.is_private
                or not frappe.db.exists(access.SOURCE, self.attached_to_name)):
            frappe.throw(_("Original files cannot be changed."), frappe.PermissionError)
        if self.get("file_url") or frappe.db.get_value(access.SOURCE, self.attached_to_name, "stored_file"):
            frappe.throw(_("An original file is already stored for this source."), frappe.PermissionError)
        if content is not None:
            self.content = content
            self.decode = decode
        original = self.get_content(encodings=[])
        if not isinstance(original, bytes) or not original:
            frappe.throw(_("The uploaded file is empty."))
        self._content = original
        self.is_private = 1
        self.file_size = self.check_max_file_size()
        self.content_hash = get_content_hash(original)
        self.content_type = mimetypes.guess_type(self.file_name)[0]
        self.file_name = generate_file_name(self.file_name, suffix=self.content_hash[-6:], is_private=True)
        # Keep native content checks (including PDF JavaScript), storage hooks
        # and write/rollback lifecycle. No global settings are changed.
        self.check_content()
        call_hook_method("before_write_file", file_size=self.file_size)
        writer = get_hook_method("write_file")
        return writer(self) if writer else self.save_file_on_filesystem()

    def check_max_file_size(self):
        if not _original_write(self):
            # Native save_file calls this after optional EXIF stripping but
            # before deduplication/custom storage hooks. Recheck transformed
            # bytes here, before a hook could write a protected copy publicly.
            _content_mutation(self, self.get("_content"))
            return super().check_max_file_size()
        size = len(self.get("_content") or b"")
        if size > MAX_ORIGINAL_BYTES:
            frappe.throw(_("The uploaded file exceeds the 100 MiB limit."), MaxFileSizeReachedError)
        return size

    def write_file(self, *args, **kwargs):
        mutation(self)
        return super().write_file(*args, **kwargs)

    def save_file_on_filesystem(self, *args, **kwargs):
        mutation(self)
        return super().save_file_on_filesystem(*args, **kwargs)

    def handle_is_private_changed(self, *args, **kwargs):
        mutation(self)
        return super().handle_is_private_changed(*args, **kwargs)

    def make_thumbnail(self, *args, **kwargs):
        mutation(self)  # Native thumbnail path reads and writes without get_content.
        return super().make_thumbnail(*args, **kwargs)

    def unzip(self, *args, **kwargs):
        mutation(self)
        return super().unzip(*args, **kwargs)

    def create_attachment_copy(self, *args, **kwargs):
        mutation(self)
        return super().create_attachment_copy(*args, **kwargs)

    @frappe.whitelist()
    def optimize_file(self, *args, **kwargs):
        mutation(self)  # Native optimize writes bytes before calling save/validate.
        return super().optimize_file(*args, **kwargs)

    def _delete_file_on_disk(self, *args, **kwargs):
        mutation(self)
        if _original_write(self):
            # Originals never deduplicate. Native cleanup checks MD5, which
            # would strand our new file when an independent attachment has
            # identical bytes. Only actual URL references prevent cleanup.
            shared = frappe.get_all("File", filters={"file_url": self.file_url,
                "name": ["!=", self.name]}, pluck="name", limit_page_length=1)
            return self.delete_file_data_content(only_thumbnail=bool(shared))
        return super()._delete_file_on_disk(*args, **kwargs)

    def delete_file_data_content(self, *args, **kwargs):
        mutation(self)
        return super().delete_file_data_content(*args, **kwargs)

    def delete_file_from_filesystem(self, *args, **kwargs):
        mutation(self)
        return super().delete_file_from_filesystem(*args, **kwargs)

    def on_rollback(self):
        if not _write_capability(self) and access.is_protected(self, include_hash=True):
            # An ordinary failed insert may have deduplicated to a protected
            # URL. It never owns those bytes; do not let native cleanup delete
            # them. Trusted new originals keep the native rollback cleanup.
            return
        return super().on_rollback()
