"""网页「销售 → 收款」页的白名单接口（列表、点开、改草稿、撤回、导出、凭证 PDF 页图、新建）。权限、规则与取数
都在 `records` / `service`（与 LINE、CC 同一份），这里只解析参数。

网页「新建」提交即放行（`service.submit_web`）；不改已放行的、不删；写错由财务在 Express 原生作废。
"""
import frappe
from frappe import _

from pearnly_erp_portal.collection import records


def _payload(value):
    return frappe.parse_json(value) if isinstance(value, str) else (value or {})


@frappe.whitelist()
def api_collections(params=None, start=0, limit=20, sort_by=None, sort_order=None):
    return records.page(_payload(params), start=start, limit=limit, sort_by=sort_by, sort_order=sort_order)


@frappe.whitelist()
def api_collection(name):
    return records.detail(name)


@frappe.whitelist(methods=["POST"])
def api_collections_export(params=None):
    """小的当场回文件，大的排后台、做完推送下载（`pearnly_core.exports`）。"""
    from pearnly_core.exports import service as exports

    return exports.deliver(count="pearnly_erp_portal.collection.output.export_size",
                           build="pearnly_erp_portal.collection.output.workbook", args={"params": _payload(params)},
                           filename="collections.xlsx",
                           content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@frappe.whitelist()
def api_collection_voucher_page(file, page=1):
    """PDF 凭证的一页（原件在原件层，由那边画）；图片凭证页面直接用文件地址（私有文件按收款记录的读权限）。"""
    from pearnly_core.originals import registered

    from pearnly_erp_portal.access import guard
    from pearnly_erp_portal.collection import service

    guard.require(records.VIEW)
    attached = frappe.get_doc("File", file)
    if attached.attached_to_doctype != records.DOCTYPE or not attached.attached_to_name:
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    service.status(attached.attached_to_name)  # 看得到这一笔才看得到它的凭证
    found = registered.entry(attached)
    if not found or found["kind"] != "pdf":
        frappe.throw(_("Invalid file"))
    try:
        return {"page": int(page), "pages": found["pages"], "image": registered.page_image(attached, int(page))}
    except (ValueError, TypeError):
        frappe.throw(_("Invalid value"))


# ── 「收款」页点开一笔：还没写进 Express 的可改、可撤回，不分谁录的（用户 11-14）；LINE 确认卡【编辑】打开的也是这一页的弹窗 ──


def _explained(function, *args, **kwargs):
    """规则不过照码给一句话（同 LINE 里的说法）。"""
    from pearnly_erp_portal.collection import contracts, texts

    try:
        return function(*args, **kwargs)
    except contracts.CollectionError as error:
        frappe.throw(texts.explain(error))


@frappe.whitelist(methods=["POST"])
def api_collection_card(name):
    """一笔收款（本公司的，不分谁录的）：确认卡内容；草稿另带可改的范围（各张此刻能收多少、这本账套的收款方式）。POST：主档还没读过时
    要排一次读取（`express.masters.ensure`），GET 里写库不提交。"""
    from pearnly_erp_portal.collection import contracts, service

    card = service.card(name)
    if card["status"] != contracts.DRAFT:
        return {"card": {**card, "express_wait": service.express_waiting(name)}}
    return {"card": card, **_explained(service.edit_choices, name), "staff": service.staff_choices(name)}


@frappe.whitelist(methods=["POST"])
def api_collection_save(name, version, invoices, cash=0, methods=None, salesman=None, department=None):
    """改草稿（各张本次收、现金、各方式与折扣、销售员、部门）：只认打开时的版本（别人或 LINE 里刚改过回原因），存好回新的确认卡。"""
    from pearnly_erp_portal.collection import service

    invoices, methods = _payload(invoices), _payload(methods) or []
    return _explained(service.update_draft, name, version, invoices if isinstance(invoices, list) else [], cash,
                      methods if isinstance(methods, list) else [], salesman=salesman, department=department)


# ── 网页「新建」（11-14：「收款」页头，`public/desk/collection-new.js`）────────────────


@frappe.whitelist(methods=["POST"])
def api_collection_new_options(company=None):
    """弹窗要的：本人能录收款的公司；给了公司再带这家此刻的账套是否要选一次、收款方式。POST：主档还没读过时要排一次读取
    （`express.masters.ensure`），GET 里写库不提交。"""
    from pearnly_erp_portal.collection import service

    out = {"companies": [{"value": row["name"], "label": row["company_name"]} for row in service.companies_for()]}
    if company:
        out["book"] = _explained(service.book_options, company)
        if not out["book"]:
            out["methods"] = _explained(service.methods, company)
    return out


@frappe.whitelist(methods=["POST"])
def api_collection_choose_book(company, key):
    from pearnly_erp_portal.collection import service

    return _explained(service.choose_book, company, key)


@frappe.whitelist()
def api_collection_customer_search(txt="", company=None, start=0):
    """客户输入框（官方 Autocomplete 的 ``get_query``）：按编号或名字服务端搜、一次一页，不一次取全。"""
    from pearnly_erp_portal.collection import service

    if not company:
        return []
    found = _explained(service.customers, company, q=txt, start=start, limit=20)
    # description 放税号：原生 Autocomplete 在本地再按「标签 + 说明 + 值」筛一遍，按税号搜到的不会被筛掉（11-15），下拉第二行就是税号
    return [{"value": row["code"], "label": f"{row['code']} {row['name']}", "description": row.get("tax_id") or ""}
            for row in found["items"]]


@frappe.whitelist(methods=["POST"])
def api_collection_open_invoices(company, customer):
    """选好客户：这位客户此刻能收的发票（同 LINE / CC，一次查完；POST：要请电脑按客户即时读一次）。"""
    from pearnly_erp_portal.collection import service

    return _explained(service.open_invoices, company, customer)


@frappe.whitelist(methods=["POST"])
def api_collection_voucher_upload():
    """转账凭证（multipart ``file``，图片或 PDF）：存原件层、归本人、先不挂单（同 LINE 发来的凭证，`service.store_voucher`）。"""
    from pearnly_erp_portal.collection import service
    from pearnly_erp_portal.recognition import originals

    sent = (frappe.request.files or {}).get("file") if frappe.request else None
    if sent is None:
        frappe.throw(_("File not found"))
    filename, content = sent.filename or "voucher.jpg", sent.stream.read(originals.MAX_BYTES + 1)
    originals._size(len(content))
    return _explained(service.store_voucher, filename, content)


@frappe.whitelist(methods=["POST"])
def api_collection_submit(company, customer, invoices, request, cash=0, methods=None, salesman=None, department=None):
    """提交即放行（`service.submit_web`）：规则不过照码回一句、什么都不留；同一个 ``request`` 再提交回已建的那一笔。"""
    from pearnly_erp_portal.collection import service

    invoices, methods = _payload(invoices), _payload(methods) or []
    return _explained(service.submit_web, company, customer, invoices if isinstance(invoices, list) else [], cash,
                      methods if isinstance(methods, list) else [], salesman=salesman, department=department,
                      request=request)


@frappe.whitelist(methods=["POST"])
def api_collection_withdraw(name):
    """撤回一笔（本公司的，不分谁录的；草稿、待写入、失败的；电脑正在写、已写入的不行），同 LINE【撤回】（`service.withdraw`）。"""
    from pearnly_erp_portal.collection import service

    return _explained(service.withdraw, name)
