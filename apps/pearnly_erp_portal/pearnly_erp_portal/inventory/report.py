"""收发存报表与进销存看板。

- **期初**由同一份库存流水投影：初始化归入期初，不计本期收发；截止日之后的初始化不提前计入。
- 初始化的历史修订仍按真实顺序滚动成本，再归入期初；不前移修订重算业务成交成本。
- **成本未知就留空**：报表不会把没确立成本的结存写成 0，宁可为空也不给一个假数字。
"""

import frappe
from frappe import _
from frappe.utils import getdate, nowdate

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.inventory import contracts, warehouses

MOVEMENT = "ERP Stock Movement"
PRODUCT = "ERP Product"
INVALID = "Invalid value"


def _date(value, default=None):
    """统一成 `YYYY-MM-DD` 字符串。

    流水日期从库里出来是 `datetime.date`，直接和调用方传的字符串比较会抛 TypeError，
    所以进出这个模块的日期一律先归一化。
    """
    if not value:
        return str(default or nowdate())
    try:
        return str(getdate(value))
    except ValueError:
        frappe.throw(_(INVALID))


def _movements(*, product=None, warehouse=None, location=None, date_to=None) -> list[dict]:
    filters: dict = {}
    if product:
        filters["product"] = product
    if warehouse:
        filters["warehouse"] = warehouses.resolve(warehouse, create=False) or "__missing__"
    else:
        # 没点名仓库 → 只看**当前公司**的仓库（一租户多公司：库存按仓库记账）。
        filters.update(warehouses.scope_filter())
    if location is not None:
        filters["location"] = str(location or "").strip()
    if date_to:
        filters["posting_date"] = ["<=", date_to]
    rows = frappe.get_list(
        MOVEMENT, filters=filters,
        fields=["name", "posting_date", "posting_time", "movement_type", "product",
                "warehouse", "location", "qty", "unit", "qty_base", "unit_cost", "cost_known",
                "ref_type", "ref_id", "doc_no", "reason"],
        order_by="posting_date asc, posting_time asc, creation asc", limit_page_length=0)
    if location is not None:
        # MariaDB's default collation folds case/accents; position names hash the
        # exact location. Keep native permission filtering, then match that identity.
        rows = [row for row in rows if (row.get("location") or "") == filters["location"]]
    return rows


def _product_names(products) -> dict:
    if not products:
        return {}
    return {row["name"]: row for row in frappe.get_list(
        PRODUCT, filters={"name": ["in", list(products)]},
        fields=["name", "product_name", "unit", "base_unit"], limit_page_length=0)}


def _public(row: dict, *, cost_visible: bool) -> dict:
    out = dict(row)
    if not cost_visible:
        for field in ("unit_cost", "unit_price", "amount", "bal_unit_cost", "bal_value"):
            if field in out:
                out[field] = None
    return out


def _initialization(row):
    # Older editable openings posted subsequent differences as adjust/opening.
    return row.get("movement_type") == contracts.OPENING or row.get("ref_type") == "opening"


def _with_hidden(balance, qty, value, known):
    """Restate a displayed balance with already-valued hidden initialization deltas."""
    if not qty and not value and not known:
        return balance
    quantity = balance.qty + qty
    amount = (balance.value or contracts.d(0)) + value if balance.value is not None or known else None
    unit = (contracts.q2(amount / quantity) if quantity else balance.unit) if amount is not None else None
    return contracts.Balance(quantity, amount, unit)


def _project_position(movements, date_from):
    """Value one position chronologically; only then fold initialization into its opening.

    A current initialization precedes that position's business movements. Legacy
    revisions may follow business rows, so moving those revisions before sales
    would change their weighted cost. Keep those real row amounts, and add the
    remaining hidden deltas to displayed balances to reconcile with the restated
    opening. Nothing here changes the persisted ledger or its valuation order.
    """
    before, period = contracts.movements_before(movements, date_from, after=True)
    carried, _ = contracts.roll(contracts.ZERO_BALANCE, before)
    final, rolled = contracts.roll(carried, period)
    hidden_qty, hidden_value, hidden_known = contracts.d(0), contracts.d(0), False
    visible = []
    for row in reversed(rolled):
        quantity = contracts.d(row["qty_base"])
        if _initialization(row):
            hidden_qty += quantity
            if quantity and row["amount"] is not None:
                hidden_value += row["amount"] if quantity > 0 else -row["amount"]
                hidden_known = True
            continue
        # The last visible row also absorbs every trailing revision. Retain the
        # real closing unit cost: rounding residuals need not equal value / qty.
        balance = final if not visible else _with_hidden(
            contracts.Balance(row["bal_qty"], row["bal_value"], row["bal_unit_cost"]),
            hidden_qty, hidden_value, hidden_known)
        visible.append({**row, "bal_qty": balance.qty, "bal_value": balance.value,
                        "bal_unit_cost": balance.unit})
    opening = _with_hidden(carried, hidden_qty, hidden_value, hidden_known)
    return opening, final, list(reversed(visible))


def _aggregate(balances):
    # A position first used during this period has no carried balance. It must
    # not erase known opening costs belonging to another warehouse or location.
    balances = [balance for balance in balances
                if balance.qty or balance.value is not None or balance.unit is not None]
    if len(balances) == 1:
        return balances[0]
    qty = sum((balance.qty for balance in balances), contracts.d(0))
    known = bool(balances) and all(balance.unit is not None for balance in balances)
    value = sum((balance.value for balance in balances), contracts.d(0)) if known else None
    unit = contracts.q2(value / qty) if known and qty else None
    return contracts.Balance(qty, value, unit)


def summary(*, date_from=None, date_to=None, product=None, warehouse=None, location=None) -> list[dict]:
    """按商品汇总：期初、本期入、本期出、期末。"""
    guard.require("report.read")
    date_from = _date(date_from)
    date_to = _date(date_to)
    visible = guard.cost_visible()
    movements = _movements(product=product, warehouse=warehouse, location=location, date_to=date_to)

    keys = sorted({(m["product"], m["warehouse"], m.get("location") or "") for m in movements})
    names = _product_names({key[0] for key in keys})
    rows = []
    for product_id, warehouse_id, location_id in keys:
        relevant = [dict(m, posting_date=_date(m["posting_date"])) for m in movements
                    if m["product"] == product_id and m["warehouse"] == warehouse_id
                    and (m.get("location") or "") == location_id]
        carried, final, period = _project_position(relevant, date_from)

        in_qty = sum((contracts.d(r["qty_base"]) for r in period
                      if contracts.d(r["qty_base"]) > 0), contracts.d(0))
        out_qty = sum((-contracts.d(r["qty_base"]) for r in period
                       if contracts.d(r["qty_base"]) < 0), contracts.d(0))
        rows.append({
            "product": product_id, "product_name": (names.get(product_id) or {}).get("product_name"),
            "warehouse": warehouse_id, "location": location_id,
            "warehouse_name": frappe.db.get_value("ERP Warehouse", warehouse_id, "warehouse_name"),
            "name": contracts.position_name("BAL", product_id, warehouse_id, location_id),
            "unit": (names.get(product_id) or {}).get("base_unit") or (names.get(product_id) or {}).get("unit"),
            "opening_qty": float(carried.qty), "in_qty": float(in_qty), "out_qty": float(out_qty),
            "closing_qty": float(final.qty),
            "closing_unit_cost": None if (not visible or final.unit is None) else float(final.unit),
            "closing_value": None if (not visible or final.value is None) else float(final.value),
            "cost_known": final.unit is not None,
        })
    return rows


def card(*, product, warehouse=None, location=None, date_from=None, date_to=None) -> dict:
    """单品收发存明细：期初一行 + 逐笔进出 + 期末。"""
    guard.require("report.read")
    date_from = _date(date_from)
    date_to = _date(date_to)
    visible = guard.cost_visible()
    movements = [dict(m, posting_date=_date(m["posting_date"]))
                 for m in _movements(product=product, warehouse=warehouse, location=location, date_to=date_to)]
    positions = {}
    for movement in movements:
        key = (movement["warehouse"], movement.get("location") or "")
        positions.setdefault(key, []).append(movement)
    projections = [_project_position(rows, date_from) for rows in positions.values()]
    carried = _aggregate([opening for opening, _final, _rows in projections])
    final = _aggregate([closing for _opening, closing, _rows in projections])
    displayed = {row["name"]: row for _opening, _closing, rows in projections for row in rows}
    rolled = [displayed[row["name"]] for row in movements if row["name"] in displayed]

    names = _product_names({product})
    meta = names.get(product) or {}
    return {
        "product": product, "product_name": meta.get("product_name"), "unit": meta.get("base_unit") or meta.get("unit"),
        "warehouse": warehouse, "location": location, "date_from": date_from, "date_to": date_to,
        # 页面顶部显示仓库名，不是内部编号（录制问题 #17）。
        "warehouse_name": frappe.db.get_value("ERP Warehouse", warehouse, "warehouse_name") if warehouse else None,
        "opening": {"date": date_from, "qty": float(carried.qty),
                    "unit_cost": None if (not visible or carried.unit is None) else float(carried.unit),
                    "value": None if (not visible or carried.value is None) else float(carried.value)},
        "rows": [
            _public({"name": r["name"], "warehouse": r["warehouse"], "location": r.get("location") or "",
                     "warehouse_name": frappe.db.get_value("ERP Warehouse", r["warehouse"], "warehouse_name"),
                     "date": r["posting_date"], "kind": r["movement_type"], "doc_no": r["doc_no"],
                     "desc": r["ref_id"] or r["reason"] or r["movement_type"],
                     "qty": float(r["qty_base"]), "unit_price": r["unit_price"],
                     "amount": r["amount"], "bal_qty": float(r["bal_qty"]),
                     "bal_unit_cost": r["bal_unit_cost"], "bal_value": r["bal_value"]},
                    cost_visible=visible)
            for r in rolled],
        "closing": {"qty": float(final.qty),
                    "unit_cost": None if (not visible or final.unit is None) else float(final.unit),
                    "value": None if (not visible or final.value is None) else float(final.value)},
        "cost_known": final.unit is not None,
    }
