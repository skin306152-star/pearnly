"""Owned local Site recovery checks; --external-read verifies an existing test order.

Never creates, updates or deletes an external order. Local fixtures are scoped
by fixed lab user IDs; the external mode requires the previously verified order.
"""
import argparse
import copy
import json
import os
from pathlib import Path
from unittest.mock import Mock, patch

import frappe
import requests

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--external-read', action='store_true')
args = parser.parse_args()
os.chdir('/home/frappe/frappe-bench/sites')
site = 'dms-line-lab.localhost' if args.external_read else 'dms-line-lab-b.localhost'
frappe.init(site=site, sites_path='.')
frappe.connect()
frappe.set_user('Administrator')

from pearnly_core import line_session as store
from pearnly_dms.booking import recovery, delivery
from pearnly_dms.line.identity import current
from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint

uid = 'Ulab-alice' if args.external_read else 'Ulab-recovery-fixture'
nonce = 'local-recovery-fixture'
checks = []

def check(label, condition):
    checks.append({'case': label, 'ok': bool(condition)})
    assert condition, label
    print(json.dumps(checks[-1]), flush=True)

try:
    assert store.get_session(uid) is None, 'Do not overwrite a user draft'
    if args.external_read:
        private = Path(site + '/private')
        qa = json.loads((private / 'line-lab-fields-expected.json').read_text())
        previous = json.loads((private / 'line-lab-fields-readback.json').read_text())
        assert previous['ok'] and previous['booking_no'] == 'BK000002609000021'
        submitted = {k.split('.', 1)[1]: v['expected'] for k, v in previous['checks'].items()
                     if k.startswith('booking.') and 'expected' in v}
        submitted['txtdocno'] = previous['booking_no']
        live = current(uid)
        binding = {'user_id': live['user'], 'binding_revision': live['binding_revision']}
        endpoint = resolve_dms_endpoint(live['user'], None)['name']
        context = {'binding': binding, 'endpoint': endpoint, 'qa': qa, 'submitted': submitted,
                   'endpoint_fingerprint': recovery.endpoint_fingerprint(endpoint)}
        number = previous['booking_no']
    else:
        qa = {'answers': {}, 'advisor': {}, 'files': {}}
        context = {'binding': {'user_id': 'local-fixture', 'binding_revision': 'r'},
                   'endpoint': 'EP-local-fixture', 'submitted': {'txtdocno': 'LOCAL-ONLY'}, 'qa': qa}
        number = 'LOCAL-ONLY'
    store.set_session(uid, 'booking_review', {'nonce': nonce, 'qa': qa})
    store.consume_nonce(uid, 'booking_review', nonce)
    check('register immutable submitted evidence', store.record_booking_attempt(
        uid, nonce, number, 'local-test-owner', context=context))
    check('wrong claim cannot replace result', not store.update_booking_attempt(uid, 'wrong', {'result': {'ok': True}}))
    check('submission contract cannot be overwritten', not store.update_booking_attempt(uid, nonce, {'context': {}}))
    marker = recovery._marker(uid)
    check('do not recover while original worker may be running', not recovery.ready(marker))

    if not args.external_read:
        check('different worker cannot advance the submission number', not store.record_booking_attempt(
            uid, nonce, 'WRONG', 'different-owner', number, context=context))
        check('same worker can advance after an explicit duplicate-number rejection', store.record_booking_attempt(
            uid, nonce, 'LOCAL-ONLY-NEXT', 'local-test-owner', number, context=context))
        number = 'LOCAL-ONLY-NEXT'
        check('old number cannot overwrite the new evidence', not store.record_booking_attempt(
            uid, nonce, 'WRONG', 'local-test-owner', 'LOCAL-ONLY', context=context))

    if args.external_read:
        # Explicit crash fixture: original worker is absent and this is an already
        # verified test order. Age only this owned marker, never the business data.
        session = store.get_session(uid)
        session['payload']['booking_attempt']['recorded_at'] = str(frappe.utils.add_to_date(minutes=-20))
        frappe.db.set_value(store.DT, uid, 'payload', json.dumps(session['payload']))
        frappe.db.commit()
        with patch.object(delivery.line, 'push', return_value=False) as send:
            # Conversation path holds the evidence (no DMS read); the record path reads it back.
            recovery.reconcile(uid)
            check('unknown evidence held, conversation freed', store.get_session(uid) is None and
                  frappe.db.get_value(delivery.DT, delivery.name_for(uid, nonce), 'status') == delivery.UNVERIFIED)
            recovery.reconcile_record(delivery.name_for(uid, nonce))
            check('existing external order recovered without a new submission', json.loads(frappe.get_doc(delivery.DT, delivery.name_for(uid, nonce)).attempt)['result'].get('ok'))
            check('receipt failure preserves actual external ID', json.loads(frappe.get_doc(delivery.DT, delivery.name_for(uid, nonce)).attempt)['result']['booking_id'] == previous['booking_id'])
            check('unknown notice and receipt were attempted once each', send.call_count == 2)
        check('success ledger not duplicated', frappe.db.count('Pearnly Push Log', {
            'actor': binding['user_id'], 'business_key': number, 'status': 'success'}) == 1)
    else:
        result = {'ok': True, 'booking_id': 'LOCAL-1', 'booking_no': number, 'attach_ok': True, 'attached': 0}
        with patch.object(delivery, 'matches', return_value=True), \
             patch.object(delivery.cards, 'booking_receipt_text', return_value='local test receipt'), \
             patch.object(delivery.line, 'push', side_effect=requests.Timeout('fixture')):
            check('network failure retains result', not recovery.finish(uid, nonce, qa, result))
    name = delivery.name_for(uid, nonce)
    saved = json.loads(frappe.get_doc(delivery.DT, name).attempt)
    check('success no longer blocks next draft', store.get_session(uid) is None)
    check('result persisted before receipt IO', saved['result']['booking_id'] == (previous['booking_id'] if args.external_read else 'LOCAL-1'))
    retry_key = saved['receipt']['key']
    if not args.external_read:
        with patch.object(delivery, 'matches', return_value=False), patch.object(delivery.line, 'push') as send:
            check('rebound employee gets no prior receipt', not delivery.deliver(name))
            check('no send to rebound identity', not send.called)
        check('rebound delivery retained for review', frappe.db.get_value(delivery.DT, name, 'status') == 'Review')
        # Reset only this owned local test fixture to exercise expiry independently.
        frappe.db.set_value(delivery.DT, name, 'status', 'Pending'); frappe.db.commit()
        with patch.object(delivery, 'matches', return_value=True), \
             patch.object(delivery, 'age', return_value=24 * 3600), patch.object(delivery.line, 'push') as send:
            check('expired retry is not sent', not delivery.deliver(name))
            check('no expired retry dispatch', not send.called)
        check('expiry retained for review', frappe.db.get_value(delivery.DT, name, 'status') == 'Review')
        frappe.db.set_value(delivery.DT, name, 'status', 'Pending'); frappe.db.commit()
    store.set_session(uid, 'collecting', {'fixture': 'next draft'})
    with patch.object(delivery, 'matches', return_value=True), patch.object(delivery.line, 'push', return_value=True) as send:
        check('retry completes existing delivery', delivery.deliver(name))
        check('retry key unchanged', send.call_args.kwargs['retry_key'] == retry_key)
        check('next draft preserved', store.get_session(uid)['payload'] == {'fixture': 'next draft'})
    check('delivered status persisted', frappe.db.get_value(delivery.DT, name, 'status') == 'Delivered')
    with patch.object(delivery.line, 'push') as send:
        check('completed delivery is idempotent', delivery.deliver(name) and not send.called)
    store.clear_session(uid)
    frappe.delete_doc(delivery.DT, name, ignore_permissions=True); frappe.db.commit()
    if not args.external_read:
        from pearnly_dms.booking import claim_recovery
        store.set_session(uid, 'booking_review', {'nonce': 'abandoned', 'qa': qa})
        store.consume_nonce(uid, 'booking_review', 'abandoned')
        with patch.object(claim_recovery, 'current', return_value={'user': 'local'}), \
             patch.object(claim_recovery.out, 'send') as send:
            check('active worker claim is not reclaimed', not claim_recovery.recover(uid))
            draft = store.get_session(uid)['payload']
            draft['booking_claimed_at'] = str(frappe.utils.add_to_date(minutes=-20))
            store.set_session(uid, 'booking_review', draft)
            check('abandoned pre-submit claim recovers to confirmation', claim_recovery.recover(uid))
            updated = store.get_session(uid)['payload']
            check('old worker loses claim; draft data preserved', bool(updated.get('nonce')) and
                  not updated.get('booking_claim') and updated['qa'] == qa and send.call_count == 1)
        store.clear_session(uid)
    evidence = {'passed': len(checks), 'checks': checks, 'external_order_writes': 0,
                'external_read': args.external_read, 'tested_at': frappe.utils.now()}
    output = Path(site + '/private') / ('line-lab-recovery-external.json' if args.external_read else 'line-lab-recovery-local.json')
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    output.chmod(0o600)
    print(json.dumps({k: v for k, v in evidence.items() if k != 'checks'}))
finally:
    # Exact disposable local test state only. Failed runs keep their evidence.
    frappe.destroy()
