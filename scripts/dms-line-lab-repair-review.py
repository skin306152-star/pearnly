"""Prepare/verify an owned obsolete-master draft for browser repair acceptance.

Only a local draft and simulated LINE messages are written. The obsolete model
must be independently absent from the real DMS before the draft is prepared.
Browser acceptance stops at the repaired summary: it must NOT confirm that card.
"""
import argparse
import copy
import json
import os
import secrets
from pathlib import Path

import frappe

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('phase', choices=('prepare', 'verify'))
args = parser.parse_args()
SITE, UID = 'dms-line-lab.localhost', 'Ulab-alice'
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site=SITE, sites_path='.')
frappe.connect()
frappe.set_user('Administrator')
from pearnly_core import line_session
from pearnly_dms.booking.conversation import qa_cards
from pearnly_dms.editor.edit_snapshot import read_edit_snapshot
from pearnly_dms.line.out import send
from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint

try:
    assert frappe.conf.pearnly_line_channel_access_token == 'lab:' + SITE
    private = Path(SITE) / 'private'
    path = private / 'line-lab-browser-repair.json'
    if args.phase == 'prepare':
        assert not path.exists(), 'Do not replace an existing repair experiment'
        assert not line_session.get_session(UID), 'Existing conversation must be preserved'
        operator = frappe.get_doc('DMS Operator', {'display_name': 'E2E DMS operator'})
        endpoint = resolve_dms_endpoint(operator.user, None)['name']
        fixture = json.loads((private / 'line-lab-master-fixture.json').read_text())
        snapshot = read_edit_snapshot(endpoint)
        assert all(str(row[0]) != str(fixture['car']) for row in snapshot['masters']['cars'])
        qa = copy.deepcopy(json.loads((private / 'line-lab-fields-expected.json').read_text()))
        qa.update(endpoint_id=endpoint, user_id=operator.user, customer_dirty=False,
                  customer_edited=True, files={}, audit=[])
        qa['answers']['car'] = {'id': str(fixture['car']), 'label': 'E2E deleted model'}
        qa['answers']['paint'] = {'id': str(fixture['carpaint']), 'name': 'E2E deleted color'}
        qa['answers']['regis_name'] = 'E2E260920 BROWSER REPAIR DO NOT SUBMIT'
        qa['payments'] = [{'channel': 'cash', 'amount': '0.01', 'extra': {}}]
        nonce = secrets.token_hex(8)
        line_session.set_session(UID, 'booking_review', {'nonce': nonce, 'qa': qa})
        frappe.db.commit()
        send(UID, qa_cards.preview_card(qa, nonce))
        evidence = {'stage': 'prepared', 'qa': qa,
                    'push_logs_before': frappe.db.count('Pearnly Push Log'),
                    'deliveries_before': frappe.db.count('DMS Booking Delivery')}
    else:
        evidence = json.loads(path.read_text())
        session = line_session.get_session(UID)
        assert session['state'] == 'booking_review' and session['payload']['nonce']
        assert not session['payload'].get('booking_attempt')
        qa = session['payload']['qa']
        before = evidence['qa']
        for key in ('customer', 'draft', 'payments', 'files'):
            assert qa[key] == before[key], key
        for key, value in before['answers'].items():
            if key not in {'car', 'paint'}:
                assert qa['answers'][key] == value, key
        assert qa['answers']['car']['id'] != before['answers']['car']['id']
        assert qa['answers']['paint']['id'] != before['answers']['paint']['id']
        assert 'repair_fields' not in qa
        assert frappe.db.count('Pearnly Push Log') == evidence['push_logs_before']
        assert frappe.db.count('DMS Booking Delivery') == evidence['deliveries_before']
        evidence.update(stage='verified', preserved_unrelated_fields=True,
                        repaired_model=qa['answers']['car']['id'],
                        repaired_color=qa['answers']['paint']['id'], new_order_created=False)
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    path.chmod(0o600)
    print(json.dumps({k: v for k, v in evidence.items() if k != 'qa'}))
finally:
    frappe.destroy()
