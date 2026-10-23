"""CC 桌面版共用机器通道的四张表只经本域写（多产品共用，ADR-025）：原生读写、查询、分享一律拒绝。

与 Cowork `companion.device_documents` 同一做法：本域写之前在文档上打一个只有本模块拿得到的标记，控制器的每个保存钩子都核它；
名字由本域定好再交进来（电脑 ``PCD-``、单据是 UUID、请求是 32 位十六进制），原生改名、删除拒绝。
"""
import frappe
from frappe import _
from frappe.model.document import Document

DEVICE = "Pearnly CC Device"
BOOK = "Pearnly CC Book"
JOB = "Pearnly CC Job"
REQUEST = "Pearnly CC Request"
DOCTYPES = (DEVICE, BOOK, JOB, REQUEST)
_WRITE = object()


def deny():
    frappe.throw(_("Not Permitted"), frappe.PermissionError)


def native_permission(doc=None, ptype=None, user=None, **kwargs):
    return False


def native_query(user=None):
    return "1=0"


def reject_share(doc, method=None):
    if doc.share_doctype in DOCTYPES:
        deny()


def new(doctype, name, values):
    doc = frappe.new_doc(doctype)
    doc.update(values)
    doc.flags.cc_desktop_name = name
    return persist(doc)


def persist(doc):
    doc.flags.cc_desktop_write = _WRITE
    doc.flags.ignore_permissions = True
    doc.flags.ignore_version = True
    doc.flags.notify_update = False
    if doc.is_new():
        return doc.insert(ignore_permissions=True)
    return doc.save(ignore_permissions=True, ignore_version=True)


def remove(doctype, name):
    """本域删一行（主机名改报后旧的可达行、取走的请求）：不经原生删除钩子的权限判断。"""
    frappe.db.delete(doctype, {"name": name})


class ChannelDocument(Document):
    def _guard(self):
        if self.flags.cc_desktop_write is not _WRITE:
            deny()

    def autoname(self):
        self._guard()
        if not self.flags.cc_desktop_name:
            deny()
        self.name = self.flags.cc_desktop_name

    def before_insert(self):
        self._guard()

    def before_validate(self):
        self._guard()

    def validate(self):
        self._guard()

    def before_save(self):
        self._guard()

    def check_permission(self, permtype="read", permlevel=None):
        self._guard()
        return True

    def has_permission(self, permtype="read", *, debug=False, user=None):
        return self.flags.cc_desktop_write is _WRITE

    def on_trash(self):
        deny()

    def before_rename(self, old, new, merge=False):
        deny()
