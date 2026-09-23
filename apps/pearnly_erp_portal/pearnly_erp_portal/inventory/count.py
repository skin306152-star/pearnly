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

from pearnly_erp_portal.access import guard, rules
from pearnly_erp_portal.inventory import stock as stock_service
from pearnly_erp_portal.inventory import count_state, documents as stock_documents, positions, warehouses
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


# ── 取数 ────────────────────────────────────────────────────────────────────


def _get(name) -> "frappe.Document":
    if not name or not frappe.db.exists(COUNT, name):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    return frappe.get_doc(COUNT, name)


def _item(name: str) -> dict:
    row = frappe.db.get_value(ITEM, name, "*", as_dict=True)
    if not row:
        frappe.throw(_(ITEM_NOT_FOUND), frappe.DoesNotExistError)
    return row


def _qty(value, *, field="Quantity") -> Decimal:
    try:
        number = Decimal(str(value).strip())
        if not number.is_finite():
            raise ValueError("non-finite quantity")
        return number
    except Exception:
        frappe.throw(_("Invalid value: {0}").format(field))


def product_of_code(code: str) -> dict | None:
    """扫到的码 → 商品。编码/条码/二维码内容都认（与商品页扫码同一套）。"""
    wanted = str(code or "").strip()
    if not wanted:
        return None
    for field in ("code", "barcode", "qr_payload"):
        row = frappe.db.get_value(PRODUCT, {field: wanted, "is_active": 1}, "*", as_dict=True)
        if row:
            return row
    return None


# ── 任务 ────────────────────────────────────────────────────────────────────


def create_count(*, title, warehouse=None, notes=None, snapshot=True) -> dict:
    """开一份盘点。

    `snapshot=True`（盘点在库）把当前结存抄成快照行；`snapshot=False`（上传导入）
    只建一张空任务，行由上传的表带进来（`apply_import`）。
    """
    guard.require("stock.write")
    name = (title or "").strip()
    if not name:
        frappe.throw(_(TITLE_REQUIRED))
    warehouse = warehouses.required(warehouse)
    doc = frappe.get_doc({"doctype": COUNT, "title": name, "warehouse": warehouse,
                          "notes": notes, "status": "Active"})
    count_state.write_document(doc, "insert")
    if not snapshot:
            return {"name": doc.name, "rows": 0}
    rows = frappe.get_list(BALANCE, filters={"warehouse": warehouse},
                           fields=["product", "qty_on_hand", "location"], limit_page_length=0)
    products = {p["name"]: p for p in frappe.get_list(
        PRODUCT, filters={"is_active": 1, "name": ["in", [r["product"] for r in rows] or [""]]},
        fields=["name", "product_name", "code", "barcode", "unit", "base_unit"],
        limit_page_length=0)}
    created = 0
    for row in rows:
        product = products.get(row["product"])
        if not product:
            continue
        qty = flt(row["qty_on_hand"], 3)
        _insert_item(doc.name, product, warehouse, qty, row.get("location"))
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
    guard.require("stock.write")
    count_state.lock_task(count)
    task = _get(count)
    if task.status != "Active":
        frappe.throw(_(CLOSED))
    applied = counted = 0
    problems = []
    for number, row in enumerate(rows, start=2):
        product = _product_of_row(row)
        if not product:
            problems.append({"row": number, "reason": "code_not_found"})
            continue
        try:
            warehouse = _warehouse_of(row.get("warehouse"),
                                      task.warehouse or warehouses.required())
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


def _product_of_row(row) -> dict | None:
    for code in ((row.get("product_code") or "").strip(), (row.get("barcode") or "").strip()):
        if code:
            found = product_of_code(code)
            if found:
                return found
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


def _insert_item(count: str, product: dict, warehouse: str, book_qty, location="") -> str:
    """一行 = **商品 × 仓库 × 库位**（与旧项目 `services/stocktake/schema.py` 的唯一键一致）。

    库位是行的身份的一部分，不是记录上的备注：同一个商品放在两个货架上就是两行、
    两个账面、两个差异。少了这一层，"每个仓位的差异"根本算不出来。
    """
    location = positions.clean_location(location)
    existing = frappe.db.get_value(ITEM, {"count": count, "product": product["name"],
                                          "warehouse": warehouse, "location": location}, "name")
    if existing:
        return existing
    doc = frappe.get_doc({
        "doctype": ITEM, "count": count, "product": product["name"],
        "product_name": product.get("product_name"), "code": product.get("code"),
        # 商品那边的"码"是一格（条码与二维码合并）：只有二维码的商品，盘点表这一列
        # 也要带上它，否则盘点员拿着这张表扫不出来（匹配那侧本来就三列一起查）。
        "barcode": product_contracts.shown_code(product) or None,
        "unit": (product.get("base_unit") or product.get("unit") or "") or None,
        "warehouse": warehouse, "location": location,
        "book_qty": flt(book_qty, 3), "counted": 0,
    })
    count_state.write_document(doc, "insert")
    return doc.name


def item_of_product(*, count: str, product: str, location="") -> dict | None:
    """这份盘点里某个商品在某个库位那一行（库位给了就精确匹配）。"""
    guard.require("stock.read")
    filters = {"count": count, "product": product}
    if location:
        filters["location"] = location
    return frappe.db.get_value(ITEM, filters, "*", as_dict=True)


def rows_of_product(*, count: str, product: str) -> list:
    """这个商品在这份盘点里的**所有行**（一个货架一行），手机端要拿它给员工挑。"""
    guard.require("stock.read")
    return frappe.get_list(ITEM, filters={"count": count, "product": product},
                           fields=["name", "product", "product_name", "code", "barcode", "unit",
                                   "warehouse", "location", "book_qty", "counted",
                                   "counted_qty", "difference"],
                           order_by="location asc, name asc", limit_page_length=0)


def ensure_item(*, count, code=None, product=None, location="") -> dict:
    """把扫到的货**登记**进这份盘点（没有就补一行，账面取当前结存）。

    与加记录分开：手机端扫一下只是"看看这是哪件、账面多少"，不该在那一下
    就写进一条实盘（写 0 会被当成"数出来是 0"，比不写更糟）。
    """
    guard.require("stock.write")
    count_state.lock_task(count)
    task = _get(count)
    if task.status != "Active":
        frappe.throw(_(CLOSED))
    found = product_of_code(code) if code else None
    if not found and product:
        found = frappe.db.get_value(PRODUCT, {"name": product, "is_active": 1}, "*", as_dict=True)
    if not found:
        frappe.throw(_(CODE_NOT_FOUND))
    warehouse = task.warehouse or warehouses.required()
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


def list_counts(*, status=None, limit=50, start=0) -> list[dict]:
    guard.require("stock.read")
    filters = dict(warehouses.scope_filter())
    if status:
        filters["status"] = status
    rows = frappe.get_list(
        COUNT, filters=filters,
        fields=["name", "title", "status", "warehouse", "item_total", "counted_total",
                "difference_total", "creation", "modified", "closed_at", "posted_at"],
        order_by="creation desc",
        limit_start=int(start or 0), limit_page_length=_size(limit),
    )

    return [dict(row, warehouse_name=_warehouse_label(row.warehouse)) for row in rows]


def count_total(*, status=None) -> int:
    guard.require("stock.read")
    filters = dict(warehouses.scope_filter())
    if status:
        filters["status"] = status
    return len(frappe.get_list(COUNT, filters=filters,
                               pluck="name", limit_page_length=0))


def get_count(name: str) -> dict:
    guard.require("stock.read")
    doc = _get(name)
    return {
        "name": doc.name, "title": doc.title, "status": doc.status, "warehouse": doc.warehouse,
        "warehouse_name": _warehouse_label(doc.warehouse),
        "notes": doc.notes, "item_total": doc.item_total, "counted_total": doc.counted_total,
        "difference_total": doc.difference_total, "closed_at": doc.closed_at,
        "closed_by": doc.closed_by, "posted_at": doc.posted_at, "posted_by": doc.posted_by,
    }


def template_rows(*, name=None, warehouse=None) -> list[dict]:
    """盘点表要填的行：给一份盘点的快照，或**按当前结存现拉一份**。

    仓库列给的是**仓库名**（人认得的那串），不是内部编号；导回来时两种都认。
    """
    guard.require("stock.read")
    if name:
        _get(name)
        return [{
            "product_code": row["code"], "product_name": row["product_name"],
            "barcode": row["barcode"], "unit": row["unit"], "book_qty": row["book_qty"],
            "counted_qty": row["counted_qty"] if row["counted"] else None,
            "warehouse": _warehouse_label(row["warehouse"]), "location": row["location"] or "",
        } for row in list_items(count=name, limit=0)]
    warehouse = (warehouses.resolve(warehouse, create=False) or "__missing__") if warehouse else warehouses.required()
    balances = frappe.get_list(BALANCE, filters={"warehouse": warehouse},
                               fields=["product", "qty_on_hand", "location"], limit_page_length=0)
    products = {row["name"]: row for row in frappe.get_list(
        PRODUCT, filters={"is_active": 1, "name": ["in", [b["product"] for b in balances] or [""]]},
        fields=["name", "product_name", "code", "barcode", "qr_payload", "unit", "base_unit"],
        limit_page_length=0)}
    label = _warehouse_label(warehouse)
    out = []
    for balance in balances:
        product = products.get(balance["product"])
        if not product:
            continue
        out.append({
            "product_code": product["code"], "product_name": product["product_name"],
            "barcode": product_contracts.shown_code(product),
            "unit": product["base_unit"] or product["unit"],
            "book_qty": flt(balance["qty_on_hand"], 3), "counted_qty": None,
            "warehouse": label, "location": balance.get("location") or "",
        })
    return out


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


def _warehouse_of(value, fallback) -> str:
    """上传导入那一列里的仓库名 → 真实的仓库（只认当前公司已有的仓）。"""
    return warehouses.required(value or fallback)


def list_items(*, count: str, q=None, filter="all", limit=50, start=0, _locked=False) -> list[dict]:
    """快照行。`filter`: all / uncounted / counted / differences（旧线上那三个筛选项）。"""
    guard.require("stock.read")
    task = _get(count)
    rows = frappe.get_list(
        ITEM, filters={"count": count},
        fields=["name", "product", "product_name", "code", "barcode", "unit", "warehouse",
                "location", "book_qty", "counted", "counted_qty", "difference",
                "last_counted_at", "last_counted_by", "basis_qty", "basis_set", "stock_token"],
        order_by="product_name asc, name asc", limit_page_length=0,
    )
    all_entries = list_entries(count=count, limit=0)
    by_item = {}
    for entry in all_entries:
        if not entry["voided"]:
            by_item.setdefault(entry["item"], []).append(entry)
    wanted = (q or "").strip().lower()
    out = []
    tokens = {}
    can_view_costs = rules.cost_visible(guard.current_roles())
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
    guard.require("stock.read")
    return len(list_items(count=count, q=q, filter=filter, limit=0))


def close_count(name: str, review_token=None) -> dict:
    """The single finish action applies reviewed differences and closes atomically."""
    return post_count(name, review_token=review_token)


def review_count(name, *, _locked=False):
    guard.require("stock.read")
    task = get_count(name)
    items = list_items(count=name, limit=0, _locked=_locked)
    entries = list_entries(count=name, limit=0)
    state = [task, items, entries]
    token = hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()
    return {"task": task, "items": items, "entries": entries, "review_token": token,
            "can_write": guard.has("stock.write"),
            "can_view_costs": rules.cost_visible(guard.current_roles()),
            "conflicts": [r["name"] for r in items if r["stock_changed"]],
            "location_total": sum(bool(r["location_difference"]) for r in items)}


def post_count(name: str, review_token=None) -> dict:
    guard.require("stock.write")
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
    # **盘点差异也开入库/出库单据**（用户 2026-09-23：这些编号要和商品库存、出入库、
    # 收发存每一笔记录联动；MR.ERP 的调整/盘点模块 `stktranmod` 同样是把带有自己编号的
    # 单据写进库存卡，旧线上盘点差异也发 IR/IS 号并落一张出入库单）。
    #
    # 因此这里不再直接写"没有单据号的 count 流水"，而是走 `create_entry`：
    # 单据拿系统发的 IR/IS 号，流水带着这个号（收发存/库存明细都显示得出），
    # 单据本身在出入库页能按号查到，`ref_type/ref_id` 指回这份盘点。
    # `commit=False`：整批盘点一个事务，失败不留半截（与过账前的行为一致）。
    applied = []
    for index, (key, delta, unit_cost) in enumerate(operations):
        if not delta:
            continue
        created = stock_documents.create_entry(
            direction="in" if delta > 0 else "out", posting_date=str(frappe.utils.nowdate()),
            warehouse=key[1],
            lines=[{"product": key[0], "qty": abs(delta), "location": key[2],
                    "unit_cost": unit_cost}],
            reason=f"stocktake:{name}", ref_type="count", ref_id=name,
            dedupe_key=f"count:{name}:{index}", submit=True, commit=False,
            # 盘点走自己的编号定义（AD 段），与入库 IR / 出库 IS 分开。
            numbering_direction="stocktake")
        applied.append({"product": key[0], "warehouse": key[1], "location": key[2],
                        "delta": float(delta), "entry": created["name"], "doc_no": created["doc_no"]})
    frappe.db.set_value(COUNT, name, {"status": "Posted", "closed_at": now(),
                        "closed_by": frappe.session.user, "posted_at": now(),
                        "posted_by": frappe.session.user}, update_modified=False)
    return {"name": name, "status": "Posted", "adjustments": applied, "deduped": False}


def delete_count(name: str) -> dict:
    guard.require("stock.write")
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


def _save_photos(entry: str, values) -> int:
    """照片挂到这条记录上（最多 5 张）。手机端给 base64，桌面端给已上传的 file_url。"""
    import base64
    import binascii

    from frappe.utils.file_manager import save_file

    values = [value for value in (values or []) if str(value or "").strip()]
    if not values:
        return 0
    existing = frappe.db.count("File", {"attached_to_doctype": ENTRY, "attached_to_name": entry})
    if existing + len(values) > PHOTO_LIMIT:
        frappe.throw(_(PHOTO_LIMIT_ERROR).format(PHOTO_LIMIT))
    saved = 0
    for value in values:
        text = str(value).strip()
        try:
            if text.startswith("/") or text.startswith("http"):
                name = frappe.db.get_value("File", {"file_url": text}, "name",
                                           order_by="creation desc")
                if not name:
                    frappe.throw(_("That photo could not be read"))
                frappe.db.set_value("File", name, {"attached_to_doctype": ENTRY,
                                                   "attached_to_name": entry})
            else:
                raw = base64.b64decode(text, validate=True)
                if not raw or len(raw) > 1024 * 1024:
                    raise ValueError("photo too large")
                save_file(f"stocktake-{entry}-{existing + saved + 1}.jpg", raw, ENTRY, entry,
                          is_private=1)
        except (ValueError, binascii.Error):
            frappe.throw(_("That photo could not be read"))
        saved += 1
    frappe.db.set_value(ENTRY, entry, "photo_total",
                        frappe.db.count("File", {"attached_to_doctype": ENTRY,
                                                 "attached_to_name": entry}),
                        update_modified=False)
    return saved


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
    guard.require("stock.read")
    task = _get(count)
    found = product_of_code(code) if code else frappe.db.get_value(
        PRODUCT, {"name": product, "is_active": 1}, "*", as_dict=True)
    if not found:
        frappe.throw(_(CODE_NOT_FOUND))
    rows = [r for r in list_items(count=count, limit=0) if r['product'] == found['name']]
    return {"product": {key: found.get(key) for key in
                        ("name", "product_name", "code", "barcode", "base_unit", "unit")},
            "rows": rows,
            "elsewhere": elsewhere_positions(found["name"],
                                             {(r["warehouse"], r["location"] or "") for r in rows}),
            "stock_token": count_state.stock_token(found['name']),
            "warehouse_name": _warehouse_label(task.warehouse)}


def elsewhere_positions(product_id: str, here: set) -> list:
    """这件货在**别的**位置上的结存（`here` 是这次盘点已经看过的那几个位置）。"""
    # ⚠️ 结存表里**没有** `unit` 列（成本/数量才是它的字段）：单位从商品主档取
    # （库存单位优先，与 `stock.resolve_factor` 同一个口径）。
    product = frappe.db.get_value(PRODUCT, product_id, ["base_unit", "unit"], as_dict=True) or {}
    unit = product.get("base_unit") or product.get("unit") or ""
    out = []
    for balance in frappe.db.get_all(BALANCE, filters={"product": product_id},
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
              note=None, request_id=None, photos=None, source="Desk", stock_token=None) -> dict:
    """A record belongs to a book row; warehouse/location describe where it was found."""
    guard.require("stock.write")
    count_state.lock_task(count)
    task = _get(count)
    quantity = _qty(qty)
    if quantity < 0:
        frappe.throw(_(QUANTITY_REQUIRED))
    if item:
        row = _item(item)
        if row['count'] != count:
            frappe.throw(_(ITEM_NOT_FOUND))
        product_id = row['product']
        actual_warehouse = warehouses.required(warehouse if warehouse is not None else row['warehouse'])
        actual_location = positions.clean_location(location if location is not None else row['location'])
    else:
        found = product_of_code(code) if code else frappe.db.get_value(
            PRODUCT, {"name": product, "is_active": 1}, "*", as_dict=True)
        if not found:
            frappe.throw(_(CODE_NOT_FOUND))
        product_id = found['name']
        actual_warehouse = warehouses.required(warehouse or task.warehouse)
        actual_location = positions.clean_location(location)
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
    token = _record_revision(count, product_id, stock_token)
    if not row['counted']:
        basis = stock_service.current_balance(product_id, row['warehouse'], row['location'])
        frappe.db.set_value(ITEM, item, {"basis_qty": basis.qty, "basis_set": 1,
                                        "basis_unit_cost": basis.unit or 0,
                                        "basis_cost_known": int(basis.unit is not None),
                                        "stock_token": token}, update_modified=False)
    entry = frappe.get_doc({"doctype": ENTRY, "count": count, "item": item, "product": product_id,
        "warehouse": actual_warehouse, "location": actual_location, "qty": flt(quantity, 3),
        "request_id": request_id, "note": note, "source": source,
        "counted_by": frappe.session.user, "counted_at": now(), "version": 1})
    count_state.write_document(entry, "insert")
    _save_photos(entry.name, photos)
    _recount_item(item, count)
    return {"entry": entry.name, "item": item, "qty": flt(quantity, 3), "deduped": False}


def edit_entry(*, entry, qty=None, voided=None, version=None, photos=None, location=None,
               warehouse=None, note=None, stock_token=None) -> dict:
    guard.require("stock.write")
    parent = frappe.db.get_value(ENTRY, entry, "count")
    count_state.lock_task(parent)
    doc = frappe.get_doc(ENTRY, entry)
    if version is not None and int(version) != int(doc.version):
        frappe.throw(_(VERSION_STALE))
    if voided is None or voided not in (True, 1, "1", "true"):
        _record_revision(parent, doc.product, stock_token)
    if qty is not None:
        quantity = _qty(qty)
        if quantity < 0:
            frappe.throw(_(QUANTITY_REQUIRED))
        doc.qty = flt(quantity, 3)
    if voided is not None:
        doc.voided = 1 if voided in (True, 1, "1", "true") else 0
    if location is not None:
        doc.location = positions.clean_location(location)
    if warehouse is not None:
        doc.warehouse = warehouses.required(warehouse)
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
    guard.require("stock.write")
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
    guard.require("stock.read")
    _get(count)
    filters = {"count": count}
    if item:
        filters["item"] = item
    result = frappe.get_list(
        ENTRY, filters=filters,
        fields=["name", "item", "product", "warehouse", "location", "qty", "voided", "version",
                "photo_total", "note", "counted_by", "counted_at", "source"],
        order_by="counted_at desc, name desc",
        limit_start=int(start or 0), limit_page_length=_size(limit),
    )
    # Export the frozen item labels, not today's possibly renamed product master.
    items = {row.name: row for row in frappe.get_list(
        ITEM, filters={"count": count}, fields=["name", "product_name", "code", "unit",
                                               "warehouse", "location"], limit_page_length=0)}
    return [dict(row, warehouse_name=_warehouse_label(row.warehouse),
                 product_name=items[row.item].product_name, code=items[row.item].code,
                 unit=items[row.item].unit,
                 book_warehouse_name=_warehouse_label(items[row.item].warehouse),
                 book_location=items[row.item].location) for row in result]


def entries_total(*, count, item=None) -> int:
    """记录的条数（列表分页要用）。"""
    guard.require("stock.read")
    filters = {"count": count}
    if item:
        filters["item"] = item
    return frappe.db.count(ENTRY, filters)


def entry_photos(entry: str) -> list[dict]:
    guard.require("stock.read")
    return frappe.get_list("File", filters={"attached_to_doctype": ENTRY,
                                            "attached_to_name": entry},
                           fields=["name", "file_url", "file_name"], order_by="creation asc",
                           limit_page_length=PHOTO_LIMIT)


def item_photos(*, count, item) -> list[dict]:
    """该商品在**本任务里所有记录**的照片（记录弹窗表格下面那一片，含作废记录）。
    带上记录自己的位置与时间，看图片时知道是哪一次、在哪儿拍的。"""
    guard.require("stock.read")
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
