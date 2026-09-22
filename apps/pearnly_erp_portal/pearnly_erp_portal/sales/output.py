"""Permission-scoped workbook for sales records."""
from io import BytesIO

import frappe
from frappe import _

from pearnly_erp_portal.sales import records

DOCTYPE = "ERP Sales Document"

# Stored enum codes stay stable; workbook cells use the same official business terms as Desk.
ENUM_FIELDS = {"doc_kind", "status", "payment_status", "payment_method", "source", "kind",
               "party_type"}
ENUM_LABELS = {
    "receipt": "Receipt", "quotation": "Quotation", "draft": "Draft", "submitted": "Submitted",
    "void": "Cancelled", "unpaid": "Unpaid", "partial": "Partly Paid", "paid": "Paid",
    "cash": "Cash", "transfer": "Bank Transfer", "promptpay": "PromptPay", "other": "Other",
    "bill": "Document", "payment_proof": "Payment Receipt", "web": "Manual", "manual": "Manual",
    "ocr": "OCR", "line": "LINE", "company": "Company", "person": "Person",
    "foreign": "Foreigner", "anonymous": "Anonymous",
}


def _cell(record, key):
    value = record.get(key)
    if key in ENUM_FIELDS and value in ENUM_LABELS:
        value = _(ENUM_LABELS[value])
    return _value(value)


def _columns(doctype):
    return [("name", "ID"), *[(field.fieldname, field.label) for field in frappe.get_meta(doctype).fields
        if field.fieldtype not in ("Section Break", "Column Break", "Tab Break", "HTML", "Button", "Table")
        and field.fieldname not in ("request_id", "request_hash")]]


def _value(value):
    # Never let user supplied text become an Excel formula.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def workbook(params=None):
    from openpyxl import Workbook
    from openpyxl.styles import Font

    from pearnly_erp_portal.sales import access

    selected = records.rows(params, limit=0)
    book = Workbook()
    book.remove(book.active)
    specs = [("ERP Sales Document", _("Sales Records"), None),
             ("ERP Sales Document Line", _("Items"), "lines"),
             ("ERP Sales Attachment", _("Attachments"), "attachments")]
    sheets = []
    for doctype, title, child in specs:
        sheet = book.create_sheet(title[:31])
        columns = _columns(doctype)
        sheet.append(([_("Sales Records")] if child else []) + [_(label) for key, label in columns])
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "B2" if child else "A2"
        sheets.append((sheet, columns, child))
    for row in selected:
        doc = frappe._dict(access.get_document(row.name).as_dict())
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
