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
from pearnly_core.execution import state as pearnly_state

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.express import push as express_push
from pearnly_erp_portal.inventory import positions, serials, stock, warehouses
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.numbering import service as numbering
from pearnly_erp_portal.products import line_memory
from pearnly_erp_portal.sales import access, contracts, files
from pearnly_erp_portal.masters import access as master_access

DOCTYPE = "ERP Sales Document"
LINE_DOCTYPE = "ERP Sales Document Line"

HEAD_FIELDS = ("customer", "party_type", "address", "source", "source_file", "note",
               "image_sha256", "printed_total", "ocr_review")
LINE_FIELDS = ("item_type", "product", "description", "qty", "unit", "unit_price", "discount",
               "discount_pct", "vat_applicable", "warehouse", "new_item")

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
        # 出不出库按行定（11-06 按行选物料与库存）：没给的提交时按预选补（`line_memory.fill`），给了的照人定的
        row["item_type"] = row.get("item_type") or None
        if not row["description"]:
            frappe.throw(_("Item Name is required"))
        if row["item_type"] not in ("goods", "service", None):
            frappe.throw(_("Invalid value"))
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
                                    header_discount_amount=payload.get("header_discount") or 0,
                                    price_includes_vat=flag(payload.get("price_includes_vat"), False))
    return {"subtotal": str(tots["subtotal"]), "discount_total": str(tots["discount_total"]),
            "vat_amount": str(tots["vat_amount"]), "grand_total": str(tots["grand_total"]),
            "lines": [{"line_no": row["line_no"], "line_total": str(row["line_total"])}
                      for row in tots["lines"]]}


def preview(payload):
    guard.require("sales.read")
    return _calculate(payload)


# ── 买方：销项的单据上就有买方块，客户主档可以有也可以没有 ──────────────────


def _buyer(payload, company=None, started=None):
    from pearnly_erp_portal.masters.selection import match_party, unlinked_party

    key = payload.get("customer")
    name = str(payload.get("buyer_name") or "").strip()
    # 识别读出的买方税号 / 分店（销售单本身不存，随提交带来）：先按税号认客户，认不出才按名称，新建时一并记下
    tax = contracts.buyer_tax(payload.get("buyer_tax_id"))
    branch = contracts.branch_no(payload.get("buyer_branch_no")) if tax else None
    if not key and tax:
        branch_type = "branch" if branch and branch != "00000" else "hq"
        key = (match_party("customer", name, tax_id=tax, branch_type=branch_type, branch_no=branch, company=company,
                           ambiguous="none")
               or unlinked_party("customer", tax, branch_type=branch_type, branch_no=branch))
    if not key:
        key = match_party("customer", name, company=company)
    if key:
        if not frappe.db.exists("ERP Customer", key):
            frappe.throw(_("Customer not found. Please select the customer again."))
        doc = frappe.get_doc("ERP Customer", key)
        if not master_access.usable(getattr(doc, master_access.OWNER, None), company):
            # 别家公司 Express 同步来的档不能挂在这家的单上（主档按公司隔离，服务端也拒；10-24 验收）
            frappe.throw(_("Customer not found. Please select the customer again."))
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
    # 新买方进客户名录（录制问题 #13）：与采购提交时自动建供应商同一做法。散客（匿名）和
    # 「未知买方」不建 —— 那不是一个真实客户。
    unknown = {_("Unknown buyer", lang=lang) for lang in ("en", "zh", "th", "ja")}
    if not key and party != "anonymous" and name not in unknown:
        identity = {"tax_id": tax, "branch_type": "branch" if branch and branch != "00000" else "hq",
                    "branch_no": branch if branch and branch != "00000" else None} if tax else {}
        # 单据开始时已接上 Express 的公司：随单建的档先归这家公司（不对全租户各家都可见），写进去后挂上 Express 码（`push.link_party`）
        owner = express_push.owner(company, started)
        key = frappe.get_doc({"doctype": "ERP Customer", "customer_name": name, "party_type": party,
                              "address": payload.get("address"), "is_active": 1, "express_company": owner,
                              **identity}).insert(ignore_permissions=True).name
    return key, name


# ── 提交 ────────────────────────────────────────────────────────────────────


def post(payload, *, request_id, name=None, finalize=None, allow_duplicate=False):
    """`allow_duplicate` is server-only (LINE intake records duplicates, user 2026-09-25)."""
    guard.require("sales.write")
    if not isinstance(payload, dict) or not request_id or len(str(request_id)) > 140:
        frappe.throw(_("Invalid value"))
    payload = dict(payload)
    fingerprint = hashlib.sha256(json.dumps([payload, name], sort_keys=True,
                                           ensure_ascii=False, default=str).encode()).hexdigest()
    with pearnly_state.store().lock(pearnly_state.store().make_key("erp-sales-post"), timeout=120, blocking_timeout=20):
        try:
            # 加锁读查重：读视图早于拿锁，普通读看不到另一路刚提交的同号单（11-28 B ③）；锁住这一行到本事务结束
            existing = frappe.db.get_value(DOCTYPE, {"request_id": request_id},
                                           ["name", "status", "request_hash", "company", "owner"], as_dict=True,
                                           for_update=True)
            if existing:
                # 权限同 `access.get_document`（公司、只看自己的），按加锁读到的这几列判：这张可能是读视图之后才建的，
                # 普通 get_doc 读不到它
                if access.document_permission(existing, "read", frappe.session.user) is False:
                    frappe.throw(_("Not permitted"), frappe.PermissionError)
                if existing.request_hash != fingerprint:
                    frappe.throw(_("Request ID already exists"))
                return {"name": existing.name, "status": existing.status, "duplicate": True}
            result = _post(payload, request_id, fingerprint, name, allow_duplicate)
            if finalize:
                finalize(result)
            express_push.queue(DOCTYPE, result["name"])  # 接了 Express 的站点：提交后交给事务所的账本
            frappe.db.commit()
            return result
        except Exception:
            frappe.db.rollback()
            raise


def _post(payload, request_id, fingerprint, name, allow_duplicate=False):
    kind = payload.get("doc_kind") or contracts.RECEIPT
    if kind not in contracts.DOC_KINDS:
        frappe.throw(_("Invalid value"))
    doc = access.get_document(name, write=True, lock=True) if name else frappe.new_doc(DOCTYPE)
    if name and doc.status != contracts.DRAFT:
        frappe.throw(_("A confirmed document cannot be edited"))
    company = companies.document_company(doc.get("company") if name else payload.get("company"))
    day = str(getdate(payload.get("doc_date") or nowdate()))
    raw_lines = list(payload.get("lines") or []) if isinstance(payload.get("lines"), list) else []
    payload["lines"] = _lines(payload)
    totals = contracts.compute_totals(
        payload["lines"],
        vat_rate=_vat_rate(payload) if flag(payload.get("has_vat"), True) else 0,
        header_discount_amount=payload.get("header_discount") or 0,
        price_includes_vat=flag(payload.get("price_includes_vat"), False))
    reasons = contracts.confirm_gates({"doc_kind": kind, "grand_total": totals["grand_total"]})
    if reasons:
        # 拒因码翻成话（`erp.detail.*`）；认不出的码不拼给用户（2026-10-09）
        details = [_("erp.detail.zero_total") for reason in reasons if reason == "zero_amount"]
        frappe.throw(f"{_(BLOCKED)}: {'; '.join(details)}" if details else _(BLOCKED))
    if payload.get("image_sha256") and not allow_duplicate:
        duplicates = frappe.get_all(DOCTYPE, filters={"image_sha256": payload["image_sha256"],
            "status": ["!=", contracts.VOID]}, pluck="name")
        if any(key != name for key in duplicates):
            frappe.throw(_("Document already exists"))
    # 单据开始时刻（草稿按建草稿，`push.started`）：随单建的客户归不归这家公司按它
    customer, buyer_name = _buyer(payload, company, express_push.started(DOCTYPE, name) if name else None)
    # 仓库按行（11-19，同 Express 原生 IV / HS 明细行选仓）：行上的仓为准；单头给的仓只作没写仓那几行的默认
    place = positions.header(payload.get("warehouse"), company=company)
    party_tax = _party_tax(customer, payload)
    line_memory.fill([row for row in payload["lines"] if not row.get("item_type")], company=company, direction="sales",
                     party_tax=party_tax, party_name=buyer_name)
    created = []
    # 认不到物料的出库行随单新建（同 LINE，评估 12 节 #2）；报价单不出库，只认不建
    create = contracts.should_post_stock(kind, contracts.SUBMITTED)
    rows = [_line_row(raw, computed, company=company, place=place, create=create, created=created)
            for raw, computed in zip(payload["lines"], totals["lines"])]
    # 单头存默认仓：没给就是第一条出库行的仓（列表、Express 兜底用它）
    place["warehouse"] = place["warehouse"] or next((row["warehouse"] for row in rows if row.get("warehouse")), None)
    collection = _collection(payload, kind, totals["grand_total"], day)
    source = payload.get("source") or doc.get("source") or "manual"
    doc.update({key: payload[key] for key in HEAD_FIELDS if key in payload})
    doc.source = source
    # 一租户多公司：单据挂公司（没给就是当前公司）。
    doc.company = company
    doc.update(place)
    # 明细必须是子表文档：直接塞 dict 列表，控制器里的 `line.description` 会拿到 dict。
    doc.set("lines", [])
    for row in rows:
        doc.append("lines", row)
    # 按台管理的商品：这一行选中的台（页面随行交来，`inventory.serials`）
    serials.set_doc_serials(doc, [{**row, "serials": (raw.get("serials") if isinstance(raw, dict) else None)}
                                  for row, raw in zip(rows, raw_lines)])
    doc.update({"doc_kind": kind, "doc_date": day, "customer": customer, "buyer_name": buyer_name,
        "has_vat": int(flag(payload.get("has_vat"), True)), "vat_rate": _vat_rate(payload),
        "price_includes_vat": int(flag(payload.get("price_includes_vat"), False)),
        "header_discount": totals["header_discount"],
        "system_no": doc.get("system_no") or numbering.allocate(
            direction="sales", on=day,
            exists=lambda value: bool(frappe.db.exists(DOCTYPE, {"system_no": value}))),
        "status": contracts.SUBMITTED, "request_id": request_id, "request_hash": fingerprint,
        "summary": ", ".join(row["description"] for row in rows), **collection})
    doc.flags.sales_action = True
    doc.save()
    line_memory.learn(company, "sales", party_tax, buyer_name, doc.lines, doc.name)
    for product in created:  # 随单新建的物料记下是哪张单建的（物料页「来由」）
        frappe.db.set_value("ERP Product", product, {"origin_doctype": DOCTYPE, "origin_name": doc.name},
                            update_modified=False)
    _post_stock(doc)
    files.bind(doc, payload.get("attachments"))
    digest = files.evidence_hash(doc)
    if digest:
        duplicates = frappe.get_all(DOCTYPE, filters={"image_sha256": digest,
            "status": ["!=", contracts.VOID]}, pluck="name")
        if not allow_duplicate and any(key != doc.name for key in duplicates):
            frappe.throw(_("Document already exists"))
        doc.image_sha256 = digest
    doc.flags.sales_action = True
    doc.save()
    return {"name": doc.name, "status": doc.status, "system_no": doc.system_no}


def _party_tax(customer, payload):
    """记忆键里的对方：客户档的税号，没有档时票面读出的买方税号（散客为空，按名称）。"""
    tax = frappe.db.get_value("ERP Customer", customer, "tax_id") if customer else None
    return tax or contracts.buyer_tax(payload.get("buyer_tax_id")) or ""


def _resolve_item(row, company=None, *, create=False, created=None):
    """明细的物料（11-06 按行选物料与库存）：出库行（``item_type = goods``）认不到就按票面新建（``create``，同 LINE，
    `products.sales.resolve`）；只记账的行不认不建，选了物料的照样核对。

    旧线上销项明细的商品关联是可空的（"现填时可为空"）：只记账、没建档的行照样能记，不动库存。
    """
    if row.get("item_type") == "goods" and (create or row.get("new_item")):
        from pearnly_erp_portal.products import sales as sale_items

        return sale_items.resolve(row, create=True, company=company, created=created)
    row.pop("new_item", None)
    auto = not row.get("product") and row.get("item_type") == "goods"  # 只记账的行不按名认物料
    if auto:
        from pearnly_erp_portal.masters.selection import match_product

        row["product"], found = match_product(row["description"], company)
        if found > 1:
            frappe.throw(_("Several matches found for {0}. Please select a record.").format(row["description"]))
    if row.get("product"):
        item = frappe.get_doc("ERP Product", row["product"])
        item.check_permission("read")
        if not master_access.usable(getattr(item, master_access.OWNER, None), company):
            frappe.throw(_("Product not found"))  # 别家公司 Express 的商品不能用在这家的单上（10-24 验收）
        if not item.is_active:
            frappe.throw(_("Item is disabled"))
        row.setdefault("unit", item.base_unit or item.unit)
        if auto:
            from pearnly_erp_portal.masters.selection import own_unit

            row["unit"] = own_unit(item, row.get("unit"))
    return row


def _line_row(raw, computed, *, company=None, place=None, create=False, created=None):
    """明细行：只带这张单自己的字段（纯规则层算出来的是"金额"，不是行结构）。"""
    row = _resolve_item(dict(raw), company, create=create, created=created)
    row["line_no"] = computed["line_no"]
    row["line_total"] = computed["line_total"]
    row["discount"] = computed["discount"]
    row["discount_pct"] = computed.get("discount_pct")
    row["vat_applicable"] = int(bool(computed["vat_applicable"]))
    place = place or {}
    return positions.prepare(row, place.get("warehouse"), company=company)


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
    units = serials.line_serials(doc)
    rows = [{"product": row.product, "qty": row.qty, "unit": row.unit, "serials": units.get(row.idx, []),
             **positions.saved(row, company=doc.get("company"))}
            for row in doc.lines if row.product and (row.item_type or "goods") == "goods"]  # 只记账的行有物料也不出库
    if not rows:
        return
    # 出库仓来自**这张单上明确的仓**（每行已经由 `positions.saved()` 定过）；行上没写时只认「这家公司只有一个仓」，
    # 多个仓报错让人选（`warehouses.receiving`，服务端不再落卖货仓，多仓闭环 P1）。
    place = warehouses.receiving(rows[0].get("warehouse"), company=doc.get("company"))
    warehouses.remember("sales", place, doc.get("company"))  # 下次预填本人上次选的仓（多仓闭环 P1，问 2）
    stock.apply_lines(warehouse=place,
                      movement_type="sale_out", lines=rows,
                      posting_date=doc.doc_date, ref_type=DOCTYPE, ref_id=doc.name,
                      doc_no=doc.system_no, internal=True, client_uuid="sales-doc:" + doc.name,
                      company=doc.get("company"))
    doc.stock_posted_at = now()


# ── 作废 / 收款 / 删草稿 ────────────────────────────────────────────────────


def cancel(name, reason=None):
    doc = access.get_document(name, write=True, lock=True)
    if doc.status != contracts.SUBMITTED:
        frappe.throw(_("Only a submitted document can be cancelled"))
    if doc.stock_posted_at:
        stock.reverse_movements(ref_type=DOCTYPE, ref_id=doc.name,
                                client_uuid="sales-doc-void:" + doc.name, reason=reason,
                                company=doc.get("company"))
    doc.status, doc.void_reason, doc.voided_at = contracts.VOID, reason, now()
    doc.stock_posted_at = None
    doc.flags.sales_action = True
    doc.save()
    express_push.void("ERP Sales Document", doc.name)  # 放行排队还没写进 Express 的写账件一并撤掉（11-13）
    return {"name": doc.name, "status": doc.status}


def payment(name, *, paid=None, amount=None, method=None, date=None, note=None):
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
    # 收款备注跟钱一起记（与采购付款单上的备注同一件事）；撤销收款就把备注一起清掉。
    doc.payment_note = (note or "").strip()[:500] or None if value > 0 else None
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
