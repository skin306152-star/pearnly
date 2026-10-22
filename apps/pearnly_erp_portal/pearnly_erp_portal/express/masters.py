"""Express 主档与库存回读进 ERP（ERP 当前台、Express 当账本，2026-10-22）。

经 Cowork 网关请办公室电脑读这家公司现用账套的整套主档与各仓结存（按需请求 ``express_masters``），读好后在 ERP 里
自动建档 / 更新：客户、供应商、商品（库存单位、售卖 / 进货单位、售价）、仓库（挂在这家公司下）。主档以 Express 为准：
带 Express 编码的行由这里改，ERP 里没编码的同名 / 同税号 / 同编码的档第一次同步时认领过来，不另建一份。
库存对齐成「Express 结存 + 还没写进 Express 的 ERP 单的变动」，差额记一笔调整流水（来源 ``express_sync``）。

触发：每小时一次（:func:`sync_all`）、ERP 单写进 Express 后一次（`push._written`）、管理员在公司页手动一次（`api`）。
电脑离线 / 还在读：这次什么都不改，下次再来。
"""
from decimal import Decimal

import frappe
from frappe.utils import now_datetime, nowdate

from pearnly_erp_portal.express import contracts, gateway
from pearnly_erp_portal.inventory import contracts as stock_contracts
from pearnly_erp_portal.inventory import stock

COMPANY = "ERP Company"
CUSTOMER = "ERP Customer"
SUPPLIER = "ERP Supplier"
PRODUCT = "ERP Product"
WAREHOUSE = "ERP Warehouse"
SYNC_REASON = "express_sync"
TOL = Decimal("0.000001")


def linked_companies():
    """接了 Express 的公司：启用、税号是 13 位（事务所按税号认公司）。"""
    rows = frappe.get_all(COMPANY, filters={"is_active": 1}, fields=["name", "tax_id", "branch"])
    return [row for row in rows if contracts.tax13(row.tax_id)]


def sync_all():
    if not gateway.configured():
        return
    for row in linked_companies():
        frappe.enqueue("pearnly_erp_portal.express.masters.sync", queue="long",
                       job_id=f"erp-express-sync:{frappe.local.site}:{row.name}", deduplicate=True, company=row.name)


def sync(company):
    """同步一家公司。回 ``{"state": ready | reading | offline | outdated | <网关原因码>}``。"""
    if not gateway.configured():
        return {"state": "not_configured"}
    row = frappe.db.get_value(COMPANY, company, ["name", "tax_id", "branch"], as_dict=True)
    if not row or not contracts.tax13(row.tax_id):
        return {"state": "company_tax_missing"}
    try:
        result = gateway.call("masters", {"company": {"tax_id": row.tax_id, "branch": row.branch or ""},
                                          "date": nowdate()}, timeout=gateway.MASTERS_TIMEOUT)
    except gateway.Unavailable:
        return {"state": "unavailable"}
    if not result.get("ok"):
        return {"state": str(result.get("code") or "failed")}
    if result.get("state") != "ready" or not isinstance(result.get("data"), dict):
        return {"state": str(result.get("state") or "reading")}
    counts = apply(company, str(result.get("account_set") or ""), result["data"])
    return {"state": "ready", "account_set": result.get("account_set"), "label": result.get("label"), **counts}


def apply(company, book, data):
    """把读好的整套主档落进 ERP（幂等：再落一次不改任何东西）。回各类新建 / 更新的条数。"""
    counts = {}
    counts["customers"] = _parties(CUSTOMER, book, data.get("customers"), _customer_fields)
    counts["suppliers"] = _parties(SUPPLIER, book, data.get("suppliers"), _supplier_fields)
    counts["products"] = _products(book, data)
    warehouses = _warehouses(company, book, data)
    counts["warehouses"] = len(warehouses)
    counts["stock"] = _stock(company, book, warehouses, data.get("stock"))
    frappe.db.set_value(COMPANY, company, {"express_book": book[:140], "express_synced_at": now_datetime()},
                        update_modified=False)
    frappe.db.commit()
    return counts


# ── 客户 / 供应商 ─────────────────────────────────────────────────────────────


def _customer_fields(item):
    # 与 ERP 客户保存时的归一同一口径（个人不存税号与分店，总店不存分店号），免得每次同步都当成有改动
    company = bool(item["tax_id"])
    return {"customer_name": item["name"], "tax_id": item["tax_id"] if company else None,
            "branch_no": item["branch"] if company and not item["head"] else None,
            "branch_type": ("hq" if item["head"] else "branch") if company else None,
            "party_type": "company" if company else "person",
            **({"address": item["address"]} if item["address"] else {}),
            **({"contact_phone": item["phone"]} if item["phone"] else {})}


def _supplier_fields(item):
    return {"supplier_name": item["name"], "tax_id": item["tax_id"] or None,
            "branch_no": None if item["head"] else item["branch"],
            "branch_type": "head_office" if item["head"] else "branch",
            **({"address": item["address"]} if item["address"] else {}),
            **({"phone": item["phone"]} if item["phone"] else {})}


def _parties(doctype, book, rows, fields_of):
    name_field = "customer_name" if doctype == CUSTOMER else "supplier_name"
    existing = frappe.get_all(doctype, fields=["name", "express_code", "express_book", "tax_id", "branch_no", name_field],
                              limit_page_length=0)
    by_code = {(row.express_book, row.express_code): row.name for row in existing if row.express_code}
    free_tax = {}
    free_name = {}
    for row in existing:
        if row.express_code:
            continue
        if contracts.tax13(row.tax_id):
            free_tax.setdefault((row.tax_id, contracts.branch5(row.branch_no)), row.name)
        free_name.setdefault(str(row.get(name_field) or "").strip().casefold(), row.name)
    changed = 0
    for raw in contracts.rows(rows):
        item = contracts.party(raw)
        values = {**fields_of(item), "express_code": item["code"], "express_book": book[:140], "is_active": 1}
        name = (by_code.get((book, item["code"])) or by_code.get((None, item["code"])) or by_code.get(("", item["code"]))
                or (free_tax.pop((item["tax_id"], item["branch"]), None) if item["tax_id"] else None)
                or free_name.pop(item["name"].casefold(), None))
        changed += _upsert(doctype, name, values)
    return changed


def _upsert(doctype, name, values):
    """有就只改不一样的字段，没有就建。回 1 表示改了 / 建了。单行出错只记日志，不挡整次同步。"""
    try:
        if name:
            doc = frappe.get_doc(doctype, name)
            diff = {key: value for key, value in values.items() if key != "units" and _same(doc.get(key)) != _same(value)}
            units = values.get("units")
            if units is not None and _unit_rows(doc) != units:
                doc.set("units", [{"unit_name": row["unit_name"], "factor_to_base": float(row["factor"])} for row in units])
                diff["units"] = True
            if not diff:
                return 0
            doc.update({key: value for key, value in diff.items() if key != "units"})
            doc.save(ignore_permissions=True)
            return 1
        record = {key: value for key, value in values.items() if key != "units"}
        doc = frappe.get_doc({"doctype": doctype, **record})
        for row in values.get("units") or []:
            doc.append("units", {"unit_name": row["unit_name"], "factor_to_base": float(row["factor"])})
        doc.insert(ignore_permissions=True)
        return 1
    except frappe.ValidationError:
        frappe.clear_messages()
        frappe.log_error(title="ERP Express master sync row failed", message=frappe.get_traceback()[-1500:])
        return 0


def _same(value):
    """比对用：空值一律当空串，数字按数值。"""
    if value in (None, "") or value is False:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return str(value)


def _unit_rows(doc):
    return [{"unit_name": row.unit_name, "factor": str(contracts.d(row.factor_to_base).normalize())}
            for row in doc.get("units") or []]


# ── 商品 ─────────────────────────────────────────────────────────────────────


def _products(book, data):
    units = contracts.unit_names(data)
    existing = frappe.get_all(PRODUCT, fields=["name", "code", "barcode", "product_name", "express_code", "express_book"],
                              limit_page_length=0)
    by_code = {(row.express_book, row.express_code): row.name for row in existing if row.express_code}
    free_code = {row.code: row.name for row in existing if row.code and not row.express_code}
    free_name = {}
    for row in existing:
        if not row.express_code:
            free_name.setdefault(str(row.product_name or "").strip().casefold(), row.name)
    taken_codes = {row.code for row in existing if row.code}
    taken_barcodes = {row.barcode: row.name for row in existing if row.barcode}
    changed = 0
    for raw in contracts.rows(data.get("products")):
        item = contracts.product(raw, units)
        name = (by_code.get((book, item["code"])) or by_code.get((None, item["code"])) or by_code.get(("", item["code"]))
                or free_code.pop(item["code"], None)
                or free_name.pop(item["name"].casefold(), None))
        values = {"product_name": item["name"], "base_unit": item["base_unit"], "unit": item["unit"],
                  "units": [{"unit_name": row["unit_name"], "factor": str(contracts.d(row["factor"]).normalize())}
                            for row in item["units"]],
                  "express_code": item["code"], "express_book": book[:140], "express_stock": int(item["stock"]),
                  "is_active": 1}
        if not name and item["code"] not in taken_codes:
            values["code"] = item["code"]
            taken_codes.add(item["code"])
        if item["price"]:
            values.update(unit_price=float(item["price"]), price_set=1)
        if item["barcode"] and taken_barcodes.get(item["barcode"], name) == name:
            values["barcode"] = item["barcode"]
        changed += _upsert(PRODUCT, name, values)
    return changed


# ── 仓库与库存 ───────────────────────────────────────────────────────────────


def _warehouses(company, book, data):
    """这家公司的仓库按 Express 仓库码建 / 认领：回 ``{仓库码: ERP 仓库}``。第一次同步时，这家公司唯一一个没编码的仓库
    认作 Express 的主仓（ERP 开户自带一个仓库）。"""
    existing = frappe.get_all(WAREHOUSE, filters={"company": company},
                              fields=["name", "warehouse_name", "express_code"], limit_page_length=0)
    by_code = {row.express_code: row.name for row in existing if row.express_code}
    free = [row for row in existing if not row.express_code]
    free_name = {str(row.warehouse_name or "").strip().casefold(): row.name for row in free}
    main = str((data.get("settings") or {}).get("mainloc") or "").strip()
    found = {}
    for raw in contracts.rows(data.get("warehouses")):
        code, label = str(raw["code"]).strip()[:4], str(raw.get("name") or raw["code"]).strip()[:140]
        name = by_code.get(code) or free_name.pop(label.casefold(), None)
        if not name and code == main and len(free) == 1 and free[0].name in free_name.values():
            name = free[0].name
            free_name = {key: value for key, value in free_name.items() if value != name}
        values = {"express_code": code, "express_book": book[:140], "company": company, "is_active": 1}
        if not name:
            values["warehouse_name"] = label
        _upsert(WAREHOUSE, name, values)
        found[code] = name or frappe.db.get_value(WAREHOUSE, {"company": company, "express_code": code}, "name")
    return {code: name for code, name in found.items() if name}


def _pending(company, products, warehouses):
    """还没写进 Express 的 ERP 单已经过了的库存变动：``{(存货码, 仓库码): 带符号基本单位数量}``。"""
    refs = []
    for doctype, ref_type in (("ERP Sales Document", "ERP Sales Document"), ("Purchase Document", "purchase")):
        names = frappe.get_all(doctype, filters={"company": company, "status": "submitted",
                                                 "express_state": ["in", contracts.OPEN_STATES]},
                               pluck="name", limit_page_length=0)
        refs.extend((ref_type, name) for name in names)
    if not refs:
        return {}
    code_of = {name: code for code, name in products.items()}
    loc_of = {name: code for code, name in warehouses.items()}
    pending = {}
    for ref_type in {ref for ref, _ in refs}:
        ids = [name for ref, name in refs if ref == ref_type]
        for row in frappe.get_all(stock.MOVEMENT, filters={"ref_type": ref_type, "ref_id": ["in", ids]},
                                  fields=["product", "warehouse", "qty_base"], limit_page_length=0):
            key = (code_of.get(row.product), loc_of.get(row.warehouse))
            if key[0] and key[1]:
                pending[key] = pending.get(key, Decimal(0)) + contracts.d(row.qty_base)
    return pending


def _stock(company, book, warehouses, stock_rows):
    """ERP 结存对齐 Express：每个（Express 存货 × Express 仓库）差多少记一笔调整流水。回调整的笔数。
    只认这份账套同步过来的商品（编码在账套内唯一）。"""
    if not warehouses:
        return 0
    products = {row.express_code: row.name for row in frappe.get_all(
        PRODUCT, filters={"express_book": book[:140], "express_code": ["is", "set"], "express_stock": 1},
        fields=["name", "express_code"], order_by="creation asc", limit_page_length=0)}
    targets = contracts.stock_targets(stock_rows, _pending(company, products, warehouses))
    current = {}
    for row in frappe.get_all(stock.BALANCE, filters={"warehouse": ["in", list(warehouses.values())]},
                              fields=["product", "warehouse", "qty_on_hand"], limit_page_length=0):
        key = (row.product, row.warehouse)
        current[key] = current.get(key, Decimal(0)) + contracts.d(row.qty_on_hand)
    wanted = {}
    for (code, loccod), qty in targets.items():
        if code in products and loccod in warehouses:
            wanted[(products[code], warehouses[loccod])] = qty
    linked = set(products.values())
    for key in current:
        if key[0] in linked and key not in wanted:
            wanted[key] = Decimal(0)  # Express 里这个仓已经没有了
    stamp = now_datetime().strftime("%Y%m%d%H%M%S")
    moved = 0
    for (product, warehouse), qty in wanted.items():
        delta = qty - current.get((product, warehouse), Decimal(0))
        if abs(delta) <= TOL:
            continue
        try:
            stock.apply_movement(product=product, warehouse=warehouse, movement_type=stock_contracts.ADJUST,
                                 qty=float(delta), posting_date=nowdate(), ref_type=SYNC_REASON, reason=SYNC_REASON,
                                 client_uuid=f"{SYNC_REASON}:{stamp}:{product}:{warehouse}"[:140],
                                 internal=True, allow_inactive=True, company=company)
            moved += 1
        except frappe.ValidationError:
            frappe.clear_messages()
            frappe.log_error(title="ERP Express stock sync failed", message=frappe.get_traceback()[-1500:])
    return moved
