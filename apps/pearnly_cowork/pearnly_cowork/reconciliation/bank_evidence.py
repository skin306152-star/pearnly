"""Owner-scoped bank evidence consumed by push; not a bank import/parser API."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.masters.push_inputs import InputDocument, key, persist, require_actor

DOCTYPE = 'Cowork Bank Evidence'


def deny(*args, **kwargs):
    frappe.throw(_('Not Permitted'), frappe.PermissionError)


def reject_share(doc, method=None):
    if doc.share_doctype == DOCTYPE:
        deny()


def record(source_key, tx_date, direction, amount):
    """Internal seam for the future authorized bank parser. Never whitelisted.

    No arbitrary owner parameter: owner is the current authenticated user.
    Source identity is immutable; exact replay does not duplicate a transaction.
    """
    require_actor('intake', 'Cowork Intake')
    try:
        when = date.fromisoformat(str(tx_date)).isoformat()
        money = Decimal(str(amount))
        valid = money.is_finite() and len(str(money)) <= 80
    except (ValueError, InvalidOperation):
        valid = False
    if (not valid or direction not in ('IN', 'OUT') or not isinstance(source_key, str)
            or not source_key.strip() or len(source_key) > 200):
        frappe.throw(_('connection.config_invalid'))
    actor = frappe.session.user
    frappe.db.get_value('User', actor, 'name', for_update=True)
    name = key(actor, source_key)
    values = {'evidence_owner': actor, 'tx_date': when, 'direction': direction, 'amount_text': str(money)}
    if frappe.db.exists(DOCTYPE, name):
        doc = frappe.get_doc(DOCTYPE, name, for_update=True)
        if any(str(doc.get(k)) != str(v) for k, v in values.items()):
            frappe.throw(_('connection.request_conflict'))
        return name
    persist(frappe.get_doc({'doctype': DOCTYPE, 'input_key': name, **values}))
    return name


def window(owner, invoice_date):
    if not owner or not invoice_date:
        return []
    when = date.fromisoformat(invoice_date)
    rows = frappe.db.sql('''SELECT tx_date,direction,amount_text FROM `tabCowork Bank Evidence`
        WHERE evidence_owner=%s AND tx_date BETWEEN %s AND %s
        ORDER BY tx_date,name LOCK IN SHARE MODE''',
        (owner, when - timedelta(days=7), when + timedelta(days=7)), as_dict=True)
    return [{'tx_date': str(r.tx_date), 'direction': r.direction, 'amount': r.amount_text} for r in rows]
