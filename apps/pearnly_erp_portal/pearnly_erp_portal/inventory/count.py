"""盘点（Stock Count）：一份冻结的账面快照 + 追加式盘点记录 + 差异过账。

口径照旧项目 Cowork/LINE 那套盘点的形状搬过来（`services/stocktake/*`）：

- **快照冻结**：开一个盘点任务时把当时的账面数量抄进快照行（`ERP Stock Count Item`），
  之后书面怎么变都不改它 —— 否则"差异"就没有基准。旧那边只能靠上传 Excel，因为账不在
  它手里；在 ERP 里直接从结存生成，同一件事。
- **记录是追加式的**（`ERP Stock Count Entry`）：扫一次、填一个数量就是一条记录，
  同一商品可以好几条；**实盘合计 = 未撤销记录之和**。记录可改可撤销，撤销不进合计。
  每条带 `request_id`（幂等：同一发重试不会记两笔）与 `version`（并发编辑当场拦）。
- **未盘点 ≠ 0**：没数过的行实盘留空，过账时也不动它（旧线上"未盘点项仍保留为空"）。
- **过账**把差异落成 `count` 流水、结存收敛到实盘（旧那边没有这一步，因为账在 ERP）。

照片是原生附件（挂在记录上，最多 5 张），不另存 base64。
"""

from decimal import Decimal
import hashlib
import json

import frappe
from frappe import _
from frappe.utils import flt, now

from pearnly_core.account import names as person_names
from pearnly_core.desk import list_filters
from pearnly_erp_portal.access import guard, listing, rules
from pearnly_erp_portal.masters import access as company_access
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.inventory import stock as stock_service
from pearnly_erp_portal.inventory import count_live, count_state, documents as stock_documents, positions, warehouses
# 商品域只管纯规则的 contracts（不依赖框架、IO 或上层流程）：盘点要照它的口径
# 决定"表上那一列显示条码还是二维码内容"。这是单向依赖，products 不认识 inventory。
from pearnly_erp_portal.products import contracts as product_contracts

COUNT = "ERP Stock Count"
ITEM = "ERP Stock Count Item"
ENTRY = "ERP Stock Count Entry"
PRODUCT = "ERP Product"
BALANCE = "ERP Stock Balance"

PHOTO_LIMIT = 5

NOT_FOUND = "Stock count not found"
TITLE_REQUIRED = "A stock count needs a name"
CLOSED = "This stock count is closed"
POSTED = "This stock count is already posted"
ITEM_NOT_FOUND = "That row is not part of this stock count"
# 这句英文**必须与术语表里别处那句同义的错开**：`No product matches that code` 在术语表里
# 有两条带 key 的词条（手机扫码那条 `stock.mobile.notFound`、盘点这条 `stock.count.problemCode`），
# 两条译文不一致 → 生成脚本按规矩**不补"英文原文"那一行**（谁覆盖谁说不清），
# 结果服务端这句在页面上直出英文。换成一个只属于这里的写法，译文照盘点那一条给。
CODE_NOT_FOUND = "No product matches this code"
QUANTITY_REQUIRED = "Enter a quantity"
VERSION_STALE = "This entry changed underneath you; reload and try again"
REQUEST_CONFLICT = "That submission was already used for a different entry"
PHOTO_LIMIT_ERROR = "At most {0} photos per entry"
CODE_REQUIRED = "Scan or enter a code"
NAME_REQUIRED = "A product needs a name"
# 盘点行的来源（`row_origin`，空 = 账上快照）：盘点时补的已有商品 / 当场新建的 / 停用后又盘到的（过账时自动启用）
ADDED, NEW, REACTIVATED = "added", "new", "reactivated"


# ── 取数 ────────────────────────────────────────────────────────────────────


def _get(name) -> "frappe.Document":
    if not name or not frappe.db.exists(COUNT, name):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    doc = frappe.get_doc(COUNT, name)
    company_access.require_warehouse(doc.warehouse)
    return doc


def _item(name: str) -> dict:
    row = frappe.db.get_value(ITEM, name, "*", as_dict=True)
    if not row:
        frappe.throw(_(ITEM_NOT_FOUND), frappe.DoesNotExistError)
    _get(row["count"])
    company_access.require_warehouse(row["warehouse"])
    return row


def _qty(value, *, field="Quantity") -> Decimal:
    try:
        number = Decimal(str(value).strip())
        if not number.is_finite():
            raise ValueError("non-finite quantity")
        return number
    except Exception:
        frappe.throw(_("Invalid value: {0}").format(field))


def products_of_code(code: str, company: str | None = None) -> list:
    """扫到的码 → 命中的商品（码的命名空间，`products.codes`）：系统编码 / 内部编码 / Express 编码 / 条码 / 二维码内容 /
    售卖单位条码都认。给了 ``company``（这份盘点仓库所属的公司）就只认这家公司 Express 的档和本地档。"""
    from pearnly_erp_portal.products import codes

    found = codes.candidates(code, company)
    if found:
        return found
    # 停用商品的码（多仓评估问 8）：照样认出这件商品记数，过账时自动重新启用；不新建重复码
    return codes.candidates(code, company, active_only=False)


def product_of_code(code: str, company: str | None = None) -> dict | None:
    """扫到的码 → 那一件商品；认不到回 None；命中几件（存量里撞了的码）报错让人挑，不默认挑一个（主会话 10-26）。"""
    found = products_of_code(code, company)
    if len(found) > 1:
        frappe.throw(_("Several matches found for {0}. Please select a record.").format(str(code).strip()))
    return frappe.db.get_value(PRODUCT, found[0]["name"], "*", as_dict=True) if found else None


UNIT_BY_UNIT = "Count products tracked by unit one unit at a time"
UNIT_COUNTED = "This unit has been counted"


def per_unit(product, company) -> bool:
    """这件商品在这家公司按台盘：商品开了按台、公司是按台的类型（普通公司一律按数量盘，用户 10-29）。"""
    return bool(product and product.get("track_serial")) and companies.unit_tracking(company)


def unit_of_code(code, company):
    """盘点扫到的号是不是一台在库的车（按台管理，`inventory.serials.lookup`）：认到唯一一台回它，认不到回 None，认出几台报错让人挑。
    普通公司不按号认台。"""
    from pearnly_erp_portal.inventory import serials

    if not companies.unit_tracking(company):
        return None
    units = serials.lookup(code, company, statuses=(serials.IN_STOCK,))
    if len(units) > 1:
        frappe.throw(_("Several matches found for {0}. Please select a record.").format(code))
    return units[0] if units else None


def _scanned(code, product, serial, company):
    """录一条盘点之前认出 (商品, 台)：编码 / 条码认商品；认不到再按号认台。按台管理的商品必须认到台。"""
    from pearnly_erp_portal.inventory import serials

    unit = None
    if serial and companies.unit_tracking(company):
        unit = frappe.db.get_value(serials.DOCTYPE, {"name": serial, "company": company, "status": serials.IN_STOCK},
                                   ["name", "product", "serial_no", "secondary_no", "color", "warehouse", "location"], as_dict=True)
        if not unit:
            frappe.throw(_(CODE_NOT_FOUND))
    found = None
    if not unit and code:
        found = product_of_code(code, company)
        if not found:
            unit = unit_of_code(code, company)
    if unit:
        found = frappe.db.get_value(PRODUCT, {"name": unit.product, "is_active": 1}, "*", as_dict=True)
    elif not found and product:
        found = frappe.db.get_value(PRODUCT, {"name": product}, "*", as_dict=True)
    if not found:
        frappe.throw(_(CODE_NOT_FOUND))
    if per_unit(found, company) and not unit:
        frappe.throw(_(UNIT_BY_UNIT))
    return found, unit


def count_company(count) -> str | None:
    """这份盘点的仓库所属的公司（认商品按它分）。"""
    warehouse = frappe.db.get_value(COUNT, count, "warehouse") if count else None
    return frappe.db.get_value("ERP Warehouse", warehouse, "company") if warehouse else None


# ── 任务 ────────────────────────────────────────────────────────────────────


def create_count(*, title, warehouse=None, notes=None, snapshot=True) -> dict:
    """开一份盘点。

    `snapshot=True`（盘点在库）把当前结存抄成快照行；`snapshot=False`（上传导入）
    只建一张空任务，行由上传的表带进来（`apply_import`）。
    """
    guard.require("count.write")
    name = (title or "").strip()
    if not name:
        frappe.throw(_(TITLE_REQUIRED))
    warehouse = warehouses.required(warehouse)
    warehouses.remember("count", warehouse)  # 下次建盘点预填本人上次选的仓（多仓闭环 P1，问 2）
    doc = frappe.get_doc({"doctype": COUNT, "title": name, "warehouse": warehouse,
                          "notes": notes, "status": "Active"})
    count_state.write_document(doc, "insert")
    if not snapshot:
            return {"name": doc.name, "rows": 0}
    rows = frappe.get_all(BALANCE, filters={"warehouse": warehouse},
                           fields=["product", "qty_on_hand", "location"], limit_page_length=0)
    products = {p["name"]: p for p in frappe.get_list(
        PRODUCT, filters={"is_active": 1, "name": ["in", [r["product"] for r in rows] or [""]]},
        # 要带二维码内容：只有二维码的商品，盘点表「条码 / 二维码」那一格才不空（录制问题 #23）。
        fields=["name", "product_name", "code", "internal_code", "express_code", "barcode", "qr_payload", "unit", "base_unit"],
        limit_page_length=0)}
    created = 0
    for row in rows:
        product = products.get(row["product"])
        if not product:
            continue
        qty = flt(row["qty_on_hand"], 3)
        _insert_item(doc.name, product, warehouse, qty, row.get("location"), origin=False)  # 账上快照
        created += 1
    _refresh_totals(doc.name)
    return {"name": doc.name, "rows": created}


def apply_import(*, count, rows) -> dict:
    """把上传的表并进这份盘点。

    每一行：按编号（找不到再按条码/二维码）认商品 → **登记快照行**（账面取表里的数，
    表里没填就取当前结存）→ 表里填了实盘数的，记一条 `Import` 记录。

    同一份表**重复上传是覆盖不是叠加**：导入记录用 `import:<任务>:<行>` 当幂等锚，
    再传一次是改那一条，不是又记一笔（用户改完数重传是常态）。
    """
    guard.require("count.write")
    count_state.lock_task(count)
    task = _get(count)
    if task.status != "Active":
        frappe.throw(_(CLOSED))
    applied = counted = 0
    problems = []
    company = count_company(task.name)
    for number, row in enumerate(rows, start=2):
        if row.get("serial_no") and companies.unit_tracking(company):
            # 按台公司一台一行（岳父 10-25）：按车架号认台；表里没有的台 = 没盘到（同手机逐台扫）
            outcome = _import_unit(task, row, company)
            if outcome not in ("counted", "listed"):
                problems.append({"row": number, "reason": outcome})
                continue
            applied += 1
            counted += outcome == "counted"
            continue
        product = _product_of_row(row, company)
        code = (row.get("barcode") or row.get("product_code") or "").strip()
        internal = (row.get("internal_code") or "").strip()
        if not product and row.get("product_name") and (code or internal) and not any(
                products_of_code(value, company) for value in (internal, row.get("product_code"), row.get("barcode")) if value):
            # 表里有品名、码在系统里完全没有：当场建（同 Desk / LINE 的就地新建）；没品名的照旧列进问题行。
            # 内部编码一列有值就记成这件的内部编码（岳父 10-25），条码 / 编码那格照旧作扫码用的码
            made = create_product_here(count=task.name, code=code or None, product_name=row["product_name"], unit=row.get("unit"),
                                       internal_code=internal or None)
            product = frappe.db.get_value(PRODUCT, made["name"], "*", as_dict=True)
        if not product:
            problems.append({"row": number, "reason": "code_not_found"})
            continue
        if row.get("counted_qty") is not None and per_unit(product, company):
            # 按台管理的商品（汽车 / 电器类公司）要逐台盘：这一行列进问题行，别的行照进（原来整张表报错，10-30）
            problems.append({"row": number, "reason": "unit_by_unit"})
            continue
        try:
            warehouse = _warehouse_of(row.get("warehouse"), task.warehouse, count_company(task.name))
        except frappe.ValidationError:
            problems.append({"row": number, "reason": "warehouse_not_found"})
            continue
        book_qty = row.get("book_qty")
        if book_qty is None:
            book_qty = stock_service.current_balance(
                product["name"], warehouse, row.get("location")).qty
        item_name = _insert_item(task.name, product, warehouse, book_qty,
                                 row.get("location"))
        applied += 1
        if row.get("counted_qty") is not None:
            _upsert_import_entry(task.name, item_name, row)
            counted += 1
    _refresh_totals(task.name)
    return {"rows": applied, "counted": counted, "problems": problems}


def _import_unit(task, row, company) -> str:
    """表里一台车那一行：认台 → 登记账面行 → 实盘 1 记这一台、0 撤掉上回导入记的那条、空着不记。

    车架号认不到（或认出几台、或和这一行的商品对不上）只报出这一行，**不当场建台**——建台走进货 / 入库单（岳父 10-25：
    当场建会绕过进货成本）。手机上已逐台扫过的台不重记；同一份表重传是改那一条（幂等锚 `import:<盘点>:unit:<台>`）。
    回 "counted" / "listed"（只登记了账面行）/ 问题原因。
    """
    from pearnly_erp_portal.inventory import serials

    units = serials.lookup(row["serial_no"], company, statuses=(serials.IN_STOCK,))
    if len(units) != 1:
        return "unit_not_found"
    unit = units[0]
    named = _product_of_row(row, company)
    if named and named["name"] != unit.product:
        return "unit_not_found"
    quantity = row.get("counted_qty")
    if quantity is not None and quantity not in (0, 1):
        return "counted_qty:invalid"
    try:
        warehouse = _warehouse_of(row.get("warehouse"), unit.warehouse, company)
    except frappe.ValidationError:
        return "warehouse_not_found"
    # 仓库那格空着：就按这台在账的位置；填了仓库就照表里的库位（空 = 不分库位）
    location = positions.clean_location(row.get("location") if row.get("warehouse") else (row.get("location") or unit.location))
    product = frappe.db.get_value(PRODUCT, unit.product, "*", as_dict=True)
    item = _insert_item(task.name, product, warehouse,
                        stock_service.current_balance(product["name"], warehouse, location).qty, location)
    if quantity is None:
        return "listed"
    request_id = f"import:{task.name}:unit:{unit.name}"
    seen = frappe.db.get_value(ENTRY, {"request_id": request_id}, ["name", "voided", "warehouse", "location"], as_dict=True)
    elsewhere = frappe.db.exists(ENTRY, {"count": task.name, "serial": unit.name, "voided": 0,
                                         "request_id": ["!=", request_id]})
    if quantity == 0:
        if seen and not seen.voided:
            edit_entry(entry=seen.name, voided=1)
        return "counted"
    if elsewhere:
        return "counted"  # 手机 / 录入框已逐台记过这一台
    if seen:
        changes = {} if (seen.warehouse, seen.location or "") == (warehouse, location) else {"warehouse": warehouse, "location": location}
        if seen.voided:
            changes["voided"] = 0
        if changes:
            edit_entry(entry=seen.name, **changes)
        return "counted"
    add_entry(count=task.name, item=item, qty=1, serial=unit.name, warehouse=warehouse, location=location,
              source="Import", request_id=request_id)
    return "counted"


def _product_of_row(row, company=None) -> dict | None:
    """导入的一行认商品：编码一列认不准（撞号几件）就当认不到，交回问题清单，不默认挑一件。"""
    for code in ((row.get("internal_code") or "").strip(), (row.get("product_code") or "").strip(),
                 (row.get("barcode") or "").strip()):
        if code:
            found = products_of_code(code, company)
            if len(found) == 1:
                return frappe.db.get_value(PRODUCT, found[0]["name"], "*", as_dict=True)
    return None


def _upsert_import_entry(count: str, item_name: str, row) -> None:
    request_id = f"import:{count}:{item_name}"
    existing = frappe.db.get_value(ENTRY, {"request_id": request_id}, "name")
    location = (row.get("location") or "").strip() or None
    if existing:
        edit_entry(entry=existing, qty=row.get("counted_qty"), location=location)
        return
    add_entry(count=count, item=item_name, qty=row.get("counted_qty"), location=location,
              source="Import", request_id=request_id)


def _insert_item(count: str, product: dict, warehouse: str, book_qty, location="", origin=None) -> str:
    """一行 = **商品 × 仓库 × 库位**（与旧项目 `services/stocktake/schema.py` 的唯一键一致）。

    库位是行的身份的一部分，不是记录上的备注：同一个商品放在两个货架上就是两行、
    两个账面、两个差异。少了这一层，"每个仓位的差异"根本算不出来。
    """
    location = positions.clean_location(location)
    if origin is None:  # 快照之外补进来的行（`origin=False` 是账上快照）
        if product.get("origin") == "stocktake" and product.get("origin_name") == count:
            origin = NEW
        else:
            origin = REACTIVATED if not product.get("is_active", 1) else ADDED
    existing = frappe.db.get_value(ITEM, {"count": count, "product": product["name"],
                                          "warehouse": warehouse, "location": location}, "name")
    if existing:
        return existing
    doc = frappe.get_doc({
        "doctype": ITEM, "count": count, "product": product["name"],
        # 「编码」显示内部编码（主会话 10-26），没有就用系统编码
        "product_name": product.get("product_name"), "code": product_contracts.count_code(product) or None,
        # 商品那边的"码"是一格（条码与二维码合并）：只有二维码的商品，盘点表这一列
        # 也要带上它，否则盘点员拿着这张表扫不出来（匹配那侧本来就三列一起查）。
        "barcode": product_contracts.shown_code(product) or None,
        "unit": (product.get("base_unit") or product.get("unit") or "") or None,
        "warehouse": warehouse, "location": location,
        "book_qty": flt(book_qty, 3), "counted": 0,
        "row_origin": origin or None,
    })
    count_state.write_document(doc, "insert")
    return doc.name


def item_of_product(*, count: str, product: str, location="") -> dict | None:
    """这份盘点里某个商品在某个库位那一行（库位给了就精确匹配）。"""
    guard.require("count.read")
    _get(count)
    filters = {"count": count, "product": product}
    if location:
        filters["location"] = location
    return frappe.db.get_value(ITEM, filters, "*", as_dict=True)


def rows_of_product(*, count: str, product: str) -> list:
    """这个商品在这份盘点里的**所有行**（一个货架一行），手机端要拿它给员工挑。"""
    guard.require("count.read")
    _get(count)
    return frappe.get_list(ITEM, filters={"count": count, "product": product},
                           fields=["name", "product", "product_name", "code", "barcode", "unit",
                                   "warehouse", "location", "book_qty", "counted",
                                   "counted_qty", "difference"],
                           order_by="location asc, name asc", limit_page_length=0)



def create_product_here(*, count, code, product_name, internal_code=None, track_serial=None, **standard) -> dict:
    """盘点里扫到系统没有的码：当场建商品（多仓评估 13.2-6，用户 10-29 批复）。有盘点录入权（``count.write``）就能建。
    Desk 弹的是物料页同一个新建弹窗（用户 11-01），``standard`` 收那几格（`product_service.standard_fields`，与商品页建档同一份）；
    LINE 手机页与盘点表导入只给品名 / 单位。扫到的码作条码 / 二维码内容，编码系统发；来由记「盘点新建 · 这份盘点」，
    谁 / 何时用原生建档人与时间。按台公司勾了按台就在建档时一并打开（新商品没有库存，不用按台期初登记，同 `serials.turn_on`）。

    在盘点锁里先按码再认一次：别人刚用同一个码建过（或这个码其实是停用商品）就直接回那件，不建重复码。之后照常记数（`add_entry`）。
    开盘点时已接上 Express 的公司建成这家公司自己的档（按开盘点那一刻，`push.owner`，主会话 11-01）；写盘点调整 TK 时按物料问一次
    （选现有存货或新建，多仓 P3x），写好后挂上电脑用的码（`push.link_products`）。"""
    from pearnly_erp_portal.express import push as express_push
    from pearnly_erp_portal.products import service as product_service

    guard.require("count.write")
    count_state.lock_task(count)
    task = _get(count)
    if task.status != "Active":
        frappe.throw(_(CLOSED))
    company = count_company(count)
    code = product_service._text(code, product_contracts.MAX_QR)
    # 上传的表里可以只给内部编码（岳父 10-25：模板带内部编码一列）；扫码 / 手输的两条路照旧要码
    internal = product_service._internal(internal_code) if internal_code else None
    if not code and not internal:
        frappe.throw(_(CODE_REQUIRED))
    for value in (internal, code):
        found = products_of_code(value, company) if value else []
        if len(found) > 1:
            frappe.throw(_("Several matches found for {0}. Please select a record.").format(value))
        if found:
            return {"name": found[0]["name"], "created": False}
    try:
        name = product_contracts.require_name(product_name)
    except ValueError:
        frappe.throw(_(NAME_REQUIRED))
    # GS1 码（Data Matrix、DataBar、GS1-128）建档只记它的商品条码：GTIN 写成 13 位 EAN（首位不是 0 的写 14 位），下次扫同一件
    # 不管带不带批号、有效期、序列号都按条码认得出（`products.gs1`，10-30）
    from pearnly_erp_portal.products import gs1

    fields = gs1.parse(code) if code else None
    if fields:
        code = fields["01"][1:] if fields["01"].startswith("0") else fields["01"]
    field = product_contracts.code_field(code) if code else None
    system_code = product_service._allocate_code()
    product_service._assert_unique(system_code, code if field == "barcode" else None, internal=internal,
                                   qr=code if field and field != "barcode" else None)
    allowed = ("unit", "unit_price", "vat_applicable", "image_url", "min_stock", "default_cost", "base_unit", "category_id",
               "default_location", "serial_kind")
    doc = frappe.get_doc({
        "doctype": PRODUCT, "product_name": name, "code": system_code, **({field: code} if code else {}), "internal_code": internal,
        **product_service.standard_fields(**{key: value for key, value in standard.items() if key in allowed}),
        "track_serial": 1 if str(track_serial or "") in ("1", "True", "true") and companies.unit_tracking(company) else 0,
        "is_active": 1,
        "express_company": express_push.owner(company, task.creation),
        "origin": "stocktake", "origin_doctype": COUNT, "origin_name": task.name,
    })
    doc.insert(ignore_permissions=True)  # 建档权限只放这一条路（`count.write`）；商品页的其余能力照旧要 `products.manage`
    product_service._attach_image(doc.image_url, doc.name)
    return {"name": doc.name, "created": True}


def next_product_code(*, count) -> dict:
    """盘点里新建物料弹窗那一格编码的只读预览（同商品页 `next_code_preview`，不占号）；盘点录入权即可。"""
    guard.require("count.write")
    _get(count)
    from pearnly_erp_portal.products import service as product_service

    return {"code": product_service._allocate_code()}

def ensure_item(*, count, code=None, product=None, location="") -> dict:
    """把扫到的货**登记**进这份盘点（没有就补一行，账面取当前结存）。

    与加记录分开：手机端扫一下只是"看看这是哪件、账面多少"，不该在那一下
    就写进一条实盘（写 0 会被当成"数出来是 0"，比不写更糟）。
    """
    guard.require("count.write")
    count_state.lock_task(count)
    task = _get(count)
    if task.status != "Active":
        frappe.throw(_(CLOSED))
    found = product_of_code(code, count_company(count)) if code else None
    if not found and product:
        found = frappe.db.get_value(PRODUCT, {"name": product}, "*", as_dict=True)
    if not found:
        frappe.throw(_(CODE_NOT_FOUND))
    warehouse = task.warehouse or warehouses.required(company=count_company(count))
    book_qty = stock_service.current_balance(found["name"], warehouse, location).qty
    name = _insert_item(task.name, found, warehouse, book_qty, location)
    _refresh_totals(task.name)
    return frappe.db.get_value(ITEM, name, "*", as_dict=True)


def _refresh_totals(count: str) -> None:
    """任务上的三个数（行数 / 已盘点 / 有差异）与行上的差异一起更新。"""
    rows = frappe.get_list(ITEM, filters={"count": count},
                           fields=["name", "book_qty", "basis_qty", "basis_set", "counted", "counted_qty"],
                           limit_page_length=0)
    counted = differences = 0
    for row in rows:
        difference = (flt(row["counted_qty"], 3) - flt(row["basis_qty"] if row["basis_set"] else row["book_qty"], 3)) if row["counted"] else 0
        frappe.db.set_value(ITEM, row["name"], "difference", difference,
                            update_modified=False)
        if row["counted"]:
            counted += 1
            original = _item(row["name"])
            moved = any((e.warehouse, e.location or "") != (original.warehouse, original.location or "")
                        for e in frappe.get_all(ENTRY, filters={"item": row["name"], "voided": 0},
                                                fields=["warehouse", "location"]))
            if difference or moved:
                differences += 1
    frappe.db.set_value(COUNT, count, {"item_total": len(rows), "counted_total": counted,
                                       "difference_total": differences}, update_modified=False)


def _size(limit):
    """`limit=0` 是"全部"的意思（调用方就是这么用的），不能悄悄变成 50。

    `int(limit or 50)` 会让 0 走到 50：导出、合计、看板都会在 50 行处**静默截断**，
    数目大了才发现"少了一截"。分页只管第一页，这里一律按"要不截断就 None"。
    """
    if limit in (None, "", 0, "0"):
        return None
    return max(0, int(limit))


# 列头排序白名单。仓库那一列显示的是**仓库名**（由 link 名翻出来），排 link 名没意义，
# 所以它不在这里 —— 那一列头保持不可点（前端见 `sort_fields`）。
SORT_FIELDS = {
    "title": "title", "status": "status", "item_total": "item_total",
    "counted_total": "counted_total", "difference_total": "difference_total",
    "creation": "creation",
}


def list_counts(*, status=None, limit=50, start=0, sort_by=None, sort_order=None) -> list[dict]:
    guard.require("count.read")
    filters = dict(warehouses.scope_filter())
    if status:
        filters["status"] = status
    rows = frappe.get_list(
        COUNT, filters=filters,
        fields=["name", "title", "status", "warehouse", "item_total", "counted_total",
                "difference_total", "creation", "modified", "closed_at", "posted_at"],
        order_by=listing.order_by(sort_by, sort_order, SORT_FIELDS, "creation desc"),
        limit_start=int(start or 0), limit_page_length=_size(limit),
    )

    return [dict(row, warehouse_name=_warehouse_label(row.warehouse)) for row in rows]


def count_total(*, status=None) -> int:
    guard.require("count.read")
    filters = dict(warehouses.scope_filter())
    if status:
        filters["status"] = status
    return list_filters.count(COUNT, filters)


def get_count(name: str) -> dict:
    guard.require("count.read")
    doc = _get(name)
    return {
        "name": doc.name, "title": doc.title, "status": doc.status, "warehouse": doc.warehouse,
        "warehouse_name": _warehouse_label(doc.warehouse),
        "notes": doc.notes, "item_total": doc.item_total, "counted_total": doc.counted_total,
        "difference_total": doc.difference_total, "closed_at": doc.closed_at,
        "closed_by": doc.closed_by, "posted_at": doc.posted_at, "posted_by": doc.posted_by,
        "express_state": doc.express_state, "express_docnum": doc.express_docnum,
    }


def _template_warehouse(name=None, warehouse=None):
    if name:
        return frappe.db.get_value(COUNT, name, "warehouse")
    return (warehouses.resolve(warehouse, create=False) or "__missing__") if warehouse else warehouses.required()


def template_unit_kind(*, name=None, warehouse=None) -> str | None:
    """盘点表要不要号码三列：仓所属公司按台（车 / 电器）就回公司类型，普通公司回 None。"""
    company = frappe.db.get_value("ERP Warehouse", _template_warehouse(name, warehouse), "company")
    return companies.company_type(company) if companies.unit_tracking(company) else None


def _units_in(company, places, products) -> list:
    """这几个仓里这几种按台商品的在库台（盘点表一台一行用）。"""
    from pearnly_erp_portal.inventory import serials

    if not (company and places and products):
        return []
    return frappe.get_all(serials.DOCTYPE, filters={"company": company, "status": serials.IN_STOCK,  # bounded: 一份盘点 / 一个仓的按台商品在库台
                                                    "product": ["in", sorted(products)], "warehouse": ["in", sorted(places)]},
                          fields=["name", "product", "serial_no", "secondary_no", "color", "warehouse", "location"],
                          order_by="serial_no asc", limit_page_length=0)


def _unit_row(base, unit, counted=None) -> dict:
    return {**base, "serial_no": unit.serial_no, "secondary_no": unit.secondary_no or "", "color": unit.color or "",
            "book_qty": 1, "counted_qty": counted}


def template_rows(*, name=None, warehouse=None) -> list[dict]:
    """盘点表要填的行：给一份盘点的快照，或**按当前结存现拉一份**。

    仓库列给的是**仓库名**（人认得的那串），不是内部编号；导回来时两种都认。
    """
    guard.require("count.read")
    if name:
        _get(name)
        rows = list_items(count=name, limit=0)
        # 「编码」一格是系统编码，「内部编码」单独一列（接 Express 的是 Express 编码，岳父 10-25）
        codes = {row["name"]: row for row in frappe.get_all(  # bounded: 这份盘点的商品
            PRODUCT, filters={"name": ["in", sorted({row["product"] for row in rows}) or [""]]},
            fields=["name", "code", "internal_code", "express_code"], limit_page_length=0)}
        # (按台公司的按台商品在 `_expand_units` 里一台一行)
        out = [{
            "product_code": (codes.get(row["product"]) or {}).get("code") or row["code"],
            "internal_code": product_contracts.internal_code(codes.get(row["product"]) or {}),
            "product_name": row["product_name"],
            "barcode": row["barcode"], "unit": row["unit"], "book_qty": row["book_qty"],
            "counted_qty": row["counted_qty"] if row["counted"] else None,
            "warehouse": _warehouse_label(row["warehouse"]), "location": row["location"] or "",
        } for row in rows]
        return _expand_units(name, rows, out) if template_unit_kind(name=name) else out
    warehouse = _template_warehouse(warehouse=warehouse)
    balances = frappe.get_all(BALANCE, filters={"warehouse": warehouse},
                               fields=["product", "qty_on_hand", "location"], limit_page_length=0)
    company = frappe.db.get_value("ERP Warehouse", warehouse, "company")
    # 只列这个仓所属公司 Express 的档和本地档（几十家公司各自同步来的商品不混进来，同期初模板，10-30）
    products = {row["name"]: row for row in frappe.get_list(
        PRODUCT, filters={"is_active": 1},
        or_filters=[["express_company", "=", company or ""], ["express_company", "is", "not set"]],
        fields=["name", "product_name", "code", "internal_code", "express_code", "barcode", "qr_payload", "unit", "base_unit",
                "track_serial"],
        limit_page_length=0)}
    label = _warehouse_label(warehouse)
    # 按台公司：按台商品一台一行（在这个仓的在库台，带各自库位），没有在库台的照旧一行
    units = {}
    if companies.unit_tracking(company):
        for unit in _units_in(company, [warehouse], [key for key, row in products.items() if row.get("track_serial")]):
            units.setdefault(unit.product, []).append(unit)
    present = {row["product"] for row in balances}
    balances.extend({"product": key, "qty_on_hand": 0, "location": ""}
                    for key in products if key not in present)
    out = []
    listed = set()
    for balance in balances:
        product = products.get(balance["product"])
        if not product or product["name"] in listed:
            continue
        if units.get(product["name"]):
            listed.add(product["name"])
            base = {"product_code": product["code"], "internal_code": product_contracts.internal_code(product),
                    "product_name": product["product_name"], "barcode": product_contracts.shown_code(product),
                    "unit": product["base_unit"] or product["unit"], "warehouse": label}
            out.extend(_unit_row({**base, "location": unit.location or ""}, unit) for unit in units[product["name"]])
            continue
        out.append({
            "product_code": product["code"], "internal_code": product_contracts.internal_code(product),
            "product_name": product["product_name"],
            "barcode": product_contracts.shown_code(product),
            "unit": product["base_unit"] or product["unit"],
            "book_qty": flt(balance["qty_on_hand"], 3), "counted_qty": None,
            "warehouse": label, "location": balance.get("location") or "",
        })
    return out


def _expand_units(name, items, out) -> list:
    """一份盘点的表，按台商品换成一台一行：记在这一行上的台（实盘 1）+ 在这一行仓 / 库位、本次还没记过的在库台（实盘空着）。
    一台都没有的行照旧一行（填了实盘数导回会被逐行报出，要逐台盘）。"""
    from pearnly_erp_portal.inventory import serials

    tracked = {row.name for row in frappe.get_all(PRODUCT, filters={"name": ["in", sorted({r["product"] for r in items}) or [""]],  # bounded: 这份盘点的商品
                                                                  "track_serial": 1}, fields=["name"],
                                                    limit_page_length=0)}
    if not tracked:
        return out
    entries = frappe.get_all(ENTRY, filters={"count": name, "voided": 0, "serial": ["is", "set"]}, fields=["item", "serial"],  # bounded: 这份盘点逐台记的
                             limit_page_length=0)
    counted_on = {}
    for entry in entries:
        counted_on.setdefault(entry.item, []).append(entry.serial)
    counted = {entry.serial for entry in entries}
    info = {unit.name: unit for unit in frappe.get_all(  # bounded: 这份盘点逐台记的台
        serials.DOCTYPE, filters={"name": ["in", sorted(counted) or [""]]},
        fields=["name", "product", "serial_no", "secondary_no", "color", "warehouse", "location"],
        limit_page_length=0)}
    waiting = {}
    for unit in _units_in(count_company(name), {r["warehouse"] for r in items if r["product"] in tracked}, tracked):
        if unit.name not in counted:
            waiting.setdefault((unit.product, unit.warehouse, unit.location or ""), []).append(unit)
    result = []
    for item, row in zip(items, out):
        if item["product"] not in tracked:
            result.append(row)
            continue
        mine = [_unit_row(row, info[serial], 1) for serial in counted_on.get(item["name"], []) if serial in info]
        mine += [_unit_row(row, unit) for unit in waiting.pop((item["product"], item["warehouse"], item["location"] or ""), [])]
        result.extend(mine or [row])
    return result


def _warehouse_label(name) -> str:
    return frappe.db.get_value("ERP Warehouse", name, "warehouse_name") or name or ""


def valuation(product_id: str, key: tuple):
    """这一笔差异按什么单价落账。

    先看**这个位置现在的成本**；位置是空的 / 新位置（没成本基准）就退回**商品参考成本**
    （`ERP Product.default_cost`，且是"设过的"才算）—— 官方 `Stock Reconciliation` 就是
    "数量差要带一个估值率"，不然数出来的货会记成 0 成本（用户 2026-09-23 在实验室实测到：
    盘点调增 80 瓶，成本 0）。两处都没有才留空，那种情况在体检里会被列成"有货但成本未知"。
    """
    unit = stock_service.current_balance(*key).unit
    if unit is not None:
        return unit
    row = frappe.db.get_value(PRODUCT, product_id, ["default_cost", "cost_set"], as_dict=True) or {}
    if row.get("cost_set") and row.get("default_cost") is not None:
        return Decimal(str(row["default_cost"]))
    return None


def _warehouse_of(value, fallback, company) -> str:
    """上传导入那一列里的仓库名 → 真实的仓库（只认这份盘点所属公司已有的仓）。"""
    return warehouses.required(value or fallback, company=company)


def list_items(*, count: str, q=None, filter="all", limit=50, start=0, _locked=False) -> list[dict]:
    """快照行。`filter`: all / uncounted / counted / differences（旧线上那三个筛选项）。"""
    guard.require("count.read")
    task = _get(count)
    rows = frappe.get_list(
        ITEM, filters={"count": count},
        fields=["name", "product", "product_name", "code", "barcode", "unit", "warehouse",
                "location", "row_origin", "book_qty", "counted", "counted_qty", "difference",
                "last_counted_at", "last_counted_by", "basis_qty", "basis_set", "stock_token"],
        order_by="product_name asc, name asc", limit_page_length=0,
    )
    all_entries = list_entries(count=count, limit=0)
    by_item = {}
    for entry in all_entries:
        if not entry["voided"]:
            by_item.setdefault(entry["item"], []).append(entry)
    # 以前建的盘点，只有二维码的商品那一格存成了空：显示时从商品上补（录制问题 #23）。
    missing = {row.product for row in rows if not row.barcode}
    shown = {p["name"]: product_contracts.shown_code(p) for p in frappe.get_all(
        PRODUCT, filters={"name": ["in", list(missing) or [""]]}, fields=["name", "barcode", "qr_payload"])}
    # 商品类别（多人分区盘点按「仓库 / 类别」分工）：跟着商品主档走，没设就是空。
    categories = {p.name: p.category_id for p in frappe.get_all(
        PRODUCT, filters={"name": ["in", list({row.product for row in rows}) or [""]]},
        fields=["name", "category_id"])}
    for row in rows:
        row.barcode = row.barcode or shown.get(row.product) or None
        category = categories.get(row.product)
        row.category = "" if category in (None, "", 0) else str(category)
    wanted = (q or "").strip().lower()
    out = []
    tokens = {}
    can_view_costs = guard.cost_visible()
    costs = {r.name: r for r in frappe.get_all(ITEM, filters={"count": count},
             fields=["name", "basis_unit_cost", "basis_cost_known"])} if can_view_costs else {}
    for row in rows:
        row.book_qty = row.basis_qty if row.basis_set else row.book_qty
        entries = by_item.get(row.name, [])
        row.actual_positions = " / ".join(
            f"{_warehouse_label(e['warehouse'])} · {e.get('location') or '—'} ({e['qty']:g})"
            for e in entries)
        row.location_difference = any(
            (e['warehouse'], e.get('location') or '') != (row.warehouse, row.location or '')
            for e in entries)
        if row.product not in tokens:
            tokens[row.product] = count_state.stock_token(row.product, for_update=_locked)
        row.stock_changed = bool(task.status == "Active" and row.counted and (not row.stock_token or row.stock_token != tokens[row.product]))
        if wanted and wanted not in " ".join(
                str(row.get(key) or "").lower()
                for key in ("product_name", "code", "barcode")).lower():
            continue
        counted = bool(row["counted"])
        if not counted:
            row.counted_qty = row.difference = None
        if can_view_costs:
            cost = costs.get(row.name)
            row.difference_value = (flt(_qty(row.difference) * _qty(cost.basis_unit_cost), 2)
                                    if counted and cost and cost.basis_cost_known else None)
        if filter == "uncounted" and counted:
            continue
        if filter == "counted" and not counted:
            continue
        if filter == "differences" and (not counted or not (row["difference"] or row.location_difference)):
            continue
        if filter == "locations" and not row.location_difference:
            continue
        out.append(dict(row, warehouse_name=_warehouse_label(row.warehouse)))
    start = int(start or 0)
    size = _size(limit)
    return out[start:] if size is None else out[start:start + size]


def items_total(*, count: str, q=None, filter="all") -> int:
    guard.require("count.read")
    return len(list_items(count=count, q=q, filter=filter, limit=0))


def close_count(name: str, review_token=None) -> dict:
    """The single finish action applies reviewed differences and closes atomically."""
    return post_count(name, review_token=review_token)


def review_count(name, *, _locked=False):
    guard.require("count.read")
    task = get_count(name)
    items = list_items(count=name, limit=0, _locked=_locked)
    entries = list_entries(count=name, limit=0)
    state = [task, items, entries]
    token = hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()
    return {"task": task, "items": items, "entries": entries, "review_token": token,
            # 进行中能不能打核对表（账面 / 实点 / 差异）：有库存管理权（用户 10-29 批复 #1）
            "can_check": guard.has("stock.manage"),
            "can_write": guard.has("count.write"),
            "can_view_costs": guard.cost_visible(),
            "conflicts": [r["name"] for r in items if r["stock_changed"]],
            "location_total": sum(bool(r["location_difference"]) for r in items)}


def post_count(name: str, review_token=None) -> dict:
    guard.require("count.write")
    count_state.lock_task(name, active=False)
    doc = _get(name)
    if doc.status == "Posted":
        return {"name": name, "status": "Posted", "adjustments": [], "deduped": True}
    if doc.status != "Active":
        frappe.throw(_(CLOSED))
    rows = list_items(count=name, limit=0)
    stock_service.lock_products([r["product"] for r in rows])
    review = review_count(name, _locked=True)
    if review_token and review_token != review["review_token"]:
        frappe.throw(_("The stock count changed. Review the differences again."))
    if review["conflicts"]:
        frappe.throw(_("Stock changed during counting. Recount the affected products."))
    rows = [r for r in review["items"] if r["counted"]]
    if not rows:
        frappe.throw(_("Count at least one item before finishing"))
    # Remove the checked book quantity at its source, add observations at their actual
    # positions. Aggregate first so A -> B and B -> A cannot overwrite one another.
    changes, incoming, costs = {}, {}, {}
    by_name = {r["name"]: r for r in rows}
    moved_products = {r["product"] for r in rows if r["location_difference"]}
    operations = []
    for row in rows:
        key = (row["product"], row["warehouse"], row["location"] or "")
        # 估值：位置现有成本 → 商品参考成本（见 `valuation`）。
        cost = valuation(row["product"], key)
        costs[row["name"]] = cost
        if row["product"] in moved_products:
            # Remove all observed source quantities before adding destinations, carrying
            # their costs. Netting quantities alone would lose valuation on equal swaps.
            operations.append((key, -_qty(row["book_qty"]), cost))
        else:
            changes[key] = changes.get(key, Decimal(0)) - _qty(row["book_qty"])
    for entry in review["entries"]:
        if entry["voided"] or entry["item"] not in by_name:
            continue
        key = (entry["product"], entry["warehouse"], entry["location"] or "")
        amount, cost = _qty(entry["qty"]), costs[entry["item"]]
        if entry["product"] in moved_products:
            incoming.setdefault(key, []).append((amount, cost))
        else:
            changes[key] = changes.get(key, Decimal(0)) + amount
    # Position transfers keep separate source-cost receipts, including mixed/unknown costs.
    for key in sorted(incoming):
        for quantity, cost in incoming[key]:
            operations.append((key, quantity, cost))
    for key, delta in sorted(changes.items()):
        operations.append((key, delta, valuation(key[0], key)))
    # 按台：盘到的车原来在别处（别的仓，或这个仓里没盘的那一行）时，原处减 1；实际处的加 1 已在上面的行差异里
    for key in _unit_sources(review["entries"], {(r["product"], r["warehouse"], r["location"] or "") for r in rows}):
        operations.append((key, Decimal(-1), valuation(key[0], key)))
    # **盘点差异也开入库/出库单据**（用户 2026-09-23：这些编号要和商品库存、出入库、
    # 收发存每一笔记录联动；MR.ERP 的调整/盘点模块 `stktranmod` 同样是把带有自己编号的
    # 单据写进库存卡，旧线上盘点差异也发 IR/IS 号并落一张出入库单）。
    #
    # 因此这里不再直接写"没有单据号的 count 流水"，而是走 `create_entry`：
    # 单据拿系统发的 IR/IS 号，流水带着这个号（收发存/库存明细都显示得出），
    # 单据本身在出入库页能按号查到，`ref_type/ref_id` 指回这份盘点。
    # `commit=False`：整批盘点一个事务，失败不留半截（与过账前的行为一致）。
    _reactivate(name)
    applied = []
    for index, (key, delta, unit_cost) in enumerate(operations):
        if not delta:
            continue
        created = stock_documents.create_entry(
            direction="in" if delta > 0 else "out", posting_date=str(frappe.utils.nowdate()),
            warehouse=key[1],
            lines=[{"product": key[0], "qty": abs(delta), "location": key[2],
                    "unit_cost": unit_cost}],
            reason=stock_documents.adjustment_note(doc.title or name), ref_type="count", ref_id=name,
            dedupe_key=f"count:{name}:{index}", submit=True, commit=False,
            # 盘点走自己的编号定义（AD 段），与入库 IR / 出库 IS 分开。
            numbering_direction="stocktake")
        applied.append({"product": key[0], "warehouse": key[1], "location": key[2],
                        "delta": float(delta), "entry": created["name"], "doc_no": created["doc_no"]})
    _move_counted_units(name, review["entries"])
    frappe.db.set_value(COUNT, name, {"status": "Posted", "closed_at": now(),
                        "closed_by": frappe.session.user, "posted_at": now(),
                        "posted_by": frappe.session.user}, update_modified=False)
    # 接了 Express 的公司：整份盘点轧差写一张盘点调整 TK（出入库第一期 B2，验收 10-25）
    from pearnly_erp_portal.express import push as express_push

    express_push.queue(COUNT, name)
    count_live.changed(name)
    return {"name": name, "status": "Posted", "adjustments": applied, "deduped": False}


REACTIVATED_NOTE = "Reactivated by stock count {0}"


def _reactivate(count) -> list:
    """停用商品在这份盘点里盘到了（实盘 > 0）：过账时自动重新启用，商品时间线记一条「盘点启用 · 盘点单号」（多仓评估问 8）。"""
    rows = frappe.get_all(ITEM, filters={"count": count, "row_origin": REACTIVATED, "counted": 1,  # bounded: 一份盘点的行
                                         "counted_qty": [">", 0]}, pluck="product", limit_page_length=0)
    done = []
    for product in sorted(set(rows)):
        if frappe.db.get_value(PRODUCT, product, "is_active"):
            continue
        frappe.db.set_value(PRODUCT, product, "is_active", 1)
        frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": PRODUCT,
                        "reference_name": product, "content": _(REACTIVATED_NOTE).format(count)}).insert(ignore_permissions=True)
        done.append(product)
    return done


def _unit_sources(entries, counted_rows) -> list:
    """盘到的车原来所在的 (商品, 仓, 库位)：车不在它被记下的地方、原处又不在这份盘点已盘的行里（那一行自己的差异会算）
    → 要在原处减 1，数量与台数两边才对得上。一台一个。"""
    from pearnly_erp_portal.inventory import serials

    out = []
    for entry in entries:
        if entry.get("voided") or not entry.get("serial"):
            continue
        unit = frappe.db.get_value(serials.DOCTYPE, entry["serial"], ["product", "warehouse", "location", "status"], as_dict=True)
        if not unit or unit.status != serials.IN_STOCK:
            continue
        source = (unit.product, unit.warehouse, unit.location or "")
        if (unit.warehouse, unit.location or "") != (entry["warehouse"], entry.get("location") or "") and source not in counted_rows:
            out.append(source)
    return out


def _move_counted_units(count, entries):
    """按台：盘到的台记在实际盘到的仓 / 库位（与数量的仓位调整同一次过账）；没盘到的台不动（主会话 10-26）。"""
    from pearnly_erp_portal.inventory import serials

    for entry in entries:
        if entry.get("voided") or not entry.get("serial"):
            continue
        unit = frappe.get_doc(serials.DOCTYPE, entry["serial"], for_update=True)
        place = (entry["warehouse"], entry.get("location") or "")
        if unit.status != serials.IN_STOCK or (unit.warehouse, unit.location or "") == place:
            continue
        unit.update({"warehouse": place[0], "location": place[1]})
        unit.save(ignore_permissions=True)
        serials._event(unit, "count", serials.COUNT_REF, count, frappe.utils.nowdate())


def delete_count(name: str) -> dict:
    guard.require("count.write")
    frappe.db.sql("select name from `tabERP Stock Count` where name=%s for update", (name,))
    doc = _get(name)
    if doc.status != "Active":
        frappe.throw(_(CLOSED))
    frappe.db.delete(ENTRY, {"count": doc.name})
    frappe.db.delete(ITEM, {"count": doc.name})
    count_state.write_document(doc, "delete")
    return {"ok": True}


# ── 盘点记录（追加式） ──────────────────────────────────────────────────────


def _recount_item(item_name: str, count_name: str) -> None:
    """把这一行的实盘合计与任务上的三个数一起算准。

    实盘合计 = **未撤销**记录之和；一条记录都没有时留空（未盘点 ≠ 0）。差异用完整文档
    保存（`db.set_value` 会把 None 当"不改"，清空就清不掉）。
    """
    rows = frappe.get_list(ENTRY, filters={"item": item_name, "voided": 0},
                           fields=["qty", "counted_by", "counted_at"], order_by="counted_at desc",
                           limit_page_length=0)
    doc = frappe.get_doc(ITEM, item_name)
    # Frappe 的 Float 列存不下 NULL（默认 0），所以"数过没有"另用一个标记记 ——
    # 否则"没数过"和"数出来是 0"在库里是同一个数字。
    doc.counted = 1 if rows else 0
    if rows:
        doc.counted_qty = flt(sum(flt(row["qty"], 3) for row in rows), 3)
        doc.last_counted_at = rows[0]["counted_at"]
        doc.last_counted_by = rows[0]["counted_by"]
    else:
        doc.counted_qty = 0
        doc.last_counted_at = None
        doc.last_counted_by = None
    doc.difference = (flt(doc.counted_qty, 3) - flt(doc.basis_qty if doc.basis_set else doc.book_qty, 3)) if doc.counted else 0
    if not rows:
        doc.basis_set = 0
        doc.stock_token = ""
    count_state.write_document(doc, "save")

    _refresh_totals(count_name)
    count_live.changed(count_name)


PHOTO_BYTES = 4 * 1024 * 1024  # 手机盘点页先在浏览器里压缩（长边 2400、JPEG 0.92，同采购上传），压完的一张远小于它


FILE_URL = r"/(private/)?files/\S+"  # 本站文件地址；其余按照片数据（base64，可带 data: 头）认


def _store_photos(values) -> list:
    """盘点照片先存好、回登记 File 名（还没挂到记录上）。在拿盘点锁**之前**调：交原件层是网络上传，不能压在数据库锁里（验收 10-26）。

    - 桌面端给已上传的地址（上传框按原件层存，`recognition.originals` 方向 ``stock_count``）：只认本人刚上传、私有、没挂过别处的 File；
    - 手机盘点页给照片数据（页面已压缩成 JPEG）：交原件层存，站点没配原件层照旧存本机私有文件。
    地址只认本站文件地址的样子：JPEG 的 base64 固定以「/9j/」开头，按首字符「/」猜会把照片当成地址（验收 10-26 实测一律存不上）。"""
    import base64
    import binascii
    import hashlib
    import re

    from pearnly_core.execution.errors import Deferred
    from pearnly_core.originals import registered

    values = [str(value).strip() for value in (values or []) if str(value or "").strip()]
    if len(values) > PHOTO_LIMIT:
        frappe.throw(_(PHOTO_LIMIT_ERROR).format(PHOTO_LIMIT))
    names = []
    for text in values:
        try:
            if re.fullmatch(FILE_URL, text):
                name = frappe.db.get_value("File", {"file_url": text, "owner": frappe.session.user, "is_private": 1,
                                                   "attached_to_name": ["is", "not set"]}, "name", order_by="creation desc")
                if not name:
                    frappe.throw(_("That photo could not be read"))
                names.append(name)
                continue
            if text.startswith("data:"):
                text = text.split(",", 1)[1] if "," in text else ""
            raw = base64.b64decode(text, validate=True)
            if not raw or len(raw) > PHOTO_BYTES:
                raise ValueError("photo too large")
            digest = hashlib.sha256(raw).hexdigest()
            filename = f"stocktake-{digest[:12]}.jpg"
            seed = f"{frappe.local.site}|{frappe.session.user}|stock_count|{digest}"
            try:
                stored = registered.put("ERO-" + hashlib.sha256(seed.encode()).hexdigest()[:48], filename, raw)
            except Deferred:
                frappe.throw(_("The file could not be saved. Please try again shortly."))
            if isinstance(stored, dict):
                raise ValueError("photo rejected")
            if stored is None:  # 这个站点没配原件层：带字节建私有 File（旧的 `file_manager.save_file` 在 Frappe 16
                # 会把写好的图片按文本读回去再去 EXIF，图片一律报错存不上；同 `recognition.originals.upload` 的兜底）
                stored = frappe.get_doc({"doctype": "File", "file_name": filename, "content": raw,
                                         "is_private": 1}).insert(ignore_permissions=True)
            names.append(stored.name)
        except (ValueError, binascii.Error):
            frappe.throw(_("That photo could not be read"))
    return names


def _save_photos(entry: str, names) -> int:
    """把 :func:`_store_photos` 存好的照片挂到这条记录上（最多 5 张，与库存单附件同一套，主会话 10-26）。在盘点锁里调。"""
    names = [name for name in dict.fromkeys(names or []) if name]
    if not names:
        return 0
    existing = frappe.db.count("File", {"attached_to_doctype": ENTRY, "attached_to_name": entry})
    if existing + len(names) > PHOTO_LIMIT:
        frappe.throw(_(PHOTO_LIMIT_ERROR).format(PHOTO_LIMIT))
    for name in names:
        frappe.db.set_value("File", name, {"attached_to_doctype": ENTRY, "attached_to_name": entry})
    frappe.db.set_value(ENTRY, entry, "photo_total",
                        frappe.db.count("File", {"attached_to_doctype": ENTRY,
                                                 "attached_to_name": entry}),
                        update_modified=False)
    return len(names)


def _record_revision(task, product, token=None):
    stock_service.lock_products([product])
    current = count_state.stock_token(product, for_update=True)
    if token and token != current:
        frappe.throw(_("Stock changed during counting. Recount the affected products."))
    previous = frappe.get_all(ITEM, filters={"count": task, "product": product, "counted": 1},
                              fields=["stock_token"])
    if any(row.stock_token and row.stock_token != current for row in previous):
        frappe.throw(_("Stock changed during counting. Recount the affected products."))
    return current


def lookup_count(*, count, code=None, product=None):
    """查一个码/一个商品，准备录这一条：书上的行 + **别处还有多少**。

    `elsewhere` 是给录入弹窗用的（用户 2026-09-23："盘点时不提示'这件货的账在别处'——
    它默默记进盘点单的仓库"）：这件货在别的仓/仓位还有结存时，界面上要摆出来，让人自己决定
    是继续（确实是新仓）还是改盘点仓库。不算"错"，但不说清楚就会看着像"库存凭空多一条"。
    """
    guard.require("count.read")
    task = _get(count)
    company = count_company(count)
    unit = None
    if code and not product:
        matches = products_of_code(code, company)
        if len(matches) > 1:
            # 存量里撞了的码：几件都列出来让人选（再带 product 查一次），不默认挑一个
            return {"candidates": [{key: row.get(key) for key in ("name", "product_name", "code", "internal_code", "barcode",
                                                                  "matched_by")} for row in matches]}
    found = product_of_code(code, company) if code and not product else frappe.db.get_value(
        PRODUCT, {"name": product}, "*", as_dict=True)
    if not found and code:
        unit = unit_of_code(code, company)  # 按台管理：扫到的是车架号
        found = frappe.db.get_value(PRODUCT, {"name": unit.product, "is_active": 1}, "*", as_dict=True) if unit else None
    if not found and code and not product:
        # 扫到系统没有的码是盘点里的正常一步（下一步当场新建物料），照官方 POS 扫不到码的做法不弹提示框：
        # 回一个结果，录入弹窗停相机、把「新建物料」摆在眼前（岳父 11-01 手机上被提示框挡住、按钮在画面下面）
        return {"not_found": True}
    if not found:
        frappe.throw(_(CODE_NOT_FOUND))
    rows = [r for r in list_items(count=count, limit=0) if r['product'] == found['name']]
    return {"product": {**{key: found.get(key) for key in ("name", "product_name", "barcode", "base_unit", "unit", "serial_kind",
                                                           "is_active")},
                        "code": product_contracts.count_code(found)},
            "rows": rows,
            "elsewhere": elsewhere_positions(found["name"],
                                             {(r["warehouse"], r["location"] or "") for r in rows}),
            "recorded": recorded_entries(count, [r["name"] for r in rows]),
            "stock_token": count_state.stock_token(found['name']),
            "warehouse_name": _warehouse_label(task.warehouse),
            "track_serial": per_unit(found, company), "unit": dict(unit) if unit else None,
            "units": units_here(count, found["name"]) if per_unit(found, company) else []}


def units_here(count, product) -> list:
    """按台管理的商品：这份盘点的仓里在库的台，标出这份盘点里盘到了没有（录入弹窗的车辆清单）。"""
    from pearnly_erp_portal.inventory import serials

    warehouse = frappe.db.get_value(COUNT, count, "warehouse")
    counted = set(frappe.get_all(ENTRY, filters={"count": count, "product": product, "voided": 0,  # bounded: 一份盘点一个车型
                                                 "serial": ["is", "set"]}, pluck="serial", limit_page_length=0))
    rows = frappe.get_all(serials.DOCTYPE, filters={"product": product, "warehouse": warehouse, "status": serials.IN_STOCK},
                          fields=["name", "serial_no", "secondary_no", "color", "location"],  # bounded: 一个仓一个车型的在库台
                          order_by="serial_no asc", limit_page_length=500)
    return [{**row, "counted": row.name in counted} for row in rows]


def recorded_entries(count: str, items: list) -> list:
    """这件货在本次盘点里**已经记过**的（谁、在哪、多少）：多人分区盘点时扫到别人盘过的货，
    当场看得见，免得同一架货被两个人各数一遍（用户 2026-10-15 多人协作口径）。"""
    out = []
    for entry in list_entries(count=count, limit=0):
        if entry["item"] in items and not entry["voided"]:
            out.append({"counted_by": entry["counted_by_name"],
                        "warehouse_name": entry["warehouse_name"], "location": entry.get("location") or "",
                        "qty": entry["qty"]})
    return out


def elsewhere_positions(product_id: str, here: set) -> list:
    """这件货在**别的**位置上的结存（`here` 是这次盘点已经看过的那几个位置）。"""
    # ⚠️ 结存表里**没有** `unit` 列（成本/数量才是它的字段）：单位从商品主档取
    # （库存单位优先，与 `stock.resolve_factor` 同一个口径）。
    product = frappe.db.get_value(PRODUCT, product_id, ["base_unit", "unit"], as_dict=True) or {}
    unit = product.get("base_unit") or product.get("unit") or ""
    out = []
    for balance in frappe.db.get_all(BALANCE, filters={"product": product_id, **warehouses.scope_filter()},
            fields=["warehouse", "location", "qty_on_hand"],
            order_by="qty_on_hand desc, warehouse asc"):
        if (balance.warehouse, balance.location or "") in here:
            continue
        if not flt(balance.qty_on_hand, 3):
            continue
        out.append({"warehouse_name": _warehouse_label(balance.warehouse),
                    "location": balance.location or "", "qty": float(balance.qty_on_hand),
                    "unit": unit})
    return out


def add_entry(*, count, qty, item=None, code=None, product=None, warehouse=None, location=None,
              note=None, request_id=None, photos=None, source="Desk", stock_token=None, serial=None) -> dict:
    """A record belongs to a book row; warehouse/location describe where it was found."""
    guard.require("count.write")
    photos = _store_photos(photos)  # 拿锁之前存好（原件层是网络上传）
    count_state.lock_task(count)
    task = _get(count)
    quantity = _qty(qty)
    if quantity < 0:
        frappe.throw(_(QUANTITY_REQUIRED))
    unit = None
    if item:
        row = _item(item)
        if row['count'] != count:
            frappe.throw(_(ITEM_NOT_FOUND))
        product_id = row['product']
        if code or serial or per_unit(frappe.db.get_value(PRODUCT, product_id, ["track_serial"], as_dict=True),
                                      count_company(count)):
            found, unit = _scanned(code, product_id, serial, count_company(count))
            if found["name"] != product_id:
                frappe.throw(_(CODE_NOT_FOUND))
        # 取仓按这份盘点的公司：手机上员工的当前公司可能不是盘点那家（10-30）
        actual_warehouse = warehouses.required(warehouse if warehouse is not None else row['warehouse'],
                                               company=count_company(count))
        actual_location = positions.clean_location(location if location is not None else row['location'])
        if (actual_warehouse, actual_location) != (row['warehouse'], row['location'] or ''):
            positions.check(actual_warehouse, actual_location)  # 记在账面那个位置照常（清单之前的旧库位也能盘）
    else:
        found, unit = _scanned(code, product, serial, count_company(count))
        product_id = found['name']
        actual_warehouse = warehouses.required(warehouse or task.warehouse, company=count_company(count))
        actual_location = positions.check(actual_warehouse, location)
        item = _insert_item(count, found, actual_warehouse,
                            stock_service.current_balance(product_id, actual_warehouse, actual_location).qty,
                            actual_location)
        row = _item(item)
    if request_id:
        seen = frappe.db.get_value(ENTRY, {"request_id": request_id}, "*", as_dict=True)
        if seen:
            if (seen['count'], seen['item'], seen['warehouse'], seen['location'] or '',
                flt(seen['qty'], 3), seen['note'] or '') != (
                    count, item, actual_warehouse, actual_location, flt(quantity, 3), note or ''):
                frappe.throw(_(REQUEST_CONFLICT))
            return {"entry": seen['name'], "item": item, "qty": seen['qty'], "deduped": True}
    if unit:
        # 按台：一台记一条、数量就是 1；同一份盘点里同一台不重复记。别的仓 / 库位的车也能记在实际盘到的地方：
        # 过账时原处减 1、实际处加 1，台跟着走（`_unit_sources`，主会话 10-26）
        quantity = Decimal(1)
        if frappe.db.exists(ENTRY, {"count": count, "serial": unit.name, "voided": 0}):
            frappe.throw(_(UNIT_COUNTED) + f": {unit.serial_no}")
    token = _record_revision(count, product_id, stock_token)
    if not row['counted']:
        basis = stock_service.current_balance(product_id, row['warehouse'], row['location'])
        frappe.db.set_value(ITEM, item, {"basis_qty": basis.qty, "basis_set": 1,
                                        "basis_unit_cost": basis.unit or 0,
                                        "basis_cost_known": int(basis.unit is not None),
                                        "stock_token": token}, update_modified=False)
    entry = frappe.get_doc({"doctype": ENTRY, "count": count, "item": item, "product": product_id,
        "warehouse": actual_warehouse, "location": actual_location, "qty": flt(quantity, 3),
        "request_id": request_id, "note": note, "source": source, "serial": unit.name if unit else None,
        "counted_by": frappe.session.user, "counted_at": now(), "version": 1})
    count_state.write_document(entry, "insert")
    _save_photos(entry.name, photos)
    _recount_item(item, count)
    return {"entry": entry.name, "item": item, "qty": flt(quantity, 3), "deduped": False}


def edit_entry(*, entry, qty=None, voided=None, version=None, photos=None, location=None,
               warehouse=None, note=None, stock_token=None) -> dict:
    guard.require("count.write")
    photos = _store_photos(photos)  # 拿锁之前存好（原件层是网络上传）
    parent = frappe.db.get_value(ENTRY, entry, "count")
    count_state.lock_task(parent)
    doc = frappe.get_doc(ENTRY, entry)
    if version is not None and int(version) != int(doc.version):
        frappe.throw(_(VERSION_STALE))
    if voided is None or voided not in (True, 1, "1", "true"):
        _record_revision(parent, doc.product, stock_token)
    if qty is not None:
        quantity = _qty(qty)
        if quantity < 0 or (doc.get("serial") and quantity != 1):
            frappe.throw(_(QUANTITY_REQUIRED))
        doc.qty = flt(quantity, 3)
    if voided is not None:
        restoring = doc.voided and voided not in (True, 1, "1", "true")
        if restoring and doc.get("serial") and frappe.db.exists(ENTRY, {"count": parent, "serial": doc.serial, "voided": 0,
                                                                          "name": ["!=", doc.name]}):
            frappe.throw(_(UNIT_COUNTED))  # 恢复作废的那条之前，这一台已经另记过了
        doc.voided = 1 if voided in (True, 1, "1", "true") else 0
    if location is not None:
        place = warehouses.required(warehouse, company=count_company(parent)) if warehouse is not None else doc.warehouse
        before = (doc.warehouse, doc.location or "")
        doc.location = positions.clean_location(location)
        if (place, doc.location) != before:
            positions.check(place, doc.location)
    if warehouse is not None:
        doc.warehouse = warehouses.required(warehouse, company=count_company(parent))
    if note is not None:
        doc.note = note
    doc.version = int(doc.version) + 1
    doc.counted_by = frappe.session.user
    doc.counted_at = now()
    count_state.write_document(doc, "save")
    if photos:
        _save_photos(doc.name, photos)
    _recount_item(doc.item, parent)
    return {"entry": doc.name, "item": doc.item, "voided": bool(doc.voided),
            "qty": flt(doc.qty, 3), "version": doc.version}


def recount_product(*, count, product):
    """Explicit recount preserves old observations as voided audit rows."""
    guard.require("count.write")
    count_state.lock_task(count)
    stock_service.lock_products([product])
    items = frappe.get_all(ITEM, filters={"count": count, "product": product}, pluck="name")
    for row in frappe.get_all(ENTRY, filters={"count": count, "product": product, "voided": 0},
                              fields=["name", "version"]):
        edit_entry(entry=row.name, voided=1, version=row.version)
    for item in items:
        _recount_item(item, count)
    return {"ok": True}


def list_entries(*, count, item=None, limit=50, start=0) -> list[dict]:
    guard.require("count.read")
    _get(count)
    filters = {"count": count}
    if item:
        filters["item"] = item
    result = frappe.get_list(
        ENTRY, filters=filters,
        fields=["name", "item", "product", "warehouse", "location", "qty", "voided", "version",
                "photo_total", "note", "counted_by", "counted_at", "source", "serial"],
        order_by="counted_at desc, name desc",
        limit_start=int(start or 0), limit_page_length=_size(limit),
    )
    # Export the frozen item labels, not today's possibly renamed product master.
    items = {row.name: row for row in frappe.get_list(
        ITEM, filters={"count": count}, fields=["name", "product_name", "code", "unit",
                                               "warehouse", "location"], limit_page_length=0)}
    numbers = dict(frappe.get_all("ERP Serial No", filters={"name": ["in", [r.serial for r in result if r.serial] or [""]]},
                                  fields=["name", "serial_no"], as_list=True,  # bounded: 这一页记录上的台
                                  limit_page_length=len(result) or 1))
    # 盘点人显示姓名（用户 10-30 个人信息加姓名；没填过的老账号是用户名），页面、导出、分区都用这一格
    people = person_names.display_names(row.counted_by for row in result)
    return [dict(row, warehouse_name=_warehouse_label(row.warehouse), serial_no=numbers.get(row.serial) or "",
                 counted_by_name=people.get(row.counted_by) or "",
                 product_name=items[row.item].product_name, code=items[row.item].code,
                 unit=items[row.item].unit,
                 book_warehouse_name=_warehouse_label(items[row.item].warehouse),
                 book_location=items[row.item].location) for row in result]


def entries_total(*, count, item=None) -> int:
    """记录的条数（列表分页要用）。"""
    guard.require("count.read")
    _get(count)
    filters = {"count": count}
    if item:
        filters["item"] = item
    return frappe.db.count(ENTRY, filters)


def entry_photos(entry: str) -> list[dict]:
    guard.require("count.read")
    _get(frappe.db.get_value(ENTRY, entry, "count"))
    return frappe.get_list("File", filters={"attached_to_doctype": ENTRY,
                                            "attached_to_name": entry},
                           fields=["name", "file_url", "file_name"], order_by="creation asc",
                           limit_page_length=PHOTO_LIMIT)


def item_photos(*, count, item) -> list[dict]:
    """该商品在**本任务里所有记录**的照片（记录弹窗表格下面那一片，含作废记录）。
    带上记录自己的位置与时间，看图片时知道是哪一次、在哪儿拍的。"""
    guard.require("count.read")
    _get(count)
    entries = list_entries(count=count, item=item, limit=0)
    if not entries:
        return []
    by_entry = {row["name"]: row for row in entries}
    photos = frappe.get_list(
        "File", filters={"attached_to_doctype": ENTRY,
                         "attached_to_name": ("in", list(by_entry))},
        fields=["name", "file_url", "file_name", "attached_to_name"],
        order_by="creation asc", limit_page_length=0)
    return [dict(photo, entry=photo["attached_to_name"],
                 qty=by_entry[photo["attached_to_name"]].get("qty"),
                 warehouse_name=by_entry[photo["attached_to_name"]].get("warehouse_name"),
                 location=by_entry[photo["attached_to_name"]].get("location"),
                 voided=by_entry[photo["attached_to_name"]].get("voided"),
                 counted_at=by_entry[photo["attached_to_name"]].get("counted_at"))
            for photo in photos]
