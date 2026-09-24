"""销售域接口层。业务与权限在服务层，这里只做参数解析。

**只有记录，没有开票**：本仓不做税务发票（用户 2026-09-23），所以没有票面/打印、
没有红冲补开、没有报价转开票。提交（post）一次落定一张销售单并过账出库。
"""

import frappe

from pearnly_erp_portal.sales import entry, records


def _payload(value):
    return frappe.parse_json(value) if isinstance(value, str) else (value or {})


# ── 销售记录 ────────────────────────────────────────────────────────────────


@frappe.whitelist(methods=["POST"])
def api_post_sale(payload, request_id, name=None):
    return entry.post(_payload(payload), request_id=request_id, name=name)


@frappe.whitelist()
def api_sales_records(params=None, start=0, limit=20, sort_by=None, sort_order=None):
    return records.page(_payload(params), start=start, limit=limit,
                        sort_by=sort_by, sort_order=sort_order)


@frappe.whitelist()
def api_sales_document(name):
    return records.get(name)


@frappe.whitelist()
def api_sales_defaults(doc_date=None):
    from pearnly_erp_portal.access import guard
    from pearnly_erp_portal.numbering import service as numbering
    from pearnly_core import ocr

    guard.require("sales.read")
    return {"system_no": numbering.preview(direction="sales", on=doc_date),
            "is_paid": 1, "default_vat_rate": 7,
            "capabilities": {"ocr": bool(ocr.is_configured() and guard.has("sales.write")),
                "line_intakes": bool(guard.has("sales.write") and frappe.db.exists("ERP LINE Intake",
                    {**guard.scope_filters(), "direction": "sales", "status": "received"}))}}


@frappe.whitelist(methods=["POST"])
def api_sales_amounts(payload):
    return entry.preview(_payload(payload))


@frappe.whitelist(methods=["POST"])
def api_cancel_sale(name, reason=None):
    return entry.cancel(name, reason)


@frappe.whitelist(methods=["POST"])
def api_sale_payment(name, payload):
    return entry.payment(name, **_payload(payload))


@frappe.whitelist(methods=["POST"])
def api_delete_sale_drafts(names):
    return entry.delete_drafts(frappe.parse_json(names))


@frappe.whitelist(methods=["POST"])
def api_sale_attachments(name, attachments):
    from pearnly_erp_portal.sales import files

    return files.add(name, frappe.parse_json(attachments))


@frappe.whitelist(methods=["POST"])
def api_remove_sale_attachment(name, file):
    from pearnly_erp_portal.sales import files

    return files.remove(name, file)


@frappe.whitelist()
def api_sales_export(params=None):
    from pearnly_erp_portal.sales import output

    frappe.local.response.update(filename="sales.xlsx", filecontent=output.workbook(_payload(params)),
                                 type="download", display_content_as="attachment")


@frappe.whitelist(methods=["POST"])
def api_recognise_sales(attachments=None, intake_name=None, document_name=None):
    from pearnly_erp_portal.sales import recognition

    return recognition.recognise(frappe.parse_json(attachments), intake_name, document_name)


@frappe.whitelist()
def api_sale_file_page(file, page=1):
    from pearnly_erp_portal.sales import preview

    return preview.page_image(file, page)
