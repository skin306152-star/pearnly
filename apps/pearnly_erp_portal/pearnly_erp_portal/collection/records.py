"""网页「销售 → 收款」的取数：列表、合计、导出共用一份条件（只读；新建 / 修改 / 删除不在网页上）。

能看收款（``collection.view``）的人看**当前公司**的全部收款（切换公司同销售记录）；服务端分页（``limit_start`` /
``limit_page_length``）、白名单排序（`access.listing.order_by`）、原生筛选条件（`pearnly_core.desk.list_filters`）。
"""
import frappe

from pearnly_core.account.names import display_name
from pearnly_core.cc_desktop import jobs
from pearnly_core.desk import list_filters

from pearnly_erp_portal.access import guard, listing
from pearnly_erp_portal.masters import company as companies

DOCTYPE = "ERP Collection"
VIEW = "collection.view"
FIELDS = ("name", "doc_date", "creation", "collector", "source", "customer", "customer_name", "invoice_summary",
          "method_summary", "cash", "transfer", "discount", "total", "voucher_count", "status", "docnum", "reason",
          "confirmed_at", "written_at", "book_label", "job")
# 原生筛选里「等于 / 介于」以外的条件可用的字段（页面 collections.js 的 filter_fields）
CONDITION_FIELDS = ("status", "source", "collector")
SEARCH_FIELDS = ("name", "customer", "customer_name", "invoice_summary", "docnum")
SORT_FIELDS = {"doc_date": "doc_date", "collector": "collector", "customer_name": "customer_name", "cash": "cash",
               "transfer": "transfer", "discount": "discount", "total": "total", "status": "status",
               "docnum": "docnum", "creation": "creation"}
DEFAULT_ORDER = "doc_date desc, creation desc"


def query(params=None):
    guard.require(VIEW)
    params = params or {}
    filters = [["company", "=", companies.active_company()]]
    for key in ("status", "source", "collector"):
        value = params.get(key)
        if value:
            filters.append([key, "in" if isinstance(value, list) else "=", value])
    if params.get("date_from"):
        filters.append(["doc_date", ">=", str(frappe.utils.getdate(params["date_from"]))])
    if params.get("date_to"):
        filters.append(["doc_date", "<=", str(frappe.utils.getdate(params["date_to"]))])
    filters.extend(list_filters.conditions(params.get("conditions"), {
        **{field: field for field in CONDITION_FIELDS}, "doc_date": "doc_date"}))
    or_filters = listing.search_or(params.get("q"), SEARCH_FIELDS)
    return filters, or_filters


def rows(params=None, *, start=0, limit=20, sort_by=None, sort_order=None):
    filters, or_filters = query(params)
    found = frappe.get_list(DOCTYPE, filters=filters, or_filters=or_filters, fields=list(FIELDS),
                            order_by=listing.order_by(sort_by, sort_order, SORT_FIELDS, DEFAULT_ORDER),
                            limit_start=max(0, int(start or 0)), limit_page_length=max(0, int(limit or 0)))
    names = {}
    for row in found:
        if row.collector not in names:
            names[row.collector] = display_name(row.collector) if row.collector else ""
        row["collector_name"] = names[row.collector]
    # 待写入、此刻在等 Express 关闭（读的时候算，一页一次查完；11-12 约定）：列表悬浮与点开都说现成那句
    queued = [row.job for row in found if row.status == "queued" and row.job]
    waiting = set(jobs.express_wait_many(queued)) if queued else set()
    for row in found:
        row["express_wait"] = bool(row.job and row.job in waiting)
        row.pop("job", None)
    return found


def total(params=None) -> int:
    filters, or_filters = query(params)
    return list_filters.count(DOCTYPE, filters, or_filters)


def page(params=None, *, start=0, limit=20, sort_by=None, sort_order=None) -> dict:
    return {"items": rows(params, start=start, limit=limit, sort_by=sort_by, sort_order=sort_order),
            "total": total(params),
            # 页头「新建」只给能录收款的人（收款岗位、管理员；服务端 `service.submit_web` 同样判）
            "can_create": guard.has("collection.write")}


def detail(name) -> dict:
    """点开一笔：抬头、各张发票、各方式与凭证（凭证文件的地址给原件查看器）。"""
    guard.require(VIEW)
    from pearnly_erp_portal.collection import service

    view = service.status(name)
    doc = frappe.get_doc(DOCTYPE, name)
    files = {row.voucher for row in doc.payments if row.voucher}
    found = {row.name: row for row in frappe.get_all("File", filters={"name": ["in", list(files) or [""]]},
                                                       fields=["name", "file_name", "file_url"],
                                                       limit_page_length=len(files) or 1)}
    from pearnly_core.originals import registered

    for method in view["methods"]:
        file = found.get(method["voucher"])
        if file is not None and registered.NAME.fullmatch(file.file_name or ""):
            file.file_name = registered.filename(file.name)  # 原件在原件层：给页面原文件名（看器按扩展名分图片 / PDF）
        method["file"] = {"file": file.name, "file_name": file.file_name, "file_url": file.file_url} if file else None
    view["collector_name"] = display_name(doc.collector) if doc.collector else ""
    view["express_wait"] = service.express_waiting(name)
    return view
