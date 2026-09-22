"""班次：开班、当班汇总、交班。

三条硬规则（照旧线上 `services/pos/shift.py`）：

1. **同一收银台同一时间只有一个开班** —— 用交易内的行锁判断，不靠前端先查一次。
2. **只能操作自己的班** —— 收银员查不到也关不掉别人的班；管理员可以代看汇总。
3. **找零只发现金**，所以"应有现金"只减现金那一份，非现金收款不计入抽屉。
"""

import frappe
from frappe import _
from frappe.utils import now

from pearnly_erp_portal.access import guard, rules
from pearnly_erp_portal.cashier import contracts, terminals

DT = "ERP Shift"
SALE = "ERP Sale"

NOT_FOUND = "Shift not found"
SHIFT_CLOSED = "This shift is already closed"
SHIFT_OPEN_ELSEWHERE = "This terminal already has an open shift"
NOT_YOURS = "This shift belongs to another cashier"


def _get(name):
    if not name or not frappe.db.exists(DT, name):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    return frappe.get_doc(DT, name)


def _own_shift(name, *, allow_admin=True):
    """取出本人（或管理员）可操作的班次。别人的班一律当作不存在，不泄露存在性。"""
    doc = _get(name)
    user = frappe.session.user
    if doc.cashier == user:
        return doc
    if allow_admin and rules.record_scope(guard.current_roles()) == rules.ALL_RECORDS:
        return doc
    frappe.throw(_(NOT_YOURS), frappe.PermissionError)


def _next_seq() -> int:
    last = frappe.db.get_value(DT, {}, "shift_seq", order_by="shift_seq desc")
    return int(last or 0) + 1


def open_shift(*, terminal=None, opening_float=0, note=None) -> dict:
    """开班。同一收银台已有开班时拒绝，且判断在锁内完成。"""
    guard.require("cashier.write")
    terminal = terminals.resolve(terminal)
    cashier = frappe.session.user

    # 行锁：两个收银员同时点开班时，只有一个能拿到这个收银台。
    frappe.db.sql("select name from `tabERP POS Terminal` where name=%s for update", terminal)
    existing = frappe.db.get_value(DT, {"terminal": terminal, "status": contracts.OPEN}, "name")
    if existing:
        frappe.throw(_(SHIFT_OPEN_ELSEWHERE))

    doc = frappe.get_doc({
        "doctype": DT, "terminal": terminal, "cashier": cashier, "status": contracts.OPEN,
        "opened_at": now(), "shift_seq": _next_seq(),
        "opening_float": contracts.q2(opening_float), "note": note,
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return {"name": doc.name, "terminal": terminal, "shift_seq": doc.shift_seq}


def current_shift(*, terminal=None) -> dict | None:
    """本人当前开着的班（收银台不带就找本人任意一个开班）。"""
    guard.require("cashier.read")
    filters = {"cashier": frappe.session.user, "status": contracts.OPEN}
    if terminal:
        filters["terminal"] = terminals.resolve(terminal)
    name = frappe.db.get_value(DT, filters, "name")
    return summary(name) if name else None


def summary(name, *, own_only=True) -> dict:
    """当班汇总：笔数、毛额、各支付方式净额、找零合计。"""
    doc = _own_shift(name) if own_only else _get(name)
    # 一次取出当班已完成单据，再按父单取付款：退款按负号回冲，抽屉与报表才对得上。
    rows = frappe.get_all(
        SALE, filters={"shift": doc.name, "status": "completed"},
        fields=["name", "sale_type", "grand_total", "change_amount"], limit_page_length=0)
    sales = [r for r in rows if r["sale_type"] == contracts.SALE]
    refunds = [r for r in rows if r["sale_type"] == contracts.REFUND]
    type_by_sale = {r["name"]: r["sale_type"] for r in rows}
    by_method: dict = {}
    payments = frappe.get_all(
        "ERP Sale Payment", filters={"parent": ["in", list(type_by_sale) or [""]]},
        fields=["method", "amount", "parent"], limit_page_length=0)
    for row in payments:
        sign = -1 if type_by_sale.get(row["parent"]) == contracts.REFUND else 1
        by_method[row["method"]] = by_method.get(row["method"], 0) + sign * float(row["amount"])
    gross = sum(float(r["grand_total"]) for r in sales)
    refunded = sum(float(r["grand_total"]) for r in refunds)
    change_total = sum(float(r["change_amount"] or 0) for r in sales)
    cash_in = by_method.get(contracts.CASH, 0.0)
    # 退款也发现金时 by_method 已经带负号，所以这里直接用净现金。
    expected = contracts.shift_expected(doc.opening_float, cash_in, change_total)
    return {
        "shift": {"name": doc.name, "terminal": doc.terminal, "cashier": doc.cashier,
                  "shift_seq": doc.shift_seq, "status": doc.status,
                  "opened_at": doc.opened_at, "opening_float": float(doc.opening_float or 0)},
        "sales_count": len(sales),
        "refund_count": len(refunds),
        "gross": gross,
        "refunded": refunded,
        "net": gross - refunded,
        "by_method": {k: round(v, 2) for k, v in sorted(by_method.items())},
        "change_total": round(change_total, 2),
        "expected_cash": float(expected),
    }


def close_shift(name, *, counted_cash) -> dict:
    """交班：算应有现金、记实点、算差额。只能关自己的班。"""
    guard.require("cashier.write")
    doc = _own_shift(name)
    if doc.status != contracts.OPEN:
        frappe.throw(_(SHIFT_CLOSED))
    data = summary(doc.name)
    expected = contracts.q2(data["expected_cash"])
    counted = contracts.q2(counted_cash)
    diff = contracts.shift_difference(counted, expected)
    doc.status = contracts.CLOSED
    doc.closed_at = now()
    doc.expected_cash = expected
    doc.counted_cash = counted
    doc.cash_diff = diff
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"name": doc.name, "status": doc.status, "expected_cash": float(expected),
            "counted_cash": float(counted), "cash_diff": float(diff), "summary": data}


def list_shifts(*, terminal=None, cashier=None, status=None, date_from=None, date_to=None,
                names=None, limit=50) -> list[dict]:
    """班次列表。非管理员只能看自己的班。`names` 用来只取指定的几个班（交班记录导出选中的几行）。"""
    guard.require("cashier.read")
    filters: dict = {}
    if not rules.record_scope(guard.current_roles()) == rules.ALL_RECORDS:
        filters["cashier"] = frappe.session.user
    elif cashier:
        filters["cashier"] = cashier
    if names:
        # 权限过滤在上一步已经写进 filters，这里再按名字收窄 —— 越权传别人的班次号只会取到空。
        filters["name"] = ["in", list(names)]
    if terminal:
        filters["terminal"] = terminal
    if status:
        filters["status"] = status
    if date_from and date_to:
        filters["opened_at"] = ["between", [f"{date_from} 00:00:00", f"{date_to} 23:59:59"]]
    return frappe.get_list(
        DT, filters=filters,
        fields=["name", "terminal", "cashier", "status", "shift_seq", "opened_at", "closed_at",
                "opening_float", "expected_cash", "counted_cash", "cash_diff"],
        order_by="opened_at desc", limit_page_length=int(limit))
