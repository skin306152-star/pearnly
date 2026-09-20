"""Read the lab-owned native directories through product mapping entrypoints.

Real DMS read-only requests. Does not change masters, submit orders or send LINE.
"""
import argparse
import copy
import json
import os
import time
from pathlib import Path

import frappe

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('phase', choices=('create', 'rename', 'disable-bank', 'delete'))
args = parser.parse_args()
SITE = 'dms-line-lab.localhost'
MARKER = 'E2E260920M'
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site=SITE, sites_path='.')
frappe.connect()
frappe.set_user('Administrator')
from pearnly_dms.adapter import master_shared
from pearnly_dms.booking.booking_org import resolve_booking_org
from pearnly_dms.booking.booking_preflight import readonly_preflight
from pearnly_dms.contracts.models import BookingDefaults
from pearnly_dms.editor.edit_snapshot import read_edit_snapshot
from pearnly_dms.intake.service import run_logged_in
from pearnly_dms.masters.advisor import match_in_masters
from pearnly_dms.masters.company_banks import company_bank_label, fetch_payment_bank_masters
from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint

private = Path(SITE) / 'private'
journal = json.loads((private / 'line-lab-directory-fixture.json').read_text())
ids = journal['ids']
checks = []


def check(name, condition):
    checks.append({'check': name, 'pass': bool(condition)})


def find(rows, record_id):
    return next((row for row in rows or [] if str(row[0]) == record_id), None)


def verify(masters, source):
    present = args.phase != 'delete'
    for kind, key in [('prefix', 'prefixes'), ('bank', 'company_banks'), ('branch', 'branches'), ('users', 'advisors')]:
        row = find(masters.get(key), ids[kind])
        expected = present and not (kind == 'bank' and args.phase == 'disable-bank')
        check(source + ':' + kind + ':presence', bool(row) == expected)
        if row and expected:
            name = ' '.join(str(value) for value in row)
            check(source + ':' + kind + ':name', MARKER in name and
                  (args.phase == 'create' or 'renamed' in name))
            if kind == 'bank':
                check(source + ':bank:account', ('0000920260' if args.phase == 'create' else '0000920261') in name)
    advisor = match_in_masters(masters, MARKER.lower() + '_login')
    check(source + ':login-not-employee-code', (advisor or {}).get('id') == ids['users'] if present else advisor is None)


try:
    operator = frappe.get_doc('DMS Operator', {'display_name': 'E2E DMS operator'})
    endpoint = resolve_dms_endpoint(operator.user, None)
    start = time.monotonic()
    conversation = master_shared.get_session_masters(endpoint)
    verify(conversation, 'conversation')
    snapshot = read_edit_snapshot(endpoint, car_ids=['2'])
    assert snapshot
    verify(snapshot['masters'], 'editor')
    qa = copy.deepcopy(json.loads((private / 'line-lab-fields-expected.json').read_text()))
    transfer = next(item for item in qa['payments'] if item['channel'] == 'transfer')
    bank = find(snapshot['masters']['company_banks'], ids['bank'])
    old_path = private / 'line-lab-directory-bank-draft.json'
    if args.phase == 'create':
        assert bank
        transfer['extra'].update(dst_id=ids['bank'], dst=company_bank_label(bank))
        qa['payments'] = [transfer]
        old_path.write_text(json.dumps(qa, ensure_ascii=False))
        old_path.chmod(0o600)
    else:
        qa = json.loads(old_path.read_text())

    def read(client, adapter):
        result = readonly_preflight(client, adapter, qa,
            fetch_masters=lambda cl: cl.fetch_masters(strict=True),
            fetch_payment_banks=lambda ad: fetch_payment_bank_masters(ad, client=client))
        if args.phase != 'delete':
            org = resolve_booking_org(client, ids['users'], BookingDefaults())
            check('organization:branch-id', org.branch.id == ids['branch'])
            check('organization:team-id', org.team.id == ids['team'])
            expected = MARKER + (' test' if args.phase == 'create' else ' renamed')
            check('organization:current-names', org.branch.name == expected and org.team.name == expected)
        return result

    preflight = run_logged_in(endpoint, read, authoritative_read=True)
    expected = {'create': 'ok', 'rename': 'changed', 'disable-bank': 'unmatched', 'delete': 'unmatched'}[args.phase]
    check('preflight:status', preflight['status'] == expected)
    if expected == 'unmatched':
        check('preflight:only-bank-needs-repair', preflight.get('field') == 'bank')
    report = {'phase': args.phase, 'checks': checks, 'preflight': preflight['status'],
              'seconds': round(time.monotonic() - start, 2), 'external_orders_written': 0}
    out = private / ('line-lab-directory-sync-' + args.phase + '.json')
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    out.chmod(0o600)
    print(json.dumps({'phase': args.phase, 'passed': sum(item['pass'] for item in checks),
                      'total': len(checks), 'failed': [item['check'] for item in checks if not item['pass']],
                      'preflight': preflight['status'], 'seconds': report['seconds']}))
    assert all(item['pass'] for item in checks)
finally:
    frappe.destroy()
