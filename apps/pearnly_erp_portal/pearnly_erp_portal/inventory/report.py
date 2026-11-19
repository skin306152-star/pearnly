"""收发存报表与进销存看板。

- **单价、金额照 MR.ERP 算**（主会话 11-09）：页面（汇总列表、单品明细）与导出（`card_export`）共用 `card_rows.product_blocks`——
  一个物料全公司一个移动平均池（`card_rows.pool_costs`，照 MR.ERP `stkcardpc.php`），按 MR.ERP 顺序（日期 → 同日先收后发 → 单号），
  结余由报表自己滚。永远有数，不留空；没有成本权限的人单价、金额留空。库存引擎（库存货值、单据成本、推 Express）按
  「物料 × 仓」算（11-19 取消仓位），所以报表和库存货值暂时会有差别（评估 §G）。
- **期初**：截止起始日前各笔收加发减；初始化归入期初，不计本期收发；截止日之后的初始化不提前计入。
- **汇总按页算**（2026-10-25，大租户上万商品×仓库）：按商品 / 仓库排序（默认）时先取全部"商品×仓库"键
  （只有两列），排好切出这一页，只读这一页商品的流水去滚成本；按数量列排序必须先算出每一行，才整批算、再切片。
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
from pearnly_erp_portal.inventory import card_rows, contracts, warehouses

MOVEMENT = "ERP Stock Movement"
PRODUCT = "ERP Product"
INVALID = "Invalid value"
# 汇总列表列头排序的白名单（页面 stock-card.js 的 sort_fields）：前三列由键决定，可先分页再算
KEY_SORTS = ("product_name", "warehouse_name")
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


def _filters(*, product=None, warehouse=None, date_to=None, products=None, conditions=None):
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
    if date_to:
        filters["posting_date"] = ["<=", date_to]
    # 原生筛选里「等于」以外的条件（页面 stock-card.js 的仓库、商品）：同一列可能有两条，改成列表形式
    extra = list_filters.conditions(conditions, {
        "warehouse": {"column": "warehouse", "link": ("ERP Warehouse", "warehouse_name")},
        "product": {"column": "product", "link": ("ERP Product", "product_name")}})
    if extra:
        return [[key, *value] if isinstance(value, list) else [key, "=", value] for key, value in filters.items()] + extra
    return filters


def _movements(*, product=None, warehouse=None, date_to=None, products=None,
               conditions=None) -> list[dict]:
    filters = _filters(product=product, warehouse=warehouse, date_to=date_to, products=products,
                       conditions=conditions)
    return frappe.get_list(
        MOVEMENT, filters=filters,
        fields=["name", "posting_date", "posting_time", "movement_type", "product",
                "warehouse", "qty", "unit", "qty_base", "unit_cost", "cost_known",
                "ref_type", "ref_id", "doc_no", "reason"],
        order_by="posting_date asc, posting_time asc, creation asc", limit_page_length=0)


def _warehouse_names(ids) -> dict:
    # 一个仓库只查一次（原来每一行查一次）
    return {warehouse_id: frappe.db.get_value("ERP Warehouse", warehouse_id, "warehouse_name")
            for warehouse_id in set(ids)}


def _product_names(products) -> dict:
    """商品名、单位与「分类 · 特性/颜色」（``spec``，岳父 11-01：品名后接着显示，同名不同色 / 尺寸分得开）。"""
    if not products:
        return {}
    from pearnly_erp_portal.products import categories

    rows = {row["name"]: row for row in frappe.get_list(
        PRODUCT, filters={"name": ["in", list(products)]},
        fields=["name", "product_name", "unit", "base_unit", "category", "attribute"], limit_page_length=0)}
    labels = categories.names(row.get("category") for row in rows.values())
    for row in rows.values():
        row["spec"] = categories.spec(labels.get(row.get("category")), row.get("attribute"))
    return rows


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


def movement_doc(row) -> str:
    """报表上的「เลขที่」：单号，没有就来源号 / 原因 / 类型。"""
    return str(row.get("doc_no") or row.get("ref_id") or row.get("reason") or row.get("movement_type") or "")


def _own_cost(row):
    """这笔收自带的单价（MR.ERP 的收按单据金额）；调拨入不带（同一个池里按池均价入、与调出相抵，同 MR.ERP 照抄调出那笔）。"""
    if not row.get("cost_known") or row.get("movement_type") == contracts.TRANSFER_IN:
        return None
    return contracts.d(row.get("unit_cost"))


def blocks(rows, *, date_from, include, group, costs) -> dict:
    """收发存页面与导出共用的唯一算法（`card_rows.product_blocks`）：一个物料全公司的流水 → `{分组: Block}`。"""
    return card_rows.product_blocks(rows, date_from=date_from, include=include, group=group, own_cost=_own_cost,
                                    is_initialization=_initialization, doc_of=movement_doc, costs=costs)


def pool_rows(products, date_to) -> dict:
    """这些物料在**当前公司所有仓**截止日前的全部流水（MR.ERP 一个物料全公司一个池，不受页面的仓筛选限制）：
    `{物料: [行]}`，行带归一后的日期和录入先后 `seq`。"""
    out: dict = {}
    for index, row in enumerate(_movements(products=set(products), date_to=date_to) if products else []):
        out.setdefault(row["product"], []).append(dict(row, posting_date=_date(row["posting_date"]), seq=index))
    return out


def _number(value):
    return None if value is None else float(value)


def _summary_rows(keys, pool, *, date_from, visible, names, warehouse_names) -> list[dict]:
    """汇总列表的行（一个「商品 × 仓库」一行）：数字取自与导出同一份算法（分组 = 仓）。"""
    wanted = set(keys)
    computed = {product: blocks(pool.get(product) or [], date_from=date_from,
                                include=lambda position, product=product: (product, *position) in wanted,
                                group=lambda position: position, costs=visible)
                for product in {key[0] for key in keys}}
    rows = []
    for product_id, warehouse_id in keys:
        item = computed[product_id].get((warehouse_id,)) or card_rows.Block(contracts.d(0), contracts.d(0))
        rows.append({
            "product": product_id, "product_name": (names.get(product_id) or {}).get("product_name"),
            "spec": (names.get(product_id) or {}).get("spec") or "",
            "warehouse": warehouse_id,
            "warehouse_name": warehouse_names.get(warehouse_id),
            "name": contracts.position_name("BAL", product_id, warehouse_id),
            "unit": (names.get(product_id) or {}).get("base_unit") or (names.get(product_id) or {}).get("unit"),
            "opening_qty": float(item.opening_qty), "in_qty": float(item.in_qty), "out_qty": float(item.out_qty),
            "closing_qty": float(item.closing_qty),
            "closing_unit_cost": _number(item.closing_price) if visible else None,
            "closing_value": _number(item.closing_amount) if visible else None,
            "cost_known": item.closing_amount is not None,
        })
    return rows


def summary(*, date_from=None, date_to=None, product=None, warehouse=None) -> list[dict]:
    """按商品汇总：期初、本期入、本期出、期末（整批，测试与内部核对用；页面走 summary_page）。"""
    guard.require("report.read")
    date_from = _date(date_from)
    date_to = _date(date_to)
    movements = _movements(product=product, warehouse=warehouse, date_to=date_to)
    keys = sorted({(m["product"], m["warehouse"]) for m in movements})
    pool = pool_rows({key[0] for key in keys}, date_to)
    return _summary_rows(keys, pool, date_from=date_from, visible=guard.cost_visible(),
                         names=_product_names({key[0] for key in keys}),
                         warehouse_names=_warehouse_names(key[1] for key in keys))


def _as_list(filters) -> list:
    if isinstance(filters, list):
        return list(filters)
    return [[key, *value] if isinstance(value, list) else [key, "=", value] for key, value in filters.items()]


def _position_sums(query, date_from) -> dict:
    """每个「商品 × 仓库」一行：截止起始日前的和、本期正 / 负数量的和、本期初始化的和（全部 / 正数）。

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

    sql = frappe.qb.get_query(MOVEMENT, fields=["product", "warehouse"], filters=filters,
                              group_by="product, warehouse", ignore_permissions=False).select(
        total(table.posting_date < date_from, "carried"), total(period & (table.qty_base > 0), "period_in"),
        total(period & (table.qty_base < 0), "period_out"), total(period & initialization, "init_all"),
        total(period & initialization & (table.qty_base > 0), "init_in"))
    out: dict = {}
    for row in sql.run(as_dict=True):
        key = (row["product"], row["warehouse"])
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


def summary_page(*, date_from=None, date_to=None, product=None, warehouse=None,
                 start=0, limit=20, sort_by=None, sort_order=None, conditions=None) -> dict:
    """汇总列表的一页：`{"items": 这一页, "total": 全部行数}`，排序口径同 `listing.sort_rows`。"""
    guard.require("report.read")
    date_from = _date(date_from)
    date_to = _date(date_to)
    start = max(int(start or 0), 0)
    limit = min(max(int(limit or 20), 1), PAGE_MAX)
    query = {"product": product, "warehouse": warehouse, "date_to": date_to,
             "conditions": conditions}
    visible = guard.cost_visible()
    sums = _position_sums(query, date_from)
    keys = sorted(sums)
    sort_key = str(sort_by or "").strip()
    # 商品名只在按商品名排序或要整批算时取全部，其余只取这一页的
    names = _product_names({key[0] for key in keys}) if sort_key in SUMMARY_SORTS and sort_key != "warehouse_name" else {}
    warehouse_names = _warehouse_names(key[1] for key in keys)
    if sort_key in SUMMARY_SORTS and sort_key not in KEY_SORTS:
        quantities = _quantities(sums)
        ordered = listing.sort_rows([{"key": key, **quantities.get(key, {})} for key in keys],
                                    sort_key, sort_order, SUMMARY_SORTS)
        page = [row["key"] for row in ordered[start:start + limit]]
        pool = pool_rows({key[0] for key in page}, date_to)
        return {"items": _summary_rows(page, pool, date_from=date_from, visible=visible, names=names,
                                       warehouse_names=warehouse_names), "total": len(keys)}
    labels = [{"key": key, "product_name": (names.get(key[0]) or {}).get("product_name"),
               "warehouse_name": warehouse_names.get(key[1])} for key in keys]
    page = [row["key"] for row in listing.sort_rows(labels, sort_key, sort_order, SUMMARY_SORTS)[start:start + limit]]
    pool = pool_rows({key[0] for key in page}, date_to)
    names = names or _product_names({key[0] for key in page})
    rows = _summary_rows(page, pool, date_from=date_from, visible=visible, names=names,
                         warehouse_names=warehouse_names)
    return {"items": rows, "total": len(keys)}


def card(*, product, warehouse=None, date_from=None, date_to=None, location=None) -> dict:
    """单品收发存明细：期初一行 + 逐笔进出 + 期末。``location``：11-19 以前的旧链接带的仓位，不再看。"""
    guard.require("report.read")
    date_from = _date(date_from)
    date_to = _date(date_to)
    visible = guard.cost_visible()
    # 池要全公司（MR.ERP 一个物料一个价）；页面点的是哪个仓，就只显示那里的行、结余按那里的行滚（同分仓版 198.php）
    rows = pool_rows({product}, date_to).get(product) or []
    store = (warehouses.resolve(warehouse, create=False) or "__missing__") if warehouse else None
    include = lambda position: store is None or position[0] == store  # noqa: E731
    item = blocks(rows, date_from=date_from, include=include, group=lambda position: None, costs=visible).get(None) \
        or card_rows.Block(contracts.d(0), contracts.d(0))
    by_seq = {row["seq"]: row for row in rows}
    rolled = [(line, by_seq[line.seq]) for line in item.lines]

    names = _product_names({product})
    meta = names.get(product) or {}
    warehouse_names = _warehouse_names(r["warehouse"] for _line, r in rolled)
    return {
        "product": product, "product_name": meta.get("product_name"), "spec": meta.get("spec") or "",
        "unit": meta.get("base_unit") or meta.get("unit"),
        "warehouse": warehouse, "date_from": date_from, "date_to": date_to,
        # 页面顶部显示仓库名，不是内部编号（录制问题 #17）。
        "warehouse_name": frappe.db.get_value("ERP Warehouse", warehouse, "warehouse_name") if warehouse else None,
        "opening": {"date": date_from, "qty": float(item.opening_qty),
                    "unit_cost": _number(item.opening_price) if visible else None,
                    "value": _number(item.opening_amount) if visible else None},
        "rows": [
            _public({"name": r["name"], "warehouse": r["warehouse"],
                     "warehouse_name": warehouse_names.get(r["warehouse"]),
                     "date": r["posting_date"], "kind": r["movement_type"], "doc_no": r["doc_no"],
                     "desc": r["ref_id"] or r["reason"] or r["movement_type"],
                     "qty": float(r["qty_base"]), "unit_price": line.unit_price,
                     "amount": line.amount, "bal_qty": float(line.bal_qty),
                     "bal_unit_cost": line.bal_price, "bal_value": line.bal_amount},
                    cost_visible=visible)
            for line, r in rolled],
        "closing": {"qty": float(item.closing_qty),
                    "unit_cost": _number(item.closing_price) if visible else None,
                    "value": _number(item.closing_amount) if visible else None},
        "cost_known": item.closing_amount is not None,
    }
