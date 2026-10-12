"""按需请求表（Cowork Companion Request）的守卫：原生接口的读、建、改、删、分享一律拒绝，只经 `request_service` 带写入令牌写。"""
import frappe
from frappe import _

REQUEST = "Cowork Companion Request"
DOCTYPES = (REQUEST,)
_WRITE = object()


def deny(*args, **kwargs):
    frappe.throw(_("Not Permitted"), frappe.PermissionError)


def deny_query(*args, **kwargs):
    return "1=0"


def reject_share(doc, method=None):
    if doc.share_doctype in DOCTYPES:
        deny()


def guard(doc):
    if doc.flags.cowork_companion_request_write is not _WRITE:
        deny()


def write(doc):
    """建或存一行（调用方已按当前身份或机器身份核过）。"""
    doc.flags.cowork_companion_request_write = _WRITE
    doc.flags.ignore_permissions = True
    doc.flags.ignore_version = True
    if doc.is_new():
        doc.insert(ignore_permissions=True)
    else:
        doc.save(ignore_permissions=True)
    return doc


def read(name, *, for_update=False):
    doc = frappe.get_doc(REQUEST, name, for_update=for_update)
    doc.flags.cowork_companion_request_write = _WRITE
    return doc
