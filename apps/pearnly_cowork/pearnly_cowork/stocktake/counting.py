"""在线盘点：查商品、逐笔记录的照片、重盘（照 ERP `inventory/count.py` 的同名做法）。

用户 2026-09-26：Cowork 保留「开始盘点 / 逐笔记录 / 重盘 / 照片」，只去掉「盘点在库」
（Cowork 没有库存系统，任务只能从上传的表建）。逐笔记录本身仍走 `service.write`
（任务行锁、版本号、操作回执与重放），这里只补它周围的几件事。

照片：桌面端用官方上传器存成**私有** File，提交记录时把 file_url 挂到这条记录上
（一条最多 5 张）。只收本人刚上传、还没挂到任何单据上的文件——别人的文件、已挂在别处的文件
一律不收，防止借记录读走不属于自己的附件。
"""
from uuid import NAMESPACE_URL, uuid5

import frappe
from frappe import _

from pearnly_cowork.stocktake import access, service

TASK, ITEM, ENTRY, OPERATION = access.TYPES
PHOTO_LIMIT = 5


def lookup(task_id, code=None, item_id=None):
    """编码或条码（精确）→ 本任务里的那一行；打开已有行时直接给 item_id。"""
    task = service._task(task_id)
    code = str(code or "").strip()
    if item_id:
        row = service._linked(ITEM, service.contract(service.identifier, item_id), task)
    elif code:
        names = frappe.db.sql("SELECT name FROM `tabCowork Stocktake Item` WHERE task=%s AND workspace=%s "
                              "AND (product_code=%s OR (barcode<>'' AND barcode=%s)) LIMIT 2",
                              (task.name, task.workspace, code, code), pluck=True)
        if len(names) != 1:
            service.fail("stocktake.not_found")
        row = frappe.get_doc(ITEM, names[0])
    else:
        service.fail("stocktake.request_invalid")
    return {"item": service._item_data(row), "active": task.status == "active"}


def attach_photos(task_id, entry_id, file_urls):
    file_urls = [str(url).strip() for url in (service.parse(file_urls) or []) if str(url or "").strip()]
    if not file_urls:
        return 0
    task = service._task(task_id)
    entry = service._linked(ENTRY, service.contract(service.identifier, entry_id), task)
    existing = frappe.db.count("File", {"attached_to_doctype": ENTRY, "attached_to_name": entry.name})
    fresh = []
    for url in file_urls:
        row = frappe.db.get_value("File", {"file_url": url, "owner": frappe.session.user, "is_private": 1},
                                  ["name", "attached_to_doctype", "attached_to_name"], as_dict=True,
                                  order_by="creation desc")
        if not row:
            service.fail("stocktake.photo_invalid")
        if row.attached_to_doctype == ENTRY and row.attached_to_name == entry.name:
            continue  # 同一请求重放：已经挂好了
        if row.attached_to_doctype or row.attached_to_name:
            service.fail("stocktake.photo_invalid")
        fresh.append(row.name)
    if existing + len(fresh) > PHOTO_LIMIT:
        frappe.throw(_("stocktake.photo_limit").format(PHOTO_LIMIT))
    for name in fresh:
        frappe.db.set_value("File", name, {"attached_to_doctype": ENTRY, "attached_to_name": entry.name},
                            update_modified=False)
    return len(fresh)


def _photos(task, entries):
    if not entries:
        return []
    by_entry = {row["id"]: row for row in entries}
    files = frappe.get_all("File", filters={"attached_to_doctype": ENTRY, "attached_to_name": ["in", list(by_entry)]},
                           fields=["name", "file_url", "file_name", "attached_to_name"], order_by="creation asc")
    return [{"file_url": row.file_url, "file_name": row.file_name, "entry_id": row.attached_to_name,
             **{key: by_entry[row.attached_to_name].get(key) for key in ("warehouse", "location", "quantity",
                                                                           "voided", "counted_at")}}
            for row in files]


def item_entries(task_id, item_id):
    """记录弹窗：这一行的全部逐笔记录（含作废的，审计要看）+ 照片。"""
    task = service._task(task_id)
    item = service._linked(ITEM, service.contract(service.identifier, item_id), task)
    entries = [row for row in service._entries(task) if row["item_id"] == item.name]
    return {"item": service._item_data(item), "entries": entries, "photos": _photos(task, entries),
            "active": task.status == "active"}


def entry_photos(task_id, entry_id):
    task = service._task(task_id)
    entry = service._linked(ENTRY, service.contract(service.identifier, entry_id), task)
    return _photos(task, [{"id": entry.name, **service._entry_data(entry)}])


def recount(task_id, item_id):
    """重盘：这一行的有效记录全部作废（留作审计），实盘回到“未盘”。

    每一笔作废都是一次正常的改记录（版本号 + 操作回执）；请求号由记录与版本推出，
    所以重复点只会发现已经没有可作废的记录，不会重复写。
    """
    task = service._task(task_id)
    item = service._linked(ITEM, service.contract(service.identifier, item_id), task)
    live = frappe.db.sql("SELECT name, version, warehouse, location, quantity FROM `tabCowork Stocktake Entry` "
                         "WHERE task=%s AND workspace=%s AND item=%s AND voided=0 ORDER BY counted_at, name",
                         (task.name, task.workspace, item.name), as_dict=True)
    for row in live:
        request_id = str(uuid5(NAMESPACE_URL, f"cowork-stocktake-recount:{row.name}:{row.version}"))
        service.write(task.name, row.name, request_id, row.quantity, row.warehouse, row.location or "",
                      version=row.version, voided=True)
    return {"ok": True, "voided": len(live)}
