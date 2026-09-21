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

import frappe
from frappe import _
from frappe.utils import flt, now

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.inventory import stock as stock_service
from pearnly_erp_portal.inventory import warehouses

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
CODE_NOT_FOUND = "No product matches that code"
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
        return Decimal(str(value).strip())
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


def create_count(*, title, warehouse=None, notes=None, only_in_stock=True) -> dict:
    """开一份盘点：把当前结存抄成快照行。"""
    guard.require("stock.write")
    name = (title or "").strip()
    if not name:
        frappe.throw(_(TITLE_REQUIRED))
    warehouse = (warehouse or "").strip() or warehouses.ensure_default()
    doc = frappe.get_doc({"doctype": COUNT, "title": name, "warehouse": warehouse,
                          "notes": notes, "status": "Active"})
    doc.insert()
    rows = frappe.get_list(BALANCE, filters={"warehouse": warehouse},
                           fields=["product", "qty_on_hand"], limit_page_length=0)
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
        if only_in_stock and not qty:
            continue
        _insert_item(doc.name, product, warehouse, qty)
        created += 1
    _refresh_totals(doc.name)
    frappe.db.commit()
    return {"name": doc.name, "rows": created}


def _insert_item(count: str, product: dict, warehouse: str, book_qty) -> str:
    existing = frappe.db.get_value(ITEM, {"count": count, "product": product["name"],
                                          "warehouse": warehouse}, "name")
    if existing:
        return existing
    doc = frappe.get_doc({
        "doctype": ITEM, "count": count, "product": product["name"],
        "product_name": product.get("product_name"), "code": product.get("code"),
        "barcode": product.get("barcode"),
        "unit": (product.get("base_unit") or product.get("unit") or "") or None,
        "warehouse": warehouse, "book_qty": flt(book_qty, 3), "counted": 0,
    })
    doc.insert(ignore_permissions=True)
    return doc.name


def item_of_product(*, count: str, product: str) -> dict | None:
    """这份盘点里某个商品那一行。按**商品**找，不是按名称字串搜。"""
    guard.require("stock.read")
    return frappe.db.get_value(ITEM, {"count": count, "product": product}, "*", as_dict=True)


def ensure_item(*, count, code=None, product=None) -> dict:
    """把扫到的货**登记**进这份盘点（没有就补一行，账面取当前结存）。

    与加记录分开：手机端扫一下只是"看看这是哪件、账面多少"，不该在那一下
    就写进一条实盘（写 0 会被当成"数出来是 0"，比不写更糟）。
    """
    guard.require("stock.write")
    task = _get(count)
    if task.status != "Active":
        frappe.throw(_(CLOSED))
    found = product_of_code(code) if code else None
    if not found and product:
        found = frappe.db.get_value(PRODUCT, {"name": product, "is_active": 1}, "*", as_dict=True)
    if not found:
        frappe.throw(_(CODE_NOT_FOUND))
    warehouse = task.warehouse or warehouses.ensure_default()
    book_qty = frappe.db.get_value(BALANCE, f"BAL-{found['name']}-{warehouse}",
                                   "qty_on_hand") or 0
    name = _insert_item(task.name, found, warehouse, book_qty)
    _refresh_totals(task.name)
    frappe.db.commit()
    return frappe.db.get_value(ITEM, name, "*", as_dict=True)


def _refresh_totals(count: str) -> None:
    """任务上的三个数（行数 / 已盘点 / 有差异）与行上的差异一起更新。"""
    rows = frappe.get_list(ITEM, filters={"count": count},
                           fields=["name", "book_qty", "counted", "counted_qty"],
                           limit_page_length=0)
    counted = differences = 0
    for row in rows:
        difference = (flt(row["counted_qty"], 3) - flt(row["book_qty"], 3)) if row["counted"] else 0
        frappe.db.set_value(ITEM, row["name"], "difference", difference,
                            update_modified=False)
        if row["counted"]:
            counted += 1
            if difference:
                differences += 1
    frappe.db.set_value(COUNT, count, {"item_total": len(rows), "counted_total": counted,
                                       "difference_total": differences}, update_modified=False)


def list_counts(*, status=None, limit=50, start=0) -> list[dict]:
    guard.require("stock.read")
    filters = {"status": status} if status else {}
    return frappe.get_list(
        COUNT, filters=filters,
        fields=["name", "title", "status", "warehouse", "item_total", "counted_total",
                "difference_total", "creation", "modified", "closed_at", "posted_at"],
        order_by="creation desc",
        limit_start=int(start or 0), limit_page_length=int(limit or 50),
    )


def count_total(*, status=None) -> int:
    guard.require("stock.read")
    return len(frappe.get_list(COUNT, filters={"status": status} if status else {},
                               pluck="name", limit_page_length=0))


def get_count(name: str) -> dict:
    guard.require("stock.read")
    doc = _get(name)
    return {
        "name": doc.name, "title": doc.title, "status": doc.status, "warehouse": doc.warehouse,
        "notes": doc.notes, "item_total": doc.item_total, "counted_total": doc.counted_total,
        "difference_total": doc.difference_total, "closed_at": doc.closed_at,
        "closed_by": doc.closed_by, "posted_at": doc.posted_at, "posted_by": doc.posted_by,
    }


def list_items(*, count: str, q=None, filter="all", limit=50, start=0) -> list[dict]:
    """快照行。`filter`: all / uncounted / counted / differences（旧线上那三个筛选项）。"""
    guard.require("stock.read")
    _get(count)
    rows = frappe.get_list(
        ITEM, filters={"count": count},
        fields=["name", "product", "product_name", "code", "barcode", "unit", "warehouse",
                "book_qty", "counted", "counted_qty", "difference", "last_counted_at",
                "last_counted_by"],
        order_by="product_name asc, name asc", limit_page_length=0,
    )
    wanted = (q or "").strip().lower()
    out = []
    for row in rows:
        if wanted and wanted not in " ".join(
                str(row.get(key) or "").lower()
                for key in ("product_name", "code", "barcode")).lower():
            continue
        counted = bool(row["counted"])
        if filter == "uncounted" and counted:
            continue
        if filter == "counted" and not counted:
            continue
        if filter == "differences" and (not counted or not row["difference"]):
            continue
        out.append(row)
    start = int(start or 0)
    return out[start:start + int(limit or 50)]


def items_total(*, count: str, q=None, filter="all") -> int:
    guard.require("stock.read")
    return len(list_items(count=count, q=q, filter=filter, limit=0))


def close_count(name: str) -> dict:
    """结束盘点：锁住记录，未盘点项仍为空。"""
    guard.require("stock.write")
    doc = _get(name)
    if doc.status == "Posted":
        frappe.throw(_(POSTED))
    _refresh_totals(doc.name)
    frappe.db.set_value(COUNT, doc.name, {"status": "Closed", "closed_at": now(),
                                          "closed_by": frappe.session.user},
                        update_modified=False)
    frappe.db.commit()
    return {"name": doc.name, "status": "Closed"}


def post_count(name: str) -> dict:
    """过账差异：只动**数过的**行，未盘点的一律不碰；差异落 `count` 流水。"""
    guard.require("stock.manage")
    doc = _get(name)
    if doc.status == "Posted":
        frappe.throw(_(POSTED))
    rows = frappe.get_list(ITEM, filters={"count": doc.name, "counted": 1},
                           fields=["product", "warehouse", "counted_qty"], limit_page_length=0)
    by_warehouse: dict = {}
    for row in rows:
        by_warehouse.setdefault(row["warehouse"], []).append(
            {"product": row["product"], "counted_qty": flt(row["counted_qty"], 3)})
    applied = []
    for warehouse, lines in by_warehouse.items():
        result = stock_service.count_lines(warehouse=warehouse, lines=lines,
                                           reason=f"stocktake:{doc.name}")
        applied.extend(result["adjustments"])
    frappe.db.set_value(COUNT, doc.name, {"status": "Posted", "posted_at": now(),
                                          "posted_by": frappe.session.user},
                        update_modified=False)
    frappe.db.commit()
    return {"name": doc.name, "status": "Posted", "adjustments": applied}


def delete_count(name: str) -> dict:
    guard.require("stock.write")
    doc = _get(name)
    if doc.status == "Posted":
        frappe.throw(_(POSTED))
    frappe.db.delete(ENTRY, {"count": doc.name})
    frappe.db.delete(ITEM, {"count": doc.name})
    frappe.delete_doc(COUNT, doc.name, force=True, ignore_permissions=True)
    frappe.db.commit()
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
    doc.difference = (flt(doc.counted_qty, 3) - flt(doc.book_qty, 3)) if doc.counted else 0
    doc.save(ignore_permissions=True)

    totals = frappe.db.sql(
        """SELECT COUNT(*) AS rows_total,
                  SUM(counted = 1) AS counted,
                  SUM(counted = 1 AND counted_qty <> book_qty) AS differences
           FROM `tabERP Stock Count Item` WHERE `count`=%s""",
        count_name, as_dict=True,
    )[0]
    frappe.db.set_value(COUNT, count_name,
                        {"item_total": int(totals["rows_total"] or 0),
                         "counted_total": int(totals["counted"] or 0),
                         "difference_total": int(totals["differences"] or 0)},
                        update_modified=False)


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


def add_entry(*, count, qty, item=None, code=None, product=None, warehouse=None, location=None,
              note=None, request_id=None, photos=None, source="Desk") -> dict:
    """加一条盘点记录。扫到快照里没有的货就**就地补一行**（账面取当前结存）。

    `request_id` 是这一发的幂等锚：手机会重试，重复发不会记两笔。
    """
    guard.require("stock.write")
    task = _get(count)
    if task.status != "Active":
        frappe.throw(_(CLOSED))
    if request_id:
        seen = frappe.db.get_value(ENTRY, {"request_id": request_id},
                                   ["name", "item", "qty"], as_dict=True)
        if seen:
            return {"entry": seen["name"], "item": seen["item"], "qty": flt(seen["qty"], 3),
                    "deduped": True}

    if item:
        row = _item(item)
        if row["count"] != task.name:
            frappe.throw(_(ITEM_NOT_FOUND))
        item_name, product_id = row["name"], row["product"]
        warehouse = row["warehouse"]
    else:
        found = None
        if code:
            found = product_of_code(code)
        elif product:
            found = frappe.db.get_value(PRODUCT, {"name": product, "is_active": 1}, "*",
                                        as_dict=True)
        if not found:
            frappe.throw(_(CODE_NOT_FOUND))
        warehouse = (warehouse or task.warehouse or warehouses.ensure_default())
        book_qty = frappe.db.get_value(BALANCE, f"BAL-{found['name']}-{warehouse}",
                                       "qty_on_hand") or 0
        item_name = _insert_item(task.name, found, warehouse, book_qty)
        product_id = found["name"]

    quantity = _qty(qty)
    if quantity < 0:
        frappe.throw(_(QUANTITY_REQUIRED))
    entry = frappe.get_doc({
        "doctype": ENTRY, "count": task.name, "item": item_name, "product": product_id,
        "warehouse": warehouse, "location": location, "qty": flt(quantity, 3),
        "request_id": request_id, "note": note, "source": source,
        "counted_by": frappe.session.user, "counted_at": now(), "version": 1,
    })
    entry.insert(ignore_permissions=True)
    _save_photos(entry.name, photos)
    _recount_item(item_name, task.name)
    frappe.db.commit()
    return {"entry": entry.name, "item": item_name, "qty": flt(quantity, 3), "deduped": False}


def edit_entry(*, entry, qty=None, voided=None, version=None, photos=None) -> dict:
    """改一笔或撤销一笔（撤销不进合计）。改/撤销天然幂等，所以不用另做幂等锚。"""
    guard.require("stock.write")
    row = frappe.db.get_value(ENTRY, entry, "*", as_dict=True)
    if not row:
        frappe.throw(_(ITEM_NOT_FOUND), frappe.DoesNotExistError)
    task = _get(row["count"])
    if task.status != "Active":
        frappe.throw(_(CLOSED))
    if version is not None and int(version) != int(row["version"]):
        frappe.throw(_(VERSION_STALE))
    doc = frappe.get_doc(ENTRY, entry)
    if qty is not None:
        quantity = _qty(qty)
        if quantity < 0:
            frappe.throw(_(QUANTITY_REQUIRED))
        doc.qty = flt(quantity, 3)
    if voided is not None:
        doc.voided = 1 if voided in (True, 1, "1", "true") else 0
    doc.version = int(row["version"]) + 1
    doc.counted_by = frappe.session.user
    doc.counted_at = now()
    doc.save(ignore_permissions=True)
    if photos:
        _save_photos(doc.name, photos)
    _recount_item(doc.item, task.name)
    frappe.db.commit()
    return {"entry": doc.name, "item": doc.item, "voided": bool(doc.voided), "qty": flt(doc.qty, 3),
            "version": doc.version}


def list_entries(*, count, item=None, limit=50, start=0) -> list[dict]:
    guard.require("stock.read")
    _get(count)
    filters = {"count": count}
    if item:
        filters["item"] = item
    return frappe.get_list(
        ENTRY, filters=filters,
        fields=["name", "item", "product", "warehouse", "location", "qty", "voided", "version",
                "photo_total", "note", "counted_by", "counted_at", "source"],
        order_by="counted_at desc, name desc",
        limit_start=int(start or 0), limit_page_length=int(limit or 50),
    )


def entry_photos(entry: str) -> list[dict]:
    guard.require("stock.read")
    return frappe.get_list("File", filters={"attached_to_doctype": ENTRY,
                                            "attached_to_name": entry},
                           fields=["name", "file_url", "file_name"], order_by="creation asc",
                           limit_page_length=PHOTO_LIMIT)
