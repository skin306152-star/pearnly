"""Submit a sale in one transaction: the record, the goods leaving and the collection mark.

与采购同源的三条口径：**一次提交即落定**（没有"先存草稿再开单"的第二步）、
**幂等锚在请求号上**（重复提交返回同一张单，不会出两次库）、**失败整个回滚**
（不留下落了一半的单）。
"""
import hashlib
import json

import frappe
from frappe import _
from frappe.utils import flt, getdate, now, nowdate

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.inventory import positions, stock, warehouses
from pearnly_erp_portal.numbering import service as numbering
from pearnly_erp_portal.sales import access, contracts, files

DOCTYPE = "ERP Sales Document"
LINE_DOCTYPE = "ERP Sales Document Line"

HEAD_FIELDS = ("customer", "party_type", "address", "source", "source_file", "note",
               "image_sha256")
LINE_FIELDS = ("product", "description", "qty", "unit", "unit_price", "discount",
               "discount_pct", "vat_applicable", "warehouse", "location")

BLOCKED = "This document cannot be recorded yet"


def flag(value, default=False):
    return default if value is None else value in (True, 1, "1", "true", "True")


# ── 明细与金额 ──────────────────────────────────────────────────────────────


def _lines(payload):
    rows = payload.get("lines")
    if not isinstance(rows, list) or not rows:
        frappe.throw(_("Items are required"))
    result = []
    for raw in rows:
        row = {key: raw[key] for key in LINE_FIELDS if key in raw}
        row["description"] = str(row.get("description") or "").strip()
        if not row["description"]:
            frappe.throw(_("Item Name is required"))
        try:
            qty = contracts._d(row.get("qty"))
            price = contracts._d(row.get("unit_price"))
            discount = contracts._d(row.get("discount"))
        except ArithmeticError:
            frappe.throw(_("Invalid value"))
        if qty <= 0:
            frappe.throw(_("Quantity must be greater than zero"))
        if price < 0 or discount < 0:
            frappe.throw(_("Invalid value"))
        if qty * price - discount < 0:
            frappe.throw(_("Discount cannot exceed the amount"))
        if row.get("warehouse"):
            row["warehouse"] = warehouses.resolve(row["warehouse"])
        result.append(row)
    return result


def _vat_rate(payload):
    try:
        rate = contracts._d(payload.get("vat_rate"))
        discount = contracts._d(payload.get("header_discount"))
    except ArithmeticError:
        frappe.throw(_("Invalid value"))
    if not 0 <= rate <= 100 or discount < 0:
        frappe.throw(_("Invalid value"))
    return rate


def _calculate(payload):
    """金额一律由纯规则层算：服务端与前端不各算一份。"""
    tots = contracts.compute_totals(payload.get("lines") or [],
                                    vat_rate=_vat_rate(payload) if flag(payload.get("has_vat"), True) else 0,
                                    header_discount_amount=payload.get("header_discount") or 0)
    return {"subtotal": str(tots["subtotal"]), "discount_total": str(tots["discount_total"]),
            "vat_amount": str(tots["vat_amount"]), "grand_total": str(tots["grand_total"]),
            "lines": [{"line_no": row["line_no"], "line_total": str(row["line_total"])}
                      for row in tots["lines"]]}


def preview(payload):
    guard.require("sales.read")
    return _calculate(payload)


# ── 买方：销项的单据上就有买方块，客户主档可以有也可以没有 ──────────────────


def _buyer(payload):
    key = payload.get("customer")
    name = str(payload.get("buyer_name") or "").strip()
    if key:
        doc = frappe.get_doc("ERP Customer", key)
        doc.check_permission("read")
        if not doc.is_active:
            frappe.throw(_("Customer is disabled"))
        name = name or doc.customer_name
        for field in ("party_type", "address"):
            if not payload.get(field):
                payload[field] = doc.get(field)
    if not name:
        frappe.throw(_("Customer is required"))
    party = payload.get("party_type") or "person"
    if party not in ("company", "person", "foreign", "anonymous"):
        frappe.throw(_("Invalid value"))
    payload["party_type"] = party
    return key, name


# ── 提交 ────────────────────────────────────────────────────────────────────


def post(payload, *, request_id, name=None, finalize=None):
    guard.require("sales.write")
    if not isinstance(payload, dict) or not request_id or len(str(request_id)) > 140:
        frappe.throw(_("Invalid value"))
    payload = dict(payload)
    fingerprint = hashlib.sha256(json.dumps([payload, name], sort_keys=True,
                                           ensure_ascii=False, default=str).encode()).hexdigest()
    with frappe.cache.lock("erp-sales-post", timeout=120, blocking_timeout=20):
        try:
            existing = frappe.db.get_value(DOCTYPE, {"request_id": request_id}, "name")
            if existing:
                doc = access.get_document(existing)
                if doc.request_hash != fingerprint:
                    frappe.throw(_("Request ID already exists"))
                return {"name": doc.name, "status": doc.status, "duplicate": True}
            result = _post(payload, request_id, fingerprint, name)
            if finalize:
                finalize(result)
            frappe.db.commit()
            return result
        except Exception:
            frappe.db.rollback()
            raise


def _post(payload, request_id, fingerprint, name):
    kind = payload.get("doc_kind") or contracts.RECEIPT
    if kind not in contracts.DOC_KINDS:
        frappe.throw(_("Invalid value"))
    day = str(getdate(payload.get("doc_date") or nowdate()))
    payload["lines"] = _lines(payload)
    totals = contracts.compute_totals(
        payload["lines"],
        vat_rate=_vat_rate(payload) if flag(payload.get("has_vat"), True) else 0,
        header_discount_amount=payload.get("header_discount") or 0)
    reasons = contracts.confirm_gates({"doc_kind": kind, "grand_total": totals["grand_total"]})
    if reasons:
        frappe.throw(f"{_(BLOCKED)}: {', '.join(reasons)}")
    doc = access.get_document(name, write=True, lock=True) if name else frappe.new_doc(DOCTYPE)
    if name and doc.status != contracts.DRAFT:
        frappe.throw(_("A confirmed document cannot be edited"))
    if payload.get("image_sha256"):
        duplicates = frappe.get_all(DOCTYPE, filters={"image_sha256": payload["image_sha256"],
            "status": ["!=", contracts.VOID]}, pluck="name")
        if any(key != name for key in duplicates):
            frappe.throw(_("Document already exists"))
    customer, buyer_name = _buyer(payload)
    rows = [_line_row(raw, computed) for raw, computed in zip(payload["lines"], totals["lines"])]
    collection = _collection(payload, kind, totals["grand_total"], day)
    doc.update({key: payload.get(key) for key in HEAD_FIELDS})
    # 明细必须是子表文档：直接塞 dict 列表，控制器里的 `line.description` 会拿到 dict。
    doc.set("lines", [])
    for row in rows:
        doc.append("lines", row)
    doc.update({"doc_kind": kind, "doc_date": day, "customer": customer, "buyer_name": buyer_name,
        "has_vat": int(flag(payload.get("has_vat"), True)), "vat_rate": _vat_rate(payload),
        "header_discount": totals["header_discount"],
        "system_no": doc.get("system_no") or numbering.allocate(
            direction="sales", on=day,
            exists=lambda value: bool(frappe.db.exists(DOCTYPE, {"system_no": value}))),
        "status": contracts.SUBMITTED, "request_id": request_id, "request_hash": fingerprint,
        "summary": ", ".join(row["description"] for row in rows), **collection})
    doc.flags.sales_action = True
    doc.save()
    _post_stock(doc)
    files.bind(doc, payload.get("attachments"))
    digest = files.evidence_hash(doc)
    if digest:
        duplicates = frappe.get_all(DOCTYPE, filters={"image_sha256": digest,
            "status": ["!=", contracts.VOID]}, pluck="name")
        if any(key != doc.name for key in duplicates):
            frappe.throw(_("Document already exists"))
        doc.image_sha256 = digest
    doc.flags.sales_action = True
    doc.save()
    return {"name": doc.name, "status": doc.status, "system_no": doc.system_no}


def _resolve_item(row):
    """明细的物料：选了就核对，没选就按名称/编码/条码在主数据里找（**不自动建商品**）。

    旧线上销项明细的商品关联是可空的（"现填时可为空"），卖的东西没建档时明细照样能记，
    只是这一行不动库存 —— 要不要建档由物料岗位决定，销售不替它建。
    """
    if not row.get("product"):
        matches = frappe.get_all("ERP Product", filters={"product_name": row["description"],
            "is_active": 1}, pluck="name", limit_page_length=2)
        row["product"] = matches[0] if len(matches) == 1 else None
    if row.get("product"):
        item = frappe.get_doc("ERP Product", row["product"])
        item.check_permission("read")
        if not item.is_active:
            frappe.throw(_("Item is disabled"))
        row.setdefault("unit", item.base_unit or item.unit)
    return row


def _line_row(raw, computed):
    """明细行：只带这张单自己的字段（纯规则层算出来的是"金额"，不是行结构）。"""
    row = _resolve_item(dict(raw))
    row["line_no"] = computed["line_no"]
    row["line_total"] = computed["line_total"]
    row["discount"] = computed["discount"]
    row["discount_pct"] = computed.get("discount_pct")
    row["vat_applicable"] = int(bool(computed["vat_applicable"]))
    return positions.prepare(row)


def _collection(payload, kind, grand_total, day):
    """收款按表头口径：收了多少记多少，状态由金额算。报价单不收钱。"""
    if kind != contracts.RECEIPT:
        return {"payment_status": "unpaid", "paid_amount": 0, "payment_method": None,
                "payment_date": None}
    paid = flag(payload.get("is_paid"), True)
    method = payload.get("payment_method") or ("cash" if paid else None)
    if paid and method not in contracts.PAYMENT_METHODS:
        frappe.throw(_("Invalid value"))
    amount = contracts._d(grand_total) if paid else contracts._d(payload.get("paid_amount"))
    if amount > contracts._d(grand_total):
        frappe.throw(_("Paid amount cannot exceed the total"))
    return {"paid_amount": amount,
            "payment_status": contracts.payment_status_for(amount, grand_total),
            "payment_method": method if amount > 0 else None,
            "payment_date": (payload.get("payment_date") or day) if amount > 0 else None}


def _post_stock(doc):
    """落定即出库。幂等锚 = 单据名；已经有留痕就不重复出。"""
    if doc.stock_posted_at or not contracts.should_post_stock(doc.doc_kind, doc.status):
        return
    rows = [{"product": row.product, "qty": row.qty, "unit": row.unit, **positions.saved(row)}
            for row in doc.lines if row.product]
    if not rows:
        return
    stock.apply_lines(warehouse=warehouses.ensure_default(), movement_type="sale_out", lines=rows,
                      posting_date=doc.doc_date, ref_type=DOCTYPE, ref_id=doc.name,
                      doc_no=doc.system_no, internal=True, client_uuid="sales-doc:" + doc.name)
    doc.stock_posted_at = now()


# ── 作废 / 收款 / 删草稿 ────────────────────────────────────────────────────


def cancel(name, reason=None):
    doc = access.get_document(name, write=True, lock=True)
    if doc.status != contracts.SUBMITTED:
        frappe.throw(_("Only a submitted document can be cancelled"))
    if doc.stock_posted_at:
        stock.reverse_movements(ref_type=DOCTYPE, ref_id=doc.name,
                                client_uuid="sales-doc-void:" + doc.name, reason=reason)
    doc.status, doc.void_reason, doc.voided_at = contracts.VOID, reason, now()
    doc.stock_posted_at = None
    doc.flags.sales_action = True
    doc.save()
    return {"name": doc.name, "status": doc.status}


def payment(name, *, paid=None, amount=None, method=None, date=None):
    """记收款：表头口径，金额是**绝对值**（不是追加），所以重复调用不会重复收钱。"""
    doc = access.get_document(name, write=True, lock=True)
    if doc.status != contracts.SUBMITTED:
        frappe.throw(_("Document must be submitted"))
    if doc.doc_kind != contracts.RECEIPT:
        frappe.throw(_("Only a receipt records a collection"))
    total = contracts._d(doc.grand_total)
    if paid is not None and not flag(paid):
        value = contracts._d(0)
    elif amount is not None and paid is None:
        value = contracts._d(amount)
    else:
        value = total
    if value < 0 or value > total:
        frappe.throw(_("Invalid payment amount"))
    # 撤销收款会把方式清空；再收一次时按移动支付/现金的默认口径补上（与收银同一默认）。
    chosen = method or doc.payment_method or "cash"
    if value > 0 and chosen not in contracts.PAYMENT_METHODS:
        frappe.throw(_("Invalid value"))
    doc.paid_amount = value
    doc.payment_status = contracts.payment_status_for(value, total)
    doc.payment_method = chosen if value > 0 else None
    doc.payment_date = (date or doc.payment_date or nowdate()) if value > 0 else None
    doc.flags.sales_action = True
    doc.save()
    return {"name": doc.name, "payment_status": doc.payment_status, "paid_amount": str(doc.paid_amount)}


def delete_drafts(names):
    """只删草稿：草稿没动库存也没收钱，删掉不留后果。已落定的只能作废。

    为什么这里不看框架的 DocType `delete` 权限位：共享口径
    （`pearnly_core.provisioning.FEATURE_PERMS`）**故意不给功能角色 delete** ——
    "业务单据只能作废不能抹掉"，桌面端与通用删除路径都按这条挡。而"删掉自己刚录、
    还没落定也没动库存的草稿"是旧产品本来就有、且录入员角色就有的业务动作
    （旧仓 `docs/permissions/02-roles-permissions.md`：`*.create / edit / delete`
    属草稿层，owner/admin/accountant/clerk 都有）。所以按产品自己的口径在这里放行，
    但前面三道闸一个都不能少：write 岗位 + 本人范围（`access.get_document`）、
    整批行锁、全部是草稿。三道都过了才绕过框架那一位。
    """
    docs = [access.get_document(name, write=True, lock=True) for name in sorted(set(names))]
    if any(doc.status != contracts.DRAFT for doc in docs):
        frappe.throw(_("Only Draft documents can be deleted"))
    for doc in docs:
        frappe.delete_doc(DOCTYPE, doc.name, ignore_permissions=True)
    return {"deleted": len(docs)}
