"""Permission-scoped workbooks and native Frappe PDF purchase vouchers."""
from io import BytesIO

import frappe
from frappe import _

from pearnly_erp_portal.purchasing import access, amounts, records


# Stored enum codes stay stable; workbook cells use the same official business terms as Desk.
ENUM_FIELDS = {"doc_kind", "status", "payment_status", "supplier_branch_type",
               "price_mode", "payment_method", "method", "source", "item_type", "kind", "approval_status"}
ENUM_LABELS = {
    "purchase_invoice": "Purchase Invoice", "purchase_order": "Purchase Order", "expense": "Expense",
    "goods": "Goods", "service": "Service", "tax_invoice": "Tax Invoice",
    "simple_tax_receipt": "Abbreviated Tax Invoice", "receipt": "Receipt",
    "substitute_receipt": "Substitute Receipt", "other": "Other", "head_office": "Head Office",
    "head": "Head Office", "branch": "Branch", "none": "None", "exclusive": "Tax Exclusive",
    "inclusive": "Tax Inclusive", "cash": "Cash", "transfer": "Bank Transfer", "promptpay": "PromptPay",
    "bank": "Bank", "cheque": "Cheque", "draft": "Draft", "submitted": "Submitted", "void": "Cancelled",
    "unpaid": "Unpaid", "partial": "Partly Paid", "paid": "Paid", "bill": "Invoice",
    "payment_proof": "Payment Receipt", "wht_cert": "Withholding Tax Certificate",
    "web": "Manual", "manual": "Manual", "ocr": "OCR", "line": "LINE",
}


def _cell(record, key):
    value = record.get(key)
    if key in ENUM_FIELDS and value in ENUM_LABELS:
        value = _(ENUM_LABELS[value])
    return _value(value)


def _columns(doctype):
    return [("name", "ID"), *[(field.fieldname, field.label) for field in frappe.get_meta(doctype).fields
        if field.fieldtype not in ("Section Break", "Column Break", "Tab Break", "HTML", "Button", "Table")
        and field.fieldname not in ("request_id", "request_hash", "dedupe_key", "dedupe_ref", "calculation_version")]]


def _value(value):
    # Never let user supplied text become an Excel formula.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def export_size(params=None):
    """导出要多少行（`pearnly_core.exports` 据此决定当场给还是排后台）：与导出同一份筛选、同一道权限，数到阈值 + 1 就停。"""
    from pearnly_core.exports import service as exports
    from pearnly_erp_portal.access import guard

    guard.require("purchase.read")
    filters, or_filters, _datefield = records.query(params)
    return len(frappe.get_list("Purchase Document", filters=filters, or_filters=or_filters, fields=["name"],
                               distinct=True, limit_page_length=exports.sync_rows() + 1))


def workbook(params=None):
    from openpyxl import Workbook
    from openpyxl.styles import Font

    selected = records.rows(params, limit=0)
    book = Workbook()
    book.remove(book.active)
    specs = [("Purchase Document", _("Purchase Invoice"), None),
             ("Purchase Line", _("Items"), "lines"),
             ("ERP Purchase Payment", _("Payments"), "payments"),
             ("ERP Purchase Attachment", _("Attachments"), "attachments")]
    sheets = []
    for doctype, title, child in specs:
        sheet = book.create_sheet(title[:31])
        columns = _columns(doctype)
        sheet.append(([_("Purchase Invoice")] if child else []) + [_(label) for key, label in columns])
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "B2" if child else "A2"
        sheets.append((sheet, columns, child))
    for row in selected:
        doc = frappe._dict(records.get(row.name))
        for sheet, columns, child in sheets:
            for record in doc.get(child, []) if child else [doc]:
                sheet.append(([doc.name] if child else []) + [_cell(record, key) for key, label in columns])
    for sheet, columns, child in sheets:
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = min(50, max(14,
                max(len(str(cell.value or "")) for cell in column) + 2))
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def voucher(name, kind):
    doc = access.get_document(name, write=True, lock=True)
    if kind not in ("substitute_receipt", "wht_cert"):
        frappe.throw(_("Invalid value"))
    if doc.status != "submitted":
        frappe.throw(_("Document must be submitted"))
    if kind == "wht_cert" and amounts.money(doc.wht_amount) <= 0:
        frappe.throw(_("Withholding Tax must be greater than zero"))
    existing = next((row for row in doc.attachments if row.kind == kind and row.generated), None)
    if existing:
        return {"file": existing.file, "file_url": existing.file_url}
    from frappe.utils.pdf import get_pdf
    from frappe.www.printview import get_print_style

    # 抬头取**这张单所属的公司**（一租户多公司：单据挂公司，凭证跟着单据走）。
    from pearnly_erp_portal.masters import company as companies
    company = companies.for_document(doc.get("company"))
    body = frappe.render_template("pearnly_erp_portal/purchasing/voucher.html", {
        "doc": doc, "company": company, "kind": kind,
        "title": _("Withholding Tax Certificate") if kind == "wht_cert" else _("Substitute Receipt"),
        "print_style": get_print_style(),
    })
    content = get_pdf(body)
    file = frappe.get_doc({"doctype": "File", "file_name": f"{doc.name}-{kind}.pdf",
        "is_private": 1, "content": content, "attached_to_doctype": doc.doctype,
        "attached_to_name": doc.name}).insert(ignore_permissions=True)
    doc.append("attachments", {"file": file.name, "file_url": file.file_url, "file_name": file.file_name,
               "kind": kind, "generated": 1})
    doc.flags.purchase_action = True
    doc.save()
    return {"file": file.name, "file_url": file.file_url}
