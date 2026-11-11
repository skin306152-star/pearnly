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
