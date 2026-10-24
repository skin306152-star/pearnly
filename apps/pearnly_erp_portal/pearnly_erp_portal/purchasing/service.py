"""采购单据的生命周期：建单、列表、取数、提交、作废、付款。

权限：能力判据在 `access.guard`；取数一律 `frappe.get_list`，并按岗位收窄到本人
（老板看全店、员工只看自己录入的）。

旧版单据沿用原金额合同；新版直接过账的单据由 purchasing.entry 承接付款与作废。
"""

from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import now, nowdate

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.purchasing import contracts, settings

DOCTYPE = "Purchase Document"
LINE_DOCTYPE = "Purchase Line"

NOT_FOUND = "Purchase document not found"
INVALID = "Invalid value"



def invalid_text(error):
    """「值无效」后面接明细校验的翻译（`contracts.detail_key`）；认不出的不接英文（2026-10-09）。"""
    key, args = contracts.detail_key(error)
    return f"{_(INVALID)}: {_(key).format(*args)}" if key else _(INVALID)


def _q2(value) -> Decimal:
    return contracts.q2(value)


def _get(name):
    if not name or not frappe.db.exists(DOCTYPE, name):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    from pearnly_erp_portal.purchasing.access import get_document

    return get_document(name)


def _dedupe_check(dedupe_key, dedupe_ref, exclude=None):
    """两把锁分开：外部幂等键（识别图片/调用方请求号）始终生效；"同一供应商的同一个
    单据号只允许一张"受采购设置的 `dedupe_block` 控制（有些门店确实会拆分记账）。
    """
    checks = [("dedupe_key", dedupe_key)]
    if settings.read_settings()["dedupe_block"]:
        checks.append(("dedupe_ref", dedupe_ref))
    for field, value in checks:
        if not value:
            continue
        filters = {field: value}
        if exclude:
            filters["name"] = ["!=", exclude]
        existing = frappe.db.get_value(DOCTYPE, filters, "name")
        if existing:
            frappe.throw(_(DUPLICATE).format(existing))


def _resolve_supplier(supplier, supplier_name):
    """把"手写供应商名"对齐到已有的供应商档案。

    为什么要对齐：去重判据是"供应商 + 单据号"。若一张单按档案编号、另一张按手写名字，
    同一个供应商会被算成两家，重复单据就拦不住了。所以按名字精确匹配到档案时自动关联；
    匹配不到就保留手写名字（旧线上也有没建档的供应商）。
    """
    if supplier:
        if not frappe.db.exists("ERP Supplier", supplier):
            frappe.throw(_("Supplier not found"))
        return supplier, frappe.db.get_value("ERP Supplier", supplier, "supplier_name")
    name = (supplier_name or "").strip()
    if not name:
        return None, supplier_name
    matched = frappe.db.get_value("ERP Supplier", {"supplier_name": name}, "name")
    return matched, name


def create_purchase(*, supplier_name, doc_date, lines, supplier=None, doc_kind="goods",
                    doc_no=None, due_date=None, currency="THB", fx_rate=1, has_vat=True,
                    discount_total=0, wht_amount=None, wht_rate=0, rounding=0,
                    supplier_tax_id=None, external_ref=None, dedupe_key=None,
                    image_sha256=None, notes=None, source=None, company=None):
    guard.require("purchase.write")
    if not isinstance(lines, list) or not lines:
        frappe.throw(_(LINES_REQUIRED))

    from pearnly_erp_portal.inventory import positions
    from pearnly_erp_portal.numbering import service as numbering

    company = companies.document_company(company)
    lines = [positions.prepare({**line, "item_type": "service" if doc_kind == "service" else line.get("item_type", "goods")},
                               company=company) for line in lines]
    has_vat = has_vat in (True, 1, "1", "true")
    config = settings.read_settings()
    supplier, supplier_name = _resolve_supplier(supplier, supplier_name)
    dedupe_ref = contracts.duplicate_key(supplier or supplier_name, doc_no)
    _dedupe_check(dedupe_key, dedupe_ref)

    try:
        totals = contracts.compute_totals(
            lines=lines, has_vat=bool(has_vat), doc_kind=doc_kind,
            discount_total=discount_total, wht_rate=wht_rate, wht_amount=wht_amount,
            rounding=rounding,
        )
    except ValueError as exc:
        frappe.throw(invalid_text(exc))

    doc = frappe.get_doc({
        "doctype": DOCTYPE,
        # 一租户多公司：单据挂在公司上；没给就是**当前公司**（切换公司改的那个用户默认值）。
        "company": company,
        "doc_kind": doc_kind,
        "supplier": supplier,
        "supplier_name": supplier_name,
        "supplier_tax_id": supplier_tax_id,
        "doc_no": doc_no,
        # 我方单据号：系统发（采购 PE），与"对方单号"（`doc_no`）并列 —— 旧线上也是两个
        # （`bill_number` + `document_number`）。收发存报表显示的是这一个。
        "system_no": numbering.allocate(
            direction="purchase", on=doc_date,
            exists=lambda value: bool(frappe.db.exists(DOCTYPE, {"system_no": value}))),
        "doc_date": doc_date,
        "due_date": due_date,
        "status": contracts.DRAFT,
        "currency": currency,
        "fx_rate": fx_rate,
        "has_vat": 1 if has_vat else 0,
        "discount_total": totals["discount_total"],
        "wht_amount": totals["wht_amount"],
        "rounding": totals["rounding"],
        "external_ref": external_ref,
        "dedupe_key": dedupe_key,
        "dedupe_ref": dedupe_ref,
        "image_sha256": image_sha256,
        "notes": notes,
        "source": source,
        "lines": [
            {
                "line_no": index,
                "item_type": "service" if doc_kind == "service" else line.get("item_type", "goods"),
                "description": line["description"],
                "product": line.get("product"),
                "warehouse": line.get("warehouse"), "location": line.get("location", ""),
                "product_ref": line.get("product_ref"),
                "qty": line.get("qty", 1),
                "unit": line.get("unit"),
                "unit_price": line.get("unit_price", 0),
                "discount": line.get("discount", 0),
                "vat_rate": line.get("vat_rate", config["default_vat_rate"]),
            }
            for index, line in enumerate(lines, start=1)
        ],
    })
    doc.insert()
    frappe.db.commit()
    return {"name": doc.name}


def _purchase_query(*, supplier=None, supplier_name=None, status=None, kind=None,
                    unpaid=False, q=None, date_from=None, date_to=None):
    """筛选条件只写一处：列表、合计、导出都从这里来，免得三处各写一套。"""
    # 一租户多公司：采购记录默认只看**当前公司**（切换公司那颗按钮改的是用户默认值）。
    filters: dict = dict(guard.scope_filters())
    filters["company"] = companies.active_company()
    if supplier:
        filters["supplier"] = supplier
    if supplier_name:
        filters["supplier_name"] = ["like", f"%{supplier_name}%"]
    if status:
        filters["status"] = status
    if kind:
        filters["doc_kind"] = kind
    if unpaid:
        filters["payment_status"] = ["in", [contracts.UNPAID, contracts.PARTIAL]]
    if date_from and date_to:
        filters["doc_date"] = ["between", [date_from, date_to]]
    elif date_from:
        filters["doc_date"] = [">=", date_from]
    elif date_to:
        filters["doc_date"] = ["<=", date_to]
    or_filters = None
    if q:
        or_filters = [["doc_no", "like", f"%{q}%"], ["supplier_name", "like", f"%{q}%"]]
    return filters, or_filters


def list_purchases(*, supplier=None, supplier_name=None, status=None, kind=None, unpaid=False,
                   q=None, date_from=None, date_to=None, limit=50, start=0):
    """采购记录一页。`limit=0` 是"全部"（导出用），不是"五十条"。

    `list_purchases` 原来只有 `limit`，页面又是自绘表格 —— 换成原生列表后要分页，
    所以补上 `start`；总数由 `purchase_total` 给。
    """
    guard.require("purchase.read")
    filters, or_filters = _purchase_query(
        supplier=supplier, supplier_name=supplier_name, status=status, kind=kind,
        unpaid=unpaid, q=q, date_from=date_from, date_to=date_to)
    return frappe.get_list(
        DOCTYPE, filters=filters, or_filters=or_filters, fields=list(LIST_FIELDS),
        order_by="doc_date desc, creation desc",
        limit_start=int(start or 0), limit_page_length=_size(limit),
    )


def purchase_total(*, supplier=None, supplier_name=None, status=None, kind=None, unpaid=False,
                   q=None, date_from=None, date_to=None) -> int:
    guard.require("purchase.read")
    filters, or_filters = _purchase_query(
        supplier=supplier, supplier_name=supplier_name, status=status, kind=kind,
        unpaid=unpaid, q=q, date_from=date_from, date_to=date_to)
    return len(frappe.get_list(DOCTYPE, filters=filters, or_filters=or_filters, pluck="name",
                               limit_page_length=0))


def _size(limit):
    """与盘点那边同一条：`0` / 空 = 不截断（分页只管第一页）。"""
    if limit in (None, "", 0, "0"):
        return None
    return max(0, int(limit))


def get_purchase(name):
    guard.require("purchase.read")
    doc = _get(name)
    data = {field: doc.get(field) for field in LIST_FIELDS}
    for field in ("subtotal", "vat_amount", "wht_amount", "discount_total", "rounding",
                  "void_reason", "voided_at", "external_ref", "notes", "stock_posted_at", "has_vat"):
        data[field] = doc.get(field)
    from pearnly_erp_portal.inventory import positions

    data["lines"] = [
        {"line_no": row.line_no, "item_type": row.item_type, "description": row.description,
         "product_ref": row.product_ref, "product": row.product,
         **(positions.public(row, company=doc.get("company")) if row.product else {}), "qty": row.qty, "unit": row.unit,
         "unit_price": row.unit_price, "discount": row.discount,
         "vat_rate": row.vat_rate, "line_total": row.line_total}
        for row in doc.get("lines") or []
    ]
    data["payments"] = [
        {"payment_date": row.payment_date, "amount": row.amount, "method": row.method,
         "note": row.note}
        for row in doc.get("payments") or []
    ]
    return data


def update_purchase(name, **changes):
    """改草稿。已提交或已作废的单据一律拒绝改动。"""
    guard.require("purchase.write")
    doc = _get(name)
    if doc.status == contracts.VOID:
        frappe.throw(_(VOIDED))
    if doc.status != contracts.DRAFT:
        frappe.throw(_(LOCKED))

    for field in ("doc_no", "doc_date", "due_date", "currency", "supplier",
                  "supplier_name", "supplier_tax_id", "external_ref", "notes", "source"):
        if field in changes:
            doc.set(field, changes[field])
    if "doc_kind" in changes:
        doc.doc_kind = changes["doc_kind"]
    if "has_vat" in changes:
        doc.has_vat = 1 if changes["has_vat"] in (True, 1, "1", "true") else 0
    if "lines" in changes:
        lines = changes["lines"]
        if not isinstance(lines, list) or not lines:
            frappe.throw(_(LINES_REQUIRED))
        doc.lines = []
        from pearnly_erp_portal.inventory import positions

        for index, line in enumerate(lines, start=1):
            line = positions.prepare({**line, "item_type": "service" if doc.doc_kind == "service"
                                      else line.get("item_type", "goods")}, company=doc.get("company"))
            doc.append("lines", {
                "line_no": index,
                "item_type": "service" if doc.doc_kind == "service" else line.get("item_type", "goods"),
                "description": line["description"],
                "product_ref": line.get("product_ref"),
                "product": line.get("product"),
                "warehouse": line.get("warehouse"), "location": line.get("location", ""),
                "qty": line.get("qty", 1),
                "unit": line.get("unit"),
                "unit_price": line.get("unit_price", 0),
                "discount": line.get("discount", 0),
                "vat_rate": line.get("vat_rate", 7),
            })
    for field in ("discount_total", "wht_amount", "rounding", "fx_rate"):
        if field in changes:
            doc.set(field, changes[field])

    ref = contracts.duplicate_key(doc.supplier or doc.supplier_name, doc.doc_no)
    _dedupe_check(doc.dedupe_key, ref, exclude=doc.name)
    doc.dedupe_ref = ref
    doc.save()
    frappe.db.commit()
    return {"name": doc.name}


def set_status(name, target, reason=None, receive_stock=None):
    """流转：草稿→提交、草稿/提交→作废。非法流转一律拒绝。"""
    guard.require("purchase.write")
    frappe.db.sql("select name from `tabPurchase Document` where name=%s for update", (name,))
    doc = _get(name)
    if (doc.calculation_version or 0) >= 2 and target == contracts.VOID:
        from pearnly_erp_portal.purchasing import entry

        result = entry.cancel(name, reason)
        frappe.db.commit()
        return result
    if not contracts.can_transition(doc.status, target):
        frappe.throw(_(BAD_TRANSITION))
    if target == contracts.VOID and doc.paid_amount and _q2(doc.paid_amount) > 0:
        # 已收付款的单据不能直接作废：先把付款冲掉，否则账上会留下无主的钱。
        frappe.throw(_("Void the payments before voiding the document"))
    doc.status = target
    if target == contracts.VOID:
        doc.void_reason = reason
        doc.voided_at = now()
    doc.save()
    if target == contracts.SUBMITTED:
        _post_stock(doc, receive_stock=receive_stock)
        from pearnly_erp_portal.express import push as express_push

        express_push.queue("Purchase Document", doc.name)  # 旧的草稿→提交入口同样交给 Express 账本
    elif target == contracts.VOID and doc.stock_posted_at:
        from pearnly_erp_portal.inventory import stock

        stock.reverse_movements(ref_type="purchase", ref_id=doc.name,
                                client_uuid=f"purchase-void:{doc.name}", reason=reason,
                                company=doc.get("company"))
    frappe.db.commit()
    return {"name": doc.name, "status": doc.status}


def _post_stock(doc, receive_stock=None) -> dict | None:
    """确认后按设置把货物过账到库存。

    - 只有采购设置里打开 `auto_stock_in` 才动库存；关着就是"只记账不动货"（旧线上
      也是这个开关），并且**不会假装扣过**。
    - 只对带商品链接、且是货物类别的行过账：服务类与没关联商品的行没有库存可言。
    - 幂等锚是单据名，所以重复确认不会重复入库。
    """
    from pearnly_erp_portal.inventory import positions, stock, warehouses

    enabled = settings.read_settings()["auto_stock_in"] if receive_stock is None else (
        receive_stock in (True, 1, "1", "true"))
    if not enabled:
        return None
    if receive_stock in (True, 1, "1", "true") and any(
            not row.product and (row.item_type or "goods") == "goods" for row in doc.lines):
        frappe.throw(_("Select a product for each stock item"))
    lines = [
        {"product": row.product, "qty": row.qty, "unit": row.unit,
         "unit_cost": _line_net_cost(row), **positions.saved(row, company=doc.get("company"))}
        for row in doc.get("lines") or []
        if row.get("product") and (row.item_type or "goods") == "goods"
    ]
    if not lines:
        return None
    result = stock.apply_lines(
        # 与 `purchasing/entry.py` 同一口径：入库仓来自**这张单上明确的仓**
        # （每行已经由 `positions.saved()` 定过）；行上也没写时落**这家公司卖货的那个仓**，
        # 没有收银台才退回"唯一仓/报错"（见 `warehouses.receiving`）。
        warehouse=warehouses.receiving(lines[0].get("warehouse"), company=doc.get("company")),
        movement_type="purchase_in", lines=lines,
        posting_date=str(doc.doc_date), ref_type="purchase", ref_id=doc.name,
        # 流水上记的是**我方单据号**（采购 PE），对方单号留在采购单上。
        doc_no=doc.system_no or doc.doc_no, client_uuid=f"purchase-in:{doc.name}", internal=True,
        company=doc.get("company"),
    )
    doc.db_set("stock_posted_at", now(), update_modified=False)
    return result


def _line_net_cost(row):
    """入库单位成本 = 行净额 / 数量（不含 VAT，折扣已扣）。成本未知时返回 None，
    让库存那边按"成本未确立"处理，而不是当成 0。"""
    try:
        qty = Decimal(str(row.qty or 0))
        if qty <= 0:
            return None
        base = row.line_total if row.line_total is not None else row.unit_price
        if base in (None, ""):
            return None
        return str(contracts.q2(Decimal(str(base)) / qty))
    except (ArithmeticError, TypeError):
        return None


def pay(name, *, amount, method=None, date=None, note=None):
    """登记一笔付款。多笔累加；超过净应付的付款被拒。"""
    guard.require("purchase.write")
    doc = _get(name)
    if (doc.calculation_version or 0) >= 2:
        from pearnly_erp_portal.purchasing import entry

        result = entry.payment(name, amount=amount, method=method or "cash", date=date, note=note)
        frappe.db.commit()
        return result
    if doc.status == contracts.VOID:
        frappe.throw(_(VOIDED))
    if doc.status != contracts.SUBMITTED:
        frappe.throw(_("Confirm the document before recording a payment"))
    try:
        paid_now = contracts.amount(amount, field="amount", allow_zero=False)
    except ValueError:
        frappe.throw(_(INVALID))
    net = _q2(doc.net_payable)
    total = _q2(doc.paid_amount) + paid_now
    if total > net:
        frappe.throw(_("The payment is more than the net payable"))
    doc.append("payments", {
        "payment_date": date or nowdate(),
        "amount": paid_now,
        "method": method,
        "note": note,
    })
    doc.paid_amount = total
    doc.payment_status = contracts.payment_status(total, net)
    doc.save()
    frappe.db.commit()
    return {"name": doc.name, "paid_amount": doc.paid_amount, "payment_status": doc.payment_status}


def set_payment_status(name, status):
    """直接把单据标成未付/已付（旧线上有这条快捷路径，用于线下已结清的单据）。"""
    guard.require("purchase.write")
    doc = _get(name)
    if (doc.calculation_version or 0) >= 2:
        from pearnly_erp_portal.purchasing import entry

        if status not in (contracts.UNPAID, contracts.PAID):
            frappe.throw(_(INVALID))
        result = entry.payment(name, paid=status == contracts.PAID)
        frappe.db.commit()
        return result
    if status not in (contracts.UNPAID, contracts.PARTIAL, contracts.PAID):
        frappe.throw(_(INVALID))
    if status == contracts.PAID:
        doc.paid_amount = doc.net_payable
    elif status == contracts.UNPAID:
        doc.paid_amount = 0
    doc.payment_status = status
    doc.save()
    frappe.db.commit()
    return {"name": doc.name, "paid_amount": doc.paid_amount, "payment_status": doc.payment_status}


def summary(*, date_from=None, date_to=None):
    """期间汇总。金额求和用 Decimal，避免浮点误差累积。"""
    guard.require("purchase.read")
    filters: dict = {"status": ["!=", contracts.VOID], **guard.scope_filters(),
                     "company": companies.active_company()}
    if date_from and date_to:
        filters["doc_date"] = ["between", [date_from, date_to]]
    rows = frappe.get_list(
        DOCTYPE, filters=filters,
        fields=["grand_total", "vat_amount", "net_payable", "paid_amount"],
        limit_page_length=0,
    )

    def field(row, key):
        return row.get(key) if isinstance(row, dict) else getattr(row, key, None)

    total = sum((_q2(field(r, "grand_total")) for r in rows), Decimal("0"))
    vat = sum((_q2(field(r, "vat_amount")) for r in rows), Decimal("0"))
    payable = sum((_q2(field(r, "net_payable")) for r in rows), Decimal("0"))
    paid = sum((_q2(field(r, "paid_amount")) for r in rows), Decimal("0"))
    return {
        "count": len(rows),
        "grand_total": str(total),
        "vat_amount": str(vat),
        "net_payable": str(payable),
        "paid_amount": str(paid),
        "outstanding": str(payable - paid),
    }
