"""Reconcile an existing submission and retry its receipt; never create an order.

The immutable attempt context is saved before the first external POST. Recovery
uses the same identity/critical-field readback contract as initial submission.
Legacy attempts without that evidence remain blocked for explicit reconciliation.
"""
import hashlib
import json
import logging
import uuid

import frappe

from pearnly_core import line_session as store
from pearnly_dms.booking import booking_ledger, delivery
from pearnly_dms.booking.booking_submit import verify_created_booking
from pearnly_dms.contracts.endpoint import endpoint_name
from pearnly_dms.adapter.operations import run_logged_in
from pearnly_dms.line.identity import matches
from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint

logger = logging.getLogger(__name__)
WORKER_GRACE_SECONDS = 660  # Existing worker timeout is 600 s.
RECEIPT_WINDOW_SECONDS = 23 * 3600  # LINE retry keys expire at 24 h.


def endpoint_fingerprint(endpoint):
    return delivery.identity_fingerprint(endpoint)


def _marker(line_user_id):
    return ((store.get_session(line_user_id) or {}).get('payload') or {}).get('booking_attempt') or {}


def _age(stamp):
    if not stamp:
        return float('inf')
    return (frappe.utils.get_datetime() - frappe.utils.get_datetime(stamp)).total_seconds()


def finish(line_user_id, nonce, qa, result):
    """Transfer verified success to durable delivery before clearing the draft."""
    marker = _marker(line_user_id)
    if (marker.get('nonce') != nonce or not result.get('ok') or not result.get('booking_id')
            or str(result.get('booking_no') or '') != str(marker.get('booking_no') or '')):
        return False
    result = {key: result[key] for key in ('ok', 'booking_no', 'booking_id', 'attach_ok',
                                         'attached', 'attach_failed') if key in result}
    if not store.update_booking_attempt(line_user_id, nonce, {'result': result}):
        return False
    marker = {**_marker(line_user_id), 'completed_at': frappe.utils.now()}
    name = delivery.retain(line_user_id, marker)
    if not store.clear_booking_attempt(line_user_id, nonce):
        return False  # The scanner can complete this handoff after a crash.
    return delivery.deliver(name)


def ready(marker):
    context = marker.get('context') or {}
    if not context.get('binding') or not context.get('submitted') or not context.get('endpoint'):
        return False
    if marker.get('result', {}).get('ok'):
        return True  # Even expired/rebound delivery must leave the active draft.
    receipt = marker.get('receipt') or {}
    if receipt and _age(receipt.get('created_at')) >= RECEIPT_WINDOW_SECONDS:
        return False
    if receipt.get('last_attempt_at') and _age(receipt['last_attempt_at']) < min(
            3600, 60 * 2 ** min(receipt.get('attempts', 0), 6)):
        return False
    if marker.get('checked_at') and _age(marker['checked_at']) < 300:
        return False
    return bool(marker.get('result', {}).get('ok') or _age(marker.get('recorded_at')) > WORKER_GRACE_SECONDS)


def request(line_user_id, marker=None):
    marker = marker or _marker(line_user_id)
    if ready(marker) and (marker.get('result', {}).get('ok') or
                          matches((marker.get('context') or {}).get('binding') or {}, line_user_id)):
        frappe.enqueue('pearnly_dms.booking.recovery.reconcile', queue='long', timeout=600,
                       job_id='dms-reconcile-' + hashlib.sha256(line_user_id.encode()).hexdigest()[:24],
                       deduplicate=True, line_user_id=line_user_id)


def reconcile(line_user_id):
    # Bounded lease excludes another recovery worker. External requests remain
    # serialized by the actual DMS account lock as well.
    cache = frappe.cache()
    key = cache.make_key('pearnly:dms:reconcile:' + line_user_id)
    owner = uuid.uuid4().hex
    if not cache.set(key, owner, nx=True, ex=660):
        return
    try:
        marker = _marker(line_user_id)
        context = marker.get('context') or {}
        binding = context.get('binding') or {}
        if not ready(marker) or (not marker.get('result', {}).get('ok') and not matches(binding, line_user_id)):
            return
        nonce = marker['nonce']
        qa = context.get('qa') or {}
        result = marker.get('result') or {}
        if not result.get('ok'):
            endpoint = resolve_dms_endpoint(binding['user_id'], context['endpoint'])
            if (endpoint_name(endpoint) != context['endpoint'] or
                    endpoint_fingerprint(endpoint) != context.get('endpoint_fingerprint')):
                return
            if not store.update_booking_attempt(line_user_id, nonce, {'checked_at': frappe.utils.now()}):
                return

            def read(client, adapter):
                booking_id = verify_created_booking(client, marker['booking_no'], context['submitted'])
                if not booking_id:
                    return {'ok': False}
                form = client._parse_form_defaults(client._post_text(
                    'drfcbc/form.php', {'status': 'e', 'id': booking_id}))
                names = {v for k, v in form.items() if k.startswith('fulcurrname')}
                files = qa.get('files') or {}
                expected = [label for key, label in (('id_card_mid', 'สำเนาบัตรประชาชน'),
                                                    ('slip_mid', 'ใบโอนเงินจอง')) if files.get(key)]
                return {'ok': True, 'booking_id': booking_id, 'booking_no': marker['booking_no'],
                        'attach_ok': all(name in names for name in expected),
                        'attached': sum(name in names for name in expected)}

            # 后台任务（scheduler 派进 long 队列），没人等着看结果 → 预算走共用口径的
            # 后台那一档（90 秒），见 `pearnly_dms.config.pacing`。
            result = run_logged_in(endpoint, read, background=True)
            if not result.get('ok'):
                return  # A missing/ambiguous result never authorizes a new POST.
            booking_ledger.log_booking(endpoint=context['endpoint'], payload={'qa': qa},
                                       result=result, actor=binding['user_id'])
        finish(line_user_id, nonce, qa, result)
    finally:
        cache.eval("if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end",
                   1, key, owner)


def reconcile_due():
    # Keyset traversal lets legacy/expired/rebound attempts remain safely stored
    # without starving all later recoverable submissions behind the first 50.
    cursor_key = 'pearnly:dms:reconcile-cursor'
    cursor = frappe.cache().get_value(cursor_key, use_local_cache=False) or ''
    rows = frappe.db.sql(
        "select name, line_user_id, payload from `tabPearnly LINE Session` "
        "where state='booking_review' and JSON_EXTRACT(payload, '$.booking_attempt') is not null "
        "and name > %s order by name asc limit 50", (cursor,), as_dict=True,
    )
    for row in rows:
        try:
            payload = json.loads(row.payload) if isinstance(row.payload, str) else row.payload
            request(row.line_user_id, (payload or {}).get('booking_attempt') or {})
        except Exception:
            logger.warning('Booking recovery scheduling failed; retained for next pass', exc_info=True)
    frappe.cache().set_value(cursor_key, rows[-1].name if len(rows) == 50 else '', expires_in_sec=86400)
