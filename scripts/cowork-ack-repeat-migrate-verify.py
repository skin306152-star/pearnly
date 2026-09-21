"""Local additive-schema data preservation check; no fixtures or external IO.

Snapshot the changed domains, including new held events, before/after repeating
native migrate. This supplements the existing pre-upgrade whole-data verifier.
"""
import hashlib
import json
from pathlib import Path
import runpy
import sys
import frappe

base = runpy.run_path(str(Path(__file__).with_name('cowork-backend-lab-verify.py')))
base['connect']()
try:
    assert len(sys.argv) == 2 and sys.argv[1] in {'before', 'after'}
    path = Path(frappe.get_site_path('.fourteenth-repeat-migrate-proof.json'))
    tables = ['Cowork OCR History', 'Cowork Push Record', 'Cowork Push Attempt',
              'Cowork Push Writer Lock', 'Cowork Push Notification', 'Cowork ERP Connection']
    def fingerprint(rows):
        return {'count': len(rows), 'sha256': hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()}
    result = {t: fingerprint(frappe.get_all(t, fields=['*'], order_by='name', limit_page_length=0)) for t in tables}
    result['__Auth'] = fingerprint(frappe.db.sql('SELECT * FROM `__Auth` ORDER BY doctype,name,fieldname', as_dict=True))
    result['Role Membership'] = fingerprint(frappe.db.sql('SELECT parent,parenttype,parentfield,role FROM `tabHas Role` ORDER BY parent,parenttype,parentfield,role', as_dict=True))
    if sys.argv[1] == 'before':
        assert result['Cowork Push Notification']['count'] > 0, 'Need a persisted synthetic ACK event'
        path.write_text(json.dumps(result))
    else:
        assert json.loads(path.read_text()) == result, 'Existing lab data changed'
    print(json.dumps({'phase': sys.argv[1], 'status': 'PASS', 'tables': result}))
finally:
    frappe.db.rollback(); frappe.destroy()
