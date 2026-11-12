"""网页「收款」只读页的白名单接口（列表、点开、导出、凭证 PDF 页图）。权限与取数在 `records` / `service`，这里只解析参数。

收款没有网页上的新建 / 修改 / 删除：录入只在 LINE 与 CC（`service`），写错由财务在 Express 原生作废。
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


# ── 收款员自己的收款（「我的收款」页；LINE 确认卡【编辑】打开的就是这一页的弹窗）──────────────────


def _explained(function, *args, **kwargs):
    """规则不过照码给一句话（同 LINE 里的说法）。"""
    from pearnly_erp_portal.collection import contracts, texts

    try:
        return function(*args, **kwargs)
    except contracts.CollectionError as error:
        frappe.throw(texts.explain(error))


@frappe.whitelist()
def api_my_collections(start=0, limit=20):
    from pearnly_erp_portal.collection import service

    return service.mine(start=start, limit=limit)


@frappe.whitelist(methods=["POST"])
def api_my_collection(name):
    """一笔本人的收款：确认卡内容；草稿另带可改的范围（各张此刻能收多少、这本账套的收款方式）。POST：主档还没读过时
    要排一次读取（`express.masters.ensure`），GET 里写库不提交。"""
    from pearnly_erp_portal.collection import contracts, service

    card = service.card(name)
    if card["status"] != contracts.DRAFT:
        return {"card": card}
    return {"card": card, **_explained(service.edit_choices, name), "staff": service.staff_choices(name)}


@frappe.whitelist(methods=["POST"])
def api_my_collection_save(name, version, invoices, cash=0, methods=None, salesman=None, department=None):
    """改草稿（各张本次收、现金、各方式与折扣、销售员、部门）：只认打开时的版本（LINE 里刚改过回原因），存好回新的确认卡。"""
    from pearnly_erp_portal.collection import service

    invoices, methods = _payload(invoices), _payload(methods) or []
    return _explained(service.update_draft, name, version, invoices if isinstance(invoices, list) else [], cash,
                      methods if isinstance(methods, list) else [], salesman=salesman, department=department)
