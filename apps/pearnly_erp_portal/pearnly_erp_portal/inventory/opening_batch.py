"""Review and atomically initialize existing products' stock positions once."""
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import re

import frappe
from frappe import _
from frappe.utils import nowdate

from pearnly_core import browser_text
from pearnly_erp_portal.access import guard
from pearnly_erp_portal.inventory import access, contracts, opening_workbook, stock, warehouses
from pearnly_erp_portal.masters import company as companies

INVALID_ROW = "Check the opening stock row"
NUMBER_REQUIRED = "Opening quantity and unit cost are required and must be non-negative"
PRECISION_INVALID = "Opening quantity supports six decimal places and unit cost supports two"
ALREADY_INITIALIZED = "This stock position is already initialized; use a stock adjustment"
HAS_HISTORY = "This stock position already has stock movements; use a stock adjustment"
STALE_REVIEW = "Opening stock changed. Preview the rows again before submitting."
PRODUCT_FIELDS = "name, product_name, code, barcode, base_unit, unit, is_active, express_company"
FILE_ERRORS = {
    "file_too_large": "The opening stock file is too large",
    "file_invalid": "That file could not be read; please use the template",
    "headers_invalid": "Use unique opening stock columns, including a product identifier, quantity and unit cost",
    "sheet_invalid": "Use one worksheet for opening stock",
    "too_many_rows": "That file has too many rows",
    "formula_invalid": "Opening stock files cannot contain formulas or error cells",
    "identifier_text_required": "Keep product IDs, codes and barcodes as text in the opening stock file",
    "empty": "That file has no rows",
}


def require_batch_access():
    guard.require("inventory.read")
    guard.require("stock.in")


def _text(value):
    return "" if value is None else str(value).strip()


def _number(value, places):
    if value is None or isinstance(value, bool) or not str(value).strip():
        raise ValueError(NUMBER_REQUIRED)
    try:
        number = Decimal(str(value).strip())
        if not number.is_finite() or number < 0 or number >= Decimal("1000000000000"):
            raise ValueError(NUMBER_REQUIRED)
        if number != number.quantize(Decimal(1).scaleb(-places)):
            raise ValueError(PRECISION_INVALID)
        if Decimal(str(float(number))) != number:
            raise ValueError("The opening stock value is too large to preserve its decimal precision")
        return number.quantize(Decimal(1).scaleb(-places))
    except InvalidOperation:
        raise ValueError(NUMBER_REQUIRED) from None


def _context(warehouse, as_of_date, *, current=False):
    value = str(as_of_date or "")
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) or date.fromisoformat(value) > date.fromisoformat(nowdate()):
            raise ValueError
    except ValueError:
        frappe.throw(_("Use a valid opening date that is not in the future"))
    company = companies.active_company()
    if not _text(warehouse):
        frappe.throw(_("Pick a warehouse"))
    if current:
        rows = frappe.db.sql(
            "select name, company, is_active from `tabERP Warehouse` where name=%s for update",
            (warehouse,), as_dict=True)
        if not rows or rows[0].company != company or not rows[0].is_active:
            frappe.throw(_("Warehouse not found"))
    else:
        warehouse = warehouses.required(warehouse, company=company)
    return {"company": company, "warehouse": warehouse, "as_of_date": value}


def _rows(value):
    if isinstance(value, str):
        value = frappe.parse_json(value)
    if not isinstance(value, list) or not value or len(value) > opening_workbook.MAX_ROWS:
        frappe.throw(_("Provide between 1 and 5000 opening stock rows"))
    return value


def _catalog(rows, *, names=None):
    if names is not None:
        if not names:
            return []
        # Stable product locks were acquired first. This is a current read, not
        # the repeatable-read snapshot established by initial validation.
        return frappe.db.sql("select " + PRODUCT_FIELDS + " from `tabERP Product` where name in %(names)s order by name for update",
                             {"names": tuple(sorted(names))}, as_dict=True)
    keys = {field: tuple(sorted({_text(row.get(field)) for row in rows if isinstance(row, dict) and _text(row.get(field))}))
            for field in ("product", "product_code", "barcode")}
    clauses = [column + " in %(" + field + ")s" for field, column in
               (("product", "name"), ("product_code", "code"), ("barcode", "barcode")) if keys[field]]
    if not clauses:
        return []
    return frappe.db.sql("select " + PRODUCT_FIELDS + " from `tabERP Product` where " + " or ".join(clauses),
                         keys, as_dict=True)


def _product(row, catalog, company=None):
    """只认这家公司 Express 的档和本地档（编码、条码不再全租户唯一，别家公司的同码商品不算歧义，10-24 集成核对）。"""
    matched = []
    for field, column in (("product", "name"), ("product_code", "code"), ("barcode", "barcode")):
        value = _text(row.get(field))
        if value:
            candidates = [product for product in catalog if product.get(column) == value
                          and (not company or not product.get("express_company") or product.get("express_company") == company)]
            if len(candidates) != 1:
                raise ValueError("Product not found or its identifiers are ambiguous")
            matched.append(candidates[0])
    if not matched:
        raise ValueError("Choose an existing product for opening stock")
    if len({product.name for product in matched}) != 1:
        raise ValueError("The product identifiers refer to different products")
    product = matched[0]
    if not product.is_active:
        raise ValueError("Product is disabled")
    return product


def _position(row, context, *, current):
    suffix = " for update" if current else ""
    parameters = (row["product"], context["warehouse"], row["location"])
    openings = frappe.db.sql(
        "select name, as_of_date, qty, unit_cost, posted_qty from `tabERP Stock Opening` where product=%s and warehouse=%s and binary coalesce(location, '')=binary %s" + suffix,
        parameters, as_dict=True)
    if openings:
        previous = openings[0]
        if (len(openings) != 1 or str(previous.as_of_date) != context["as_of_date"]
                or contracts.d(previous.qty) != row["qty"] or contracts.d(previous.unit_cost) != row["unit_cost"]
                or contracts.d(previous.posted_qty) != row["qty"]):
            raise ValueError(ALREADY_INITIALIZED)
        return "skipped"
    movements = frappe.db.sql(
        "select name from `tabERP Stock Movement` where product=%s and warehouse=%s and binary coalesce(location, '')=binary %s limit 1" + suffix,
        parameters)
    balances = frappe.db.sql(
        "select qty_on_hand, value, cost_known from `tabERP Stock Balance` where product=%s and warehouse=%s and binary coalesce(location, '')=binary %s" + suffix,
        parameters, as_dict=True)
    if movements or any(contracts.d(row.qty_on_hand) or contracts.d(row.value) or row.cost_known for row in balances):
        raise ValueError(HAS_HISTORY)
    return "ready"


def _review(rows, context, catalog, *, current=False):
    output, seen = [], {}
    for number, source in enumerate(rows, 1):
        row = {key: source.get(key) for key in ("product", "product_code", "barcode", "unit", "location", "qty", "unit_cost")} if isinstance(source, dict) else {}
        row.update(row_number=source.get("row_number", number) if isinstance(source, dict) else number,
                   product_name="", code="", amount=None, status="error", error="")
        try:
            if not isinstance(source, dict):
                raise ValueError(INVALID_ROW)
            product = _product(row, catalog, context.get("company"))
            unit = _text(product.base_unit or product.unit)
            if not unit or (_text(row.get("unit")) and _text(row["unit"]) != unit):
                raise ValueError("Opening stock must use the product base unit")
            location = _text(row.get("location"))
            if len(location) > 140:
                raise ValueError(INVALID_ROW)
            row.update(product=product.name, product_name=product.product_name, code=product.code or "",
                       unit=unit, location=location)
            identity = (product.name, location)
            if identity in seen:
                previous = seen[identity]
                previous.update(status="error", error=_("Each product and location may appear only once"))
                raise ValueError("Each product and location may appear only once")
            seen[identity] = row
            row["qty"] = _number(row.get("qty"), 6)
            row["unit_cost"] = _number(row.get("unit_cost"), 2)
            row["amount"] = contracts.q2(row["qty"] * row["unit_cost"])
            if row["amount"] >= Decimal("1000000000000"):
                raise ValueError("The opening stock amount is too large")
            row["status"] = _position(row, context, current=current)
        except ValueError as error:
            row["error"] = _(str(error))
        output.append(row)
    return output


def _token(rows, context, note):
    secret = frappe.conf.get("encryption_key") or frappe.conf.get("db_password")
    if not secret:
        frappe.throw(_("Opening stock review is not configured"))
    payload = {**context, "site": frappe.local.site, "user": frappe.session.user, "note": _text(note),
               "rows": [{key: format(row[key], "f") if key in ("qty", "unit_cost") else row[key]
                         for key in ("product", "unit", "location", "qty", "unit_cost")} for row in rows]}
    return hmac.new(str(secret).encode(), json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256).hexdigest()


def _result(rows, context, note):
    valid = all(row["status"] != "error" for row in rows)
    token = _token(rows, context, note) if valid else None
    total = sum((row["amount"] or Decimal(0) for row in rows), Decimal(0))
    result = [{key: (format(value, "f") if key in ("qty", "unit_cost") else float(value))
               if isinstance(value, Decimal) else value for key, value in row.items()} for row in rows]
    return {"rows": result, "valid": valid, "review_token": token, "total_value": float(total)}


def preview(*, warehouse, as_of_date, rows, note=None):
    require_batch_access()
    context = _context(warehouse, as_of_date)
    rows = _rows(rows)
    return _result(_review(rows, context, _catalog(rows)), context, note)


def _submit(*, warehouse, as_of_date, rows, note, review_token=None, legacy=False):
    context = _context(warehouse, as_of_date)
    rows = _rows(rows)
    catalog = _catalog(rows)
    preliminary = _review(rows, context, catalog)
    if any(row["status"] == "error" for row in preliminary):
        if legacy:
            frappe.throw(next(row["error"] for row in preliminary if row["status"] == "error"))
        frappe.throw(_("Fix all opening stock errors before submitting"))
    # Product locks serialize all stock writers, including the first movement.
    stock.lock_products([row["product"] for row in preliminary])
    context = _context(context["warehouse"], as_of_date, current=True)
    current_catalog = _catalog(rows, names={row["product"] for row in preliminary})
    reviewed = _review(rows, context, current_catalog, current=True)
    if any(row["status"] == "error" for row in reviewed):
        frappe.throw(_(STALE_REVIEW))
    if any(before["unit"] != after["unit"] for before, after in zip(preliminary, reviewed)):
        frappe.throw(_(STALE_REVIEW))
    if not legacy and (not isinstance(review_token, str) or not re.fullmatch(r"[0-9a-f]{64}", review_token) or not hmac.compare_digest(review_token, _token(reviewed, context, note))):
        frappe.throw(_(STALE_REVIEW))
    savepoint = "opening_batch_" + frappe.generate_hash(length=12)
    frappe.db.savepoint(savepoint)
    created, skipped, names = 0, 0, []
    try:
        for row in reviewed:
            name = contracts.position_name("OPN", row["product"], context["warehouse"], row["location"])
            names.append(name)
            if row["status"] == "skipped":
                skipped += 1
                continue
            doc = frappe.get_doc({"doctype": "ERP Stock Opening", "product": row["product"],
                                  "warehouse": context["warehouse"], "location": row["location"],
                                  "as_of_date": context["as_of_date"], "qty": row["qty"], "posted_qty": row["qty"],
                                  "unit_cost": row["unit_cost"], "note": _text(note)})
            access.write_opening(doc, "insert")
            stock.apply_movement(product=row["product"], warehouse=context["warehouse"], location=row["location"],
                                 movement_type=contracts.OPENING, qty=row["qty"], unit=row["unit"], unit_cost=row["unit_cost"],
                                 posting_date=context["as_of_date"], posting_time="00:00:00", ref_type="opening", ref_id=doc.name,
                                 reason="opening stock", client_uuid="opening:" + hashlib.sha256(name.encode()).hexdigest(),
                                 internal=True, company=context["company"])
            created += 1
    except Exception as error:
        # InnoDB error 1213 already rolled back the whole transaction, including
        # this savepoint. Error 1020 keeps it alive and still needs our rollback.
        cause = error.__cause__ or error
        if cause.args and isinstance(cause.args[0], BaseException):
            cause = cause.args[0]
        transaction_aborted = isinstance(error, frappe.QueryDeadlockError) and cause.args and cause.args[0] == 1213
        if not transaction_aborted:
            frappe.db.rollback(save_point=savepoint)
        raise
    return {"created": created, "skipped": skipped, "openings": names}


def submit(*, warehouse, as_of_date, rows, review_token, note=None):
    require_batch_access()
    return _submit(warehouse=warehouse, as_of_date=as_of_date, rows=rows, note=note, review_token=review_token)


def upsert_one(*, product, warehouse, as_of_date, qty, unit_cost=None, note=None, location=""):
    """Old report API retains its authorization, but shares initialize-once rules."""
    guard.require("report.write")
    result = _submit(warehouse=warehouse, as_of_date=as_of_date,
                     rows=[{"product": product, "location": location, "qty": qty, "unit_cost": unit_cost}],
                     note=note, legacy=True)
    return {"name": result["openings"][0], "qty": float(contracts.d(qty)), "unit_cost": float(contracts.d(unit_cost))}


def template(warehouse):
    require_batch_access()
    context = _context(warehouse, nowdate())
    # 只列这家公司 Express 的档和本地档（几十家公司各自同步来的商品不混进来，合起来还会超过模板行数上限）
    rows = frappe.get_all("ERP Product", filters={"is_active": 1},
                          or_filters=[["express_company", "=", context["company"]], ["express_company", "is", "not set"]],
                          fields=["name", "product_name", "code", "barcode", "base_unit", "unit"], order_by="name", limit_page_length=opening_workbook.MAX_ROWS + 1)
    if len(rows) > opening_workbook.MAX_ROWS:
        frappe.throw(_("That file has too many rows"))
    values = [{"product": row.name, "product_name": row.product_name, "product_code": row.code,
               "barcode": row.barcode, "unit": row.base_unit or row.unit, "location": "", "qty": None, "unit_cost": None} for row in rows]
    return opening_workbook.build_bytes([browser_text.label(key) for _, key in opening_workbook.FIELD_LABELS], values)


def read_file(file_url):
    require_batch_access()
    url = _text(file_url)
    if not url.startswith(("/files/", "/private/files/")) or ".." in url.split("/"):
        frappe.throw(_("Choose a local opening stock file"))
    name = frappe.db.get_value("File", {"file_url": url}, "name", order_by="creation desc")
    if not name:
        frappe.throw(_("Choose a file to import"))
    doc = frappe.get_doc("File", name)
    doc.check_permission("read")
    if not str(doc.file_name or "").lower().endswith(".xlsx"):
        frappe.throw(_("Use an XLSX opening stock file"))
    if (doc.file_size or 0) > opening_workbook.MAX_BYTES:
        frappe.throw(_(FILE_ERRORS["file_too_large"]))
    labels = {text: field for field, key in opening_workbook.FIELD_LABELS
              for text in browser_text.all_languages(key).values() if text}
    try:
        return {"rows": opening_workbook.parse_workbook(doc.get_content(), opening_workbook.header_aliases(labels))}
    except ValueError as error:
        frappe.throw(_(FILE_ERRORS.get(str(error), FILE_ERRORS["file_invalid"])))
