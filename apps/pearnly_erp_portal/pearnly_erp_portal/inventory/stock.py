"""库存流水与结存：本域的核心写入路径。

两条不变量：
1. **流水不可变** —— 只追加，不修改、不删除；冲销靠反向流水。
2. **结存由流水推出** —— `ERP Stock Balance` 是物化结果，任何时刻都能从流水重建。

数量以基准单位存（`qty_base` 带符号），入库为正、出库为负；成本未确立时金额与单价
留空而不是写 0。
"""

import frappe
from frappe import _
from frappe.utils import now, nowdate

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.inventory import contracts

MOVEMENT = "ERP Stock Movement"
BALANCE = "ERP Stock Balance"
PRODUCT = "ERP Product"

PRODUCT_NOT_FOUND = "Product not found"
INVALID = "Invalid value"
DUPLICATES = "This request was already recorded"


def _require(role_capability: str = "stock.write") -> None:
    guard.require(role_capability)


def _product(product: str):
    if not product or not frappe.db.exists(PRODUCT, product):
        frappe.throw(_(PRODUCT_NOT_FOUND), frappe.DoesNotExistError)
    doc = frappe.get_doc(PRODUCT, product)
    if not doc.is_active:
        frappe.throw(_(PRODUCT_NOT_FOUND))
    return doc


def resolve_factor(product_doc, unit_name=None) -> tuple[str, object]:
    """返回 (基准单位, 系数)。没给单位或就是基准单位 → 1；给了别的单位必须在该商品
    的单位表里，否则报错（旧线上同样口径，避免随手写个单位就把数量算错）。"""
    base = (product_doc.unit or "").strip()
    if not unit_name or unit_name == base:
        return base, contracts.factor(1)
    for row in product_doc.get("units") or []:
        if row.unit_name == unit_name:
            return base, contracts.factor(row.factor_to_base)
    frappe.throw(_("This unit is not defined on the product"))


def _balance_doc(product: str, warehouse: str):
    name = f"BAL-{product}-{warehouse}"
    if frappe.db.exists(BALANCE, name):
        return frappe.get_doc(BALANCE, name)
    return frappe.get_doc({"doctype": BALANCE, "product": product, "warehouse": warehouse,
                           "qty_on_hand": 0, "value": 0, "unit_cost": 0, "cost_known": 0})


def current_balance(product: str, warehouse: str) -> contracts.Balance:
    name = f"BAL-{product}-{warehouse}"
    if not frappe.db.exists(BALANCE, name):
        return contracts.ZERO_BALANCE
    row = frappe.db.get_value(BALANCE, name,
                              ["qty_on_hand", "value", "unit_cost", "cost_known"], as_dict=True)
    if not row["cost_known"]:
        return contracts.Balance(qty=contracts.d(row["qty_on_hand"]), value=None, unit=None)
    return contracts.Balance(qty=contracts.d(row["qty_on_hand"]), value=contracts.q2(row["value"]),
                             unit=contracts.q2(row["unit_cost"]))


def _save_balance(product: str, warehouse: str, balance: contracts.Balance) -> None:
    doc = _balance_doc(product, warehouse)
    doc.qty_on_hand = balance.qty
    doc.value = balance.value if balance.value is not None else 0
    doc.unit_cost = balance.unit if balance.unit is not None else 0
    doc.cost_known = 1 if balance.unit is not None else 0
    doc.last_movement_at = now()
    if doc.is_new():
        doc.insert(ignore_permissions=True)
    else:
        doc.save(ignore_permissions=True)


def apply_movement(*, product: str, warehouse: str, movement_type: str, qty, unit=None,
                   unit_cost=None, posting_date=None, posting_time=None, ref_type=None,
                   ref_id=None, doc_no=None, reason=None, client_uuid=None,
                   allow_negative=True) -> dict:
    """写一笔流水并推进结存。`client_uuid` 是整请求的幂等锚：命中即原样返回，不重复记账。

    `allow_negative` 默认与旧线上一致（允许超卖，告警在上层）；要拦负库存时显式传 False。
    """
    _require()
    product_doc = _product(product)

    if client_uuid:
        existing = frappe.db.get_value(MOVEMENT, {"client_uuid": client_uuid}, "name")
        if existing:
            row = frappe.db.get_value(MOVEMENT, existing,
                                      ["product", "warehouse", "qty_base"], as_dict=True)
            return {"movement": existing, "deduped": True,
                    "qty_on_hand": float(current_balance(row["product"], row["warehouse"]).qty)}

    base_unit, factor = resolve_factor(product_doc, unit)
    try:
        base_qty = contracts.signed_base(movement_type, qty, factor)
        cost = None if unit_cost in (None, "") else contracts.d(unit_cost)
    except ValueError as exc:
        frappe.throw(f"{_(INVALID)}: {exc}")

    warehouse_doc = frappe.db.exists("ERP Warehouse", warehouse)
    if not warehouse_doc:
        frappe.throw(_("Warehouse not found"), frappe.DoesNotExistError)

    balance = current_balance(product, warehouse)
    if not allow_negative and balance.qty + base_qty < 0:
        frappe.throw(_("Not enough stock on hand"))

    # 成本只在"入方向"参与计价；出库按当前结存单价，给了成本也不用。
    new_balance, detail = contracts.apply_move(
        balance, base_qty, unit_cost=cost if base_qty > 0 else None)

    doc = frappe.get_doc({
        "doctype": MOVEMENT,
        "posting_date": posting_date or nowdate(),
        "posting_time": posting_time,
        "movement_type": movement_type,
        "product": product,
        "warehouse": warehouse,
        "qty": qty,
        "unit": unit or base_unit,
        "factor": factor,
        "qty_base": base_qty,
        "cost_known": 1 if detail["unit_price"] is not None else 0,
        "unit_cost": detail["unit_price"] if detail["unit_price"] is not None else 0,
        "value_delta": detail["amount"] if detail["amount"] is not None else 0,
        "ref_type": ref_type,
        "ref_id": ref_id,
        "doc_no": doc_no,
        "reason": reason,
        "client_uuid": client_uuid,
        "created_by": frappe.session.user,
    })
    doc.insert(ignore_permissions=True)
    _save_balance(product, warehouse, new_balance)
    return {"movement": doc.name, "deduped": False, "qty_on_hand": float(new_balance.qty),
            "unit_cost": None if new_balance.unit is None else float(new_balance.unit)}


def apply_lines(*, warehouse: str, movement_type: str, lines, posting_date=None,
                ref_type=None, ref_id=None, doc_no=None, reason=None, client_uuid=None,
                allow_negative=True) -> dict:
    """整单动库存：逐行换算并落流水；首行带幂等锚，重复整单提交直接跳过。"""
    if not isinstance(lines, list) or not lines:
        frappe.throw(_("Provide at least one line item"))

    if client_uuid and frappe.db.exists(MOVEMENT, {"client_uuid": client_uuid}):
        return {"movements": [], "updated": [], "deduped": True}

    movements, updated = [], []
    for index, line in enumerate(lines):
        result = apply_movement(
            product=line["product"], warehouse=warehouse, movement_type=movement_type,
            qty=line["qty"], unit=line.get("unit"), unit_cost=line.get("unit_cost"),
            posting_date=posting_date, ref_type=ref_type, ref_id=ref_id, doc_no=doc_no,
            reason=reason, client_uuid=client_uuid if index == 0 else None,
            allow_negative=allow_negative,
        )
        movements.append(result["movement"])
        updated.append({"product": line["product"], "qty_on_hand": result["qty_on_hand"]})
    return {"movements": movements, "updated": updated, "deduped": False}


def count_lines(*, warehouse: str, lines, posting_date=None, reason="stocktake") -> dict:
    """盘点：实盘数与系统数比，差异落一笔 count 流水（数量收敛到实盘值）。"""
    _require()
    adjustments = []
    for line in lines:
        product = line["product"]
        _product(product)
        system_qty = current_balance(product, warehouse).qty
        try:
            counted = contracts.d(line["counted_qty"])
        except ValueError:
            frappe.throw(_(INVALID))
        delta = counted - system_qty
        movement = None
        if delta != 0:
            movement = apply_movement(
                product=product, warehouse=warehouse, movement_type=contracts.COUNT,
                qty=delta, posting_date=posting_date, ref_type="count", reason=reason,
            )["movement"]
        adjustments.append({"product": product, "system_qty": float(system_qty),
                            "counted_qty": float(counted), "delta": float(delta),
                            "movement": movement})
    return {"adjustments": adjustments}


def stock_overview(*, warehouse=None, q=None, include_zero=False, limit=200) -> list[dict]:
    """在库一览。成本按岗位遮蔽：没有成本权限的岗位拿到空值，不是 0。"""
    _require("stock.read")
    from pearnly_erp_portal.access import rules

    filters: dict = {}
    if warehouse:
        filters["warehouse"] = warehouse
    rows = frappe.get_list(BALANCE, filters=filters,
                           fields=["product", "warehouse", "qty_on_hand", "unit_cost",
                                   "value", "cost_known"],
                           order_by="modified desc", limit_page_length=int(limit))
    products = {p["name"]: p for p in frappe.get_list(
        PRODUCT, filters={"name": ["in", [r["product"] for r in rows]] or [""]},
        fields=["name", "product_name", "unit", "min_stock", "is_active"],
        limit_page_length=0)}
    visible = rules.cost_visible(guard.current_roles())
    out = []
    for row in rows:
        product = products.get(row["product"]) or {}
        if q and q.lower() not in (product.get("product_name") or "").lower():
            continue
        if not include_zero and not row["qty_on_hand"]:
            continue
        min_stock = contracts.d(product.get("min_stock") or 0)
        qty_on_hand = contracts.d(row["qty_on_hand"])
        if qty_on_hand <= 0:
            status = "out"
        elif min_stock and qty_on_hand <= min_stock:
            status = "low"
        else:
            status = "ok"
        out.append({
            "product": row["product"], "product_name": product.get("product_name"),
            "warehouse": row["warehouse"], "unit": product.get("unit"),
            "qty_on_hand": float(qty_on_hand), "min_stock": float(min_stock), "status": status,
            "cost_known": bool(row["cost_known"]),
            "unit_cost": float(row["unit_cost"]) if visible and row["cost_known"] else None,
            "value": float(row["value"]) if visible and row["cost_known"] else None,
        })
    return out


def rebuild_balance(product: str, warehouse: str) -> dict:
    """按流水重算某一行结存。用来验"结存确实能从流水推出来"，也用于修复。"""
    _require()
    movements = frappe.get_all(
        MOVEMENT, filters={"product": product, "warehouse": warehouse},
        fields=["movement_type", "qty_base", "unit_cost", "cost_known", "posting_date",
                "posting_time", "name"],
        order_by="posting_date asc, posting_time asc, creation asc", limit_page_length=0)
    balance, _rows = contracts.roll(contracts.ZERO_BALANCE, movements)
    _save_balance(product, warehouse, balance)
    return {"product": product, "warehouse": warehouse, "qty_on_hand": float(balance.qty),
            "unit_cost": None if balance.unit is None else float(balance.unit)}
