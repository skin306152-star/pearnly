"""删除识别记录。来源：旧线上 `routes/history_routes.py::history_batch_delete`（ec2e5c957e6b）。

旧版：一次最多 500 条，删本租户看得见的记录，回 `deleted` / `failed` 两个数；原件 PDF 在没有
别的记录再引用时一并删掉。

现在（用户 2026-10-08「删就删除所有记录和储存，不要给服务器带来负担」）：删就删干净 ——
  · 这张票的推送记录、推送尝试、推送通知、纠错样本一起删；
  · 原件（含识别批次、50 ทวิ 参考记录、服务器上的文件、原件桶里的原件与预览）在没有别的票再用时一起删
    （`files.service.purge`）；
  · 只有推送还没出结论的票（准备好 / 排队 / 小助手在写 / 结果不明）不删，计入 `skipped`：推送收尾要回写这张票。
扣费流水在控制面的账本里，不在这里，删票不改余额。
"""
import frappe
from frappe import _
from pearnly_cowork.access import scope
from pearnly_cowork.files import service as files
from . import storage

MAX_RECORDS = 500
# 只有本服务核过权限与推送之后才给识别记录的 `on_trash` 放行（见 DocType 控制器）。
DELETE = object()
RECORD = "Cowork Push Record"
# 推送还没出结论：收尾（成功回写、失败记原因、结果核实）都要读这张票。
_IN_FLIGHT = ("prepared", "pending", "leased")


def _pushing(doc):
    if doc.get("last_push_status") == "pending":
        return True
    records = frappe.get_all(RECORD, filters={"history": doc.name}, fields=["name", "status", "result_unknown"],
                             ignore_permissions=True)
    return any(row.status in _IN_FLIGHT or row.result_unknown for row in records) or bool(
        records and frappe.db.exists("Cowork Push Writer Lock", {"holder": ["in", [row.name for row in records]]}))


def _drop_related(name):
    records = frappe.get_all(RECORD, filters={"history": name}, pluck="name", ignore_permissions=True)
    if records:
        frappe.db.delete("Cowork Push Attempt", {"push_record": ["in", records]})
        frappe.db.delete("Cowork Push Notification", {"push_record": ["in", records]})
        frappe.db.delete(RECORD, {"name": ["in", records]})
    frappe.db.delete("Cowork Push Notification", {"history": name})
    frappe.db.delete("Cowork OCR Correction", {"source_history": name})


def delete(records):
    scope.require("intake")
    names = storage.parse(records)
    if (not isinstance(names, list) or not 1 <= len(names) <= MAX_RECORDS
            or any(not isinstance(name, str) or not name or len(name) > 140 for name in names)):
        frappe.throw(_("Select between 1 and 500 records."))
    deleted = skipped = 0
    sources = []
    for name in dict.fromkeys(names):
        if not frappe.db.get_value(storage.DOCTYPE, name, "name", for_update=True):
            skipped += 1
            continue
        doc = frappe.get_doc(storage.DOCTYPE, name)
        scope.require_scope(doc.workspace, doc.client)
        if _pushing(doc):
            skipped += 1
            continue
        if doc.source_file:
            sources.append(doc.source_file)
        _drop_related(name)
        # 不在「Deleted Document」里留备份（删就删干净，不占库）。
        frappe.delete_doc(storage.DOCTYPE, name, force=True, ignore_permissions=True, delete_permanently=True,
                          flags={"cowork_history_delete": DELETE})
        deleted += 1
    if sources:
        files.purge(sources)
    return {"deleted": deleted, "skipped": skipped}
