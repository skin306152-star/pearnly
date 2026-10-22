"""ERP 采购 / 销售单推进事务所 Express（经 Cowork 网关），并把结果（Express 单号、停下的原因）记回 ERP 单据。

所有入口（网页手录、网页拍照、LINE 拍照）都汇到 `sales.entry.post` / `purchasing.entry.post`，提交前调 :func:`queue`：
这张单该推（这站点接了 Express、是真进货 / 真销售）就标「排队中」，事务提交后再交给网关（:func:`send`）。
之后每分钟问一次网关（:func:`poll`）：写进去了记单号；停在事务所（要人选、找不到账套、没有电脑能写）记原因、继续问；
网关连不上不改状态，下一分钟再交 / 再问。ERP 自己的库存照旧按单过账，写进 Express 后由主档同步对齐（`masters`）。
"""
import frappe
from frappe.utils import now_datetime

from pearnly_erp_portal.express import contracts, gateway

SALES = "ERP Sales Document"
PURCHASE = "Purchase Document"
DIRECTIONS = {SALES: "sales", PURCHASE: "purchase"}
POLL_MAX = 100


def _eligible(doctype, doc) -> bool:
    if doc.get("status") != "submitted":
        return False
    kinds = contracts.SALES_KINDS if doctype == SALES else contracts.PURCHASE_KINDS
    if (doc.get("doc_kind") or "") not in kinds:
        return False
    tax = frappe.db.get_value("ERP Company", doc.get("company"), "tax_id") if doc.get("company") else ""
    return bool(contracts.tax13(tax))


def queue(doctype, name):
    """提交事务里调（`post` 的 ``finalize`` 之后、提交之前）：该推就标「排队中」并排提交后的任务；不该推什么都不做。"""
    if doctype not in DIRECTIONS or not gateway.configured():
        return
    doc = frappe.db.get_value(doctype, name, ["name", "status", "doc_kind", "company", "express_state"], as_dict=True)
    if not doc or doc.express_state or not _eligible(doctype, doc):
        return
    frappe.db.set_value(doctype, name, {"express_state": contracts.QUEUED, "express_reason": ""},
                        update_modified=False)
    frappe.enqueue("pearnly_erp_portal.express.push.send", queue="short", enqueue_after_commit=True,
                   doctype=doctype, name=name)


def ref(name, creation) -> str:
    """交给网关的单据标识：单号 + 建单时刻。单号在一个站点里不重复，但站点从备份恢复、重建后会从头再编，
    带上建单时刻免得撞上以前交过的同号单（网关按它认是不是同一件）。"""
    stamp = frappe.utils.get_datetime(creation).strftime("%Y%m%d%H%M%S%f") if creation else ""
    return f"{name}@{stamp}" if stamp else str(name)


def _party(doctype, doc):
    if doctype == SALES:
        row = frappe.db.get_value("ERP Customer", doc.customer, ["express_code", "customer_name", "tax_id", "branch_no"],
                                  as_dict=True) if doc.customer else None
        return {"code": (row or {}).get("express_code") or "", "name": doc.buyer_name or (row or {}).get("customer_name"),
                "tax_id": (row or {}).get("tax_id") or "", "branch": (row or {}).get("branch_no") or ""}
    row = frappe.db.get_value("ERP Supplier", doc.supplier, ["express_code", "supplier_name", "tax_id", "branch_no"],
                              as_dict=True) if doc.supplier else None
    return {"code": (row or {}).get("express_code") or "", "name": doc.supplier_name or (row or {}).get("supplier_name"),
            "tax_id": doc.supplier_tax_id or (row or {}).get("tax_id") or "",
            "branch": doc.supplier_branch_no or (row or {}).get("branch_no") or ""}


def _lines(doctype, doc):
    products, warehouses, out = {}, {}, []
    for line in doc.lines:
        product = products.get(line.product)
        if line.product and product is None:
            product = products[line.product] = frappe.db.get_value(
                "ERP Product", line.product, ["product_name", "express_code", "express_stock"], as_dict=True) or {}
        product = product or {}
        if line.warehouse and line.warehouse not in warehouses:
            warehouses[line.warehouse] = frappe.db.get_value("ERP Warehouse", line.warehouse, "express_code") or ""
        code = product.get("express_code") or ""
        if code:
            stock = bool(product.get("express_stock"))
        elif doctype == PURCHASE:
            stock = (line.get("item_type") or "goods") == "goods" and bool(line.product)
        else:
            stock = bool(line.product)
        out.append({"name": line.description or product.get("product_name") or code, "code": code, "unit": line.unit,
                    "qty": line.qty, "amount": line.line_total, "stock": stock,
                    "loccod": warehouses.get(line.warehouse, "") if line.warehouse else ""})
    return out


def document(doctype, name) -> dict:
    """这张 ERP 单交给网关的样子（`contracts.document`）。"""
    doc = frappe.get_doc(doctype, name)
    company = frappe.db.get_value("ERP Company", doc.company, ["tax_id", "branch"], as_dict=True) or {}
    if doctype == SALES:
        number, paid, currency = doc.system_no, doc.payment_status == "paid", "THB"
    else:
        number, paid = doc.doc_no or doc.system_no, bool(doc.is_paid) or doc.payment_status == "paid"
        currency = doc.currency or "THB"
    return contracts.document(ref=ref(doc.name, doc.creation), direction=DIRECTIONS[doctype],
                              doc_date=str(doc.doc_date), number=number,
                              company=company, party=_party(doctype, doc), paid=paid, has_vat=bool(doc.has_vat),
                              total=doc.grand_total, vat=doc.vat_amount, lines=_lines(doctype, doc), currency=currency)


def _record(doctype, name, result):
    values = {"express_state": contracts.state(result.get("state")) or contracts.QUEUED,
              "express_docnum": str(result.get("docnum") or "")[:40],
              "express_reason": str(result.get("reason") or "")[:500]}
    frappe.db.set_value(doctype, name, values, update_modified=False)
    return values


def send(doctype, name):
    """交给网关一次。网关拒收（单据不合规、公司在事务所里找不到）记「交不进」与原因；连不上不改状态，等下一轮。"""
    if doctype not in DIRECTIONS or not gateway.configured():
        return
    try:
        body = document(doctype, name)
        result = gateway.call("submit", body)
    except gateway.Unavailable:
        return
    frappe.db.set_value(doctype, name, "express_sent_at", now_datetime(), update_modified=False)
    if not result.get("ok"):
        _record(doctype, name, {"state": contracts.FAILED, "reason": result.get("code")})
    else:
        values = _record(doctype, name, result)
        if values["express_state"] == contracts.WRITTEN:
            _written(doctype, name)
    frappe.db.commit()


def _written(doctype, name):
    """写进 Express 了：这家公司的库存要按 Express 对齐（排一次主档同步，同一家公司排着的不重复排）。"""
    from pearnly_erp_portal.express import masters

    company = frappe.db.get_value(doctype, name, "company")
    if company:
        masters.enqueue(company)


def poll():
    """每分钟：没交出去的再交，交出去还没结果的问一次网关。没有要问的就什么都不做（一条带索引的查询）。"""
    if not gateway.configured():
        return
    for doctype, direction in DIRECTIONS.items():
        rows = frappe.get_all(doctype, filters={"express_state": ["in", contracts.OPEN_STATES]},
                              fields=["name", "creation", "express_sent_at", "express_state", "express_docnum"],
                              order_by="modified asc", limit_page_length=POLL_MAX)
        unsent = [row.name for row in rows if not row.express_sent_at]
        for name in unsent:
            send(doctype, name)
        asked = [row for row in rows if row.express_sent_at]
        if not asked:
            continue
        try:
            result = gateway.call("status", {"documents": [{"ref": ref(row.name, row.creation), "direction": direction}
                                                           for row in asked]})
        except gateway.Unavailable:
            continue
        if not result.get("ok"):
            continue
        known = {item.get("ref"): item for item in result.get("documents") or [] if isinstance(item, dict)}
        for row in asked:
            item = known.get(ref(row.name, row.creation))
            if not item:
                continue
            before = (row.express_state, row.express_docnum or "")
            values = _record(doctype, row.name, item)
            if values["express_state"] == contracts.WRITTEN and before != (contracts.WRITTEN, values["express_docnum"]):
                _written(doctype, row.name)
        frappe.db.commit()
