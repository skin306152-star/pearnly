"""ERP 库存单（出库 / 入库 / 调拨 / 物料调整）写进 MR.ERP（评估 2026-11-19，主会话审定）：账本是 MR.ERP 的公司提交库存单后，
由 `mrerp.push` 同一条排队 / 领取 / 结论流程交到这里准备载荷，经共用层导入（`pearnly_core.mrerp.push.stock`）。

- 入库 → MR.ERP「รับสินค้า」导入（impstktranrec），带单价、金额；出库 → 「จ่ายสินค้า」导入（impstktraniss），成本 MR.ERP 自己算。
  调拨（ย้ายคลังสินค้า）与调整（ปรับปรุงสินค้า）MR.ERP 没有导入，要照网页表单原生保存（评估方案甲，下一步）；做好之前如实停在
  「失败 · 此单据类型无法写入 MR.ERP」（``ERR_MRERP_MODULE_UNAVAILABLE``），不装成功、也不静默不写。
- 菜单：入库一个；出库按 ERP 用途各一个（`menus.stock_out_kind`，用途清单就是 `inventory.documents.PURPOSES`，不另列），都只列已
  关联日记账的，一个就自动用，多个问一次记在公司上（同采购 / 销售的「MR.ERP 导入菜单」）。分店、部门、งาน 用这份账套的默认值，没定的
  问一次（`mrerp.choices`）。
- 物料 / 仓库码：ERP 物料与仓库从 MR.ERP 主档整读来，编码记在 ``express_code``；没有编码的（在 ERP 里手建的）停在
  ``ERR_MRERP_ITEM_UNMAPPED`` / ``ERR_MRERP_WAREHOUSE_UNMAPPED``。数量换成库存单位（MR.ERP 导入没有单位列，记物料的标准单位）。
- 入库单价：ERP 行上有单价就用；没有的照 MR.ERP 原生入库网页——选物料时带物料的标准成本（``coststd``，`checkcoststd.php`），
  原生不拦 0 → 由共用层导入时按主档读到的标准成本补（载荷不带价），读不到按 0。
- 负库存：MR.ERP 只有出库网页按「ตั้งค่าระบบสินค้า」的 อนุญาตให้สินค้าติดลบ 拦（全部仓合计、截至单据日；导入、调拨、调整都不查）。
  照原生：这份账套设成不允许时，ERP 出库单提交前按本公司各仓合计的结存拦下（:func:`check_negative`，说「库存不足」）。
"""
from __future__ import annotations

from decimal import Decimal

import frappe

from pearnly_erp_portal.mrerp import contracts

DOCTYPE = "ERP Stock Entry"  # = `express.stock_entry.DOCTYPE`（那边连着 Express 一侧，用到时才导入）
IN, OUT, TRANSFER, ADJUST = "in", "out", "transfer", "adjust"
FORM_PENDING = "ERR_MRERP_MODULE_UNAVAILABLE"   # 调拨 / 调整：网页表单直写做好之前
ITEM_UNMAPPED = "ERR_MRERP_ITEM_UNMAPPED"            # 行上的物料在 MR.ERP 里没有对应的物料（`mrerp.error.<码>` 四语）
WAREHOUSE_UNMAPPED = "ERR_MRERP_WAREHOUSE_UNMAPPED"  # 仓库在 MR.ERP 里没有对应的仓库
NEGATIVE_FIELD = "stock_negative"                # 主档记录里这份账套「允许负库存」：1 允许 / 2 不允许（`masters.observe`）
NOT_ALLOWED = "2"


SKIPPED_REFS = ("purchase", "count")  # = `express.stock_entry.SKIPPED_REFS`：采购自动入库、历史盘点生成的单不写（同 Express）


def company_of(name):
    """库存单没有公司栏：按单头仓库的公司（同 `express.stock_entry.company_of`）。"""
    warehouse = frappe.db.get_value(DOCTYPE, name, "warehouse") if name else None
    return frappe.db.get_value("ERP Warehouse", warehouse, "company") if warehouse else None


def date_of(name) -> str:
    return str(frappe.db.get_value(DOCTYPE, name, "posting_date") or "")[:10]


def eligible(doc) -> bool:
    """这张库存单该不该写进 MR.ERP（公司的账本是 MR.ERP 由调用方判）：已提交、不是采购自动入库 / 历史盘点（同 Express）、
    在公司接上 MR.ERP 之后建的。"""
    if doc.get("status") != "submitted" or (doc.get("ref_type") or "") in SKIPPED_REFS:
        return False
    company = doc.get("company") or company_of(doc.get("name"))
    since = frappe.db.get_value("ERP Company", company, "mrerp_linked_at") if company else None
    created = frappe.db.get_value(DOCTYPE, doc.get("name"), "creation")
    return bool(since and created and frappe.utils.get_datetime(created) >= frappe.utils.get_datetime(since))


def menu_kind(direction, purpose=None) -> str:
    from pearnly_core.mrerp import menus

    return menus.STOCK_IN if direction == IN else menus.stock_out_kind(purpose)


def kinds_of(direction) -> tuple:
    """这个方向会用到的菜单类：入库一个；出库按全部用途（`inventory.documents.PURPOSES`）。"""
    from pearnly_core.mrerp import menus
    from pearnly_erp_portal.inventory import documents

    if direction == IN:
        return (menus.STOCK_IN,)
    return tuple(dict.fromkeys(menus.stock_out_kind(purpose) for purpose in (*documents.PURPOSES.get(OUT, ()), "")))


def needed(row, book, kind) -> list:
    """此刻要人选的（同 `contracts.needed` 的形状）：这张单的菜单类与分店 / 部门 / งาน。"""
    from pearnly_core.mrerp import masters as master_rules, menus as menu_rules
    from pearnly_erp_portal.mrerp import choices

    connection, account = choices.place(row, book)
    menu_options, master_options = choices._options(connection)
    menu_view = menu_rules.view(row.mrerp_menus, menu_options, account, [kind])
    master_view = master_rules.view(row.mrerp_defaults, master_options, account)
    return contracts.needed(menu_view, master_view, "stock", kinds=(kind,))


def _base(item) -> tuple:
    """行 → （库存单位数量, 每库存单位单价或 None）：载荷照行上单位交、带换算率（`stock_entry.document`），MR.ERP 记标准单位。"""
    factor = Decimal(str(item.get("factor") or "1"))
    qty = Decimal(str(item.get("qty") or "0")) * factor
    cost = item.get("unit_cost")
    return qty, (Decimal(str(cost)) / factor if cost not in (None, "") else None)


def _plain(value) -> str:
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "") else text


def payload(name, body, menus, defaults) -> dict:
    """共用层 `push.stock.execute` 的载荷（见那边文件头）；物料 / 仓库码缺了回 ``{"hold": 原因}``。"""
    direction = frappe.db.get_value(DOCTYPE, name, "direction")
    purpose = frappe.db.get_value(DOCTYPE, name, "purpose")
    kind = IN if direction == IN else OUT
    if body.get("unmapped_warehouses"):
        return {"hold": WAREHOUSE_UNMAPPED}
    lines = []
    for item in body["lines"]:
        if not str(item.get("code") or "").strip():
            return {"hold": ITEM_UNMAPPED}
        if not str(item.get("loccod") or body.get("loccod") or "").strip():
            return {"hold": WAREHOUSE_UNMAPPED}
        qty, cost = _base(item)
        line = {"code": item["code"], "store": item.get("loccod") or body.get("loccod"), "qty": _plain(qty)}
        if kind == IN and cost is not None:
            line["price"] = _plain(cost.quantize(Decimal("0.0001")))
        lines.append(line)
    notes = [str(body.get("remark") or "")[:50], "", str(body.get("number") or "")[:50]]
    return {"payload": {"kind": kind, "number": str(body.get("number") or "")[:18], "date": body["date"],
                        "notes": notes, "lines": lines, "menu": (menus or {}).get(menu_kind(direction, purpose)) or "",
                        "defaults": dict(defaults or {})}}


def prepare(name, body, row, book) -> dict:
    """（`mrerp.push.prepare` 交过来：公司与账套已定、调拨 / 调整已先挡下）准备一张入库 / 出库单：``{"payload", "stock": True, ...}`` /
    ``{"hold", "needed"}``。``body``：`express.stock_entry.document` 的形状（`express.push.document` 建的）。"""
    from pearnly_erp_portal.mrerp import choices

    direction, purpose = frappe.db.get_value(DOCTYPE, name, ["direction", "purpose"])
    kind = menu_kind(direction, purpose)
    want = needed(row, book, kind)
    if want:
        return {"hold": contracts.CHOICE, "needed": want, "book": book}
    menus, defaults = choices.chosen(row, book)
    out = payload(name, body, menus, defaults)
    if "hold" in out:
        return {**out, "book": book}
    return {**out, "stock": True, "book": book, "company": row, "direction": "stock", "menu_kind": kind}


def negative_blocked(company, book=None) -> bool:
    """这份账套在 MR.ERP 里设成不允许负库存（主档整读时读到的，`masters.observe` 的 ``stock_negative``）。读不到按允许（同原生
    「没设置」时出库网页不拦）。"""
    from pearnly_core.mrerp import masters as master_rules
    from pearnly_erp_portal.mrerp import choices

    row = choices.company_row(company)
    if not row or not row.mrerp_enabled:
        return False
    connection, account = choices.place(row, book)
    observed = master_rules.observed_for(choices._options(connection)[1], account) or {}
    return str(observed.get(NEGATIVE_FIELD) or "") == NOT_ALLOWED


def _factor(product, unit) -> Decimal:
    """行上单位 → 库存单位的换算率（同流水落账，`inventory.stock.resolve_factor`）。"""
    from pearnly_erp_portal.inventory import stock as ledger

    return Decimal(str(ledger.resolve_factor(frappe.get_doc("ERP Product", product), unit)[1]))  # loop-query: 一张单的几行


def check_negative(doc, company) -> None:
    """（过账出库单前，`inventory.documents._post`）账本是 MR.ERP、这份账套不允许负库存：各物料出库数（库存单位）超过本公司
    各仓合计结存就拦下（MR.ERP 出库网页按全部仓合计核，`baprice.php`）。其余方向、Express 公司、允许负库存的不拦（同原生）。"""
    if doc.direction != OUT:
        return
    from pearnly_erp_portal.mrerp import push as mrerp_push

    if not mrerp_push.handles(company) or not negative_blocked(company):
        return
    from pearnly_erp_portal.inventory import warehouses

    need = {}
    for line in doc.lines:
        factor = _factor(line.product, line.unit)  # loop-query: 一张单的几行
        need[line.product] = need.get(line.product, Decimal("0")) + Decimal(str(line.qty or 0)) * Decimal(str(factor or 1))
    names = warehouses.names_of(company) or []
    rows = frappe.get_all("ERP Stock Balance", filters={"product": ["in", list(need)], "warehouse": ["in", names]},  # bounded: 一张单的物料 × 本公司的仓
                          fields=["product", "qty"], limit_page_length=0) if need and names else []
    have = {}
    for row in rows:
        have[row.product] = have.get(row.product, Decimal("0")) + Decimal(str(row.qty or 0))
    if any(qty > have.get(product, Decimal("0")) for product, qty in need.items()):
        frappe.throw(frappe._("Not enough stock on hand"))


__all__ = ["ADJUST", "DOCTYPE", "FORM_PENDING", "IN", "ITEM_UNMAPPED", "NEGATIVE_FIELD", "OUT", "TRANSFER",
           "WAREHOUSE_UNMAPPED", "check_negative", "company_of", "date_of", "eligible", "kinds_of", "menu_kind",
           "needed", "negative_blocked", "payload", "prepare"]
