"""Transactional stocktake snapshots, scan entries and immutable operation receipts.

All reads/writes stay in the current Frappe Site. A task row lock serializes
count, correction, close and report; no inventory ledger is written here.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import json
from uuid import uuid4

import frappe
from frappe import _
from pearnly_cowork.stocktake import access
from pearnly_cowork.stocktake.calculations import active_quantity, summarize_item
from pearnly_cowork.stocktake.contracts import StocktakeValidationError, entry_request
from pearnly_cowork.stocktake.documents import persist
from pearnly_cowork.stocktake.export import TEXT_KEYS, workbook
from pearnly_cowork.stocktake.native_contract import decoded_import, encode, identifier, revision, void_flag

TASK, ITEM, ENTRY, OPERATION = access.TYPES


def fail(code):
    frappe.throw(_(code))


def contract(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except StocktakeValidationError as error:
        message = _(error.code)
        if error.row_number is not None:
            message = _("Row {0}: {1}").format(error.row_number, message)
        frappe.throw(message)


def parse(value):
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        fail("stocktake.request_invalid")


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@contextmanager
def atomic():
    """Also roll back partial writes when an internal caller catches an error."""
    point = "stocktake_" + uuid4().hex
    frappe.db.savepoint(point)
    try:
        yield
    except frappe.QueryDeadlockError:
        frappe.db.rollback()
        raise
    except Exception:
        frappe.db.rollback(save_point=point)
        raise


def _task(task_id):
    access.require()
    task_id = contract(identifier, task_id)
    try:
        task = frappe.get_doc(TASK, task_id, for_update=True)
    except frappe.DoesNotExistError:
        fail("stocktake.not_found")
    access.require(task.workspace)
    return task


def _linked(doctype, name, task):
    try:
        doc = frappe.get_doc(doctype, name, for_update=True)
    except frappe.DoesNotExistError:
        fail("stocktake.not_found")
    if doc.task != task.name or doc.workspace != task.workspace:
        fail("stocktake.not_found")
    return doc


def _rows(doctype, task, order):
    # All identifiers/order clauses are internal constants. Current reads are
    # essential: permission reads may already have established an older snapshot
    # before this request acquired the task row lock (MariaDB REPEATABLE READ).
    assert doctype in (ITEM, ENTRY)
    assert order in ("product_code, warehouse, location, name", "counted_at DESC, name")
    return frappe.db.sql(f"SELECT * FROM `tab{doctype}` WHERE task=%s AND workspace=%s "
                         f"ORDER BY {order} FOR UPDATE", (task.name, task.workspace), as_dict=True)


def _new(doctype, record_id, workspace, **values):
    return frappe.get_doc({"doctype": doctype, "record_id": record_id, "workspace": workspace, **values})


def create(workspace, title, request_id, header, rows):
    access.require(workspace)
    request_id = contract(identifier, request_id)
    title, items, digest = contract(decoded_import, title, parse(header), parse(rows))
    # Decoded-import replay compares canonical row content. It does not claim
    # compatibility with legacy raw-XLSX digest keys or import existing IDs.
    if frappe.db.exists(TASK, request_id):
        return _existing(request_id, workspace, title, digest)
    with atomic():
        task = _new(TASK, request_id, workspace, title=title, source_digest=digest,
                    source_format="decoded_rows_v1", count_mode="scan", status="active",
                    created_by=frappe.session.user, created_at=now())
        frappe.db.savepoint("stocktake_create_insert")
        try:
            persist(task)
        except frappe.DuplicateEntryError:
            frappe.db.rollback(save_point="stocktake_create_insert")
            return _existing(request_id, workspace, title, digest)
        for item in items:
            persist(_new(ITEM, str(uuid4()), workspace, task=request_id, **item,
                         actual_qty=None, version=0))
    return {"id": request_id}


def _existing(task_id, workspace, title, digest):
    task = _task(task_id)
    if task.workspace != workspace or task.title != title or task.source_digest != digest:
        fail("stocktake.import_conflict")
    return {"id": task.name}


def _task_data(task):
    return {"id": task.name, "workspace": task.workspace, "name": task.title,
            **{field: task.get(field) for field in ("status", "count_mode", "created_by", "created_at",
                "closed_by", "closed_at", "source_digest", "source_format")}}


def _item_data(row):
    result = {field: row.get(field) for field in ("product_code", "product_name", "barcode", "warehouse",
              "location", "unit", "book_qty", "version", "counted_by", "counted_at")}
    result.update(id=row.name, actual_qty=row.actual_qty or None)
    result["difference"] = (format(Decimal(row.actual_qty) - Decimal(row.book_qty), ".6f")
                            if row.actual_qty else None)
    return result


def _entry_data(row):
    return {"id": row.name, "item_id": row.item, "voided": bool(row.voided),
            **{field: row.get(field) for field in ("warehouse", "location", "quantity", "version",
                "counted_by", "counted_at", "updated_by", "updated_at")}}


def _entries(task, *, start=0, limit=None):
    sql = """SELECT e.*, i.product_code, i.product_name, i.barcode, i.unit,
        COALESCE(NULLIF(u.full_name,''),u.name) AS counted_by_name,
        COALESCE(NULLIF(m.full_name,''),m.name) AS updated_by_name
        FROM `tabCowork Stocktake Entry` e JOIN `tabCowork Stocktake Item` i
        ON i.name=e.item AND i.task=e.task AND i.workspace=e.workspace
        LEFT JOIN `tabUser` u ON u.name=e.counted_by
        LEFT JOIN `tabUser` m ON m.name=e.updated_by
        WHERE e.task=%s AND e.workspace=%s ORDER BY e.counted_at DESC,e.name"""
    values = [task.name, task.workspace]
    if limit is not None:
        sql += " LIMIT %s OFFSET %s"
        values.extend((limit, start))
    rows = frappe.db.sql(sql + " FOR UPDATE", values, as_dict=True)
    return [{**_entry_data(row), **{key: row.get(key) for key in ("product_code", "product_name",
                "barcode", "unit", "counted_by_name", "updated_by_name")}} for row in rows]


def _entry_total(task):
    return frappe.db.sql("SELECT COUNT(*) FROM `tabCowork Stocktake Entry` "
        "WHERE task=%s AND workspace=%s FOR UPDATE", (task.name, task.workspace))[0][0]


def _snapshot(task, *, export=False):
    result = _task_data(task)
    result["items"] = [_item_data(row) for row in _rows(ITEM, task, "product_code, warehouse, location, name")]
    if task.count_mode == "scan":
        result["entries"] = _entries(task, limit=None if export else 100)
        result["entry_total"] = _entry_total(task)
        result["places"] = frappe.db.sql("SELECT DISTINCT warehouse, location FROM `tabCowork Stocktake Entry` "
            "WHERE task=%s AND workspace=%s AND voided=0 ORDER BY warehouse, location FOR UPDATE",
            (task.name, task.workspace), as_dict=True)
    result.update(total=len(result["items"]), counted=sum(i["actual_qty"] is not None for i in result["items"]),
                  differences=sum(i["difference"] is not None and Decimal(i["difference"]) != 0 for i in result["items"]))
    return result


def detail(task_id):
    return _snapshot(_task(task_id))


def page(start=0, limit=50):
    start, limit = contract(revision, start), contract(revision, limit)
    return start, max(1, min(limit, 100))


def records(workspace, start=0, limit=50):
    access.require(workspace)
    start, limit = page(start, limit)
    rows = frappe.get_list(TASK, filters={"workspace": workspace}, fields=["*"],
                           order_by="created_at desc, name desc", limit_start=start, limit_page_length=limit)
    result = []
    for row in rows:
        projected = _task_data(row)
        # Decimal casts preserve numeric comparison including signed zero.
        summary = frappe.db.sql("SELECT COUNT(*) AS total, COUNT(NULLIF(actual_qty,'')) AS counted, "
            "COALESCE(SUM(CASE WHEN NULLIF(actual_qty,'') IS NOT NULL AND "
            "CAST(actual_qty AS DECIMAL(20,6)) <> CAST(book_qty AS DECIMAL(20,6)) THEN 1 ELSE 0 END),0) "
            "AS differences FROM `tabCowork Stocktake Item` WHERE task=%s AND workspace=%s",
            (row.name, workspace), as_dict=True)[0]
        projected.update({key: int(summary[key]) for key in ("total", "counted", "differences")})
        result.append(projected)
    return {"tasks": result}


def entries(task_id, start=0, limit=100):
    task = _task(task_id)
    if task.count_mode != "scan":
        fail("stocktake.legacy_task")
    start, limit = page(start, limit)
    return {"entries": _entries(task, start=start, limit=limit), "total": _entry_total(task)}


def _replay(request_id, request, task):
    # A first operation is expected to have no receipt. Avoid a caught
    # get_doc/DoesNotExistError leaving a false _server_messages error in an
    # otherwise successful native RPC response.
    rows = frappe.db.sql("SELECT workspace, task, request_json, entry, version "
        "FROM `tabCowork Stocktake Operation` WHERE name=%s FOR UPDATE", (request_id,), as_dict=True)
    if not rows:
        return None
    event = rows[0]
    if event.workspace != task.workspace or event.task != task.name or parse(event.request_json) != request:
        fail("stocktake.operation_conflict")
    return {"ok": True, "entry_id": event.entry, "version": event.version}


def _state(entry):
    return {"warehouse": entry.warehouse, "location": entry.location,
            "quantity": entry.quantity, "voided": bool(entry.voided)}


def write(task_id, target_id, request_id, qty, warehouse, location="", *, version=None, voided=False):
    access.require()
    task_id, target_id, request_id = [contract(identifier, value) for value in (task_id, target_id, request_id)]
    if not isinstance(warehouse, str) or not isinstance(location, str):
        fail("stocktake.warehouse_required")
    if len(str(qty)) > 40:
        fail("stocktake.quantity_invalid")
    version = None if version is None else contract(revision, version)
    voided = contract(void_flag, voided)
    if version is None and voided:
        fail("stocktake.request_invalid")
    request = contract(entry_request, task_id, target_id, qty, warehouse, location,
                       actor_id=frappe.session.user, version=version, voided=voided)
    with atomic():
        task = _task(task_id)
        if task.count_mode != "scan":
            fail("stocktake.legacy_task")
        receipt = _replay(request_id, request, task)
        if receipt:
            return receipt
        if task.status != "active":
            fail("stocktake.closed")
        timestamp, previous = now(), None
        if version is None:
            item = _linked(ITEM, target_id, task)
            entry = _new(ENTRY, request_id, task.workspace, task=task.name, item=item.name,
                         version=0, counted_by=frappe.session.user, counted_at=timestamp)
        else:
            entry = _linked(ENTRY, target_id, task)
            if int(entry.version) != version:
                fail("stocktake.conflict")
            previous = _state(entry)
            item = _linked(ITEM, entry.item, task)
            entry.version = version + 1
        entry.warehouse, entry.location = request["warehouse"], request["location"]
        entry.quantity, entry.voided = request["quantity"], int(voided)
        entry.updated_by, entry.updated_at = frappe.session.user, timestamp
        try:
            persist(entry)
            persist(_new(OPERATION, request_id, task.workspace, task=task.name, item=item.name,
                entry=entry.name, version=entry.version, actor=frappe.session.user,
                request_json=encode(request), previous_json=encode(previous), current_json=encode(_state(entry))))
        except frappe.DuplicateEntryError:
            fail("stocktake.operation_conflict")
        # Use a current read after acquiring the task lock, including this write.
        current = frappe.db.sql("SELECT quantity, voided FROM `tabCowork Stocktake Entry` "
            "WHERE task=%s AND workspace=%s AND item=%s FOR UPDATE",
            (task.name, task.workspace, item.name), as_dict=True)
        total = contract(active_quantity, current)
        item.actual_qty = format(total, ".6f") if total is not None else None
        item.version = int(item.version) + 1
        item.counted_by, item.counted_at = frappe.session.user, timestamp
        persist(item)
        return {"ok": True, "entry_id": entry.name, "version": entry.version}


def close(task_id):
    with atomic():
        task = _task(task_id)
        if task.status == "active":
            task.status, task.closed_by, task.closed_at = "closed", frappe.session.user, now()
            persist(task)
    return {"ok": True}


def report(task_id):
    """JSON equivalent of the summary/detail data, with exact quantity strings."""
    task = _task(task_id)
    snapshot = _snapshot(task, export=True)
    if task.count_mode != "scan":
        # Legacy data can be read/closed, but never reinterpreted as scan entries.
        fail("stocktake.legacy_task")
    grouped = {}
    for entry in snapshot["entries"]:
        grouped.setdefault(entry["item_id"], []).append(entry)
    snapshot["summary"] = []
    for item in snapshot["items"]:
        summary = summarize_item(item, grouped.get(item["id"], ()))
        snapshot["summary"].append({key: format(value, ".6f") if isinstance(value, Decimal) else value
                                    for key, value in summary.items()})
    return snapshot


def export_size(task_id):
    """Rows the export holds (items + entries), behind the same access and legacy checks as ``export``;
    ``pearnly_core.exports`` decides from it whether to answer now or in the background."""
    task = _task(task_id)
    if task.count_mode != "scan":
        fail("stocktake.legacy_task")
    items = frappe.db.sql("SELECT COUNT(*) FROM `tabCowork Stocktake Item` WHERE task=%s AND workspace=%s",
                          (task.name, task.workspace))[0][0]
    return int(items) + int(_entry_total(task))


def export(task_id):
    """Use the full authorized locked snapshot, never the paginated preview.

    表头与标签按导出人的界面语言取（翻译包 `cowork.stocktake.export.*`）；取不到的回落泰文。"""
    texts = {}
    for key in TEXT_KEYS:
        msgid = "cowork.stocktake.export." + key
        value = _(msgid)
        if value and value != msgid:
            texts[key] = value
    return contract(workbook, report(task_id), texts)
