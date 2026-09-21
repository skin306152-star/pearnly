"""Rehearse additive Companion schema upgrades on marked, isolated lab Sites.

before creates synthetic fixtures under the previous fixed source and stores
only original column names, row counts and hashes in this lab Site volume.
after compares those original values; it does not export business records.
Never runs migrations, deploys or connects to any external business service.
"""
import runpy,sys,json,hashlib
from pathlib import Path
import frappe
base=runpy.run_path('/workspace/scripts/cowork-connections-lab-verify.py')
if len(sys.argv) != 2 or sys.argv[1] not in {'before', 'after'}:
    raise SystemExit('Expected before or after')
base['BASE']['connect']()
try:
 if sys.argv[1]=='before':
  cls=base['NativeConnections'];cls.setUpClass();t=cls();t.setUp();row=t.create();t.install(row,prepared=t.preparation());frappe.db.commit()
 path=Path('/home/frappe/frappe-bench/sites/.tenth-upgrade-proof.json')
 tables=['Cowork Workspace','Cowork Client','Cowork ERP Connection','Cowork ERP Snapshot','Cowork ERP Refresh','Cowork Push Record','Cowork Push Attempt','Cowork Push Writer Lock','Cowork Express Agent Credential','Cowork Source File','Cowork OCR History','Cowork OCR Batch','Cowork OCR Correction','Pearnly OCR Work']
 cols={t:frappe.db.get_table_columns(t) for t in tables} if sys.argv[1]=='before' else json.loads(path.read_text())['columns']
 def digest(t):
  rows=frappe.get_all(t,fields=cols[t],order_by='name',limit_page_length=0)
  return {'rows':len(rows),'sha256':hashlib.sha256(json.dumps(rows,sort_keys=True,default=str).encode()).hexdigest()}
 result={t:digest(t) for t in tables}
 if sys.argv[1]=='before':path.write_text(json.dumps({'columns':cols,'result':result}))
 else:assert result==json.loads(path.read_text())['result'],(result,json.loads(path.read_text())['result'])
 print(json.dumps({'phase':sys.argv[1],'tables':result,'status':'PASS'},sort_keys=True))
finally:frappe.db.rollback();frappe.destroy()
