"""One explicitly requested paid OCR on the synthetic laboratory ID image.

Signed LINE fixture -> native queue -> real configured provider -> local wallet.
No external DMS writes, real LINE recipients, or real payments.
"""
import argparse
import base64
from decimal import Decimal
import json
import os
from pathlib import Path
import time

import frappe
import requests
from pearnly_core import line_session
from pearnly_core.execution import work

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--execute-real-ocr', action='store_true')
parser.add_argument('--verify-work', help='Verify a previously completed lab work item without paying again')
args = parser.parse_args()
assert args.execute_real_ocr or args.verify_work
os.chdir('/home/frappe/frappe-bench/sites')
SITE = 'dms-line-lab.localhost'


def connect(site=SITE):
    frappe.init(site=site, sites_path='.')
    frappe.connect()
    frappe.set_user('Administrator')


connect()
assert frappe.conf.pearnly_line_channel_access_token == 'lab:' + SITE
assert frappe.conf.pearnly_ocr_backend == 'vertex'
assert not frappe.db.count(work.DT, {'status':['in',work.ACTIVE]})
identity = json.loads(Path(SITE+'/private/line-lab-load-fixtures.json').read_text())[0]
started = time.monotonic()
if args.verify_work:
    name = args.verify_work
    assert frappe.db.get_value(work.DT, name, 'user') == identity['user']
else:
    previous = line_session.get_session(identity['line'])
    assert not previous, 'Do not replace an existing draft'
    line_session.set_session(identity['line'], 'collecting', {'mode':'customer'})
    frappe.db.commit()
    frappe.destroy()
    content = base64.b64encode(Path('/lab-private/ocr-load-sample.jpg').read_bytes()).decode()
    response = requests.post('http://simulator:8090/__lab/send', headers={'Host':SITE},
                             json={'user':identity['line'], 'image':content}, timeout=40)
    response.raise_for_status()
    connect()
    assert frappe.db.get_value('Pearnly LINE Event', response.json()['event_id'], 'status') == 'done'
    rows = frappe.get_all(work.DT, filters={'user':identity['user']}, fields=['name'],
                         order_by='creation desc', limit_page_length=1)
    name = rows[0].name
frappe.destroy()
deadline = time.monotonic() + 240
while time.monotonic() < deadline:
    connect()
    job = frappe.get_doc(work.DT, name)
    if job.status not in work.ACTIVE:
        break
    frappe.destroy()
    time.sleep(2)
assert job.status == 'succeeded', (job.status, job.last_code)
draft = line_session.get_session(identity['line'])
# Exact printed digits on /lab-private/ocr-load-sample.jpg, visually checked.
assert draft['payload']['id_card']['people_id'] == '1000000730317'
checkpoints = json.loads(job.checkpoints)
assert len(checkpoints) == 1 and all(c['state'] == 'done' for c in checkpoints.values())
assert all(c['body'].get('usageMetadata') for c in checkpoints.values())
assert not Path(SITE+'/private/files/ocr-work/'+name).exists(), 'Input was not cleaned after completion'
reference = 'idcard:line:' + job.reference
records = frappe.get_all('Pearnly OCR Record', filters={'reference':reference}, fields=['charged_thb'])
assert len(records) == 1 and Decimal(str(records[0].charged_thb)) == Decimal('1.5'), records
work.run(name)  # Duplicate delivery is a no-op after a completed job.
assert frappe.db.get_value(work.DT, name, 'checkpoints') == job.checkpoints
assert frappe.db.count('Pearnly OCR Record', {'reference':reference}) == 1
line_session.clear_session(identity['line'])
frappe.db.commit()
frappe.destroy()
connect('dms-line-lab-control.localhost')
charges = frappe.get_all('Pearnly Credit Transaction', filters={'reference':reference},
                        fields=['amount_thb','pages','billing_mode'])
assert len(charges) == 1 and Decimal(str(charges[0].amount_thb)) == Decimal('-1.5'), charges
assert charges[0].pages == 1 and charges[0].billing_mode == 'payg'
frappe.destroy()
print(json.dumps({'passed':True, 'seconds':round(time.monotonic()-started,2),
    'provider_results':len(checkpoints), 'charged_thb':1.5, 'input_removed':True,
    'duplicate_delivery_noop':True, 'work':name, 'external_orders_created':0}))
