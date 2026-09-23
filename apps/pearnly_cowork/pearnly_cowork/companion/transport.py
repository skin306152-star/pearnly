"""Local Express queue adapter into the fenced native push ledger.

Internal service only: no HTTP route, worker, scheduler, commit or external ERP
write. The caller must commit before exposing a lease response. Prepared records
are never activated by polling; explicit dispatch activation is a separate step.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

import frappe
from frappe import _

from pearnly_cowork.connections import refresh
from pearnly_cowork.push.native import service as ledger
from pearnly_cowork.push.native.contracts import ReservationContext
from . import credentials, delivery_store, queue_contracts, receipt_auth


@contextmanager
def _atomic():
    point = 'cowork_delivery_' + uuid4().hex
    frappe.db.savepoint(point)
    try:
        yield
    except frappe.QueryDeadlockError:
        # Native ledger may already have rolled back a deadlocked transaction.
        # The transport does not retry or commit any part of the caller's unit.
        raise
    except Exception:
        frappe.db.rollback(save_point=point)
        raise


def _value(function, *args):
    try:
        return function(*args)
    except queue_contracts.QueueContractError as error:
        frappe.throw(_(error.code))


def _context(row, credential):
    if ((row.connection, row.workspace, row.client or '', row.branch, int(row.binding_generation or 0)) !=
            (credential.connection, credential.workspace, credential.client, 'legacy', 0)):
        frappe.throw(_('Not Permitted'), frappe.PermissionError)
    # Actor, versions, target, account and payload always come from stored work.
    return ReservationContext(row.actor, row.workspace, row.client or '', row.history,
        int(row.history_version), row.connection, row.account_key, branch=row.branch,
        binding_generation=int(row.binding_generation), connection_revision=int(row.connection_revision))


def _adapter(connection, owner):
    return ledger.trusted_adapter_context(connection, owner, 0)


def _recover(credential, now):
    # Only this adapter's persisted deliveries. A reset credential may observe
    # an old attempt's expiry but cannot acknowledge it or release its locks.
    rows = frappe.db.sql('''SELECT r.name, d.owner_id FROM `tabCowork Push Record` r
        INNER JOIN `tabCowork Companion Delivery` d ON d.push_attempt=r.active_attempt
        WHERE r.connection=%s AND r.status='leased' AND r.lease_expires_at<=%s
        ORDER BY r.created_at,r.name''', (credential.connection, ledger.now_utc(now)), as_dict=True)
    for item in rows:
        row = frappe.get_doc(ledger.RECORD, item.name)
        ledger.recover_expired(row.name, context=_context(row, credential),
            adapter=_adapter(credential.connection, item.owner_id), now=now)


def lease(context, body):
    """Claim at most one FIFO job: the endpoint has one durable writer lock."""
    _value(queue_contracts.lease_request, body)
    with _atomic():
        credential = credentials.revalidate(context)
        now = datetime.now(timezone.utc)
        _recover(credential, now)
        # Filter internal ERP records before LIMIT. A bounded sweep quarantines
        # definitely stale work without hiding it or regenerating its payload.
        candidates = frappe.db.sql('''SELECT r.name FROM `tabCowork Push Record` r
            INNER JOIN `tabCowork OCR History` h ON h.name=r.history
            WHERE r.connection=%s AND r.workspace=%s AND COALESCE(r.client,'')=%s
              AND r.branch='legacy' AND r.binding_generation=0 AND r.status='pending'
              AND COALESCE(r.result_unknown,0)=0 AND COALESCE(h.source,'') NOT IN ('erp_web','line_erp')
            ORDER BY r.created_at,r.name LIMIT 50''',
            (credential.connection, credential.workspace, credential.client))
        jobs = []
        adapter = _adapter(credential.connection, delivery_store.owner(credential))
        for candidate in candidates:
            row = frappe.get_doc(ledger.RECORD, candidate[0])
            reservation = _context(row, credential)
            try:
                bound = (ledger.project(row)['frozen_target'].get('preparation_evidence') or {}).get('credential')
                if bound and bound != {'credential_id': credential.credential_id,
                        'generation': credential.token_generation, 'issuer': credential.issuer}:
                    raise ledger.DispatchRejected('push.native_target_changed')
                claim = ledger.lease(row.name, context=reservation, adapter=adapter, now=now)
            except ledger.LeaseUnavailable:
                # A pre-existing RR snapshot can select an already-taken row;
                # the locked ledger state wins. No mutation or generic catch.
                continue
            except (ledger.DispatchRejected, frappe.PermissionError) as error:
                reason = error.code if isinstance(error, ledger.DispatchRejected) else 'companion.queue_access_revoked'
                ledger.quarantine_pending(row.name, context=reservation, adapter=adapter, reason=reason)
                continue
            if claim['leased']:
                delivery = delivery_store.create(credential=credential, claim=claim)
                jobs.append(_value(queue_contracts.job, delivery.name, claim['record']))
                break
            if claim.get('reason') == 'waiting_lock':
                break
        doc = frappe.get_doc('Cowork ERP Connection', credential.connection, for_update=True)
        requested_refresh = refresh.lease(doc, '', credential.credential_id, credential.token_generation, ledger.now_utc(now))
        return _value(queue_contracts.lease_response, jobs, requested_refresh)


def acknowledge(context, body):
    request = _value(queue_contracts.ack_request, body)
    with _atomic():
        delivery = receipt_auth.revalidate(context)
        if request['log_id'] != delivery.name:
            frappe.throw(_('companion.unauthorized'), frappe.AuthenticationError)
        row = frappe.get_doc(ledger.RECORD, delivery.push_record)
        result = ledger.ack(row.name, context=_context(row, context),
            adapter=_adapter(context.connection, delivery.owner_id), fence=int(delivery.fence),
            attempt_token=delivery_store.secret(delivery), success=request['result'] == 'success',
            outcome=request['outcome'], express_docnum=request['express_docnum'], error=request['error'],
            line_modes=request['line_modes'], meta=request['meta'])
        return _value(queue_contracts.ack_response, result)
