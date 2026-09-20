"""Real menu-5 external reads; aggregate output, no customer/order mutations."""
import json
import os
import time
import argparse
from pathlib import Path

import frappe

os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dms-line-lab.localhost', sites_path='.')
frappe.connect()
frappe.set_user('Administrator')

from pearnly_dms.query.sales_readback import fetch_sales_records, fetch_top_sales, query_capabilities

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--manager', action='store_true', help='Use the separately owned local manager test operator')
args = parser.parse_args()

checks = []
details = {}
def record(label, result, predicate):
    ok = bool(result.get('ok') and predicate(result))
    checks.append({'case': label, 'ok': ok, 'rows': len(result.get('rows') or []),
                   'error_code': result.get('error_code')})
    details[label] = result
    print(json.dumps(checks[-1]), flush=True)
    return ok

try:
    display = 'E2E query manager' if args.manager else 'E2E DMS operator'
    user = frappe.db.get_value('DMS Operator', {'display_name': display}, 'user')
    assert user, 'Create the dedicated manager fixture first'
    number = 'BK000002609000020'
    result = fetch_sales_records(user, query=number, status='all')
    if not record('exact booking number', result, lambda r: len(r['rows']) == 1 and r['rows'][0]['booking_no'] == number):
        raise AssertionError('The owned booking fixture cannot be read')
    row = result['rows'][0]
    for field in ('customer', 'vehicle', 'color', 'advisor', 'booking_date', 'delivery_date'):
        value = row[field]
        result = fetch_sales_records(user, field=field, query=value, status='all')
        record('search ' + field, result, lambda r, f=field, v=value: bool(r['rows']) and all(v.casefold() in x[f].casefold() for x in r['rows']))
    first = fetch_sales_records(user, status='all', limit=3, page=1)
    second = fetch_sales_records(user, status='all', limit=3, page=2)
    record('page 1', first, lambda r: len(r['rows']) == 3 and r['has_more'])
    record('page 2 disjoint', second, lambda r: bool(r['rows']) and not ({x['id'] for x in r['rows']} & {x['id'] for x in first.get('rows', [])}))
    for status in ('cancelled', 'active', 'draft', 'booking', 'finance_pending', 'finance_approved',
                   'manager_pending', 'ready', 'unallocated', 'allocated_no_contract',
                   'contract_opened', 'contract_not_opened', 'delivery_overdue'):
        result = fetch_sales_records(user, status=status)
        record('status ' + status, result, lambda r: r['status'] == status)
    capabilities = query_capabilities(user)
    assert capabilities.get('ok'), 'Could not establish native external scope'
    record('native ranking capability', capabilities, lambda r: isinstance(r['top_sales'], bool))
    for group in ('model', 'type', 'subtype'):
        for metric in ('quantity', 'amount'):
            result = fetch_top_sales(user, group=group, metric=metric,
                                     date_from='01/09/2569', date_to='30/09/2569', limit=3)
            if capabilities['top_sales']:
                record('top ' + group + '/' + metric, result, lambda r: len(r['rows']) <= 3 and r['date_from'] == '01/09/2569')
            else:
                denied = result.get('ok') is False and result.get('error_code') == 'ERR_DMS_QUERY_SCOPE'
                checks.append({'case': 'unauthorized top ' + group + '/' + metric, 'ok': denied})
                print(json.dumps(checks[-1]), flush=True)
finally:
    path = Path('dms-line-lab.localhost/private/line-lab-query-' + ('manager' if args.manager else 'sales') + '-matrix.json')
    path.write_text(json.dumps({'checks': checks, 'details': details}, ensure_ascii=False, indent=2))
    path.chmod(0o600)
    frappe.destroy()

print(json.dumps({'passed': sum(c['ok'] for c in checks), 'cases': len(checks), 'external_writes': 0}))
if not all(c['ok'] for c in checks):
    raise SystemExit(1)
