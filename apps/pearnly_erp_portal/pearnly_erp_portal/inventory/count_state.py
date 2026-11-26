"""Stock-count locking, inventory revisions and native-document write boundaries."""
import hashlib
import json

import frappe
from frappe import _
from pearnly_erp_portal.masters import access as company_access


def lock_task(name, *, active=True):
    rows = frappe.db.sql("select name, status, warehouse from `tabERP Stock Count` where name=%s for update",
                         (name,), as_dict=True)
    if not rows:
        frappe.throw(_("Stock count not found"), frappe.DoesNotExistError)
    company_access.require_warehouse(rows[0].warehouse)
    if active and rows[0].status != "Active":
        frappe.throw(_("This stock count is closed"))
    return rows[0]


def stock_token(product, *, for_update=False):
    # Count + maximum creation detect even an issue followed by an equal receipt.
    # MariaDB repeatable-read can retain a pre-lock snapshot. Writers need current
    # locking reads after the product lock; ordinary lookup/review stays read-only.
    suffix = " for update" if for_update else ""
    try:
        movement = frappe.db.sql(
            "select count(*), max(creation), max(name) from `tabERP Stock Movement` where product=%s" + suffix,
            (product,))
        balances = frappe.db.sql(
            "select name, qty_on_hand, value, unit_cost, cost_known from `tabERP Stock Balance` where product=%s order by name" + suffix,
            (product,))
        units = frappe.db.sql("select base_unit, unit from `tabERP Product` where name=%s" + suffix,
                              (product,))
    except frappe.QueryDeadlockError:  # conflict-ok: 只读的加锁读（盘点比对库存），冲突转成「库存变了、请重盘」的业务提示
        # Snapshot isolation may reject the changed row instead of returning it.
        # The request rolls back, preserving both the observation and the receipt.
        frappe.throw(_("Stock changed during counting. Recount the affected products."))
    return hashlib.sha256(json.dumps([movement, balances, units], default=str).encode()).hexdigest()


def stock_tokens(products, *, for_update=False) -> dict:
    """一批物料的 :func:`stock_token`（盘点明细 / 查找一次算几百个）：只读时按名单一次取三张表、按物料分组后照原样算，
    结果与逐个 ``stock_token`` 逐字相等（性能体检 11-14：251 行原先发 753 条）。加锁读（结束盘点）仍逐个取，锁的行与先后不变。"""
    names = sorted({p for p in products if p})
    if for_update:
        return {p: stock_token(p, for_update=True) for p in names}  # loop-query: 加锁读保持逐个取（结束盘点，锁的行与先后不变）
    out = {}
    for start in range(0, len(names), 500):
        chunk = tuple(names[start:start + 500])
        movement = {row[0]: (tuple(row[1:]),) for row in frappe.db.sql(
            "select product, count(*), max(creation), max(name) from `tabERP Stock Movement` where product in %s group by product",
            (chunk,))}
        balances = {}
        for row in frappe.db.sql(
                "select product, name, qty_on_hand, value, unit_cost, cost_known from `tabERP Stock Balance` where product in %s "
                "order by product, name", (chunk,)):
            balances.setdefault(row[0], []).append(tuple(row[1:]))
        units = {row[0]: (tuple(row[1:]),) for row in frappe.db.sql(
            "select name, base_unit, unit from `tabERP Product` where name in %s", (chunk,))}
        for product in chunk:
            state = [movement.get(product, ((0, None, None),)), tuple(balances.get(product, ())), units.get(product, ())]
            out[product] = hashlib.sha256(json.dumps(state, default=str).encode()).hexdigest()
    return out


def validate_document(doc):
    """Generic Desk/RPC writes must not bypass totals, locks or closed-task immutability."""
    if doc.doctype == "ERP Stock Count":
        if not doc.is_new():
            lock_task(doc.name)
        if doc.status != "Active" or not getattr(frappe.local, "stock_count_service_write", False):
            frappe.throw(_("Use the stock count workbench"))
        return
    if not (doc.is_new() and getattr(frappe.local, "stock_count_locked", None) == doc.get("count")):
        lock_task(doc.get("count"))  # 抄快照批量新建时盘点单已在这次请求里锁过一次（`count._insert_snapshot`）
    if not getattr(frappe.local, "stock_count_service_write", False):
        frappe.throw(_("Use the stock count workbench"))
    if not doc.is_new():
        previous = frappe.db.get_value(doc.doctype, doc.name, "count")
        if previous != doc.get("count"):
            frappe.throw(_("That row is not part of this stock count"))


def before_delete(doc):
    lock_task(doc.name if doc.doctype == "ERP Stock Count" else doc.get("count"))
    if not getattr(frappe.local, "stock_count_service_write", False):
        frappe.throw(_("Use the stock count workbench"))


def write_document(doc, method):
    """Service context is not serializable in a Desk/RPC document's flags."""
    previous = getattr(frappe.local, "stock_count_service_write", False)
    frappe.local.stock_count_service_write = True
    try:
        return getattr(doc, method)(ignore_permissions=True)
    finally:
        frappe.local.stock_count_service_write = previous
