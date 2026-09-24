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
COUNTED_REQUIRED = "Counted amount is required"

# 班次对账的支付方式：与收款那一套同一份清单（`contracts.PAYMENT_METHODS`），
# 顺序照官方收款面板（现金 / 刷卡 / 扫码 / 转账）。
METHODS = (contracts.CASH, contracts.CARD, contracts.PROMPTPAY, contracts.TRANSFER)


def _clean_amounts(values) -> dict:
    """把 {支付方式: 金额} 收干净：只认认识的支付方式，空值当 0。

    也接受子表格那种 `[{method, opening_amount}]` / `[{method, counted_amount}]`。
    """
    if isinstance(values, str):
        values = frappe.parse_json(values)
    if isinstance(values, list):
        values = {row.get("method"): (row.get("opening_amount") if row.get("opening_amount")
                                      is not None else row.get("counted_amount"))
                  for row in values if isinstance(row, dict) and row.get("method")}
    amounts = {}
    for method, amount in (values or {}).items():
        method = str(method or "").strip().lower()
        if method not in METHODS:
            continue
        amounts[method] = contracts.q2(amount or 0)
    return amounts


def _balance_rows(doc, *, by_method=None, change_total=0.0) -> list[dict]:
    """按支付方式的那张对账表：期初 / 应有 / 实点 / 差额。

    应有 = 期初 + 这种方式收进来的净额（现金再减去找零）。子表是 2026-09-23 才加的，
    之前开的班没有这些行 —— 那种老班次按"只有现金一笔备用金"合出来，旧数据不会变空白。
    """
    by_method = by_method or {}
    stored = {row.method: row for row in (doc.get("balances") or [])}
    methods = [method for method in METHODS if method in stored or method in by_method]
    if not methods:
        methods = [contracts.CASH]
    rows = []
    for method in methods:
        row = stored.get(method)
        if row is not None:
            opening = contracts.d(row.opening_amount or 0)
            counted = None if row.counted_amount is None else contracts.d(row.counted_amount)
        else:
            # 老班次：现金那一份是父字段 `opening_float` / `counted_cash`（实点只在交班后才有意义）。
            opening = contracts.d(doc.opening_float or 0) if method == contracts.CASH else contracts.d(0)
            counted = (contracts.d(doc.counted_cash)
                       if method == contracts.CASH and doc.status == contracts.CLOSED
                       else None)
        expected = contracts.q2(opening + contracts.d(by_method.get(method, 0.0))
                                - (contracts.d(change_total) if method == contracts.CASH else contracts.d(0)))
        rows.append({
            "method": method,
            "opening_amount": float(contracts.q2(opening)),
            "expected_amount": float(expected),
            "counted_amount": None if counted is None else float(contracts.q2(counted)),
            "difference": None if counted is None else float(contracts.shift_difference(counted, expected)),
        })
    return rows


def _get(name):
    if not name or not frappe.db.exists(DT, name):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    from pearnly_erp_portal.cashier.access import require_record

    doc = frappe.get_doc(DT, name)
    require_record(doc)
    return doc


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


def open_shift(*, terminal=None, opening_float=0, balances=None, note=None) -> dict:
    """开班。同一收银台已有开班时拒绝，且判断在锁内完成。"""
    guard.require("cashier.write")
    terminal = terminals.resolve(terminal)
    cashier = frappe.session.user
    # 期初按支付方式各记一笔（官方 Opening Balance Details 那张表）；现金那一份同时写回父
    # 字段 `opening_float` —— 交班汇总与旧口径都读它。
    opening = _clean_amounts(balances)
    if opening:
        opening_float = opening.get(contracts.CASH, 0)

    # 行锁：两个收银员同时点开班时，只有一个能拿到这个收银台。
    frappe.db.sql("select name from `tabERP POS Terminal` where name=%s for update", terminal)
    existing = frappe.db.get_value(DT, {"terminal": terminal, "status": contracts.OPEN}, "name")
    if existing:
        frappe.throw(_(SHIFT_OPEN_ELSEWHERE))

    doc = frappe.get_doc({
        "doctype": DT, "terminal": terminal, "cashier": cashier, "status": contracts.OPEN,
        "opened_at": now(), "shift_seq": _next_seq(),
        "opening_float": contracts.q2(opening_float), "note": note,
        "balances": [{"method": method, "opening_amount": opening.get(method, 0)}
                     for method in METHODS],
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return {"name": doc.name, "terminal": terminal, "shift_seq": doc.shift_seq}


def current_shift(*, terminal=None) -> dict | None:
    """本人当前开着的班（收银台不带就找本人任意一个开班）。"""
    guard.require("cashier.read")
    filters = {"cashier": frappe.session.user, "status": contracts.OPEN, **terminals.scope_filter()}
    if terminal:
        filters["terminal"] = terminals.resolve(terminal)
    name = frappe.db.get_value(DT, filters, "name")
    return summary(name) if name else None


def summary(name, *, own_only=True) -> dict:
    """当班汇总：笔数、毛额、各支付方式净额、找零合计。"""
    guard.require("cashier.read" if guard.has("cashier.read") else "shifts.read")
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
        # 按支付方式的对账表（官方 POS Closing Entry 那张 Payment Reconciliation 同款）。
        "balances": _balance_rows(doc, by_method=by_method, change_total=change_total),
    }


def close_shift(name, *, counted_cash=None, counted=None, note=None) -> dict:
    """交班：算应有、记实点、算差额。只能关自己的班。

    `counted` 是 {支付方式: 实点金额}（官方那张对账表按方式各填一格）；不给就退回
    `counted_cash` 那一个数（交班记录页与老调用方仍在用）。
    """
    guard.require("cashier.write")
    doc = _own_shift(name)
    if doc.status != contracts.OPEN:
        frappe.throw(_(SHIFT_CLOSED))
    data = summary(doc.name)
    amounts = _clean_amounts(counted)
    if not amounts:
        if counted_cash is None:
            frappe.throw(_(COUNTED_REQUIRED))
        amounts = {contracts.CASH: contracts.q2(counted_cash)}
    expected_by_method = {row["method"]: row["expected_amount"] for row in data["balances"]}
    rows = {row.method: row for row in (doc.get("balances") or [])}
    for method, amount in amounts.items():
        row = rows.get(method)
        if row is None:
            row = doc.append("balances", {
                "method": method,
                "opening_amount": contracts.q2(doc.opening_float or 0)
                                  if method == contracts.CASH else 0})
            rows[method] = row
        row.expected_amount = contracts.q2(expected_by_method.get(method, 0.0))
        row.counted_amount = contracts.q2(amount)
        row.difference = contracts.shift_difference(row.counted_amount, row.expected_amount)
    cash_row = rows.get(contracts.CASH)
    expected = contracts.q2(cash_row.expected_amount if cash_row else data["expected_cash"])
    counted_total = contracts.q2(cash_row.counted_amount if cash_row
                                 else amounts.get(contracts.CASH, 0))
    diff = contracts.shift_difference(counted_total, expected)
    doc.status = contracts.CLOSED
    doc.closed_at = now()
    doc.expected_cash = expected
    doc.counted_cash = counted_total
    doc.cash_diff = diff
    if note is not None:
        doc.note = note
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"name": doc.name, "status": doc.status, "expected_cash": float(expected),
            "counted_cash": float(counted_total), "cash_diff": float(diff), "summary": data}


def list_shifts(*, terminal=None, cashier=None, status=None, date_from=None, date_to=None,
                names=None, limit=50) -> list[dict]:
    """班次列表。非管理员只能看自己的班。`names` 用来只取指定的几个班（交班记录导出选中的几行）。"""
    guard.require("shifts.read")
    # 一租户多公司：只看当前公司那几台收银台上的班次（班次表本身没有仓库列）。
    filters: dict = dict(terminals.scope_filter())
    if not rules.record_scope(guard.current_roles()) == rules.ALL_RECORDS:
        filters["cashier"] = frappe.session.user
    elif cashier:
        filters["cashier"] = cashier
    if names:
        # 权限过滤在上一步已经写进 filters，这里再按名字收窄 —— 越权传别人的班次号只会取到空。
        filters["name"] = ["in", list(names)]
    if terminal:
        filters["terminal"] = terminals.resolve(terminal)
    if status:
        filters["status"] = status
    if date_from and date_to:
        filters["opened_at"] = ["between", [f"{date_from} 00:00:00", f"{date_to} 23:59:59"]]
    return frappe.get_list(
        DT, filters=filters,
        fields=["name", "terminal", "cashier", "status", "shift_seq", "opened_at", "closed_at",
                "opening_float", "expected_cash", "counted_cash", "cash_diff"],
        order_by="opened_at desc", limit_page_length=int(limit))
