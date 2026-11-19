"""入库 / 出库单据：建单、提交（落流水）、作废（反向冲销）、以及采购确认后的自动入库。"""

import frappe
from frappe import _
from frappe.utils import now

from pearnly_core.desk import list_filters
from pearnly_erp_portal.access import guard, listing
from pearnly_erp_portal.inventory import access, contracts, positions, serials, stock, warehouses
from pearnly_erp_portal.numbering import service as numbering

DOCTYPE = "ERP Stock Entry"

NOT_FOUND = "Stock entry not found"
LINES_REQUIRED = "Provide at least one line item"
BAD_TRANSITION = "This status change is not allowed"
LOCKED = "A submitted entry cannot be changed"
SAME_WAREHOUSE = "The destination warehouse must differ from the source warehouse"
TO_WAREHOUSE_REQUIRED = "Pick a destination warehouse"

TRANSFER = "transfer"  # 调拨：出入库单第三个方向（多仓闭环 P2，同公司两仓之间，一出一进、成本随货走）
# 物料调整：第四个方向（岳父 11-06，号 IA）。一张单正负混录（减少直接输负数，同 Express），流水是带符号的 `adjust`；
# 盘点差异也生成它（草稿，人提交才入账，`count.adjustment_draft`）。历史盘点生成的 AD 单（方向 in / out、`ref_type = count`）
# 也算这一类：列在物料调整页、按 `stock.adjust` 判权限（`access.entry_kind`）
ADJUST = access.ADJUST
DIRECTIONS = ("in", "out", TRANSFER, ADJUST)
COUNT_REF = access.COUNT_REF
ZERO_QTY = "Quantity cannot be 0"

LIST_FIELDS = ("name", "direction", "posting_date", "warehouse", "to_warehouse", "supplier", "doc_no",
               "status", "purpose", "reason", "ref_type", "ref_id", "stock_count", "submitted_at", "ledger_state", "ledger_docnum",
               "ledger_void_state")

# 用途（录制问题 #14：以前只有自由填写的备注）：入库是退库，出库是内部领用 / 报损 / 样品 / 生产领料；都可选「其他」或不填。
# 接了 Express 的公司出库按用途写进对应的领用簿（`express.stock_entry.PURPOSE_BOOKS`，11-18 D12）。
PURPOSES = {"in": ("return", "other"), "out": ("internal_use", "damage", "sample", "production", "other")}


def _purpose(direction, value):
    value = str(value or "").strip()
    if value and value not in PURPOSES.get(direction, ()):
        frappe.throw(_("Invalid value"))
    return value or None


def require_kind(direction, ref_type=None, stock_count=None) -> None:
    """开 / 改 / 提交 / 作废这一类单的权限：按单的种类（`access.entry_kind`）要 `stock.<种类>`；盘点生成的调整单盘点员（`count.write`）
    也能提交（以前「结束盘点」就是 `count.write` 过账，主会话 11-06 审定 #2）。"""
    kind = access.entry_kind(direction, ref_type)
    if kind == ADJUST and stock_count and guard.has("count.write"):
        return
    guard.require("stock." + kind)


def _get(name):
    if not name or not frappe.db.exists(DOCTYPE, name):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    doc = frappe.get_doc(DOCTYPE, name)
    doc.check_permission("read")
    return doc


def _posting_now(value) -> bool:
    """`submit=1`：建单/改单的同时直接过账（用户 2026-09-22 口径：入库/出库不再留草稿，
    界面上只有一颗"提交"）。同一事务里过账，失败就整笔不留（不留下孤立的草稿单）。"""
    return value in (True, 1, "1", "true", "True")


def _business_lines(lines) -> list:
    """只留**真有商品**的明细行，然后要求至少一行。

    界面上的明细表是可编辑的空表，框架会给它带一行空白的（数量是字段默认 1、没有商品）。
    那种行不是业务明细：以前它一路走到建单那里，在 `line["product"]` 上抛 KeyError，
    用户看到的是"服务器错误"而不是"至少要有一行明细"。所以在这里挡住 —— 顺便也挡住
    第三方直接调接口时塞进来的空行。
    """
    if not isinstance(lines, list):
        frappe.throw(_(LINES_REQUIRED))
    kept = [line for line in lines if isinstance(line, dict) and str(line.get("product") or "").strip()]
    if not kept:
        frappe.throw(_(LINES_REQUIRED))
    return kept


def _destination(warehouse, to_warehouse, to_location, company) -> dict:
    """调拨的调入仓与单头调入库位：必须是同一家公司启用的另一个仓（同仓不同库位不是仓间调拨，本期不开）；调入库位按调入仓的清单核。"""
    if not str(to_warehouse or "").strip():
        frappe.throw(_(TO_WAREHOUSE_REQUIRED))
    to_warehouse = warehouses.required(to_warehouse, company=company)
    if to_warehouse == warehouse:
        frappe.throw(_(SAME_WAREHOUSE))
    return positions.header(to_warehouse, to_location, company=company)


def _transfer_lines(lines, to_location, to_warehouse):
    """明细行的调入库位：行上写了用行上的，没写用单头的；按调入仓的清单核。"""
    for line, raw in lines:
        place = raw.get("to_location") if "to_location" in raw else to_location
        line["to_location"] = positions.check(to_warehouse, place)
    return [line for line, _raw in lines]


def _line_row(line) -> dict:
    return {"product": line["product"], "qty": line["qty"], "unit": line.get("unit"), "unit_cost": line.get("unit_cost"),
            "unit_cost_manual": 1 if line.get("unit_cost_manual") else 0,
            "warehouse": line["warehouse"], "location": line["location"], "to_location": line.get("to_location") or ""}


def stock_units(products) -> dict:
    """物料 → 库存单位（`base_unit`，没填退回 `unit`，同 `stock.resolve_factor` 的基准）。只取给定的这几件物料。"""
    names = sorted({str(product) for product in products if product})
    if not names:
        return {}
    rows = frappe.get_all("ERP Product", filters={"name": ["in", names]},  # bounded: 这张单的物料
                          fields=["name", "base_unit", "unit"], limit_page_length=0)
    return {row.name: (row.base_unit or row.unit or "").strip() for row in rows}


def _with_units(lines) -> list:
    """行上没写单位的补物料的库存单位。服务端自己生成的行（盘点差异生成的调整单、按库存单位算的系统单）不带单位，
    数量本来就按库存单位入账（空单位 = 库存单位，`stock.resolve_factor`），补上只是让单据上看得见、写 Express 有单位，换算不变。"""
    missing = [line["product"] for line in lines if not str(line.get("unit") or "").strip()]
    units = stock_units(missing)
    return [line if str(line.get("unit") or "").strip() else {**line, "unit": units.get(line["product"]) or None}
            for line in lines]


def _flag(value) -> bool:
    return value in (True, 1, "1", "true", "True")


def default_unit_cost(product, warehouse, location="", unit=None):
    """物料调整增加行的默认单价：这个位置现在的均价，没有就退回物料参考成本（同盘点估值 `count.valuation`），再没有留空；
    按行上单位换成每个单位的价（均价是每个库存单位的）。"""
    from pearnly_erp_portal.inventory import count as counts

    unit_cost = counts.valuation(product, (product, warehouse, positions.clean_location(location)))
    if unit_cost is None:
        return None
    product_doc = frappe.get_doc("ERP Product", product)
    _base, factor = stock.resolve_factor(product_doc, unit)
    return contracts.q2(unit_cost * factor)


def _adjust_lines(lines, raw, *, trusted=False) -> list:
    """物料调整的行：数量带正负、不能为 0；单价只用于增加的行。人亲手填 / 改过的单价记 `unit_cost_manual`（写 Express TK 时只带这种，
    主会话 11-06 审定 #5）。能看成本的人：给了价就照存（页面带出的默认价没改过时 `unit_cost_manual` 为假）；看不到成本的人给的价不算，
    增加行一律按当前均价（`default_unit_cost`）。``trusted``：服务端自己生成的行（盘点估值，`count.adjustment_draft`），给的价照存、
    不算人填。减少的行按出货当时的均价出（`stock.apply_movement`），不存价。"""
    visible = guard.cost_visible()
    out = []
    for line, source in zip(lines, raw):
        try:
            qty = contracts.d(line.get("qty"))
        except ValueError:
            frappe.throw(_("Invalid value"))
        if not qty:
            frappe.throw(_(ZERO_QTY))
        given = source.get("unit_cost")
        priced = given not in (None, "") and (visible or trusted)
        if qty < 0:
            cost, manual = None, False
        elif priced:
            cost, manual = given, bool(visible and not trusted and _flag(source.get("unit_cost_manual")))
        else:
            cost, manual = default_unit_cost(line["product"], line["warehouse"], line.get("location"), line.get("unit")), False
        out.append({**line, "qty": qty, "unit_cost": cost, "unit_cost_manual": manual})
    return out


def create_entry(*, direction, posting_date, lines, warehouse=None, supplier=None,
                 reason=None, ref_type=None, ref_id=None, dedupe_key=None, submit=False,
                 commit=True, purpose=None, location=None, to_warehouse=None, to_location=None,
                 stock_count=None, trusted=False):
    """建一张入库 / 出库 / 调拨 / 物料调整单；`submit=True` 时同一事务里过账。

    `commit=True`（默认）在建单 / 过账后提交事务；服务端自己的流程（盘点生成调整单草稿）传 `commit=False` 自己决定何时提交。

    一张单一个仓库（岳父 11-06）：明细行的仓一律是单头的仓，行上只能改仓位。唯一例外：盘点生成的调整单（`stock_count`）里
    盘点时在别的仓找到的货，那几行保留自己的仓（主会话 11-06 审定 #8）。`trusted`：服务端自己生成的行（盘点估值照存，不算人填的价）。
    """
    if direction not in DIRECTIONS:
        frappe.throw(_("Invalid value"))
    if stock_count and direction != ADJUST:
        frappe.throw(_("Invalid value"))
    require_kind(direction, ref_type, stock_count)
    # 顺序要紧：**先把这张单的仓定死，再铺行**。单据没写仓时只认「这家公司只有一个仓」，多个仓报错让人选
    # （`warehouses.receiving`，多仓闭环 P1 不再落卖货仓）。
    warehouse = warehouses.receiving(warehouse)
    company = warehouses.company_of(warehouse)
    transfer = direction == TRANSFER
    destination = _destination(warehouse, to_warehouse, to_location, company) if transfer else {"warehouse": None, "location": ""}
    if not ref_type and not stock_count:  # 人建的单（盘点、采购入库等系统单不算）：下次预填本人上次选的仓（多仓闭环 P1，问 2）
        warehouses.remember(TRANSFER if transfer else "stock_" + direction, warehouse, company)
    # 单头仓位（多仓闭环 P1 单头「仓库 + 仓位」）：行上没写仓位的用它
    location = positions.header(warehouse, location, company=company)["location"]
    raw = _business_lines(lines)
    raw = _own_warehouse(raw, warehouse, stock_count)
    lines = _with_units([positions.prepare(line, warehouse, company=company, location=location, allow_inactive=bool(stock_count))
                         for line in raw])
    if transfer:
        lines = _transfer_lines(list(zip(lines, raw)), destination["location"], destination["warehouse"])
    if direction == ADJUST:
        lines = _adjust_lines(lines, raw, trusted=trusted)
    # 单据号由系统发（入库 IR / 出库 IS / 调拨 TF / 物料调整 IA，类型码 + 佛历年 + 六位流水），不接受手输 ——
    # 收发存报表"关联单据"那一列显示的就是它，口径见 `numbering.contracts`。撞号继续往后找（MR.ERP 建单时也是先查库再加）。
    doc_no = numbering.allocate(direction=direction, on=posting_date,
                                exists=lambda value: bool(frappe.db.exists(DOCTYPE, {"doc_no": value})))
    doc = frappe.get_doc({
        "doctype": DOCTYPE, "direction": direction, "posting_date": posting_date,
        "warehouse": warehouse, "location": location, "to_warehouse": destination["warehouse"],
        "to_location": destination["location"], "supplier": None if transfer or direction == ADJUST else supplier,
        "doc_no": doc_no, "reason": reason, "purpose": _purpose(direction, purpose),
        "ref_type": ref_type, "ref_id": ref_id, "stock_count": stock_count, "dedupe_key": dedupe_key, "status": "draft",
        # 调拨不带单价（调出按调出位置均价、调入随调出，过账时算）
        "lines": [{**_line_row(line), **({"unit_cost": None} if transfer else {})} for line in lines],
    })
    serials.set_doc_serials(doc, lines)  # 按台管理的物料：这一行登记 / 选中的台（`inventory.serials`）
    access.write_entry(doc, "insert")
    # 直接过账：与建单在同一个事务里（中途失败整笔回滚，不会剩一张草稿）。
    if _posting_now(submit):
        _post(doc)
    if commit:
        frappe.db.commit()
    return {"name": doc.name, "doc_no": doc.doc_no, "status": doc.status}


def _own_warehouse(raw, warehouse, stock_count=None) -> list:
    """一张单一个仓库：行上的仓一律换成单头的仓。盘点生成的调整单里，行上带着的别的仓（盘点时在别的仓找到的货）照留。"""
    if stock_count:
        return [{**line, "warehouse": line.get("warehouse") or warehouse} for line in raw]
    return [{**line, "warehouse": warehouse} for line in raw]


# 列头排序白名单。单据号那一列显示的是 `doc_no or name`，排库里的 doc_no 就够用。
SORT_FIELDS = {
    "doc_no": "doc_no", "posting_date": "posting_date", "warehouse": "warehouse",
    "direction": "direction", "status": "status", "creation": "creation",
    # 列表上的列名叫法（前端 `sort_fields` 用页面上的列名，这里一一对应到库字段）。
    "warehouse_name": "warehouse",
    # 「备注」列（`reason` → `note`）**故意不开放排序**：`note` 是字段级受权限保护的字段，
    # `frappe.get_list(order_by="note")` 会直接抛 `PermissionError`（实测："您无权访问字段：
    # ERP Stock Entry.note"）。要排它就得把这一页从"服务端分页"改成"整批取回内存排序"，
    # 而单据会越积越多，为了一列备注不值当 —— 宁可这一列不可点，也不要点了报错。
}


def _entry_filters(*, direction=None, warehouse=None, status=None, date_from=None, date_to=None) -> tuple:
    """列表的 ``(filters, or_filters)``。种类按 `access.entry_kind`：物料调整 = 方向 adjust **或** 历史盘点生成的 AD 单
    （``ref_type = count``）；入库 / 出库 / 调拨不含 AD 单（岳父 11-06）。"""
    guard.require("stock.read")
    allowed = [key for key in DIRECTIONS if guard.has("stock." + key)]
    if direction and not (direction == ADJUST and guard.has("count.write")):  # 盘点员看盘点生成的调整单（`access.entry_query` 再限）
        guard.require("stock." + direction)
    elif not direction and not allowed:
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    kinds = [direction] if direction else allowed
    filters: list = []
    or_filters: list = []
    plain = [key for key in kinds if key != ADJUST]
    if ADJUST in kinds and not plain:
        or_filters = [["direction", "=", ADJUST], ["ref_type", "=", COUNT_REF]]
    elif ADJUST in kinds:
        or_filters = [["direction", "=", ADJUST], ["ref_type", "=", COUNT_REF], ["direction", "in", plain]]
    else:
        filters += [["direction", "in", plain], ["ref_type", "not in", [COUNT_REF]]]
    if warehouse:
        filters.append(["warehouse", "=", warehouse])
    else:
        # 不点名仓库 → 只看当前公司那几个仓库的单据（仓库属于公司）。
        filters += [[key, *value] if isinstance(value, list) else [key, "=", value]
                    for key, value in warehouses.scope_filter().items()]
    if status:
        filters.append(["status", "=", status])
    if date_from and date_to:
        filters.append(["posting_date", "between", [date_from, date_to]])
    return filters, or_filters


def count_entries(*, direction=None, warehouse=None, status=None, date_from=None, date_to=None) -> int:
    """出入库单列表的总数（与列表同一份筛选、同一道权限，数据库里数）。"""
    filters, or_filters = _entry_filters(direction=direction, warehouse=warehouse, status=status, date_from=date_from,
                                         date_to=date_to)
    return list_filters.count(DOCTYPE, filters, or_filters)


def list_entries(*, direction=None, warehouse=None, status=None, date_from=None, date_to=None,
                 limit=50, start=0, sort_by=None, sort_order=None):
    filters, or_filters = _entry_filters(direction=direction, warehouse=warehouse, status=status, date_from=date_from,
                                         date_to=date_to)
    rows = frappe.get_list(DOCTYPE, filters=filters, or_filters=or_filters, fields=list(LIST_FIELDS),
                           order_by=listing.order_by(sort_by, sort_order, SORT_FIELDS,
                                                     "posting_date desc, creation desc"),
                           limit_start=int(start), limit_page_length=int(limit))
    # 列表的「仓库」一栏显示仓库名（录制问题 #16：只给了仓库编号，那一栏一直是空的）。
    places = {r.warehouse for r in rows} | {r.to_warehouse for r in rows if r.to_warehouse}
    names = dict(frappe.get_all("ERP Warehouse", filters={"name": ["in", list(places) or [""]]},
                                fields=["name", "warehouse_name"], as_list=True))
    for row in rows:
        row["warehouse_name"] = names.get(row.warehouse) or row.warehouse
        row["to_warehouse_name"] = (names.get(row.to_warehouse) or row.to_warehouse) if row.to_warehouse else ""
    return _shown_reasons(rows)


def adjustment_note(title) -> str:
    """盘点调整单的备注：「盘点：盘点名称」（录制问题 #24：原来写内部编号 stocktake:STC-xxxxx）。
    单据仍由 ref_type / ref_id 指回那份盘点。"""
    return f"{_('nav.stockCount')}: {title}"


_OLD_COUNT_NOTE = "stocktake:"


def _shown_reasons(rows):
    """以前的盘点调整单备注是内部编号：显示时照新口径换成「盘点：盘点名称」（库里不改）。"""
    old = [r for r in rows if r.get("ref_type") == "count" and str(r.get("reason") or "").startswith(_OLD_COUNT_NOTE)]
    titles = dict(frappe.get_all("ERP Stock Count", filters={"name": ["in", [r["ref_id"] for r in old] or [""]]},
                                 fields=["name", "title"], as_list=True))
    for row in old:
        row["reason"] = adjustment_note(titles.get(row["ref_id"]) or row["ref_id"])
    return rows


def get_entry(name):
    guard.require("stock.read")
    doc = _get(name)
    guard.require("stock." + access.entry_kind(doc.direction, doc.get("ref_type")))
    company = warehouses.company_of(doc.warehouse)
    data = _shown_reasons([{field: doc.get(field) for field in LIST_FIELDS}])[0]
    data["kind"] = access.entry_kind(doc.direction, doc.get("ref_type"))
    if doc.get("stock_count"):
        data["stock_count_title"] = frappe.db.get_value("ERP Stock Count", doc.stock_count, "title") or doc.stock_count
    data["location"] = doc.get("location") or ""
    data["to_location"] = doc.get("to_location") or ""
    if doc.get("to_warehouse"):
        data["to_warehouse_name"] = frappe.db.get_value("ERP Warehouse", doc.to_warehouse, "warehouse_name") or doc.to_warehouse
    units = serials.line_serials(doc)
    # 明细带上品名与内部编码（收发货页显示内部编码，10-29）；只取这张单的商品
    names = sorted({row.product for row in doc.get("lines") or [] if row.product})
    shown = {item.name: item for item in frappe.get_all(  # bounded: 这张单的商品
        "ERP Product", filters={"name": ["in", names]}, fields=["name", "product_name", "internal_code", "category", "attribute"],
        limit_page_length=0)} if names else {}
    # 「分类 · 特性/颜色」（岳父 11-01）：按物料现值显示，单据不存
    from pearnly_erp_portal.products import categories

    labels = categories.names(item.category for item in shown.values())
    # 老单 / 修复前生成的草稿行上没存单位：显示库存单位（空单位本来就按库存单位入账）
    stock_unit = stock_units(row.product for row in doc.get("lines") or [] if not row.unit)
    data["lines"] = [
        {"product": row.product, "qty": row.qty, "unit": row.unit or stock_unit.get(row.product) or None,
         "product_name": (shown.get(row.product) or {}).get("product_name") or "",
         "internal_code": (shown.get(row.product) or {}).get("internal_code") or "",
         "spec": categories.spec(labels.get((shown.get(row.product) or {}).get("category")),
                                 (shown.get(row.product) or {}).get("attribute")),
         "unit_cost": row.unit_cost, "unit_cost_manual": bool(row.get("unit_cost_manual")),
         "serials": units.get(row.idx, []), "to_location": row.get("to_location") or "",
         **positions.public(row, doc.warehouse, company=company)}
        for row in doc.get("lines") or []
    ]
    # 一张单一个仓库：行上的仓都是单头的就不显示仓库列；老单（或盘点在别的仓找到货的调整单）有不一样的才显示，读出来不变样
    data["line_warehouses"] = any(line["warehouse"] != doc.warehouse for line in data["lines"])
    return data


def update_entry(name, **changes):
    submit = changes.pop("submit", None)
    for field in ("stock_count", "direction", "doc_no", "trusted"):
        changes.pop(field, None)  # 来源盘点、方向、单号不能从这里改
    doc = _get(name)
    require_kind(doc.direction, doc.get("ref_type"), doc.get("stock_count"))
    if doc.status != "draft":
        frappe.throw(_(LOCKED))
    company = warehouses.company_of(doc.warehouse)
    # `doc_no` 不在可改字段里：号是发出去的，改它等于让历史流水对不上单据。
    if "purpose" in changes:
        doc.purpose = _purpose(doc.direction, changes["purpose"])
    before = doc.warehouse
    for field in ("posting_date", "warehouse", "supplier", "reason"):
        if field in changes:
            doc.set(field, warehouses.required(changes[field], company=company) if field == "warehouse" else changes[field])
    if "location" in changes or "warehouse" in changes:
        # 换了仓又没给库位：旧仓的单头库位不带过去（验收复核 P1）
        moved = "warehouse" in changes and "location" not in changes and doc.warehouse != before
        doc.location = positions.header(doc.warehouse, "" if moved else changes.get("location", doc.get("location")),
                                        company=company)["location"]
    transfer = doc.direction == TRANSFER
    if transfer and ({"warehouse", "to_warehouse", "to_location"} & set(changes)):
        moved = "to_warehouse" in changes and "to_location" not in changes and changes["to_warehouse"] != doc.to_warehouse
        destination = _destination(doc.warehouse, changes.get("to_warehouse", doc.to_warehouse),
                                   "" if moved else changes.get("to_location", doc.get("to_location")), company)
        doc.to_warehouse, doc.to_location = destination["warehouse"], destination["location"]
    if "warehouse" in changes and "lines" not in changes and doc.warehouse != before:
        # 换了单头仓：一张单一个仓库，行跟着单头仓重铺（仓位回到单头的）
        changes["lines"] = [{"product": row.product, "qty": row.qty, "unit": row.unit, "to_location": row.to_location or "",
                             "unit_cost": row.unit_cost, "unit_cost_manual": row.get("unit_cost_manual"),
                             **({"warehouse": row.warehouse} if doc.get("stock_count") and row.warehouse != before else {})}
                            for row in doc.lines]
    if "lines" in changes:
        raw = _own_warehouse(_business_lines(changes["lines"]), doc.warehouse, doc.get("stock_count"))
        lines = _with_units([positions.prepare(line, doc.warehouse, company=company, location=doc.get("location"),
                                               allow_inactive=bool(doc.get("stock_count"))) for line in raw])
        if transfer:
            lines = _transfer_lines(list(zip(lines, raw)), doc.get("to_location"), doc.to_warehouse)
        if doc.direction == ADJUST:
            lines = _adjust_lines(lines, raw)
        doc.lines = []
        for line in lines:
            doc.append("lines", {**_line_row(line), **({"unit_cost": None} if transfer else {})})
        serials.set_doc_serials(doc, lines)
    access.write_entry(doc, "save")
    if _posting_now(submit):
        _post(doc)
    frappe.db.commit()
    return {"name": doc.name, "status": doc.status}


def _movement_type(doc) -> str:
    """入库按单据性质分：带采购引用的是采购入库，其余是普通入库；物料调整是带符号的 `adjust`（收发存按正负分收入 / 发出）。"""
    if doc.direction == ADJUST:
        return contracts.ADJUST
    if doc.direction == "in":
        return contracts.PURCHASE_IN if doc.ref_type == "purchase" else contracts.STOCK_IN
    return contracts.STOCK_OUT


def _post(doc) -> dict:
    """把单据的每一行落成流水，并把单据置为"已提交"。**不提交事务** —— 调用方决定
    是"建单 + 过账"一笔提交，还是单独提交一张草稿。

    流水幂等锚是单据名，所以重复提交不会记两次。
    """
    company = warehouses.company_of(doc.warehouse)
    if doc.direction == "out":
        # 账本是 MR.ERP、这份账套设成不允许负库存：照 MR.ERP 出库网页，提交前按本公司各仓合计拦（`mrerp.stock`；其余不拦）
        from pearnly_erp_portal.mrerp import stock as mrerp_stock

        mrerp_stock.check_negative(doc, company)
    units = serials.line_serials(doc)
    if doc.direction == TRANSFER:
        # 调拨：一出一进同一个事务（`stock.apply_transfer`）；调入仓在建单时已核过同公司、不同仓
        result = stock.apply_transfer(
            warehouse=doc.warehouse, to_warehouse=doc.to_warehouse,
            lines=[{"product": row.product, "qty": row.qty, "unit": row.unit, "serials": units.get(row.idx, []),
                    **positions.saved(row, doc.warehouse, company=company), "to_location": row.get("to_location") or ""}
                   for row in doc.lines],
            posting_date=str(doc.posting_date), ref_type=doc.ref_type or "stock_entry", ref_id=doc.name,
            doc_no=doc.doc_no, reason=doc.reason, client_uuid=f"stock-entry:{doc.name}", company=company)
        return _posted(doc, result)
    counted = doc.direction == ADJUST and doc.get("stock_count")
    if counted:
        # 盘点生成的调整单：盘点带来的事挪到提交时做（岳父 11-06）——盘到的停用物料重新启用（过账前，否则认不出）
        from pearnly_erp_portal.inventory import count as counts

        counts.before_adjustment_posted(doc.stock_count, doc.name)
    movement_type = _movement_type(doc)
    result = stock.apply_lines(
        warehouse=doc.warehouse, movement_type=movement_type,
        lines=[{"product": row.product, "qty": row.qty, "unit": row.unit, "serials": units.get(row.idx, []),
                "unit_cost": row.unit_cost, **positions.saved(row, doc.warehouse, company=company)} for row in doc.lines],
        posting_date=str(doc.posting_date), ref_type=doc.ref_type or "stock_entry",
        ref_id=doc.name, doc_no=doc.doc_no, reason=doc.reason,
        client_uuid=f"stock-entry:{doc.name}", internal=True, company=company,
        # 盘点来的：照盘点口径不登记 / 不出台（盘到别处的台在下面换位），仓位在盘点记录时已核过（旧仓位也能盘）
        units=not counted, place_checked=bool(counted),
    )
    if counted:
        counts.adjustment_posted(doc.stock_count, doc.name)
    return _posted(doc, result)


def _posted(doc, result) -> dict:
    doc.status = "submitted"
    doc.submitted_at = now()
    access.write_entry(doc, "save")
    # 接了 Express 的公司：提交后写进事务所的账本（出库 → 领用 OU、入库 → 调整 JU、物料调整 → TK、调拨 → RL；采购自动入库、
    # 历史盘点生成的 AD 单不写，见 express.stock_entry）
    from pearnly_erp_portal.express import push as express_push

    express_push.queue(DOCTYPE, doc.name)
    return result


def delete_draft(name):
    """删一张草稿（真删，号不回收，同 MR.ERP 先占号）。盘点生成的调整单草稿删了，盘点回到可以再生成。"""
    frappe.db.sql("select name from `tabERP Stock Entry` where name=%s for update", (name,))
    doc = _get(name)
    require_kind(doc.direction, doc.get("ref_type"), doc.get("stock_count"))
    if doc.status != "draft":
        frappe.throw(_(BAD_TRANSITION))
    count = doc.get("stock_count")
    if count:  # 先让盘点放开这张单（盘点上的链接还指着它时框架不让删）
        from pearnly_erp_portal.inventory import count as counts

        counts.adjustment_released(count, name)
    access.write_entry(doc, "delete")  # 草稿的附件框架连带删（`attachments.guard_file` 放行草稿）
    frappe.db.commit()
    return {"ok": True}


def submit_entry(name):
    """提交一张**已存在的草稿**（老数据、列表里留下的草稿、盘点生成的物料调整单）。"""
    frappe.db.sql("select name from `tabERP Stock Entry` where name=%s for update", (name,))
    doc = _get(name)
    require_kind(doc.direction, doc.get("ref_type"), doc.get("stock_count"))
    if doc.status != "draft":
        frappe.throw(_(BAD_TRANSITION))
    result = _post(doc)
    frappe.db.commit()
    return {"name": doc.name, "status": doc.status, **result}


def void_entry(name):
    """作废：追加反向流水把数量还回去。流水不动、不删，历史看得见。盘点生成的调整单作废后，盘点回到「已结束」，可以再生成。"""
    frappe.db.sql("select name from `tabERP Stock Entry` where name=%s for update", (name,))
    doc = _get(name)
    require_kind(doc.direction, doc.get("ref_type"), doc.get("stock_count"))
    if doc.status != "submitted":
        frappe.throw(_(BAD_TRANSITION))
    if doc.ref_type == "count" and frappe.db.get_value("ERP Stock Count", doc.ref_id, "ledger_state"):
        # 盘点进了写 Express（整份盘点轧差写一张 TK，它生成的单不逐张写）：单独作废这张，Express 的 TK 不会跟着动（验收 10-25）
        frappe.throw(_("stock.countEntryVoid"))
    stock.reverse_movements(ref_type=doc.ref_type or "stock_entry", ref_id=doc.name,
                            client_uuid=f"stock-void:{doc.name}", reason=f"void of {doc.name}",
                            company=warehouses.company_of(doc.warehouse))
    doc.status = "void"
    access.write_entry(doc, "save")
    if doc.direction == ADJUST and doc.get("stock_count"):
        from pearnly_erp_portal.inventory import count as counts

        counts.adjustment_released(doc.stock_count, doc.name)
    # 已写进 Express 的：照 Express 原生作废原单（出入库第一期 B 段 ①）；没写进去的只在 ERP 作废
    from pearnly_erp_portal.express import push as express_push

    express_push.void(DOCTYPE, doc.name)
    frappe.db.commit()
    return {"name": doc.name, "status": doc.status}
