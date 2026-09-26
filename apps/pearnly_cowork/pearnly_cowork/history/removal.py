"""删除识别记录。来源：旧线上 `routes/history_routes.py::history_batch_delete`（ec2e5c957e6b）。

旧版：一次最多 500 条，删本租户看得见的记录，回 `deleted` / `failed` 两个数；原件 PDF 在没有
别的记录再引用时一并删掉。

新平台的有意差异：
  · 已推送的（有推送记录 / 推送通知）不删，计入 `skipped` —— 推送账本要能追到推的是哪张票；
    与 ERP 列表「能删的删，不能删的留着」同一口径（用户 2026-09-26）；
  · 原件（Cowork Source File）不删：计费批次引用它作凭证；同一份原件里没删的票照常能看页图；
  · 纠错样本（Cowork OCR Correction）按设计不可删，原样留着：学习取样按内连接取记录，记录删了
    它就不再参与。删除时因此跳过框架的关联检查（`force`），推送这两类关联已在前面核过；
  · 框架照常在「Deleted Document」里留一份删除前的备份。
"""
import frappe
from frappe import _
from pearnly_cowork.access import scope
from . import storage

MAX_RECORDS = 500
# 只有本服务核过权限与推送之后才给识别记录的 `on_trash` 放行（见 DocType 控制器）。
DELETE = object()
_PUSH_LINKS = (("Cowork Push Record", "history"), ("Cowork Push Notification", "history"))


def _pushed(doc):
    return bool(doc.get("last_push_status")) or any(
        frappe.db.exists(doctype, {field: doc.name}) for doctype, field in _PUSH_LINKS)


def delete(records):
    scope.require("intake")
    names = storage.parse(records)
    if (not isinstance(names, list) or not 1 <= len(names) <= MAX_RECORDS
            or any(not isinstance(name, str) or not name or len(name) > 140 for name in names)):
        frappe.throw(_("Select between 1 and 500 records."))
    deleted = skipped = 0
    for name in dict.fromkeys(names):
        if not frappe.db.get_value(storage.DOCTYPE, name, "name", for_update=True):
            skipped += 1
            continue
        doc = frappe.get_doc(storage.DOCTYPE, name)
        scope.require_scope(doc.workspace, doc.client)
        if _pushed(doc):
            skipped += 1
            continue
        frappe.delete_doc(storage.DOCTYPE, name, force=True, ignore_permissions=True,
                          flags={"cowork_history_delete": DELETE})
        deleted += 1
    return {"deleted": deleted, "skipped": skipped}
