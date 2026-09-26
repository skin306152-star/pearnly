"""盘点页的接口（Cowork）：**与 ERP 盘点页同名、同形**，让页面照搬 ERP 的前端文件。

用户 2026-09-26：“只去掉盘点在库就好了，其它照搬，不要自己画。” 所以页面文件
（`public/desk/stocktake.js`、`stocktake-entry.js`）是 ERP 那两份的原样副本，只改接口前缀与词条前缀；
这里把 ERP 页面要的接口名与返回形状，接到 Cowork 自己的盘点服务上（`service`、`upload`、`counting`）。

对应关系（ERP 的“商品”在 Cowork 就是任务里的那一行，一个商品只有一行）：
  任务 name = 任务 id；状态 active → "Active"、closed → "Closed"；
  行 name = product = 行 id；仓库没有主档，warehouse_name 就是填写的仓库名；
  没有成本 → can_view_costs = False；没有库存账 → 不会有“库存已变动”的冲突。
"""
import hashlib
import json
from decimal import Decimal
from uuid import uuid4

import frappe
from frappe.core.doctype.access_log.access_log import make_access_log

from pearnly_cowork.access import scope
from pearnly_cowork.stocktake import access, counting, service, upload

TASK, ITEM, ENTRY, OPERATION = access.TYPES
SORTS = {"title": "title", "status": "status", "item_total": "total", "counted_total": "counted",
         "difference_total": "differences", "creation": "created_at"}


def _float(value):
    return None if value in (None, "") else float(Decimal(str(value)))


def _status(value):
    return "Active" if value == "active" else "Closed"


def _transaction(function, *args, **kwargs):
    for attempt in range(3):
        try:
            return function(*args, **kwargs)
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
            if attempt == 2:
                raise


# ── 列表 / 新建 / 删除 ────────────────────────────────────────────────────────


@frappe.whitelist()
def api_list_stock_counts(start=0, limit=20, sort_by=None, sort_order=None, status=None):
    result = upload.records(start, limit, SORTS.get(sort_by or "", "created_at"), sort_order or "desc")
    return [{"name": row["id"], "title": row["name"], "status": _status(row["status"]),
             "item_total": row["total"], "counted_total": row["counted"],
             "difference_total": row["differences"], "creation": row["created_at"]} for row in result["tasks"]]


@frappe.whitelist()
def api_stock_count_total(status=None):
    workspace = scope.default_workspace()
    access.require(workspace)
    return frappe.db.count(TASK, {"workspace": workspace})


@frappe.whitelist(methods=["POST"])
def api_stock_count_delete(name):
    return _transaction(upload.delete, [name])


def _uploaded(file_url):
    """新建弹窗用官方上传器传的表：只认本人刚传的私有文件。读完即删（表的内容已进任务）。"""
    row = frappe.db.get_value("File", {"file_url": file_url, "owner": frappe.session.user, "is_private": 1,
                                       "attached_to_doctype": ("is", "not set")}, "name", order_by="creation desc")
    if not row:
        service.fail("stocktake.file_invalid")
    doc = frappe.get_doc("File", row)
    return doc, doc.get_content()


@frappe.whitelist(methods=["POST"])
def api_create_stock_count(title, file_url=None, notes=None, warehouse=None):
    # 只有“上传导入”一种（Cowork 没有库存系统，去掉了“盘点在库”）。
    if not file_url:
        service.fail("stocktake.file_invalid")
    doc, content = _uploaded(file_url)
    created = _transaction(upload.create, title, str(uuid4()), content if isinstance(content, bytes) else content.encode())
    if notes:
        frappe.db.set_value(TASK, created["id"], "notes", str(notes)[:2000], update_modified=False)
    frappe.delete_doc("File", doc.name, ignore_permissions=True, force=True)
    return {"name": created["id"], "rows": created["imported"], "counted": created["counted"],
            "problems": created["problems"]}


@frappe.whitelist(methods=["GET"])
def api_stock_count_template(warehouse=None, name=None):
    content = upload.template()
    frappe.local.response.update(type="download", filename="cowork-stock-count-template.xlsx",
        filecontent=content, display_content_as="attachment",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ── 工作台：复核快照 ─────────────────────────────────────────────────────────


def _review(name):
    task = service._task(name)
    snapshot = service.report(name)
    live = {}
    for entry in snapshot["entries"]:
        if not entry["voided"]:
            live.setdefault(entry["item_id"], []).append(entry)
    items = []
    for row in snapshot["items"]:
        entries = live.get(row["id"], [])
        counted = row["actual_qty"] is not None
        items.append({
            "name": row["id"], "product": row["id"], "product_name": row["product_name"],
            "code": row["product_code"], "barcode": row["barcode"], "unit": row["unit"],
            "warehouse": row["warehouse"], "warehouse_name": row["warehouse"] or "", "location": row["location"],
            "book_qty": _float(row["book_qty"]), "counted": int(counted),
            "counted_qty": _float(row["actual_qty"]) if counted else None,
            "difference": _float(row["difference"]) if counted else None, "difference_value": None,
            "actual_positions": " / ".join(f"{e['warehouse']} · {e['location'] or '—'} ({_float(e['quantity']):g})"
                                           for e in entries),
            "location_difference": any((e["warehouse"], e["location"] or "") != (row["warehouse"] or "-", row["location"] or "")
                                       and (row["warehouse"] or row["location"]) for e in entries),
            "stock_changed": False,
        })
    items.sort(key=lambda r: (r["product_name"] or "", r["name"]))
    notes = frappe.db.get_value(TASK, task.name, "notes")
    head = {"name": task.name, "title": task.title, "status": _status(task.status), "warehouse": "",
            "warehouse_name": "", "notes": notes, "item_total": snapshot["total"],
            "counted_total": snapshot["counted"], "difference_total": snapshot["differences"],
            "closed_at": task.closed_at, "closed_by": task.closed_by}
    entries = [_entry(e) for e in snapshot["entries"]]
    token = hashlib.sha256(json.dumps([head, items, entries], sort_keys=True, default=str).encode()).hexdigest()
    return {"task": head, "items": items, "entries": entries, "review_token": token,
            "can_write": task.status == "active" and access.allowed(),
            "can_view_costs": False, "conflicts": [],
            "location_total": sum(bool(r["location_difference"]) for r in items)}


def _entry(entry):
    note = frappe.db.get_value(ENTRY, entry["id"], "note")
    return {"name": entry["id"], "item": entry["item_id"], "product": entry["item_id"],
            "warehouse": entry["warehouse"], "warehouse_name": entry["warehouse"], "location": entry["location"],
            "qty": _float(entry["quantity"]), "voided": int(bool(entry["voided"])), "version": entry["version"],
            "note": note or "", "counted_by": entry.get("counted_by_name") or entry.get("counted_by"),
            "counted_at": entry["counted_at"],
            "photo_total": frappe.db.count("File", {"attached_to_doctype": ENTRY, "attached_to_name": entry["id"]})}


@frappe.whitelist()
def api_stock_count_review(name):
    return _transaction(_review, name)


@frappe.whitelist(methods=["POST"])
def api_stock_count_close(name, review_token=None):
    return _transaction(service.close, name)


@frappe.whitelist()
def api_stock_count_export(name):
    content = _transaction(service.export, name)
    make_access_log(doctype=TASK, document=name, file_type="stocktake_workbook")
    frappe.local.response.update(type="download", filename="cowork-stocktake.xlsx", filecontent=content,
        display_content_as="attachment", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    frappe.local.cowork_private_response = True


# ── 逐笔记录 ─────────────────────────────────────────────────────────────────


@frappe.whitelist()
def api_stock_count_lookup(count, code=None, product=None):
    found = _transaction(counting.lookup, count, code, product)["item"]
    row = next(r for r in _review(count)["items"] if r["name"] == found["id"])
    return {"product": {"name": found["id"], "product_name": found["product_name"], "code": found["product_code"],
                        "barcode": found["barcode"], "base_unit": found["unit"], "unit": found["unit"]},
            "rows": [row], "elsewhere": [], "stock_token": "", "warehouse_name": found["warehouse"] or ""}


def _note(entry_id, note):
    if note is not None:
        frappe.db.set_value(ENTRY, entry_id, "note", str(note)[:2000], update_modified=False)


def _add(count, qty, item, product, warehouse, location, note, request_id, photos):
    item_id = item or product
    result = service.write(count, item_id, request_id or str(uuid4()), qty, warehouse or "", location or "")
    _note(result["entry_id"], note)
    counting.attach_photos(count, result["entry_id"], photos)
    return {"entry": result["entry_id"], "item": item_id, "qty": _float(qty), "deduped": False}


@frappe.whitelist(methods=["POST"])
def api_stock_count_add_entry(count, qty, item=None, product=None, warehouse=None, location=None, note=None,
                              request_id=None, photos=None, stock_token=None, code=None):
    return _transaction(_add, count, qty, item, product, warehouse, location, note, request_id, photos)


def _edit(entry, qty, voided, version, photos, location, warehouse, note):
    doc = frappe.get_doc(ENTRY, entry)
    voided = voided in (True, 1, "1", "true")
    result = service.write(doc.task, doc.name, str(uuid4()), qty if qty is not None else doc.quantity,
                           warehouse if warehouse is not None else doc.warehouse,
                           location if location is not None else (doc.location or ""),
                           version=int(version) if version is not None else int(doc.version), voided=voided)
    _note(doc.name, note)
    if photos:
        counting.attach_photos(doc.task, doc.name, photos)
    return {"entry": doc.name, "item": doc.item, "voided": voided, "version": result["version"]}


@frappe.whitelist(methods=["POST"])
def api_stock_count_edit_entry(entry, qty=None, voided=None, version=None, photos=None, location=None,
                               warehouse=None, note=None, stock_token=None):
    return _transaction(_edit, entry, qty, voided, version, photos, location, warehouse, note)


@frappe.whitelist(methods=["POST"])
def api_stock_count_recount(count, product):
    return _transaction(counting.recount, count, product)


@frappe.whitelist()
def api_stock_count_photos(entry):
    task = frappe.db.get_value(ENTRY, entry, "task")
    return [{"file_url": row["file_url"], "file_name": row["file_name"]}
            for row in _transaction(counting.entry_photos, task, entry)]


@frappe.whitelist()
def api_stock_count_item_photos(count, item):
    data = _transaction(counting.item_entries, count, item)
    return [{"file_url": row["file_url"], "file_name": row["file_name"], "entry": row["entry_id"],
             "qty": _float(row["quantity"]), "warehouse_name": row["warehouse"], "location": row["location"],
             "voided": row["voided"], "counted_at": row["counted_at"]} for row in data["photos"]]
