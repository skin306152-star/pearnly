"""库存单附件（入库 / 出库单附图片、PDF，或当场拍照；岳父 10-25，主会话 10-26 定）。

- 上传走统一原件层（`recognition.originals`，方向 ``stock_in`` / ``stock_out``，页面先压缩照片），本机只留登记 File；
- 挂接用原生 ``attached_to_doctype / attached_to_name``（不另建子表）：只挂本人刚上传、私有、没挂过别的单的 File（同采购）；
- 每张单最多 :data:`LIMIT` 个；已提交 / 作废的单只能加不能删（留证据），草稿可删；作废的单不再加；
- 读写都跟着单据：看要能读这张单，加 / 删要有这个方向的开单能力（`stock.in` / `stock.out`）与这个仓（`inventory.access`）。
不写 Express、不识别、不扣费。
"""
import frappe
from frappe import _
from pearnly_core.originals import registered

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.inventory import documents

DOCTYPE = "ERP Stock Entry"
LIMIT = 10
LIMIT_ERROR = "stock.attachmentLimit"
CANCELLED = "Document has been cancelled"


def _entry(name, write=False):
    guard.require("stock.read")
    doc = documents._get(name)
    guard.require("stock." + doc.direction)
    if write and doc.status == "void":
        frappe.throw(_(CANCELLED))
    return doc


def _attached(name):
    return frappe.get_all("File", filters={"attached_to_doctype": DOCTYPE, "attached_to_name": name},  # bounded: 一张单最多 10 个
                          fields=["name", "file_url", "file_name", "owner", "creation"], order_by="creation asc",
                          limit_page_length=LIMIT * 2)


def files(name) -> list:
    """这张单的附件：``[{name, file_url, file_name}]``（原文件名，原件在原件层的取登记里的）。"""
    _entry(name)
    return [{"name": row.name, "file_url": row.file_url, "file_name": registered.filename(row.name)}
            for row in _attached(name)]


def _owned(file_name, doc=None):
    """只认本人刚上传、私有、没挂过别的单的 File（挂过这张单的照常认）。"""
    if not isinstance(file_name, str) or not frappe.db.exists("File", file_name):
        frappe.throw(_("File not found"))
    file = frappe.get_doc("File", file_name)
    same = doc is not None and file.attached_to_doctype == DOCTYPE and file.attached_to_name == doc.name
    if not file.is_private or (not same and (file.owner != frappe.session.user or file.attached_to_name)):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    return file, same


def check(file_names, existing=0) -> list:
    """建单前先核：每份都能挂、总数不过上限（建了单才发现挂不上会留下没附件的单）。"""
    names = [name for name in dict.fromkeys(file_names or []) if name]
    for name in names:
        _owned(name)
    if existing + len(names) > LIMIT:
        frappe.throw(_(LIMIT_ERROR).format(LIMIT))
    return names


def bind(doc, file_names) -> int:
    """把这些 File 挂到单上（已挂在这张单上的跳过），回新挂的份数。"""
    names = [name for name in dict.fromkeys(file_names or []) if name]
    have = {row.name for row in _attached(doc.name)}
    new = [name for name in names if name not in have]
    if len(have) + len(new) > LIMIT:
        frappe.throw(_(LIMIT_ERROR).format(LIMIT))
    for name in new:
        file, _same = _owned(name, doc)
        file.db_set({"attached_to_doctype": DOCTYPE, "attached_to_name": doc.name}, update_modified=False)
    return len(new)


def add(name, file_names) -> list:
    doc = _entry(name, write=True)
    frappe.db.sql("select name from `tabERP Stock Entry` where name=%s for update", (name,))
    bind(doc, file_names)
    frappe.db.commit()
    return files(name)


def remove(name, file_name) -> list:
    """删一个附件：只有草稿单能删（已提交 / 作废的只能加不能删，主会话 10-26）。删登记 File，原件层那份随后台一起真删。"""
    doc = _entry(name, write=True)
    if doc.status != "draft":
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    file = frappe.db.get_value("File", file_name, ["attached_to_doctype", "attached_to_name"], as_dict=True) if file_name else None
    if not file or file.attached_to_doctype != DOCTYPE or file.attached_to_name != doc.name:
        frappe.throw(_("File not found"))
    frappe.delete_doc("File", file_name, ignore_permissions=True)
    frappe.db.commit()
    return files(name)


__all__ = ["LIMIT", "add", "bind", "check", "files", "remove"]
