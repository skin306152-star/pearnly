"""Local ACK effects. No queue dispatch, LINE binding lookup or network IO.

Only the fenced ledger calls this on the first accepted success. Every write
shares its savepoint, so an error leaves the attempt and writer locks intact.
"""
import hashlib
import json
import frappe
from pearnly_cowork.connections import stock_effects
from pearnly_cowork.push.express import common
from .documents import persist

NOTIFICATION = 'Cowork Push Notification'


def created_stock_count(branch, modes):
    return sum(1 for mode in (modes or []) if isinstance(mode, dict)
               and mode.get('mode') == common.ITEM_MODE_STOCK and mode.get('created')) if branch == 'legacy' else 0


def success(row, timestamp):
    response = json.loads(row.response_json)
    stock = stock_effects.apply(row.connection, json.loads(row.target_json),
        created_stock_count(row.branch, response.get('line_modes')))
    history = frappe.get_doc('Cowork OCR History', row.history, for_update=True)
    event = None
    if history.source == 'cowork_line':
        event = 'CWN-' + hashlib.sha256(('success:' + row.name).encode()).hexdigest()[:48]
        # The ledger serializes the first success; a colliding existing event
        # is an invariant failure, not permission to replace its saved payload.
        payload = json.loads(row.payload_json)
        body = {'version': 1, 'source': history.source, 'channel': 'cowork',
            'actor': row.actor, 'workspace': row.workspace, 'client': row.client or '',
            'history': row.history, 'push_record': row.name, 'connection': row.connection,
            'account_set': row.account_key, 'invoice_no': row.invoice_no or '',
            'total_amount': payload.get('total_amount'), 'external_docnum': row.external_docnum,
            'succeeded_at': timestamp.isoformat()}
        doc = frappe.get_doc({'doctype': NOTIFICATION, 'record_id': event,
            'push_record': row.name, 'workspace': row.workspace, 'client': row.client,
            'history': row.history, 'actor': row.actor, 'connection': row.connection,
            'status': 'held', 'payload_json': json.dumps(body, ensure_ascii=False, sort_keys=True)})
        persist(doc)
    row.completion_json = json.dumps({'stock': stock, 'notification_event': event}, sort_keys=True)
