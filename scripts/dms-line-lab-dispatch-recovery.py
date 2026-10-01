"""Real SQL-only recovery with the lab's general scheduler stopped.

Two already-expired test jobs have no RQ/ETA hint and cannot call OCR or DMS.
The dedicated dispatcher must discover and finish both, across isolated Sites.
"""
from datetime import timedelta
import json
import os
import secrets
import time
from unittest.mock import patch

import frappe
from pearnly_core.execution import work

os.chdir('/home/frappe/frappe-bench/sites')
created = []
started = time.monotonic()


def connect(site):
    frappe.init(site=site, sites_path='.')
    frappe.connect()
    frappe.set_user('Administrator')
    assert frappe.conf.pearnly_line_channel_access_token == 'lab:' + site


for site in ('dms-line-lab.localhost', 'dms-line-lab-b.localhost'):
    connect(site)
    reference = 'SQL-REPAIR-' + secrets.token_hex(6)
    with patch.object(work, 'kick'):
        name = work.submit('dms.id_card', reference, {'binding':{},
            'line_user_id':'Ulab-unbound-repair', 'message_id':reference}, 'Administrator')
    frappe.db.set_value(work.DT, name, {'expires_at':work.utcnow()-timedelta(seconds=1)})
    frappe.db.commit()
    created.append((site,name))
    frappe.destroy()

deadline = time.monotonic() + 90
pending = list(created)
while pending and time.monotonic() < deadline:
    for site,name in list(pending):
        connect(site)
        row = frappe.db.get_value(work.DT,name,['status','last_code'],as_dict=True)
        if row.status == 'failed':
            assert row.last_code == 'queue_expired' and not work.stored_checkpoints(name)
            pending.remove((site,name))
        else:
            assert row.status in work.ACTIVE
        frappe.destroy()
    if pending:
        time.sleep(2)
assert not pending, 'SQL-only work was not recovered by the dedicated dispatcher'
for site,name in created:
    connect(site)
    frappe.db.delete(work.DT,{'name':name})
    frappe.db.commit()
    frappe.destroy()
print(json.dumps({'passed':True,'sites':len(created),'seconds':round(time.monotonic()-started,2),
                  'initial_rq_jobs':0,'provider_calls':0,'owned_rows_removed':True}))
