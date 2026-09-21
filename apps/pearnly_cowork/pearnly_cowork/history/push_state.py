"""Narrow internal projection of the authoritative push ledger.

Caller holds Connection -> History -> writer -> Record locks. No authorization
upgrade, review edit, commit or external IO; old issued receipts survive revoke.
"""
import frappe
from frappe import _

FIELDS = ('last_push_status', 'last_pushed_at', 'last_push_record')


def mirror(row, timestamp, *, select=False):
    if row.status not in {'pending', 'success', 'manual'}:
        return
    found = frappe.db.sql("""SELECT workspace,client,last_push_record,last_push_status
        FROM `tabCowork OCR History` WHERE name=%s FOR UPDATE""", (row.history,), as_dict=True)
    if not found or (found[0].workspace, found[0].client or '') != (row.workspace, row.client or ''):
        frappe.throw(_('Not Permitted'), frappe.PermissionError)
    previous = found[0]
    # A late result for another account/connection must not replace the push
    # the user selected more recently. Its own ledger receipt remains visible.
    if not select and previous.last_push_record not in (None, '', row.name):
        return
    if previous.last_push_record == row.name and previous.last_push_status == row.status:
        return
    frappe.db.set_value('Cowork OCR History', row.history, {
        'last_push_status': row.status, 'last_pushed_at': timestamp,
        'last_push_record': row.name}, update_modified=False)
