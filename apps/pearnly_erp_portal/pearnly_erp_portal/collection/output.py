"""收款导出（与列表同一份取数、同一道权限）：一张「收款」表一行一笔，另两张是各张发票与各种方式。"""
from io import BytesIO

import frappe
from frappe import _

from pearnly_erp_portal.collection import records

HEAD = (("name", "ID"), ("doc_date", "Collection Date"), ("collector_name", "Collector"), ("source", "Source"),
        ("customer", "Customer Code"), ("customer_name", "Customer Name"), ("total", "Total"), ("cash", "Cash"),
        ("transfer", "Bank Transfer"), ("discount", "Discount"), ("method_summary", "Collection Method"),
        ("voucher_count", "Attachments"), ("status", "Status"), ("docnum", "Express No."), ("reason", "Reason"),
        ("book_label", "Account Set"))
INVOICES = (("docnum", "Invoice No"), ("invoice_date", "Invoice Date"), ("remaining", "Outstanding Amount"),
            ("amount", "Amount"), ("salesman", "Sales Person"))
PAYMENTS = (("prefix", "Prefix"), ("type_name", "Collection Method"), ("amount", "Amount"))


def _value(value):
    # 用户填的字不能变成 Excel 公式
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _cell(row, key):
    value = row.get(key)
    if key == "status" and value:
        value = _(f"erp.collection.status.{value}")
    elif key == "source" and value:
        value = _(f"erp.collection.source.{value}")
    return _value(value)


def export_size(params=None):
    from pearnly_core.exports import service as exports

    return len(records.rows(params, start=0, limit=exports.sync_rows() + 1))


def workbook(params=None):
    from openpyxl import Workbook
    from openpyxl.styles import Font

    selected = records.rows(params, limit=0)
    book = Workbook()
    book.remove(book.active)
    sheets = []
    for title, columns, child in ((_("Collection"), HEAD, None), (_("Invoices"), INVOICES, "invoices"),
                                  (_("Payments"), PAYMENTS, "payments")):
        sheet = book.create_sheet(title[:31])
        sheet.append(([_("Collection")] if child else []) + [_(label) for _key, label in columns])
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "B2" if child else "A2"
        sheets.append((sheet, columns, child))
    for row in selected:
        doc = frappe.get_doc(records.DOCTYPE, row.name)
        for sheet, columns, child in sheets:
            if child:
                for record in doc.get(child) or []:
                    sheet.append([doc.name] + [_cell(record.as_dict(), key) for key, _label in columns])
            else:
                sheet.append([_cell(row, key) for key, _label in columns])
    for sheet, _columns, _child in sheets:
        sheet.auto_filter.ref = sheet.dimensions
    output = BytesIO()
    book.save(output)
    return output.getvalue()
