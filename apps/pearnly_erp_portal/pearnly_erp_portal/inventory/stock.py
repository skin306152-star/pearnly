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
from pearnly_erp_portal.inventory import contracts, positions, warehouses

MOVEMENT = "ERP Stock Movement"
BALANCE = "ERP Stock Balance"
PRODUCT = "ERP Product"

PRODUCT_NOT_FOUND = "Product not found"
INVALID = "Invalid value"
DUPLICATES = "This request was already recorded"


def _require(role_capability: str = "stock.write") -> None:
    guard.require(role_capability)


def _product(product: str, *, allow_inactive=False):
    if not product or not frappe.db.exists(PRODUCT, product):
        frappe.throw(_(PRODUCT_NOT_FOUND), frappe.DoesNotExistError)
    doc = frappe.get_doc(PRODUCT, product)
    if not doc.is_active and not allow_inactive:
        frappe.throw(_(PRODUCT_NOT_FOUND))
    return doc


def resolve_factor(product_doc, unit_name=None) -> tuple[str, object]:
    """返回 (基准单位, 系数)。没给单位或就是基准单位 → 1；给了别的单位必须在该商品
    的单位表里，否则报错（旧线上同样口径，避免随手写个单位就把数量算错）。

    基准单位是商品的**库存单位**（`base_unit`）—— 旧线上 `services/inventory/ledger.py`
    折算成 `base_unit` 落账，`unit` 只是默认售卖单位。库存单位没填才退回售卖单位，
    免得同一批数量被解释成另一个单位。
    """
    base = (product_doc.base_unit or product_doc.unit or "").strip()
    if not unit_name or unit_name == base:
        return base, contracts.factor(1)
    for row in product_doc.get("units") or []:
        if row.unit_name == unit_name:
            return base, contracts.factor(row.factor_to_base)
    frappe.throw(_("This unit is not defined on the product"))


def _balance_doc(product: str, warehouse: str, location=""):
    name = contracts.position_name("BAL", product, warehouse, location)
    if frappe.db.exists(BALANCE, name):
        return frappe.get_doc(BALANCE, name)
    return frappe.get_doc({"doctype": BALANCE, "product": product, "warehouse": warehouse,
                           "location": location,
                           "qty_on_hand": 0, "value": 0, "unit_cost": 0, "cost_known": 0})


def current_balance(product: str, warehouse: str, location="") -> contracts.Balance:
    name = contracts.position_name("BAL", product, warehouse, location)
    if not frappe.db.exists(BALANCE, name):
        return contracts.ZERO_BALANCE
    row = frappe.db.get_value(BALANCE, name,
                              ["qty_on_hand", "value", "unit_cost", "cost_known"], as_dict=True)
    if not row["cost_known"]:
        return contracts.Balance(qty=contracts.d(row["qty_on_hand"]), value=None, unit=None)
    return contracts.Balance(qty=contracts.d(row["qty_on_hand"]), value=contracts.q2(row["value"]),
                             unit=contracts.q2(row["unit_cost"]))


def _save_balance(product: str, warehouse: str, balance: contracts.Balance, location="") -> None:
    doc = _balance_doc(product, warehouse, location)
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
                   allow_negative=True, internal=False, location="", allow_inactive=False) -> dict:
    """写一笔流水并推进结存。`client_uuid` 是整请求的幂等锚：命中即原样返回，不重复记账。

    `allow_negative` 默认与旧线上一致（允许超卖，告警在上层）；要拦负库存时显式传 False。

    `internal=True` 表示这是**另一个域**授权后触发的库存动作（目前是销售与退货）：
    收银员卖货要出货，但那不是在"管理库存"，所以不该要求库存岗位。调用方必须自己已经
    做过权限判断，这里不再重复要 `stock.write` —— 一个用户动作只过一道闸。
    """
    if not internal:
        _require()
    product_doc = _product(product, allow_inactive=internal and allow_inactive)
    location = positions.clean_location(location)
    warehouse = warehouses.resolve(warehouse)
    lock_products([product])

    if client_uuid:
        existing = frappe.db.get_value(MOVEMENT, {"client_uuid": client_uuid}, "name")
        if existing:
            row = frappe.db.get_value(MOVEMENT, existing,
                                      ["product", "warehouse", "location", "qty_base"], as_dict=True)
            return {"movement": existing, "deduped": True,
                    "qty_on_hand": float(current_balance(
                        row["product"], row["warehouse"], row["location"]).qty)}

    base_unit, factor = resolve_factor(product_doc, unit)
    try:
        base_qty = contracts.signed_base(movement_type, qty, factor)
        cost = None if unit_cost in (None, "") else contracts.d(unit_cost) / factor
    except ValueError as exc:
        frappe.throw(f"{_(INVALID)}: {exc}")

    warehouse_doc = frappe.db.exists("ERP Warehouse", warehouse)
    if not warehouse_doc:
        frappe.throw(_("Warehouse not found"), frappe.DoesNotExistError)

    balance = current_balance(product, warehouse, location)
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
        "location": location,
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
    _save_balance(product, warehouse, new_balance, location)
    return {"movement": doc.name, "deduped": False, "qty_on_hand": float(new_balance.qty),
            "unit_cost": None if new_balance.unit is None else float(new_balance.unit)}


def lock_products(products):
    # Stable lock order also protects first-ever stock in a new position.
    for product in sorted(set(products)):
        frappe.db.sql("select name from `tabERP Product` where name=%s for update", (product,))


def apply_lines(*, warehouse: str, movement_type: str, lines, posting_date=None,
                ref_type=None, ref_id=None, doc_no=None, reason=None, client_uuid=None,
                allow_negative=True, internal=False) -> dict:
    """整单动库存：逐行换算并落流水；首行带幂等锚，重复整单提交直接跳过。"""
    if not isinstance(lines, list) or not lines:
        frappe.throw(_("Provide at least one line item"))

    if not internal:
        _require()
    lines = [positions.prepare(line, warehouse) for line in lines]
    lock_products([line["product"] for line in lines])
    if client_uuid and frappe.db.exists(MOVEMENT, {"client_uuid": client_uuid}):
        return {"movements": [], "updated": [], "deduped": True}

    movements, updated = [], []
    for index, line in enumerate(lines):
        place = line
        result = apply_movement(
            product=line["product"], warehouse=place["warehouse"], location=place["location"],
            movement_type=movement_type,
            qty=line["qty"], unit=line.get("unit"), unit_cost=line.get("unit_cost"),
            posting_date=posting_date, ref_type=ref_type, ref_id=ref_id, doc_no=doc_no,
            reason=reason, client_uuid=client_uuid if index == 0 else None,
            allow_negative=allow_negative, internal=True,
        )
        movements.append(result["movement"])
        updated.append({"product": line["product"], "warehouse": place["warehouse"],
                        "location": place["location"], "qty_on_hand": result["qty_on_hand"]})
    return {"movements": movements, "updated": updated, "deduped": False}


def count_lines(*, warehouse: str, lines, posting_date=None, reason="stocktake") -> dict:
    """盘点：实盘数与系统数比，差异落一笔 count 流水（数量收敛到实盘值）。"""
    _require()
    adjustments = []
    lock_products([line["product"] for line in lines])
    for line in lines:
        product = line["product"]
        _product(product)
        place = positions.prepare(line, warehouse)
        system_qty = current_balance(product, place["warehouse"], place["location"]).qty
        try:
            counted = contracts.d(line["counted_qty"])
        except ValueError:
            frappe.throw(_(INVALID))
        delta = counted - system_qty
        movement = None
        if delta != 0:
            movement = apply_movement(
                product=product, **{k: place[k] for k in ("warehouse", "location")},
                movement_type=contracts.COUNT,
                qty=delta, posting_date=posting_date, ref_type="count", reason=reason,
            )["movement"]
        adjustments.append({"product": product, "warehouse": place["warehouse"],
                            "location": place["location"], "system_qty": float(system_qty),
                            "counted_qty": float(counted), "delta": float(delta),
                            "movement": movement})
    return {"adjustments": adjustments}


def reverse_movements(*, ref_type, ref_id, client_uuid, reason=None):
    """Internal only: caller authorizes reversal; original ledger fixes the storage position.

    反向流水**带上原单的单据号**：收发存与库存明细的"关联单据"列显示的是 `doc_no`
    （入库 IR / 出库 IS / 采购 PE / 销售 SI 那种系统号），没有号才退回显示内部单据名。
    原先这里不传 `doc_no`，于是作废产生的那一行显示成 `SD-00286` 这种内部名 ——
    用户 2026-09-23 报"单号在收发存显示得不对"就是这一处：**同一个单据的冲销行，
    号要和原单一样**，否则报表上那笔冲销认不出是谁。
    """
    rows = frappe.get_all(MOVEMENT, filters={"ref_type": ref_type, "ref_id": ref_id},
                          fields=["name", "product", "warehouse", "location", "qty_base", "doc_no"],
                          order_by="creation asc", limit_page_length=0)
    lock_products([row.product for row in rows])
    for row in rows:
        apply_movement(product=row.product, warehouse=row.warehouse, location=row.location,
                       movement_type=contracts.ADJUST, qty=-contracts.d(row.qty_base),
                       ref_type="void", ref_id=ref_id, doc_no=row.doc_no, reason=reason,
                       client_uuid=f"{client_uuid}:{row.name}", internal=True, allow_inactive=True)


def _overview_rows(*, warehouse=None, location=None, q=None, include_zero=False, only_low=False) -> list[dict]:
    """在库一览的全部命中行。成本按岗位遮蔽：没有成本权限的岗位拿到空值，不是 0。

    `only_low` 只留低库存与缺货，并且**隐含带上零库存** —— 缺货就是结存 0，
    否则"只看缺货"会一条都看不到。
    """
    _require("stock.read")
    from pearnly_erp_portal.access import rules

    filters: dict = {}
    if warehouse:
        filters["warehouse"] = warehouses.resolve(warehouse, create=False) or "__missing__"
    if location is not None:
        filters["location"] = positions.clean_location(location)
    rows = frappe.get_list(BALANCE, filters=filters,
                           fields=["name", "product", "warehouse", "location", "qty_on_hand", "unit_cost",
                                   "value", "cost_known"],
                           order_by="modified desc", limit_page_length=0)
    products = {p["name"]: p for p in frappe.get_list(
        PRODUCT, filters={"name": ["in", [r["product"] for r in rows]] or [""]},
        fields=["name", "product_name", "code", "barcode", "unit", "base_unit", "min_stock",
                "is_active"],
        limit_page_length=0)}
    names = {w["name"]: w["warehouse_name"] for w in frappe.get_list(
        "ERP Warehouse", filters={"name": ["in", [r["warehouse"] for r in rows] or [""]]},
        fields=["name", "warehouse_name"], limit_page_length=0)}
    visible = rules.cost_visible(guard.current_roles())
    out = []
    for row in rows:
        product = products.get(row["product"]) or {}
        if q and q.lower() not in (product.get("product_name") or "").lower():
            continue
        # "只看低库存/缺货"时零库存行必须在（缺货就是 0），所以那个开关隐含带上零库存。
        if not include_zero and not only_low and not row["qty_on_hand"]:
            continue
        min_stock = contracts.d(product.get("min_stock") or 0)
        qty_on_hand = contracts.d(row["qty_on_hand"])
        if qty_on_hand <= 0:
            status = "out"
        elif min_stock and qty_on_hand <= min_stock:
            status = "low"
        else:
            status = "ok"
        if only_low and status == "ok":
            continue
        out.append({
            # 一行的身份 = 结存行的名字（商品×仓库）。列表框架按 `name` 去重与认行，
            # 同名在这里等于"只剩一行"，所以它必须唯一。
            "name": row["name"],
            "product": row["product"], "product_name": product.get("product_name"),
            "code": product.get("code"), "barcode": product.get("barcode"),
            "warehouse": row["warehouse"],
            "location": row.get("location") or "",
            "warehouse_name": names.get(row["warehouse"]),
            # 数量是基准（库存）单位，所以这里显示库存单位 —— 与落账口径同一处。
            "unit": (product.get("base_unit") or product.get("unit") or "").strip() or None,
            "qty_on_hand": float(qty_on_hand), "min_stock": float(min_stock), "status": status,
            "cost_known": bool(row["cost_known"]),
            "unit_cost": float(row["unit_cost"]) if visible and row["cost_known"] else None,
            "value": float(row["value"]) if visible and row["cost_known"] else None,
        })
    return out


def stock_overview(*, warehouse=None, location=None, q=None, include_zero=False, only_low=False,
                   start=0, limit=200) -> list[dict]:
    """在库一览（分页）。`limit=0` 表示不分页，整批给。"""
    rows = _overview_rows(warehouse=warehouse, location=location, q=q, include_zero=include_zero,
                          only_low=only_low)
    start = int(start or 0)
    if not limit:
        return rows[start:]
    return rows[start:start + int(limit)]


def stock_count(*, warehouse=None, location=None, q=None, include_zero=False, only_low=False) -> int:
    """与列表同一套筛选的命中行数（页脚"第 N 项/共 M 项"用）。"""
    return len(_overview_rows(warehouse=warehouse, location=location, q=q, include_zero=include_zero,
                              only_low=only_low))


def stock_totals(*, warehouse=None, q=None) -> dict:
    """在库合计：有货的 SKU 数、库存金额、低库存数、缺货数。

    与旧线上库存页那四个数同一口径：缺货/低库存要能数出 0 库存的那些行，所以这里
    **始终带零库存**统计（列表上的"显示零库存"只管列表本身）。金额按岗位遮蔽：
    没有成本权限就回 None，不回 0 —— 0 会被当成"库存一文不值"。
    """
    from pearnly_erp_portal.access import rules

    rows = _overview_rows(warehouse=warehouse, q=q, include_zero=True)
    visible = rules.cost_visible(guard.current_roles())
    valued = [row for row in rows if row.get("cost_known")]
    return {
        "sku_count": len({row["product"] for row in rows if row["qty_on_hand"]}),
        "row_count": len(rows),
        "value": float(sum(row["value"] or 0 for row in valued)) if visible else None,
        "low_count": len([row for row in rows if row["status"] == "low"]),
        "out_count": len([row for row in rows if row["status"] == "out"]),
    }


def rebuild_balance(product: str, warehouse: str, location="") -> dict:
    """按流水重算某一行结存。用来验"结存确实能从流水推出来"，也用于修复。"""
    _require()
    warehouse = warehouses.resolve(warehouse)
    location = positions.clean_location(location)
    lock_products([product])
    movements = frappe.get_all(
        MOVEMENT, filters={"product": product, "warehouse": warehouse, "location": location},
        fields=["movement_type", "qty_base", "unit_cost", "cost_known", "posting_date",
                "posting_time", "name"],
        order_by="posting_date asc, posting_time asc, creation asc", limit_page_length=0)
    balance, _rows = contracts.roll(contracts.ZERO_BALANCE, movements)
    _save_balance(product, warehouse, balance, location)
    return {"product": product, "warehouse": warehouse, "location": location,
            "qty_on_hand": float(balance.qty),
            "unit_cost": None if balance.unit is None else float(balance.unit)}
