"""入库 / 出库单据：建单、提交（落流水）、作废（反向冲销）、以及采购确认后的自动入库。"""

import frappe
from frappe import _
from frappe.utils import now

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.inventory import contracts, positions, stock, warehouses
from pearnly_erp_portal.numbering import service as numbering

DOCTYPE = "ERP Stock Entry"

NOT_FOUND = "Stock entry not found"
LINES_REQUIRED = "Provide at least one line item"
BAD_TRANSITION = "This status change is not allowed"
LOCKED = "A submitted entry cannot be changed"

LIST_FIELDS = ("name", "direction", "posting_date", "warehouse", "supplier", "doc_no",
               "status", "reason", "ref_type", "ref_id", "submitted_at")


def _get(name):
    if not name or not frappe.db.exists(DOCTYPE, name):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    return frappe.get_doc(DOCTYPE, name)


def create_entry(*, direction, posting_date, lines, warehouse=None, supplier=None,
                 reason=None, ref_type=None, ref_id=None, dedupe_key=None):
    guard.require("stock.write")
    if direction not in ("in", "out"):
        frappe.throw(_("Direction must be in or out"))
    if not isinstance(lines, list) or not lines:
        frappe.throw(_(LINES_REQUIRED))
    lines = [positions.prepare(line, warehouse) for line in lines]
    warehouse = warehouses.resolve(warehouse)
    # 单据号由系统发（入库 IR / 出库 IS，佛历日期 + 当日流水），不接受手输 —— 收发存报表
    # "关联单据"那一列显示的就是它，口径见 `numbering.contracts`。
    doc_no = numbering.allocate(direction=direction, on=posting_date)
    doc = frappe.get_doc({
        "doctype": DOCTYPE, "direction": direction, "posting_date": posting_date,
        "warehouse": warehouse, "supplier": supplier, "doc_no": doc_no, "reason": reason,
        "ref_type": ref_type, "ref_id": ref_id, "dedupe_key": dedupe_key, "status": "draft",
        "lines": [{
            "product": line["product"], "qty": line["qty"], "unit": line.get("unit"),
            "unit_cost": line.get("unit_cost"), "batch_no": line.get("batch_no"),
            "warehouse": line["warehouse"], "location": line["location"],
        } for line in lines],
    })
    doc.insert()
    frappe.db.commit()
    return {"name": doc.name, "doc_no": doc.doc_no}


def list_entries(*, direction=None, warehouse=None, status=None, date_from=None, date_to=None,
                 limit=50, start=0):
    guard.require("stock.read")
    filters: dict = {}
    if direction:
        filters["direction"] = direction
    if warehouse:
        filters["warehouse"] = warehouse
    if status:
        filters["status"] = status
    if date_from and date_to:
        filters["posting_date"] = ["between", [date_from, date_to]]
    return frappe.get_list(DOCTYPE, filters=filters, fields=list(LIST_FIELDS),
                           order_by="posting_date desc, creation desc",
                           limit_start=int(start), limit_page_length=int(limit))


def get_entry(name):
    guard.require("stock.read")
    doc = _get(name)
    data = {field: doc.get(field) for field in LIST_FIELDS}
    data["lines"] = [
        {"product": row.product, "qty": row.qty, "unit": row.unit,
         "unit_cost": row.unit_cost, "batch_no": row.batch_no, **positions.public(row, doc.warehouse)}
        for row in doc.get("lines") or []
    ]
    return data


def update_entry(name, **changes):
    guard.require("stock.write")
    doc = _get(name)
    if doc.status != "draft":
        frappe.throw(_(LOCKED))
    # `doc_no` 不在可改字段里：号是发出去的，改它等于让历史流水对不上单据。
    for field in ("posting_date", "warehouse", "supplier", "reason",
                  "ref_type", "ref_id"):
        if field in changes:
            doc.set(field, warehouses.resolve(changes[field]) if field == "warehouse" else changes[field])
    if "lines" in changes:
        lines = changes["lines"]
        if not isinstance(lines, list) or not lines:
            frappe.throw(_(LINES_REQUIRED))
        doc.lines = []
        for line in lines:
            line = positions.prepare(line, changes.get("warehouse"))
            doc.append("lines", {
                "product": line["product"], "qty": line["qty"], "unit": line.get("unit"),
                "unit_cost": line.get("unit_cost"), "batch_no": line.get("batch_no"),
                "warehouse": line["warehouse"], "location": line["location"],
            })
    doc.save()
    frappe.db.commit()
    return {"name": doc.name}


def _movement_type(doc) -> str:
    """入库按单据性质分：带采购引用的是采购入库，其余是普通入库。"""
    if doc.direction == "in":
        return contracts.PURCHASE_IN if doc.ref_type == "purchase" else contracts.STOCK_IN
    return contracts.STOCK_OUT


def submit_entry(name):
    """提交：把每一行落成流水。流水幂等锚是单据名，所以重复提交不会记两次。"""
    guard.require("stock.write")
    frappe.db.sql("select name from `tabERP Stock Entry` where name=%s for update", (name,))
    doc = _get(name)
    if doc.status != "draft":
        frappe.throw(_(BAD_TRANSITION))
    movement_type = _movement_type(doc)
    result = stock.apply_lines(
        warehouse=doc.warehouse, movement_type=movement_type,
        lines=[{"product": row.product, "qty": row.qty, "unit": row.unit,
                "unit_cost": row.unit_cost, **positions.saved(row, doc.warehouse)} for row in doc.lines],
        posting_date=str(doc.posting_date), ref_type=doc.ref_type or "stock_entry",
        ref_id=doc.name, doc_no=doc.doc_no, reason=doc.reason,
        client_uuid=f"stock-entry:{doc.name}",
    )
    doc.status = "submitted"
    doc.submitted_at = now()
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"name": doc.name, "status": doc.status, **result}


def void_entry(name):
    """作废：追加反向流水把数量还回去。流水不动、不删，历史看得见。"""
    guard.require("stock.write")
    frappe.db.sql("select name from `tabERP Stock Entry` where name=%s for update", (name,))
    doc = _get(name)
    if doc.status != "submitted":
        frappe.throw(_(BAD_TRANSITION))
    stock.reverse_movements(ref_type=doc.ref_type or "stock_entry", ref_id=doc.name,
                            client_uuid=f"stock-void:{doc.name}", reason=f"void of {doc.name}")
    doc.status = "void"
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"name": doc.name, "status": doc.status}
