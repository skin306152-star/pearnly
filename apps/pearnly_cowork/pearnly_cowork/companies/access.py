"""公司主档四张私有表（`Cowork Company`、`Cowork Company Note`，ADR-020；`Cowork Company Choice`，第 2 期公司规则；
`Cowork Company Automation`，第 3 期自动推送档位）的守卫：原生接口的读、建、改、删、分享一律拒绝，
只经本域带写入令牌写（同 `push/choices.py`）。"""
import frappe
from frappe import _

COMPANY = "Cowork Company"
NOTE = "Cowork Company Note"
CHOICE = "Cowork Company Choice"
AUTOMATION = "Cowork Company Automation"
DOCTYPES = (COMPANY, NOTE, CHOICE, AUTOMATION)
_WRITE = object()


def deny(*args, **kwargs):
    frappe.throw(_("Not Permitted"), frappe.PermissionError)


def deny_query(*args, **kwargs):
    return "1=0"


def reject_share(doc, method=None):
    if doc.share_doctype in DOCTYPES:
        deny()


def guard(doc):
    if doc.flags.cowork_company_write is not _WRITE:
        deny()


def write(doc, *, versioned=False):
    """建或存一行（调用方已按当前身份核过权限）。``versioned``：这次改动留原生修改记录（Version：谁、什么时候、从什么改成
    什么；只给要追溯的改动用，例如公司税号 `service.set_own_tax`），其余照旧不留。"""
    doc.flags.cowork_company_write = _WRITE
    doc.flags.ignore_permissions = True
    doc.flags.ignore_version = not versioned
    if doc.is_new():
        doc.insert(ignore_permissions=True)
    else:
        doc.save(ignore_permissions=True)
    return doc


def read(doctype, name, *, for_update=False):
    doc = frappe.get_doc(doctype, name, for_update=for_update)
    doc.flags.cowork_company_write = _WRITE
    return doc
