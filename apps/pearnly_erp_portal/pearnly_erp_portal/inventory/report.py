"""收发存报表与进销存看板。

- **期初**由同一份库存流水投影：初始化归入期初，不计本期收发；截止日之后的初始化不提前计入。
- 初始化的历史修订仍按真实顺序滚动成本，再归入期初；不前移修订重算业务成交成本。
- **成本未知就留空**：报表不会把没确立成本的结存写成 0，宁可为空也不给一个假数字。
- **汇总按页算**（2026-10-25，大租户上万商品×仓库）：按商品 / 仓库 / 仓位排序（默认）时先取全部"商品×仓库×仓位"键
  （只有三列），排好切出这一页，只读这一页商品的流水去滚成本；按数量列排序必须先算出每一行，才整批算、再切片。
  两种都按键一次分组，不再每个键扫一遍全部流水、每行单查一次仓库名。
- **键与数量**（10-25 验收 30 万行流水：默认页 6.3 秒、按数量排序 13.5 秒）：一条分组查询（`_position_sums`，CASE 求和，
  走流水表的覆盖索引）同时给出全部键和期初 / 入 / 出 / 期末数量；按数量列排序就按它排，只把这一页商品的流水读进来滚成本。
  数量口径与逐笔滚存一致：期初 = 截止起始日前的和 + 本期内的初始化；入 / 出 = 本期内非初始化的正 / 负数量；期末 = 截止日前全部的和。
"""

import frappe
from frappe import _
from frappe.utils import getdate, nowdate

from pearnly_core.desk import list_filters
from pearnly_erp_portal.access import guard, listing
from pearnly_erp_portal.inventory import contracts, warehouses

MOVEMENT = "ERP Stock Movement"
PRODUCT = "ERP Product"
INVALID = "Invalid value"
# 汇总列表列头排序的白名单（页面 stock-card.js 的 sort_fields）：前三列由键决定，可先分页再算
KEY_SORTS = ("product_name", "warehouse_name", "location")
SUMMARY_SORTS = {name: name for name in (*KEY_SORTS, "opening_qty", "in_qty", "out_qty", "closing_qty")}
PAGE_MAX = 500


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


def _filters(*, product=None, warehouse=None, location=None, date_to=None, products=None, conditions=None):
    filters: dict = {}
    if product:
        filters["product"] = product
    elif products is not None:
        filters["product"] = ["in", sorted(products) or ["__none__"]]
    if warehouse:
        filters["warehouse"] = warehouses.resolve(warehouse, create=False) or "__missing__"
    else:
        # 没点名仓库 → 只看**当前公司**的仓库（一租户多公司：库存按仓库记账）。
        filters.update(warehouses.scope_filter())
    if location is not None:
        filters["location"] = str(location or "").strip()
    if date_to:
        filters["posting_date"] = ["<=", date_to]
    # 原生筛选里「等于」以外的条件（页面 stock-card.js 的仓库、商品）：同一列可能有两条，改成列表形式
    extra = list_filters.conditions(conditions, {
        "warehouse": {"column": "warehouse", "link": ("ERP Warehouse", "warehouse_name")},
        "product": {"column": "product", "link": ("ERP Product", "product_name")}})
    if extra:
        return [[key, *value] if isinstance(value, list) else [key, "=", value] for key, value in filters.items()] + extra
    return filters


def _exact_location(rows, filters, location=None):
    # MariaDB's default collation folds case/accents; position names hash the
    # exact location. Keep native permission filtering, then match that identity.
    if location is None:
        return rows
    wanted = str(location or "").strip()
    return [row for row in rows if (row.get("location") or "") == wanted]


def _movements(*, product=None, warehouse=None, location=None, date_to=None, products=None,
               conditions=None) -> list[dict]:
    filters = _filters(product=product, warehouse=warehouse, location=location, date_to=date_to, products=products,
                       conditions=conditions)
    rows = frappe.get_list(
        MOVEMENT, filters=filters,
        fields=["name", "posting_date", "posting_time", "movement_type", "product",
                "warehouse", "location", "qty", "unit", "qty_base", "unit_cost", "cost_known",
                "ref_type", "ref_id", "doc_no", "reason"],
        order_by="posting_date asc, posting_time asc, creation asc", limit_page_length=0)
    return _exact_location(rows, filters, location)


def _warehouse_names(ids) -> dict:
    # 一个仓库只查一次（原来每一行查一次）
    return {warehouse_id: frappe.db.get_value("ERP Warehouse", warehouse_id, "warehouse_name")
            for warehouse_id in set(ids)}


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


def _summary_rows(keys, movements, *, date_from, visible, names, warehouse_names) -> list[dict]:
    grouped: dict = {}
    for movement in movements:
        key = (movement["product"], movement["warehouse"], movement.get("location") or "")
        grouped.setdefault(key, []).append(dict(movement, posting_date=_date(movement["posting_date"])))
    rows = []
    for product_id, warehouse_id, location_id in keys:
        carried, final, period = _project_position(grouped.get((product_id, warehouse_id, location_id), []), date_from)

        in_qty = sum((contracts.d(r["qty_base"]) for r in period
                      if contracts.d(r["qty_base"]) > 0), contracts.d(0))
        out_qty = sum((-contracts.d(r["qty_base"]) for r in period
                       if contracts.d(r["qty_base"]) < 0), contracts.d(0))
        rows.append({
            "product": product_id, "product_name": (names.get(product_id) or {}).get("product_name"),
            "warehouse": warehouse_id, "location": location_id,
            "warehouse_name": warehouse_names.get(warehouse_id),
            "name": contracts.position_name("BAL", product_id, warehouse_id, location_id),
            "unit": (names.get(product_id) or {}).get("base_unit") or (names.get(product_id) or {}).get("unit"),
            "opening_qty": float(carried.qty), "in_qty": float(in_qty), "out_qty": float(out_qty),
            "closing_qty": float(final.qty),
            "closing_unit_cost": None if (not visible or final.unit is None) else float(final.unit),
            "closing_value": None if (not visible or final.value is None) else float(final.value),
            "cost_known": final.unit is not None,
        })
    return rows


def summary(*, date_from=None, date_to=None, product=None, warehouse=None, location=None) -> list[dict]:
    """按商品汇总：期初、本期入、本期出、期末（整批，测试与内部核对用；页面走 summary_page）。"""
    guard.require("report.read")
    date_from = _date(date_from)
    date_to = _date(date_to)
    movements = _movements(product=product, warehouse=warehouse, location=location, date_to=date_to)
    keys = sorted({(m["product"], m["warehouse"], m.get("location") or "") for m in movements})
    return _summary_rows(keys, movements, date_from=date_from, visible=guard.cost_visible(),
                         names=_product_names({key[0] for key in keys}),
                         warehouse_names=_warehouse_names(key[1] for key in keys))


def _as_list(filters) -> list:
    if isinstance(filters, list):
        return list(filters)
    return [[key, *value] if isinstance(value, list) else [key, "=", value] for key, value in filters.items()]


def _position_sums(query, date_from) -> dict:
    """每个「商品 × 仓库 × 仓位」一行：截止起始日前的和、本期正 / 负数量的和、本期初始化的和（全部 / 正数）。

    一条分组查询用 CASE 一次求完（验收 10-25：分 5 次分组要扫 5 遍 30 万行，4.6–4.8 秒）；键也从这里出。用 Frappe 原生的
    `frappe.qb.get_query(..., ignore_permissions=False)` 生成带 DocType 权限与本页条件的查询，再加求和列；走流水表的覆盖索引
    （`erp_stock_movement.INDEXES`）。初始化用「类型 = 期初 或 来源 = 期初」挑（来源为空时整个条件为假，不会被误算进去）。
    """
    from frappe.query_builder import Case
    from frappe.query_builder.functions import Sum

    filters = _as_list(_filters(**query))
    table = frappe.qb.DocType(MOVEMENT)
    period = table.posting_date >= date_from
    initialization = (table.movement_type == contracts.OPENING) | (table.ref_type == "opening")

    def total(condition, name):
        return Sum(Case().when(condition, table.qty_base).else_(0)).as_(name)

    sql = frappe.qb.get_query(MOVEMENT, fields=["product", "warehouse", "location"], filters=filters,
                              group_by="product, warehouse, location", ignore_permissions=False).select(
        total(table.posting_date < date_from, "carried"), total(period & (table.qty_base > 0), "period_in"),
        total(period & (table.qty_base < 0), "period_out"), total(period & initialization, "init_all"),
        total(period & initialization & (table.qty_base > 0), "init_in"))
    out: dict = {}
    for row in _exact_location(sql.run(as_dict=True), filters, query.get("location")):
        key = (row["product"], row["warehouse"], row.get("location") or "")
        sums = out.setdefault(key, {name: contracts.d(0) for name in ("carried", "period_in", "period_out", "init_all",
                                                                        "init_in")})
        for name in sums:
            sums[name] += contracts.d(row.get(name) or 0)
    return out


def _quantities(sums) -> dict:
    """每个键的期初 / 入 / 出 / 期末数量（口径同 `_project_position` 的数量部分）：
    期初 = 起始日前的和 + 本期初始化；入 / 出 = 本期非初始化的正 / 负数量（本期全部 − 本期初始化）；期末 = 截止日前全部的和。"""
    out = {}
    for key, value in sums.items():
        init_out = value["init_all"] - value["init_in"]
        out[key] = {"opening_qty": float(value["carried"] + value["init_all"]),
                    "in_qty": float(value["period_in"] - value["init_in"]),
                    "out_qty": float(-(value["period_out"] - init_out)),
                    "closing_qty": float(value["carried"] + value["period_in"] + value["period_out"])}
    return out


def summary_page(*, date_from=None, date_to=None, product=None, warehouse=None, location=None,
                 start=0, limit=20, sort_by=None, sort_order=None, conditions=None) -> dict:
    """汇总列表的一页：`{"items": 这一页, "total": 全部行数}`，排序口径同 `listing.sort_rows`。"""
    guard.require("report.read")
    date_from = _date(date_from)
    date_to = _date(date_to)
    start = max(int(start or 0), 0)
    limit = min(max(int(limit or 20), 1), PAGE_MAX)
    query = {"product": product, "warehouse": warehouse, "location": location, "date_to": date_to,
             "conditions": conditions}
    visible = guard.cost_visible()
    sums = _position_sums(query, date_from)
    keys = sorted(sums)
    sort_key = str(sort_by or "").strip()
    # 商品名只在按商品名排序或要整批算时取全部，其余只取这一页的
    names = _product_names({key[0] for key in keys}) if sort_key in SUMMARY_SORTS and sort_key not in (
        "warehouse_name", "location") else {}
    warehouse_names = _warehouse_names(key[1] for key in keys)
    if sort_key in SUMMARY_SORTS and sort_key not in KEY_SORTS:
        quantities = _quantities(sums)
        ordered = listing.sort_rows([{"key": key, **quantities.get(key, {})} for key in keys],
                                    sort_key, sort_order, SUMMARY_SORTS)
        page = [row["key"] for row in ordered[start:start + limit]]
        movements = _movements(**query, products={key[0] for key in page}) if page else []
        return {"items": _summary_rows(page, movements, date_from=date_from, visible=visible, names=names,
                                       warehouse_names=warehouse_names), "total": len(keys)}
    labels = [{"key": key, "product_name": (names.get(key[0]) or {}).get("product_name"),
               "warehouse_name": warehouse_names.get(key[1]), "location": key[2]} for key in keys]
    page = [row["key"] for row in listing.sort_rows(labels, sort_key, sort_order, SUMMARY_SORTS)[start:start + limit]]
    movements = _movements(**query, products={key[0] for key in page}) if page else []
    names = names or _product_names({key[0] for key in page})
    rows = _summary_rows(page, movements, date_from=date_from, visible=visible, names=names,
                         warehouse_names=warehouse_names)
    return {"items": rows, "total": len(keys)}


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
    warehouse_names = _warehouse_names(r["warehouse"] for r in rolled)
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
                     "warehouse_name": warehouse_names.get(r["warehouse"]),
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
