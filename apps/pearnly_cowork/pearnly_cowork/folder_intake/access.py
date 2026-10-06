"""文件夹自动录入三张私有表的守卫：原生接口的读、建、改、删、分享一律拒绝，只经本域带写入令牌写。"""
import frappe
from frappe import _

RULE = "Cowork Folder Intake Rule"
RUN = "Cowork Folder Intake Run"
FILE = "Cowork Folder Intake File"
DOCTYPES = (RULE, RUN, FILE)
_WRITE = object()


def deny(*args, **kwargs):
    frappe.throw(_("Not Permitted"), frappe.PermissionError)


def deny_query(*args, **kwargs):
    return "1=0"


def reject_share(doc, method=None):
    if doc.share_doctype in DOCTYPES:
        deny()


def guard(doc):
    if doc.flags.cowork_folder_write is not _WRITE:
        deny()


def write(doc):
    """建或存一行（调用方已按当前身份核过权限）。"""
    doc.flags.cowork_folder_write = _WRITE
    doc.flags.ignore_permissions = True
    doc.flags.ignore_version = True
    if doc.is_new():
        doc.insert(ignore_permissions=True)
    else:
        doc.save(ignore_permissions=True)
    return doc


def read(doctype, name, *, for_update=False):
    doc = frappe.get_doc(doctype, name, for_update=for_update)
    doc.flags.cowork_folder_write = _WRITE
    return doc
