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

LIST_FIELDS = ("name", "direction", "posting_date", "warehouse", "supplier", "doc_no",
               "status", "purpose", "reason", "ref_type", "ref_id", "submitted_at", "express_state", "express_docnum",
               "express_void_state")

# 用途（录制问题 #14：以前只有自由填写的备注）：入库是退库，出库是内部领用 / 报损；都可选「其他」或不填。
PURPOSES = {"in": ("return", "other"), "out": ("internal_use", "damage", "other")}


def _purpose(direction, value):
    value = str(value or "").strip()
    if value and value not in PURPOSES.get(direction, ()):
        frappe.throw(_("Invalid value"))
    return value or None


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


def create_entry(*, direction, posting_date, lines, warehouse=None, supplier=None,
                 reason=None, ref_type=None, ref_id=None, dedupe_key=None, submit=False,
                 commit=True, numbering_direction=None, purpose=None, location=None):
    """建一张入库/出库单据；`submit=True` 时同一事务里过账。

    `commit=True`（默认）在建单/过账后提交事务。**盘点过账**要"整批一个事务"，
    所以它传 `commit=False` 自己决定何时提交（与 `_post` 的"调用方决定"同一口径）。

    `numbering_direction` 覆盖取号方向：入库/出库默认各用自己的定义（IR/IS），
    **盘点差异传 `stocktake`**，走它自己的 `AD` 定义 —— 单据还是 in/out（决定货的
    方向），但号是盘点那一段，与 MR.ERP"调整是第三个模块、有自己的编号定义"一致。
    """
    guard.require("count.write" if numbering_direction == "stocktake" else "stock." + str(direction))
    if direction not in ("in", "out"):
        frappe.throw(_("Direction must be in or out"))
    # 顺序要紧：**先把这张单的仓定死，再铺行** —— 行上没写仓时跟着单据走，
    # 不会各行一个仓（以前 `prepare` 会回落商品的"默认仓"，单据与行就会各说各话）。
    # 单据也没写仓时只认「这家公司只有一个仓」，多个仓报错让人选（`warehouses.receiving`，多仓闭环 P1 不再落卖货仓）。
    warehouse = warehouses.receiving(warehouse)
    company = warehouses.company_of(warehouse)
    if not ref_type:  # 人建的出入库单（盘点轧差、采购入库等系统单不算）：下次预填本人上次选的仓（多仓闭环 P1，问 2）
        warehouses.remember("stock_" + direction, warehouse, company)
    # 单头库位（多仓闭环 P1 单头「仓库 + 库位」）：行上没写库位的用它
    location = positions.header(warehouse, location, company=company)["location"]
    lines = [positions.prepare(line, warehouse, company=company, location=location) for line in _business_lines(lines)]
    # 单据号由系统发（入库 IR / 出库 IS，类型码 + 佛历年 + 六位流水），不接受手输 ——
    # 收发存报表"关联单据"那一列显示的就是它，口径见 `numbering.contracts`。
    # 撞号继续往后找（MR.ERP 建单时也是先查库再加）。
    doc_no = numbering.allocate(direction=numbering_direction or direction, on=posting_date,
                                exists=lambda value: bool(frappe.db.exists(DOCTYPE, {"doc_no": value})))
    doc = frappe.get_doc({
        "doctype": DOCTYPE, "direction": direction, "posting_date": posting_date,
        "warehouse": warehouse, "location": location, "supplier": supplier, "doc_no": doc_no, "reason": reason,
        "purpose": _purpose(direction, purpose),
        "ref_type": ref_type, "ref_id": ref_id, "dedupe_key": dedupe_key, "status": "draft",
        "lines": [{
            "product": line["product"], "qty": line["qty"], "unit": line.get("unit"),
            "unit_cost": line.get("unit_cost"),
            "warehouse": line["warehouse"], "location": line["location"],
        } for line in lines],
    })
    serials.set_doc_serials(doc, lines)  # 按台管理的商品：这一行登记 / 选中的台（`inventory.serials`）
    access.write_entry(doc, "insert")
    # 直接过账：与建单在同一个事务里（中途失败整笔回滚，不会剩一张草稿）。
    if _posting_now(submit):
        _post(doc)
    if commit:
        frappe.db.commit()
    return {"name": doc.name, "doc_no": doc.doc_no, "status": doc.status}


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


def _entry_filters(*, direction=None, warehouse=None, status=None, date_from=None, date_to=None) -> dict:
    guard.require("stock.read")
    allowed = [key for key in ("in", "out") if guard.has("stock." + key)]
    if direction:
        guard.require("stock." + direction)
    elif not allowed:
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    filters: dict = {"direction": ["in", allowed]}
    if direction:
        filters["direction"] = direction
    if warehouse:
        filters["warehouse"] = warehouse
    else:
        # 不点名仓库 → 只看当前公司那几个仓库的单据（仓库属于公司）。
        filters.update(warehouses.scope_filter())
    if status:
        filters["status"] = status
    if date_from and date_to:
        filters["posting_date"] = ["between", [date_from, date_to]]
    return filters


def count_entries(*, direction=None, warehouse=None, status=None, date_from=None, date_to=None) -> int:
    """出入库单列表的总数（与列表同一份筛选、同一道权限，数据库里数）。"""
    return list_filters.count(DOCTYPE, _entry_filters(direction=direction, warehouse=warehouse, status=status,
                                                      date_from=date_from, date_to=date_to))


def list_entries(*, direction=None, warehouse=None, status=None, date_from=None, date_to=None,
                 limit=50, start=0, sort_by=None, sort_order=None):
    filters = _entry_filters(direction=direction, warehouse=warehouse, status=status, date_from=date_from,
                             date_to=date_to)
    rows = frappe.get_list(DOCTYPE, filters=filters, fields=list(LIST_FIELDS),
                           order_by=listing.order_by(sort_by, sort_order, SORT_FIELDS,
                                                     "posting_date desc, creation desc"),
                           limit_start=int(start), limit_page_length=int(limit))
    # 列表的「仓库」一栏显示仓库名（录制问题 #16：只给了仓库编号，那一栏一直是空的）。
    names = dict(frappe.get_all("ERP Warehouse", filters={"name": ["in", [r.warehouse for r in rows] or [""]]},
                                fields=["name", "warehouse_name"], as_list=True))
    for row in rows:
        row["warehouse_name"] = names.get(row.warehouse) or row.warehouse
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
    guard.require("stock." + doc.direction)
    company = warehouses.company_of(doc.warehouse)
    data = _shown_reasons([{field: doc.get(field) for field in LIST_FIELDS}])[0]
    data["location"] = doc.get("location") or ""
    units = serials.line_serials(doc)
    # 明细带上品名与内部编码（收发货页显示内部编码，10-29）；只取这张单的商品
    names = sorted({row.product for row in doc.get("lines") or [] if row.product})
    shown = {item.name: item for item in frappe.get_all(
        "ERP Product", filters={"name": ["in", names]}, fields=["name", "product_name", "internal_code"],
        limit_page_length=0)} if names else {}  # bounded: 这张单的商品
    data["lines"] = [
        {"product": row.product, "qty": row.qty, "unit": row.unit,
         "product_name": (shown.get(row.product) or {}).get("product_name") or "",
         "internal_code": (shown.get(row.product) or {}).get("internal_code") or "",
         "unit_cost": row.unit_cost, "serials": units.get(row.idx, []),
         **positions.public(row, doc.warehouse, company=company)}
        for row in doc.get("lines") or []
    ]
    return data


def update_entry(name, **changes):
    guard.require("stock.write")
    submit = changes.pop("submit", None)
    doc = _get(name)
    guard.require("stock." + doc.direction)
    if doc.status != "draft":
        frappe.throw(_(LOCKED))
    company = warehouses.company_of(doc.warehouse)
    # `doc_no` 不在可改字段里：号是发出去的，改它等于让历史流水对不上单据。
    if "purpose" in changes:
        doc.purpose = _purpose(doc.direction, changes["purpose"])
    before = doc.warehouse
    for field in ("posting_date", "warehouse", "supplier", "reason",
                  "ref_type", "ref_id"):
        if field in changes:
            doc.set(field, warehouses.required(changes[field], company=company) if field == "warehouse" else changes[field])
    if "location" in changes or "warehouse" in changes:
        # 换了仓又没给库位：旧仓的单头库位不带过去（验收复核 P1）
        moved = "warehouse" in changes and "location" not in changes and doc.warehouse != before
        doc.location = positions.header(doc.warehouse, "" if moved else changes.get("location", doc.get("location")),
                                        company=company)["location"]
    if "lines" in changes:
        lines = _business_lines(changes["lines"])
        doc.lines = []
        for line in lines:
            line = positions.prepare(line, doc.warehouse, company=company, location=doc.get("location"))
            doc.append("lines", {
                "product": line["product"], "qty": line["qty"], "unit": line.get("unit"),
                "unit_cost": line.get("unit_cost"),
                "warehouse": line["warehouse"], "location": line["location"],
            })
        serials.set_doc_serials(doc, lines)
    access.write_entry(doc, "save")
    if _posting_now(submit):
        _post(doc)
    frappe.db.commit()
    return {"name": doc.name, "status": doc.status}


def _movement_type(doc) -> str:
    """入库按单据性质分：带采购引用的是采购入库，其余是普通入库。"""
    if doc.direction == "in":
        return contracts.PURCHASE_IN if doc.ref_type == "purchase" else contracts.STOCK_IN
    return contracts.STOCK_OUT


def _post(doc) -> dict:
    """把单据的每一行落成流水，并把单据置为"已提交"。**不提交事务** —— 调用方决定
    是"建单 + 过账"一笔提交，还是单独提交一张草稿。

    流水幂等锚是单据名，所以重复提交不会记两次。
    """
    movement_type = _movement_type(doc)
    company = warehouses.company_of(doc.warehouse)
    units = serials.line_serials(doc)
    result = stock.apply_lines(
        warehouse=doc.warehouse, movement_type=movement_type,
        lines=[{"product": row.product, "qty": row.qty, "unit": row.unit, "serials": units.get(row.idx, []),
                "unit_cost": row.unit_cost, **positions.saved(row, doc.warehouse, company=company)} for row in doc.lines],
        posting_date=str(doc.posting_date), ref_type=doc.ref_type or "stock_entry",
        ref_id=doc.name, doc_no=doc.doc_no, reason=doc.reason,
        client_uuid=f"stock-entry:{doc.name}", internal=True, company=company,
    )
    doc.status = "submitted"
    doc.submitted_at = now()
    access.write_entry(doc, "save")
    # 接了 Express 的公司：提交后写进事务所的账本（出库 → 领用 OU、入库 → 调整 JU、盘点 → TK；采购自动入库不写，见 express.stock_entry）
    from pearnly_erp_portal.express import push as express_push

    express_push.queue(DOCTYPE, doc.name)
    return result


def submit_entry(name):
    """提交一张**已存在的草稿**（老数据、或列表里留下的草稿）。"""
    guard.require("stock.write")
    frappe.db.sql("select name from `tabERP Stock Entry` where name=%s for update", (name,))
    doc = _get(name)
    guard.require("stock." + doc.direction)
    if doc.status != "draft":
        frappe.throw(_(BAD_TRANSITION))
    result = _post(doc)
    frappe.db.commit()
    return {"name": doc.name, "status": doc.status, **result}


def void_entry(name):
    """作废：追加反向流水把数量还回去。流水不动、不删，历史看得见。"""
    guard.require("stock.write")
    frappe.db.sql("select name from `tabERP Stock Entry` where name=%s for update", (name,))
    doc = _get(name)
    guard.require("stock." + doc.direction)
    if doc.status != "submitted":
        frappe.throw(_(BAD_TRANSITION))
    if doc.ref_type == "count" and frappe.db.get_value("ERP Stock Count", doc.ref_id, "express_state"):
        # 盘点进了写 Express（整份盘点轧差写一张 TK，它生成的单不逐张写）：单独作废这张，Express 的 TK 不会跟着动（验收 10-25）
        frappe.throw(_("stock.countEntryVoid"))
    stock.reverse_movements(ref_type=doc.ref_type or "stock_entry", ref_id=doc.name,
                            client_uuid=f"stock-void:{doc.name}", reason=f"void of {doc.name}",
                            company=warehouses.company_of(doc.warehouse))
    doc.status = "void"
    access.write_entry(doc, "save")
    # 已写进 Express 的：照 Express 原生作废原单（出入库第一期 B 段 ①）；没写进去的只在 ERP 作废
    from pearnly_erp_portal.express import push as express_push

    express_push.void(DOCTYPE, doc.name)
    frappe.db.commit()
    return {"name": doc.name, "status": doc.status}
