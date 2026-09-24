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
                 commit=True, numbering_direction=None):
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
    # 单据也没写仓时落"这家公司卖货的那个仓"（见 `warehouses.receiving`）。
    warehouse = warehouses.receiving(warehouse)
    lines = [positions.prepare(line, warehouse) for line in _business_lines(lines)]
    # 单据号由系统发（入库 IR / 出库 IS，类型码 + 佛历年 + 六位流水），不接受手输 ——
    # 收发存报表"关联单据"那一列显示的就是它，口径见 `numbering.contracts`。
    # 撞号继续往后找（MR.ERP 建单时也是先查库再加）。
    doc_no = numbering.allocate(direction=numbering_direction or direction, on=posting_date,
                                exists=lambda value: bool(frappe.db.exists(DOCTYPE, {"doc_no": value})))
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
    doc.insert(ignore_permissions=numbering_direction == "stocktake")
    # 直接过账：与建单在同一个事务里（中途失败整笔回滚，不会剩一张草稿）。
    if _posting_now(submit):
        _post(doc)
    if commit:
        frappe.db.commit()
    return {"name": doc.name, "doc_no": doc.doc_no, "status": doc.status}


def list_entries(*, direction=None, warehouse=None, status=None, date_from=None, date_to=None,
                 limit=50, start=0):
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
    return frappe.get_list(DOCTYPE, filters=filters, fields=list(LIST_FIELDS),
                           order_by="posting_date desc, creation desc",
                           limit_start=int(start), limit_page_length=int(limit))


def get_entry(name):
    guard.require("stock.read")
    doc = _get(name)
    guard.require("stock." + doc.direction)
    data = {field: doc.get(field) for field in LIST_FIELDS}
    data["lines"] = [
        {"product": row.product, "qty": row.qty, "unit": row.unit,
         "unit_cost": row.unit_cost, "batch_no": row.batch_no, **positions.public(row, doc.warehouse)}
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
    # `doc_no` 不在可改字段里：号是发出去的，改它等于让历史流水对不上单据。
    for field in ("posting_date", "warehouse", "supplier", "reason",
                  "ref_type", "ref_id"):
        if field in changes:
            doc.set(field, warehouses.required(changes[field]) if field == "warehouse" else changes[field])
    if "lines" in changes:
        lines = _business_lines(changes["lines"])
        doc.lines = []
        for line in lines:
            line = positions.prepare(line, changes.get("warehouse"))
            doc.append("lines", {
                "product": line["product"], "qty": line["qty"], "unit": line.get("unit"),
                "unit_cost": line.get("unit_cost"), "batch_no": line.get("batch_no"),
                "warehouse": line["warehouse"], "location": line["location"],
            })
    doc.save()
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
    result = stock.apply_lines(
        warehouse=doc.warehouse, movement_type=movement_type,
        lines=[{"product": row.product, "qty": row.qty, "unit": row.unit,
                "unit_cost": row.unit_cost, **positions.saved(row, doc.warehouse)} for row in doc.lines],
        posting_date=str(doc.posting_date), ref_type=doc.ref_type or "stock_entry",
        ref_id=doc.name, doc_no=doc.doc_no, reason=doc.reason,
        client_uuid=f"stock-entry:{doc.name}", internal=True,
    )
    doc.status = "submitted"
    doc.submitted_at = now()
    doc.save(ignore_permissions=True)
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
    stock.reverse_movements(ref_type=doc.ref_type or "stock_entry", ref_id=doc.name,
                            client_uuid=f"stock-void:{doc.name}", reason=f"void of {doc.name}")
    doc.status = "void"
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"name": doc.name, "status": doc.status}
