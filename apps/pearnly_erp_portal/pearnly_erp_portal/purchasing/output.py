"""Permission-scoped workbooks and native Frappe PDF purchase vouchers."""
from io import BytesIO

import frappe
from frappe import _

from pearnly_erp_portal.purchasing import access, amounts, records


def _columns(doctype):
    return [("name", "ID"), *[(field.fieldname, field.label) for field in frappe.get_meta(doctype).fields
        if field.fieldtype not in ("Section Break", "Column Break", "Tab Break", "HTML", "Button", "Table")
        and field.fieldname not in ("request_id", "request_hash", "dedupe_key", "dedupe_ref", "calculation_version")]]


def _value(value):
    # Never let user supplied text become an Excel formula.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


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
        doc = access.get_document(row.name)
        for sheet, columns, child in sheets:
            for record in doc.get(child, []) if child else [doc]:
                sheet.append(([doc.name] if child else []) + [_value(record.get(key)) for key, label in columns])
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

    company_name = frappe.db.get_value("ERP Company", {"is_default": 1}, "name")
    company = frappe.get_doc("ERP Company", company_name).as_dict() if company_name else {}
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
