"""Hash existing local lab rows across additive Delivery schema migration.

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
    path = Path(frappe.get_site_path('.eleventh-delivery-upgrade-proof.json'))
    tables = ['Cowork Workspace', 'Cowork Client', 'Cowork ERP Connection', 'Cowork ERP Snapshot',
              'Cowork ERP Refresh', 'Cowork Express Agent Credential', 'Cowork Push Record',
              'Cowork Push Attempt', 'Cowork Push Writer Lock', 'Cowork OCR History',
              'Cowork Source File', 'Cowork OCR Batch', 'Cowork OCR Correction', 'Pearnly OCR Work']
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
    if before:
        path.write_text(json.dumps({'columns': columns, 'result': result}))
    else:
        assert result == json.loads(path.read_text())['result'], 'Existing lab data changed'
        assert frappe.db.exists('DocType', 'Cowork Companion Delivery')
    print(json.dumps({'phase': sys.argv[1], 'site': frappe.local.site, 'tables': result, 'status': 'PASS'}))
finally:
    frappe.db.rollback()
    frappe.destroy()
