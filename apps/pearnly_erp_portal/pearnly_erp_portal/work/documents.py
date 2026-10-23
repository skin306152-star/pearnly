"""原生控制器的写入闸：看板、列表、卡片、标签、活动等只能由本域服务写。

原生 REST / 表单拿到的只有读权限；就算有人补了写权限，没有这里的进程内凭据也写不进去，
活动记录和位置规则不会被绕开。
"""
import frappe
from frappe import _
from frappe.model.document import Document
from pearnly_erp_portal.work import access

_WRITE_TOKEN = object()


def persist(doc):
    doc.flags.work_write = _WRITE_TOKEN
    return doc.insert(ignore_permissions=True) if doc.is_new() else doc.save(ignore_permissions=True)


def remove(doc):
    # 原生 delete_doc 会重新取一份文档，flags 带不过去；按本请求里的"正在删"名单放行。
    deleting = getattr(frappe.local, "erp_work_deleting", None)
    if deleting is None:
        deleting = frappe.local.erp_work_deleting = set()
    key = (doc.doctype, doc.name)
    deleting.add(key)
    try:
        frappe.delete_doc(doc.doctype, doc.name, ignore_permissions=True, force=True)
    finally:
        deleting.discard(key)


class WorkDocument(Document):
    def _require_service(self):
        access.require()
        if self.flags.work_write is not _WRITE_TOKEN:
            frappe.throw(_("erp.work.error.dedicated_api"), frappe.PermissionError)

    def before_insert(self):
        self._require_service()

    def before_validate(self):
        # 框架带信任标志时会跳过 validate，这里仍然要拦。
        self._require_service()

    def validate(self):
        self._require_service()

    def on_trash(self):
        access.require()
        if (self.doctype, self.name) not in getattr(frappe.local, "erp_work_deleting", ()):
            frappe.throw(_("erp.work.error.dedicated_api"), frappe.PermissionError)

    def before_rename(self, old, new, merge=False):
        frappe.throw(_("erp.work.error.dedicated_api"), frappe.PermissionError)
