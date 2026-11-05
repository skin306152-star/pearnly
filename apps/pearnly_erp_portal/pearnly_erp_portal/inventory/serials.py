"""按台管理（车辆按台补充设计 `docs/work/2026-10-26-vehicle-units-设计.md`，主会话 10-26 审定）。

商品开了「按台管理」（``ERP Product.track_serial``）以后，每一台是一条 ``ERP Serial No``（车 = 车架号 / 发动机号 / 颜色，
其他商品 = S/N / 第二个号）。库存数量照旧按商品走流水，台只跟着单据改状态，挂在库存层同一个汇合点上：

- 各单据把这一行登记 / 选中的台随行交给 ``stock.apply_lines``（行上的 ``serials``）：进（进货、入库「其他」）登记新台，进（收银退货、
  入库「退货」）收回已售的台，出（销售、收银、出库）把在库的台改成已售 / 已出库；台数必须等于这一行的基本单位数量，不等不让过账。
- 作废走 ``stock.reverse_movements`` → :func:`reverse`：按「按台流水」（``ERP Serial Movement``）逐条反向——这张单进的台还在库就作废，
  有一台已经卖出 / 出库就整单拒（先作废卖出的单）；出的台回到在库、原仓原库位；收回的台回到原来的已售 / 已出库。
- 盘点生成的出入库单（``ref_type = count``）不登记、不改台（没盘到的台不自动改状态，主会话 10-26）。
- 已有库存的商品开按台要先做「按台期初登记」：每个仓登记的台数等于账面数，同一个事务里打开；打开后只有全部台已售或作废才能关。
- 认台（扫码 / 手输）：主号全等 → 副号全等 → 主号末 8 位，只认本公司的、没作废的、唯一的那一台。
不开按台的商品不经这里，行为不变。
"""
import frappe
from frappe import _
from frappe.utils import nowdate

DOCTYPE = "ERP Serial No"
MOVEMENT = "ERP Serial Movement"
PRODUCT = "ERP Product"
IN_STOCK, SOLD, ISSUED, VOID = "in_stock", "sold", "issued", "void"
# 出库时改成「已售」的单据（其余出库改「已出库」）：收银小票、销售单
SALE_REFS = ("sale", "ERP Sales Document")
COUNT_REF = "count"
OPENING_REF = "serial_opening"
IN_TYPES = ("opening", "purchase_in", "stock_in")
OUT_TYPES = ("sale_out", "stock_out")
MAX_NO, MAX_COLOR, TAIL, VIN_LENGTH = 40, 60, 8, 17
KINDS = ("vehicle", "device")

QTY_MISMATCH = "The number of units does not match the quantity"
WHOLE_UNITS = "Quantities of products tracked by unit must be whole numbers in the base unit"
DUPLICATE = "This unit number is already registered"
NOT_FOUND = "Unit not found"
NOT_IN_STOCK = "This unit is not in stock in this warehouse"
TRANSFER_OUT, TRANSFER_IN = "transfer_out", "transfer_in"  # 调拨换仓的两条按台流水（多仓闭环 P2）
LEFT_ALREADY = "Units already sold or issued cannot be voided with this document"
OPENING_MISMATCH = "The units must match the stock of each warehouse"
# 打开按台时别家公司（这个人看不到的）还有没登记的账面：不露那家与仓的名称、数量，合成这一句（主会话 10-26，G17）
OTHER_COMPANIES = "Other companies have unregistered stock"
CANNOT_TURN_OFF = "Tracking by unit can only be turned off when every unit is sold or void"


def clean(value, limit) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        frappe.throw(_("Invalid value"))
    return text


def key(company, serial_no) -> str:
    """同一家公司同一个主号只有一条在用的（作废的不占号；不跨公司校验，不泄露别家数据）。"""
    return f"{company}|{serial_no.upper()}"


def tail(serial_no) -> str:
    return serial_no.upper()[-TAIL:]


def tracked(products, company) -> set:
    """这几件里按台管理的：只在按台的公司（汽车 / 手机电器类型，`companies.unit_tracking`）才算；普通公司一律空。"""
    from pearnly_erp_portal.masters import company as companies

    names = sorted({name for name in products if name})
    if not names or not companies.unit_tracking(company):
        return set()
    return set(frappe.get_all(PRODUCT, filters={"name": ["in", names], "track_serial": 1}, pluck="name",  # bounded: 这张单的商品
                              limit_page_length=len(names)))


def _base_qty(line):
    """这一行的基本单位数量，必须是整数（一台一台算）。"""
    from pearnly_erp_portal.inventory import stock

    product = frappe.get_doc(PRODUCT, line["product"])
    _base, factor = stock.resolve_factor(product, line.get("unit"))
    qty = frappe.utils.flt(line["qty"]) * frappe.utils.flt(factor)
    if qty <= 0 or abs(qty - round(qty)) > 1e-9:
        frappe.throw(_(WHOLE_UNITS))
    return int(round(qty))


def _event(unit, action, ref_type, ref_id, posting_date, prev_status="", warehouse=None, location=None):
    frappe.get_doc({"doctype": MOVEMENT, "serial": unit.name, "product": unit.product, "company": unit.company,
                    "warehouse": warehouse if warehouse is not None else unit.warehouse,
                    "location": location if location is not None else (unit.location or ""), "action": action,
                    "prev_status": prev_status, "ref_type": ref_type, "ref_id": ref_id,
                    "posting_date": posting_date or nowdate(), "reversed": 0}).insert(ignore_permissions=True)


def _unit(name):
    if not name or not frappe.db.exists(DOCTYPE, name):
        frappe.throw(_(NOT_FOUND))
    return frappe.get_doc(DOCTYPE, name, for_update=True)


def register(*, product, company, warehouse, location, entry, ref_type, ref_id, posting_date, action="in"):
    """登记一台新车（进货、入库「其他」、按台期初）。"""
    serial_no = clean(entry.get("serial_no"), MAX_NO)
    if not serial_no:
        frappe.throw(_(QTY_MISMATCH))
    if frappe.db.exists(DOCTYPE, {"serial_key": key(company, serial_no)}):
        frappe.throw(_(DUPLICATE) + f": {serial_no}")
    from pearnly_erp_portal.products import codes

    secondary_no = clean(entry.get("secondary_no"), MAX_NO)
    for number in (serial_no, secondary_no):  # 车架号、发动机号与商品的码同一个命名空间（主会话 10-26）
        if number:
            codes.assert_free(number, owner=company)
    unit = frappe.get_doc({"doctype": DOCTYPE, "product": product, "company": company, "serial_no": serial_no,
                           "serial_key": key(company, serial_no), "serial_tail": tail(serial_no),
                           "secondary_no": secondary_no or None,
                           "color": clean(entry.get("color"), MAX_COLOR) or None, "status": IN_STOCK,
                           "warehouse": warehouse, "location": location or "", "in_ref_type": ref_type, "in_ref": ref_id,
                           "in_date": posting_date or nowdate()})
    unit.insert(ignore_permissions=True)
    _event(unit, action, ref_type, ref_id, posting_date)
    return unit


def apply(*, lines, movement_type, ref_type, ref_id, company, posting_date=None):
    """``stock.apply_lines`` 在落流水之前调（同一个事务）：开了按台的商品逐行核台数、登记 / 收回 / 出台。``lines`` 已经过
    ``positions.prepare``（有仓、库位）；每行的 ``serials`` 是 ``[{serial} | {serial_no, secondary_no, color}]``。"""
    if ref_type == COUNT_REF:
        return
    products = tracked((line.get("product") for line in lines), company)
    if not products:
        return
    if movement_type not in IN_TYPES + OUT_TYPES:
        frappe.throw(_(QTY_MISMATCH))
    seen = set()
    for line in lines:
        if line.get("product") not in products:
            continue
        entries = [entry for entry in (line.get("serials") or []) if isinstance(entry, dict)]
        if len(entries) != _base_qty(line):
            frappe.throw(_(QTY_MISMATCH))
        for entry in entries:
            if movement_type in IN_TYPES and not entry.get("serial"):
                number = clean(entry.get("serial_no"), MAX_NO).upper()
                if not number or number in seen:
                    frappe.throw(_(DUPLICATE) + (f": {number}" if number else ""))
                seen.add(number)
                register(product=line["product"], company=company, warehouse=line["warehouse"],
                         location=line.get("location"), entry=entry, ref_type=ref_type, ref_id=ref_id,
                         posting_date=posting_date)
                continue
            unit = _unit(entry.get("serial"))
            if unit.name in seen or unit.company != company or unit.product != line["product"]:
                frappe.throw(_(NOT_FOUND))
            seen.add(unit.name)
            if movement_type in IN_TYPES:  # 退回：收回已售 / 已出库的台
                if unit.status not in (SOLD, ISSUED):
                    frappe.throw(_(NOT_FOUND))
                prev = unit.status
                unit.update({"status": IN_STOCK, "warehouse": line["warehouse"], "location": line.get("location") or ""})
                unit.save(ignore_permissions=True)
                _event(unit, "return", ref_type, ref_id, posting_date, prev_status=prev)
            else:
                # 出台核仓与库位（G17）：台记的位置要等于这一行出货的位置，否则结存与台各在一处
                if unit.status != IN_STOCK or not _at(unit, line["warehouse"], line.get("location")):
                    frappe.throw(_(NOT_IN_STOCK) + f": {unit.serial_no}")
                unit.update({"status": SOLD if ref_type in SALE_REFS else ISSUED, "out_ref_type": ref_type,
                             "out_ref": ref_id, "out_date": posting_date or nowdate()})
                unit.save(ignore_permissions=True)
                _event(unit, "out", ref_type, ref_id, posting_date, prev_status=IN_STOCK)


def _at(unit, warehouse, location) -> bool:
    return unit.warehouse == warehouse and (unit.location or "") == (location or "")


def transfer(*, lines, to_warehouse, ref_type, ref_id, company, posting_date=None):
    """``stock.apply_transfer`` 在落流水之前调（同一个事务）：开了按台的商品逐行核台数，选中的台必须在这一行的调出仓 / 库位在库，
    换到调入仓 / 库位（状态仍是在库）；记两条按台流水（调出位置、调入位置），作废时据此退回。"""
    products = tracked((line.get("product") for line in lines), company)
    if not products:
        return
    seen = set()
    for line in lines:
        if line.get("product") not in products:
            continue
        entries = [entry for entry in (line.get("serials") or []) if isinstance(entry, dict)]
        if len(entries) != _base_qty(line):
            frappe.throw(_(QTY_MISMATCH))
        for entry in entries:
            unit = _unit(entry.get("serial"))
            if unit.name in seen or unit.company != company or unit.product != line["product"]:
                frappe.throw(_(NOT_FOUND))
            seen.add(unit.name)
            if unit.status != IN_STOCK or not _at(unit, line["warehouse"], line.get("location")):
                frappe.throw(_(NOT_IN_STOCK) + f": {unit.serial_no}")
            _event(unit, TRANSFER_OUT, ref_type, ref_id, posting_date, prev_status=IN_STOCK)
            unit.update({"warehouse": to_warehouse, "location": line.get("to_location") or ""})
            unit.save(ignore_permissions=True)
            _event(unit, TRANSFER_IN, ref_type, ref_id, posting_date, prev_status=IN_STOCK)


def reverse(ref_type, ref_id):
    """``stock.reverse_movements`` 先调：这张单动过的台逐条反向（后动的先回）。"""
    events = frappe.get_all(MOVEMENT, filters={"ref_type": ref_type, "ref_id": ref_id, "reversed": 0},  # bounded: 一张单的台
                            fields=["name", "serial", "action", "prev_status", "warehouse", "location"],
                            order_by="creation desc", limit_page_length=0)
    # 调拨：先核每台还在调入位置（`transfer_in`），再退回调出位置（`transfer_out`）——同一时刻写的两条不靠时间先后
    events.sort(key=lambda event: event.action != TRANSFER_IN)
    for event in events:
        unit = _unit(event.serial)
        if event.action in ("in", "opening"):
            if unit.status != IN_STOCK:
                frappe.throw(_(LEFT_ALREADY) + f": {unit.serial_no}")
            unit.update({"status": VOID, "serial_key": None})
        elif event.action == "out":
            # 这台车现在必须还是这张单出的（验收 10-26：出库后又经入库「退货」收回，再作废出库单会多出一台）
            if unit.status not in (SOLD, ISSUED) or unit.out_ref != ref_id:
                frappe.throw(_(LEFT_ALREADY) + f": {unit.serial_no}")
            unit.update({"status": IN_STOCK, "warehouse": event.warehouse, "location": event.location or "",
                         "out_ref_type": None, "out_ref": None, "out_date": None})
        elif event.action == TRANSFER_IN:
            # 台已从调入仓卖出 / 出库 / 再调走：先作废后面那张
            if unit.status != IN_STOCK or not _at(unit, event.warehouse, event.location):
                frappe.throw(_(LEFT_ALREADY) + f": {unit.serial_no}")
        elif event.action == TRANSFER_OUT:
            unit.update({"warehouse": event.warehouse, "location": event.location or ""})
        elif event.action == "return":
            if unit.status != IN_STOCK:
                frappe.throw(_(LEFT_ALREADY) + f": {unit.serial_no}")
            unit.status = event.prev_status or SOLD
        unit.save(ignore_permissions=True)
        frappe.db.set_value(MOVEMENT, event.name, "reversed", 1, update_modified=False)


# ── 认台 ─────────────────────────────────────────────────────────────────────


def lookup(code, company, *, product=None, statuses=None, warehouse=None, location=None) -> list:
    """扫到 / 手输的号 → 本公司没作废的台：主号全等 → 副号全等 → 主号末 8 位（各一层认到就停）。回 ``[台]``（认出几台交给人选）。
    GS1 码（Data Matrix 等，``01`` GTIN + ``21`` 序列号）按里面的序列号认（10-30）。"""
    from pearnly_erp_portal.products import gs1

    text = clean(gs1.lookup(code)["serial"] or code, MAX_NO).upper()
    if not text or not company:
        return []
    base = {"company": company, "status": ["in", list(statuses or (IN_STOCK, SOLD, ISSUED))]}
    if product:
        base["product"] = product
    if warehouse:
        base["warehouse"] = warehouse
        if location is not None:
            base["location"] = location or ""  # 选台按这一行的仓 + 库位（G17）
    fields = ["name", "product", "serial_no", "secondary_no", "color", "status", "warehouse", "location", "out_ref"]
    # 17 位完整车架号只认完全一样的；末 8 位只在输入的就是 8 位时认；发动机号照旧全等（主会话 10-26）
    full = len(text) == VIN_LENGTH
    for extra in ({"serial_key": key(company, text)}, None if full else {"secondary_no": text},
                  {"serial_tail": text} if len(text) == TAIL else None):
        if not extra:
            continue
        rows = frappe.get_all(DOCTYPE, filters={**base, **extra}, fields=fields, limit_page_length=20)
        if rows:
            return rows
    return []


# ── 开 / 关按台 ──────────────────────────────────────────────────────────────


def stock_by_warehouse(product, company=None) -> dict:
    """这件商品各仓账面数（基本单位）：``{仓: 数量}``，只列不为 0 的。"""
    filters = {"product": product}
    if company:
        mine = frappe.get_all("ERP Warehouse", filters={"company": company}, pluck="name", limit_page_length=0)  # bounded: 一家公司的仓
        filters["warehouse"] = ["in", mine or [""]]
    out = {}
    for row in frappe.get_all("ERP Stock Balance", filters=filters, fields=["warehouse", "qty_on_hand"],  # bounded: 一件商品
                              limit_page_length=0):
        out[row.warehouse] = out.get(row.warehouse, 0) + frappe.utils.flt(row.qty_on_hand)
    return {warehouse: qty for warehouse, qty in out.items() if abs(qty) > 1e-9}


def turn_on(product, units=None, kind=None):
    """开按台。没有库存的直接开；有库存的要随附按台期初登记 ``units``（``[{serial_no, secondary_no, color, warehouse, location}]``），
    每个仓登记的台数等于账面数才在同一个事务里开（主会话 10-26）。"""
    from pearnly_erp_portal.inventory import stock, warehouses

    stock.lock_products([product])  # 与过账同一把锁：开按台期间这件商品不进出
    doc = frappe.get_doc(PRODUCT, product, for_update=True)
    if kind and kind not in KINDS:
        frappe.throw(_("Invalid value"))
    if doc.track_serial:
        return {"name": product, "track_serial": 1, "registered": 0}
    on_hand = stock_by_warehouse(product)
    units = [unit for unit in (units or []) if isinstance(unit, dict)]
    counted = {}
    for unit in units:
        warehouse = warehouses.required(unit.get("warehouse"))
        counted[warehouse] = counted.get(warehouse, 0) + 1
        unit["warehouse"] = warehouse
    if any(qty < 0 or abs(qty - round(qty)) > 1e-9 for qty in on_hand.values()) or \
            {w: int(round(q)) for w, q in on_hand.items()} != counted:
        frappe.throw(_shortfall(on_hand, counted), title=_(OPENING_MISMATCH))
    today, seen = nowdate(), set()
    for unit in units:
        company = warehouses.company_of(unit["warehouse"])
        number = clean(unit.get("serial_no"), MAX_NO).upper()
        if not number or (company, number) in seen:
            frappe.throw(_(DUPLICATE) + (f": {number}" if number else ""))
        seen.add((company, number))
        register(product=product, company=company, warehouse=unit["warehouse"],
                 location=clean(unit.get("location"), 140), entry=unit, ref_type=OPENING_REF, ref_id=product,
                 posting_date=today, action="opening")
    doc.track_serial = 1
    if kind:
        doc.serial_kind = kind
    doc.save()
    return {"name": product, "track_serial": 1, "registered": len(units)}


def names_of_company(company) -> list:
    return frappe.get_all("ERP Warehouse", filters={"company": company}, pluck="name", limit_page_length=0)  # bounded: 一家公司的仓


def _visible(warehouses):
    """``{仓: (仓名, 公司名)}``：这个人能用的公司的仓；别家的不在里面（按台是商品级开关，共用商品各公司都有账，主会话 10-26）。"""
    from pearnly_erp_portal.masters import company as companies

    mine = set(companies.allowed_companies())
    out = {}
    for warehouse in warehouses:
        row = frappe.db.get_value("ERP Warehouse", warehouse, ["warehouse_name", "company"], as_dict=True) or {}
        if row.get("company") in mine:
            out[warehouse] = (row.get("warehouse_name") or warehouse,
                              frappe.db.get_value("ERP Company", row["company"], "company_name") or row["company"])
    return out


def _shortfall(on_hand, counted) -> str:
    """开不了按台时说清楚差在哪：看得到的仓逐个列「仓 · 公司：已登记 / 账面」，看不到的公司只合成一句，不露名称与数量（G17）。"""
    places = set(on_hand) | set(counted)
    visible = _visible(places)
    lines, hidden = [], False
    for warehouse in sorted(places, key=lambda name: visible.get(name, (name, ""))):
        stock, done = on_hand.get(warehouse, 0), counted.get(warehouse, 0)
        if abs(stock - done) <= 1e-9:
            continue
        if warehouse not in visible:
            hidden = True
            continue
        name, company = visible[warehouse]
        lines.append(f"{frappe.utils.escape_html(name)} · {frappe.utils.escape_html(company)}: {done} / {frappe.utils.flt(stock, 3):g}")
    if hidden:
        lines.append(_(OTHER_COMPANIES))
    return "<br>".join(lines) or _(OPENING_MISMATCH)


def turn_off(product):
    """关按台：只有这件商品的台全部已售或作废才行。"""
    doc = frappe.get_doc(PRODUCT, product)
    if not doc.track_serial:
        return {"name": product, "track_serial": 0}
    if frappe.db.exists(DOCTYPE, {"product": product, "status": ["in", [IN_STOCK, ISSUED]]}) or stock_by_warehouse(product):
        frappe.throw(_(CANNOT_TURN_OFF))
    doc.track_serial = 0
    doc.save()
    return {"name": product, "track_serial": 0}


def refund_picks(original, product, qty, picks=None, *, company=None) -> list:
    """收银退货这一行退回的台：只能是原小票卖出、现在还是已售的那几台（同车型）。页面没选就按原小票卖出的顺序取够数量；
    不开按台的商品回空。"""
    if product not in tracked([product], company):
        return []
    sold = frappe.get_all(DOCTYPE, filters={"out_ref": original, "product": product, "status": SOLD},  # bounded: 一张小票卖出的台
                          pluck="name", order_by="modified asc", limit_page_length=0)
    names = [str(entry.get("serial") if isinstance(entry, dict) else entry or "") for entry in (picks or [])]
    names = [name for name in names if name]
    if not names:
        # 没选：只有退的数量正好是原小票卖出的全部时才自动取；部分退要人选退哪几台，不默认挑（主会话 10-26）
        if int(frappe.utils.flt(qty)) != len(sold):
            frappe.throw(_(QTY_MISMATCH))
        names = sold
    if any(name not in sold for name in names) or len(set(names)) != len(names):
        frappe.throw(_(NOT_FOUND))
    return [{"serial": name} for name in names]


def line_serials(doc) -> dict:
    """单据上登记 / 选中的台按行号分好：``{行号: [{serial, serial_no, secondary_no, color}]}``（行号 = 明细行 idx）。"""
    out = {}
    for row in doc.get("serials") or []:
        out.setdefault(int(row.line_no or 0), []).append(
            {"serial": row.serial or None, "serial_no": row.serial_no or "", "secondary_no": row.secondary_no or "",
             "color": row.color or ""})
    return out


def set_doc_serials(doc, lines):
    """把页面随行交来的台（明细行上的 ``serials``）记到单据的 ``serials`` 子表（行号 = 明细行的位置，从 1 起）。"""
    doc.set("serials", [])
    for index, line in enumerate(lines or [], start=1):
        for entry in (line.get("serials") if isinstance(line, dict) else None) or []:
            if not isinstance(entry, dict):
                continue
            doc.append("serials", {"line_no": index, "product": line.get("product"), "serial": entry.get("serial") or None,
                                   "serial_no": clean(entry.get("serial_no"), MAX_NO),
                                   "secondary_no": clean(entry.get("secondary_no"), MAX_NO),
                                   "color": clean(entry.get("color"), MAX_COLOR)})


def strip_line_serials(lines):
    """明细行写进子表之前摘掉 ``serials``（明细行 DocType 没有这一格）。"""
    return [{key: value for key, value in line.items() if key != "serials"} if isinstance(line, dict) else line
            for line in lines or []]


__all__ = ["IN_STOCK", "ISSUED", "SOLD", "VOID", "apply", "line_serials", "lookup", "register", "reverse",
           "set_doc_serials", "stock_by_warehouse", "strip_line_serials", "tracked", "turn_off", "turn_on"]


# ── 接口（页面：认台、选台、按台记录列表、开 / 关按台、按台期初 Excel）──────────────────────

READ_CAPABILITIES = ("inventory.read", "stock.read", "cashier.read", "receipts.read", "sales.read", "purchase.read", "count.read",
                     "products.view", "report.read")
LIST_FIELDS = ["name", "product", "serial_no", "secondary_no", "color", "status", "warehouse", "location",
               "in_ref_type", "in_ref", "in_date", "out_ref_type", "out_ref", "out_date"]
STATUSES = (IN_STOCK, SOLD, ISSUED, VOID)
RETURNABLE = "returnable"  # 退回能选的台：已售或已出库
# 按台期初 Excel 的列：字段 → 界面词条（车 / 其他商品的叫法都认，模板按商品的号码种类出）
WORKBOOK_FIELDS = ("serial_no", "secondary_no", "color", "warehouse", "location")
PRIMARY_LABELS = {"vehicle": "serial.vehicle.primary", "device": "serial.device.primary"}
SECONDARY_LABELS = {"vehicle": "serial.vehicle.secondary", "device": "serial.device.secondary"}
MAX_IMPORT = 2000


def _require_read():
    from pearnly_erp_portal.access import guard

    if not any(guard.has(capability) for capability in READ_CAPABILITIES):
        frappe.throw(_("Not permitted"), frappe.PermissionError)


def _company(company=None):
    from pearnly_erp_portal.masters import company as companies

    if company and company in companies.allowed_companies():
        return company
    return companies.active_company()


@frappe.whitelist()
def api_lookup(code=None, product=None, warehouse=None, status=None, company=None, location=None):
    """扫到 / 手输的号 → 本公司的台（主号 → 副号 → 末 8 位；认出几台都列出来让人选）。"""
    _require_read()
    statuses = [SOLD, ISSUED] if status == RETURNABLE else [status] if status in STATUSES else None
    return lookup(code, _company(company), product=product or None, statuses=statuses, warehouse=warehouse or None,
                  location=location)


@frappe.whitelist()
def search_units(doctype=None, txt="", searchfield=None, start=0, page_len=20, filters=None):
    """选台下拉（原生 Link 的 ``get_query``，服务端分页搜索）：``filters`` 可带 product / warehouse / location / status / out_ref
    （location 只跟着 warehouse 用，空串 = 不分库位的那些台）。
    只列本公司的台；搜主号、副号、颜色。"""
    _require_read()
    filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
    status = filters.get("status")
    conditions = {"company": _company(filters.get("company")),
                  "status": ["in", [SOLD, ISSUED]] if status == RETURNABLE else status if status in STATUSES else IN_STOCK}
    for field in ("product", "warehouse", "out_ref"):
        if filters.get(field):
            conditions[field] = filters[field]
    if filters.get("warehouse") and "location" in filters:
        conditions["location"] = filters.get("location") or ""
    text = clean(txt, MAX_NO)
    or_filters = [[field, "like", f"%{text}%"] for field in ("serial_no", "secondary_no", "color")] if text else None
    rows = frappe.get_all(DOCTYPE, filters=conditions, or_filters=or_filters,
                          fields=["name", "serial_no", "secondary_no", "color"], order_by="serial_no asc",
                          limit_start=int(start or 0), limit_page_length=min(int(page_len or 20), 50))
    return [(row.name, row.serial_no, " · ".join(value for value in (row.secondary_no, row.color) if value))
            for row in rows]


def _list_filters(product=None, status=None, warehouse=None, q=None, company=None):
    filters = {"company": _company(company)}
    if product:
        filters["product"] = product
    if status in STATUSES:
        filters["status"] = status
    if warehouse:
        filters["warehouse"] = warehouse
    text = clean(q, MAX_NO)
    or_filters = [[field, "like", f"%{text}%"] for field in ("serial_no", "secondary_no", "color")] if text else None
    return filters, or_filters


@frappe.whitelist()
def api_list_units(product=None, status=None, warehouse=None, q=None, start=0, limit=50, company=None):
    """按台记录列表（服务端分页）。"""
    _require_read()
    filters, or_filters = _list_filters(product, status, warehouse, q, company)
    return frappe.get_list(DOCTYPE, filters=filters, or_filters=or_filters, fields=LIST_FIELDS,
                           order_by="modified desc", limit_start=int(start or 0),
                           limit_page_length=min(int(limit or 50), 200))


@frappe.whitelist()
def api_count_units(product=None, status=None, warehouse=None, q=None, company=None):
    from pearnly_core.desk import list_filters

    _require_read()
    filters, or_filters = _list_filters(product, status, warehouse, q, company)
    return list_filters.count(DOCTYPE, filters, or_filters)


def _require_manage():
    from pearnly_erp_portal.access import guard

    guard.require("products.manage")


@frappe.whitelist()
def api_opening_stock(product=None):
    """开按台之前看这件商品各仓的账面数：有数就要先按台期初登记。"""
    _require_manage()
    on_hand = stock_by_warehouse(product)
    visible = _visible(on_hand)
    rows = [{"warehouse": warehouse, "warehouse_name": f"{visible[warehouse][0]} · {visible[warehouse][1]}", "qty": qty}
            for warehouse, qty in sorted(on_hand.items(), key=lambda item: visible.get(item[0], ("", ""))) if warehouse in visible]
    if len(rows) < len(on_hand):
        # 别家公司（这个人看不到的）还有账面：不露名称与数量，合成一行；开按台时那几个仓同样要登记，由有权限的人处理（G17）
        rows.append({"warehouse": "", "warehouse_name": _(OTHER_COMPANIES), "qty": None})
    return rows


@frappe.whitelist(methods=["POST"])
def api_turn_on(product=None, units=None, kind=None):
    _require_manage()
    units = frappe.parse_json(units) if isinstance(units, str) else units
    result = turn_on(product, units, kind)
    frappe.db.commit()
    return result


@frappe.whitelist(methods=["POST"])
def api_turn_off(product=None):
    _require_manage()
    result = turn_off(product)
    frappe.db.commit()
    return result


def _labels(kind):
    from pearnly_core import browser_text

    keys = {"serial_no": PRIMARY_LABELS.get(kind, PRIMARY_LABELS["vehicle"]),
            "secondary_no": SECONDARY_LABELS.get(kind, SECONDARY_LABELS["vehicle"]),
            "color": "serial.color", "warehouse": "stock.col.warehouse", "location": "stock.count.location"}
    return {field: browser_text.label(key) for field, key in keys.items()}


def _aliases():
    """表头 → 字段：字段名、车 / 其他商品两套叫法的四种语言都认。"""
    from pearnly_core import browser_text

    keys = {"serial_no": list(PRIMARY_LABELS.values()), "secondary_no": list(SECONDARY_LABELS.values()),
            "color": ["serial.color"], "warehouse": ["stock.col.warehouse"], "location": ["stock.count.location"]}
    aliases = {field: field for field in WORKBOOK_FIELDS}
    for field, names in keys.items():
        for key in names:
            for text in browser_text.all_languages(key).values():
                if text:
                    aliases[str(text).strip().casefold()] = field
    return aliases


@frappe.whitelist()
def api_opening_template(product=None):
    """按台期初模板：每个仓的账面数各留那么多行（仓名填好，号码空着）。"""
    import io

    import openpyxl

    _require_manage()
    doc = frappe.get_doc(PRODUCT, product)
    labels = _labels(doc.serial_kind)
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append([labels[field] for field in WORKBOOK_FIELDS])
    from pearnly_erp_portal.masters import company as companies

    # 只给当前公司的仓留行（开按台时仓名在当前公司里认，别家的仓名不进模板、也不留同名的歧义行；验收复核 P0 低优先 2）。
    # 一件商品在几家公司都有账时按台开不起来是开按台的口径（商品级开关），按公司开按台记为后续项
    mine = set(names_of_company(companies.active_company()))
    on_hand = {warehouse: qty for warehouse, qty in stock_by_warehouse(product).items() if warehouse in mine}
    visible = _visible(on_hand)
    for warehouse, qty in sorted(on_hand.items(), key=lambda item: visible.get(item[0], ("", ""))):
        if warehouse not in visible:
            continue
        name = visible[warehouse][0]
        for _index in range(min(int(round(qty)), MAX_IMPORT)):
            sheet.append(["", "", "", name, ""])
    for column in "ABCDE":
        sheet.column_dimensions[column].width = 22
    buffer = io.BytesIO()
    book.save(buffer)
    frappe.local.response.filename = f"units-{doc.code or product}.xlsx"
    frappe.local.response.filecontent = buffer.getvalue()
    frappe.local.response.type = "binary"


def parse_workbook(data) -> list:
    """按台期初 Excel → ``[{serial_no, secondary_no, color, warehouse, location}]``（仓库按名称或编号，开按台时在本公司里认）。"""
    import io

    import openpyxl

    try:
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        frappe.throw(_("That file is not an Excel workbook"))
    rows = book.active.iter_rows(values_only=True)
    header = next(rows, None) or []
    aliases = _aliases()
    columns = {index: aliases.get(str(cell or "").strip().casefold()) for index, cell in enumerate(header)}
    if "serial_no" not in columns.values() or "warehouse" not in columns.values():
        frappe.throw(_("The sheet needs a unit number column and a warehouse column"))
    out = []
    for row in rows:
        values = {columns[index]: str(cell).strip() for index, cell in enumerate(row)
                  if columns.get(index) and cell not in (None, "")}
        if not values.get("serial_no"):
            continue
        # 仓库名原样交给开按台时的 `warehouses.required`（按名称或编号、只在本公司里认）；这里不再按名称跨公司先找一遍——
        # 两家都叫 Main 时会取到别家的仓，随后被拒成「找不到仓库」（G17，多仓闭环 P0）
        values["warehouse"] = values.get("warehouse") or ""
        out.append(values)
        if len(out) > MAX_IMPORT:
            frappe.throw(_("That file has too many rows"))
    return out


@frappe.whitelist(methods=["POST"])
def api_turn_on_from_file(product=None, file_url=None, kind=None):
    """按台期初登记（Excel 逐台导入）后打开按台：上传框传上来的文件按地址读回（只认本人刚上传的）。"""
    _require_manage()
    name = frappe.db.get_value("File", {"file_url": file_url, "owner": frappe.session.user}, "name") if file_url else None
    if not name:
        frappe.throw(_("Choose a file to import"))
    with open(frappe.get_doc("File", name).get_full_path(), "rb") as handle:
        units = parse_workbook(handle.read())
    result = turn_on(product, units, kind)
    frappe.db.commit()
    return result


@frappe.whitelist()
def api_export_units(product=None, status=None, warehouse=None, q=None):
    """按台记录导出 Excel（这家公司、按筛选，最多 :data:`MAX_IMPORT` 行，分页取）。"""
    import io

    import openpyxl

    _require_read()
    filters, or_filters = _list_filters(product, status, warehouse, q)
    kind = frappe.db.get_value(PRODUCT, product, "serial_kind") if product else "vehicle"
    labels = _labels(kind)
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append([labels[field] for field in WORKBOOK_FIELDS] + [_("Status")])
    names, start = {}, 0
    while start < MAX_IMPORT:
        rows = frappe.get_list(DOCTYPE, filters=filters, or_filters=or_filters, fields=LIST_FIELDS,
                               order_by="serial_no asc", limit_start=start, limit_page_length=500)
        for row in rows:
            if row.warehouse not in names:
                names[row.warehouse] = frappe.db.get_value("ERP Warehouse", row.warehouse, "warehouse_name") or row.warehouse
            sheet.append([row.serial_no, row.secondary_no or "", row.color or "", names[row.warehouse] or "",
                          row.location or "", _(f"serial.status.{row.status}")])
        if len(rows) < 500:
            break
        start += 500
    buffer = io.BytesIO()
    book.save(buffer)
    frappe.local.response.filename = "units.xlsx"
    frappe.local.response.filecontent = buffer.getvalue()
    frappe.local.response.type = "binary"
