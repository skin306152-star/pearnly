"""Read-only native address CRUD visibility and stale-ID submit validation."""
import argparse
import json
import os
from pathlib import Path

import frappe

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('phase', choices=('create', 'rename', 'reparent', 'delete'))
args = parser.parse_args()
SITE = 'dms-line-lab.localhost'
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site=SITE, sites_path='.')
frappe.connect()
frappe.set_user('Administrator')
from pearnly_dms.editor.edit_snapshot import read_edit_snapshot
from pearnly_dms.editor.booking_edit import _customer_master_labels
from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_dms.intake.service import run_logged_in
from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint

private = Path(SITE) / 'private'
ids = json.loads((private / 'line-lab-geo-fixture.json').read_text())['ids']
qa = json.loads((private / 'line-lab-fields-expected.json').read_text())
customer = {**qa['draft'], 'province_id': ids['provinces'], 'district_id': ids['districts'],
            'subdistrict_id': ids['subdistricts'], 'zipcode_id': ids['zipcodes']}
checks = []


def check(label, ok):
    checks.append({'check': label, 'pass': bool(ok)})


try:
    user = frappe.db.get_value('DMS Operator', {'display_name': 'E2E DMS operator'}, 'user')
    endpoint = resolve_dms_endpoint(user, None)
    snapshot = read_edit_snapshot(endpoint, customer=customer)
    assert snapshot
    expected_label = 'E2E260920G ' + ('test' if args.phase == 'create' else 'renamed')
    for kind, parent in [('provinces', ''), ('districts', ids['provinces']),
                         ('subdistricts', ids['districts']), ('zipcodes', ids['subdistricts'])]:
        row = next((r for r in snapshot['geo'][kind] if str(r[0]) == ids[kind]), None)
        if args.phase in ('create', 'rename'):
            expected = ('99998' if args.phase == 'create' else '99997') if kind == 'zipcodes' else expected_label
            check(kind + ':current-label', bool(row) and row[1] == expected)
        elif args.phase == 'delete' or kind == 'districts':
            check(kind + ':obsolete-id-absent', row is None)
    labels = _customer_master_labels(snapshot, customer)
    valid = args.phase in ('create', 'rename')
    check('editor:valid-hierarchy-only', bool(labels) == valid)

    def validate(client, adapter):
        form = client._post_text('cus/form.php', {'status': 'n'})
        data = client._parse_form_defaults(form)
        client._apply_customer_fields(data, customer)
        try:
            client._guard_required_selects(data, form)
        except DmsClientError as error:
            return {'valid': False, 'error': error.error_code}
        return {'valid': True}
    guard = run_logged_in(endpoint, validate, authoritative_read=True)
    check('customer-write-guard:valid-hierarchy-only', guard.get('valid') == valid)
    report = {'phase': args.phase, 'checks': checks, 'guard': guard, 'external_business_writes': 0}
    path = private / ('line-lab-geo-sync-' + args.phase + '.json')
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    path.chmod(0o600)
    print(json.dumps({'phase': args.phase, 'passed': sum(c['pass'] for c in checks),
                      'total': len(checks), 'failed': [c['check'] for c in checks if not c['pass']], 'guard': guard}))
    assert all(c['pass'] for c in checks)
finally:
    frappe.destroy()
