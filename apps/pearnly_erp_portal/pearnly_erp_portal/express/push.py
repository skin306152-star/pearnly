"""ERP 采购 / 销售单写进 Express（CC 桌面版直连 ERP，ADR-025），并把结果（Express 单号、停下的原因）记回 ERP 单据。

所有入口（网页手录、网页拍照、LINE 拍照）都汇到 `sales.entry.post` / `purchasing.entry.post`，提交前调 :func:`queue`：这张单该写
（是真进货 / 真销售，本公司的账套有 CC 桌面版报过）就标「排队中」，事务提交后在后台准备（:func:`send`）：选账套、按共用写账规则
备好载荷（`prepare`），交进共用机器通道的写账队列（`pearnly_core.cc_desktop.jobs`，录入人 = 开这张单的人）。之后由电脑领、写、
回报；每次状态变了，共用通道在同一事务里交回 :func:`job_changed`，单据上记 Express 单号或原因（HTTP 成功、交进队列都不算写入）。

停下的单（要人选的、没有账套、没有电脑能写、主档还在读）记原因；会自己好的（`contracts.RETRY_REASONS`）每分钟再准备一次
（:func:`poll`），要人选的在单据上选一次（`api.answer`）后再准备。ERP 自己的库存照旧按单过账，写进 Express 后由主档同步对齐。
"""
import json

import frappe
from frappe.utils import now_datetime

from pearnly_core.cc_desktop import books, jobs

from pearnly_erp_portal.express import contracts, prepare

SALES = "ERP Sales Document"
PURCHASE = "Purchase Document"
DIRECTIONS = {SALES: "sales", PURCHASE: "purchase"}
POLL_MAX = 100
COMPANY = "ERP Company"


def _company(name):
    return frappe.db.get_value(COMPANY, name, ["name", "company_name", "tax_id", "branch"], as_dict=True) if name else None


def linked(company) -> bool:
    """这家公司接了 Express：CC 桌面版报过它（税号 + 分店）的账套。没有就不推、不同步（ERP 单据照旧只在 ERP 里）。"""
    row = _company(company)
    return bool(row and contracts.tax13(row.tax_id) and books.for_company(row.tax_id, row.branch))


def _eligible(doctype, doc) -> bool:
    if doc.get("status") != "submitted":
        return False
    kinds = contracts.SALES_KINDS if doctype == SALES else contracts.PURCHASE_KINDS
    return (doc.get("doc_kind") or "") in kinds and linked(doc.get("company"))


def queue(doctype, name):
    """提交事务里调（`post` 的 ``finalize`` 之后、提交之前）：该写就标「排队中」并排提交后的准备；不该写什么都不做。"""
    if doctype not in DIRECTIONS:
        return
    doc = frappe.db.get_value(doctype, name, ["name", "status", "doc_kind", "company", "express_state"], as_dict=True)
    if not doc or doc.express_state or not _eligible(doctype, doc):
        return
    frappe.db.set_value(doctype, name, {"express_state": contracts.QUEUED, "express_reason": ""},
                        update_modified=False)
    frappe.enqueue("pearnly_erp_portal.express.push.send", queue="short", enqueue_after_commit=True,
                   doctype=doctype, name=name)


def ref(name, creation) -> str:
    """单据标识：单号 + 建单时刻。单号在一个站点里不重复，但站点从备份恢复、重建后会从头再编，带上建单时刻免得撞上以前写过的
    同号单（写账队列按它认是不是同一件）。"""
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
    """这张 ERP 单交给写账规则的样子（`contracts.document`）。"""
    doc = frappe.get_doc(doctype, name)
    company = frappe.db.get_value(COMPANY, doc.company, ["tax_id", "branch"], as_dict=True) or {}
    if doctype == SALES:
        number, paid, currency = doc.system_no, doc.payment_status == "paid", "THB"
    else:
        number, paid = doc.doc_no or doc.system_no, bool(doc.is_paid) or doc.payment_status == "paid"
        currency = doc.currency or "THB"
    return contracts.document(ref=ref(doc.name, doc.creation), direction=DIRECTIONS[doctype],
                              doc_date=str(doc.doc_date), number=number,
                              company=company, party=_party(doctype, doc), paid=paid, has_vat=bool(doc.has_vat),
                              total=doc.grand_total, vat=doc.vat_amount, lines=_lines(doctype, doc), currency=currency)


def _set(doctype, name, values):
    frappe.db.set_value(doctype, name, values, update_modified=False)


def send(doctype, name):
    """准备并交进写账队列（后台任务；停下的单每分钟由 :func:`poll` 再叫）。已经交进去的不再交。"""
    if doctype not in DIRECTIONS:
        return
    row = frappe.db.get_value(doctype, name, ["name", "creation", "status", "company", "owner", "express_state",
                                              "express_job", "doc_kind"], as_dict=True)
    if not row or row.status != "submitted" or row.express_job or row.express_state not in contracts.OPEN_STATES:
        return
    from pearnly_erp_portal.express import masters

    body = document(doctype, name)
    result = prepare.prepare(body, _company(row.company), masters.ensure)
    book = result.get("book") or {}
    if "failed" in result:
        _set(doctype, name, {"express_state": contracts.FAILED, "express_reason": result["failed"][:500],
                             "express_needed": None})
    elif "hold" in result:
        _set(doctype, name, {"express_state": contracts.HELD, "express_reason": result["hold"][:500],
                             "express_book": book.get("key") or None,
                             "express_needed": json.dumps(result.get("needed") or [], ensure_ascii=False) or None})
    else:
        _set(doctype, name, {"express_book": book["key"], "express_needed": None, "express_sent_at": now_datetime()})
        payload = result["payload"]
        # 再写一次（`api.retry`）撤掉的那件占着原来的请求号：后面的按第几件编号
        earlier = len(jobs.of_source(doctype, name))
        key = f"erp:{DIRECTIONS[doctype]}:{body['ref']}" + (f"#{earlier + 1}" if earlier else "")
        job = jobs.enqueue(source_doctype=doctype, source_name=name, request_key=key, actor=row.owner,
                           book_key=book["key"], payload=payload, payload_version=int(payload.get("payload_version") or 1),
                           label=body["number"])
        _set(doctype, name, {"express_job": job["job"]})
    frappe.db.commit()


def _state(view):
    status = view.get("status")
    if status == "success":
        return contracts.MISMATCH if view.get("verify_state") == "mismatch" else contracts.WRITTEN
    if status in ("queued", "leased"):
        return contracts.QUEUED
    if status == "cancelled":
        return contracts.FAILED
    return contracts.HELD


def job_changed(view):
    """共用写账队列里这张单有了新状态（同一事务里叫）：记回单据；第一次写进去后排一次主档同步（库存按 Express 对齐）。"""
    doctype, name = view.get("source_doctype"), view.get("source_name")
    if doctype not in DIRECTIONS or not name or not frappe.db.exists(doctype, name):
        return
    before = frappe.db.get_value(doctype, name, ["express_state", "express_docnum", "company"], as_dict=True)
    state = _state(view)
    values = {"express_state": state, "express_job": view.get("job"), "express_docnum": view.get("docnum") or "",
              "express_reason": "" if state in (contracts.WRITTEN, contracts.QUEUED) else str(view.get("reason") or "")[:500]}
    _set(doctype, name, values)
    if state in (contracts.WRITTEN, contracts.MISMATCH) and (before.express_state, before.express_docnum or "") != (
            state, values["express_docnum"]) and before.company:
        from pearnly_erp_portal.express import masters

        masters.enqueue(before.company)


def _queued_note(row):
    """排队中的单此刻为什么还没写（页面写原因，不改状态）：录入人一台登录过 CC 的电脑都没有 →「CC 桌面版未连接」；他能写的
    电脑都关着 → 离线；他的电脑都够不到这份账套 → 没有电脑能写。"""
    found = jobs.writers(row.owner, row.express_book) if row.express_book else {"state": "ok"}
    return {"missing": contracts.NOT_CONNECTED, "offline": contracts.OFFLINE,
            "unreachable": contracts.NO_WRITER}.get(found["state"], "")


def poll():
    """每分钟：停下的单里会自己好的再准备一次；排队中还没交进队列的交一次；排队中的记下此刻为什么还没写。没有要看的就只是
    一条带索引的查询。"""
    for doctype in DIRECTIONS:
        rows = frappe.get_all(doctype, filters={"express_state": ["in", contracts.OPEN_STATES], "status": "submitted"},
                              fields=["name", "owner", "express_state", "express_reason", "express_job", "express_book"],
                              order_by="modified asc", limit_page_length=POLL_MAX)
        for row in rows:
            if not row.express_job and (row.express_state == contracts.QUEUED
                                        or (row.express_reason or "") in contracts.RETRY_REASONS):
                send(doctype, row.name)
            elif row.express_job and row.express_state == contracts.QUEUED:
                note = _queued_note(row)
                if note != (row.express_reason or ""):
                    _set(doctype, row.name, {"express_reason": note})
        frappe.db.commit()
