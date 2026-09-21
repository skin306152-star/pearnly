"""Hash existing local lab rows across additive preparation schema migration.

Only the guarded lab Site is accessible. No fixture generation, migration or
external IO. Output contains counts and hashes, including encrypted __Auth rows.
"""
import hashlib
import json
from pathlib import Path
import runpy
import sys

import frappe

base = runpy.run_path('/workspace/scripts/cowork-backend-lab-verify.py')
if len(sys.argv) != 2 or sys.argv[1] not in {'before', 'after'}:
    raise SystemExit('Expected before or after')
base['connect']()
try:
    path = Path(frappe.get_site_path('.thirteenth-inputs-upgrade-proof-v3.json'))
    tables = ['Cowork Workspace', 'Cowork Client', 'Cowork ERP Connection', 'Cowork ERP Snapshot',
              'Cowork ERP Refresh', 'Cowork Express Agent Credential', 'Cowork Push Record',
              'Cowork Push Attempt', 'Cowork Push Writer Lock', 'Cowork OCR History',
              'Cowork Source File', 'Cowork OCR Batch', 'Cowork OCR Correction', 'Pearnly OCR Work',
              'Cowork ERP Mapping', 'Cowork Supplier Posting Profile', 'Cowork Bank Evidence',
              'User', 'User Permission', 'Has Role', 'DefaultValue']
    before = sys.argv[1] == 'before'
    if before and frappe.db.exists('DocType', 'Cowork Companion Delivery'):
        tables.append('Cowork Companion Delivery')
    columns = ({table: frappe.db.get_table_columns(table) for table in tables} if before
               else json.loads(path.read_text())['columns'])
    def digest(rows):
        return {'rows': len(rows), 'sha256': hashlib.sha256(json.dumps(
            rows, sort_keys=True, default=str).encode()).hexdigest()}
    result = {table: digest(frappe.get_all(table, fields=columns[table],
                order_by='name', limit_page_length=0)) for table in columns}
    result['__Auth'] = digest(frappe.db.sql('SELECT * FROM `__Auth` ORDER BY doctype,name,fieldname', as_dict=True))
    role_rows = frappe.get_all('Has Role', fields=['*'], order_by='name', limit_page_length=0)
    roles = sorted((r.parent, r.parenttype, r.parentfield, r.role) for r in role_rows)
    raw_role_digest = result.pop('Has Role')
    result['Role Membership'] = digest(roles)
    if before:
        path.write_text(json.dumps({'columns': columns, 'result': result, 'role_rows': role_rows}, default=str))
    else:
        saved = json.loads(path.read_text())
        assert result == saved['result'], 'Existing lab data or role membership changed'
        previous = {r['name']: r for r in saved['role_rows']}
        current = {r.name: {k: str(v) if hasattr(v, 'isoformat') else v for k,v in r.items()} for r in role_rows}
        changed = [name for name in set(previous) | set(current) if previous.get(name) != current.get(name)]
        parents = {str((previous.get(name) or current[name]).get('parent')) for name in changed}
        page_names = {'pearnly-company', 'pearnly-suppliers', 'pearnly-buy-settings', 'pearnly-guide',
            'pearnly-inventory', 'pearnly-products', 'pearnly-sales', 'pearnly-integrations',
            'pearnly-stock-in', 'pearnly-push-logs', 'pearnly-work', 'pearnly-clients',
            'pearnly-home', 'pearnly-stock-out', 'pearnly-reconcile', 'pearnly-team',
            'pearnly-intake', 'pearnly-stocktake', 'pearnly-history', 'pearnly-stock-card'}
        assert not (parents - page_names), 'Unexpected role-row rewrite outside installed Pages'
        assert all((previous.get(name) or current[name])['parenttype'] == 'Page' for name in changed)
        assert all(name not in previous or name not in current for name in changed), 'Existing role row changed'
        # Frappe sync replaces Page role children with new row IDs. The exact
        # parent/type/field/role multiset above must still be identical; User
        # role rows and every other stored row remain byte-equivalent.
        print(json.dumps({'role_child_rows_rewritten': len(changed), 'parents': sorted(parents),
                          'role_membership_preserved': True}))
        assert all(frappe.db.exists('DocType', dt) for dt in ['Cowork ERP Mapping', 'Cowork Supplier Posting Profile', 'Cowork Bank Evidence'])
    print(json.dumps({'phase': sys.argv[1], 'site': frappe.local.site, 'tables': result, 'status': 'PASS'}))
finally:
    frappe.db.rollback()
    frappe.destroy()
