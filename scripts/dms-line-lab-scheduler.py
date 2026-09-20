"""Verify native scheduling in the owned lab; LINE targets its local simulator.

Enables the lab's native scheduler and runs the four registered reconciliation
jobs through native RQ. No new order is submitted. Existing test order recovery
may perform readback/attachment repair, using its existing guards and identity.
"""
import json
import os
import time

import frappe
from frappe.utils.scheduler import enable_scheduler, is_scheduler_inactive

os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='dms-line-lab.localhost', sites_path='.')
frappe.connect()
frappe.set_user('Administrator')
methods = ['pearnly_dms.menu.rich_menu.reconcile',
           'pearnly_dms.booking.recovery.reconcile_due',
           'pearnly_dms.booking.delivery.schedule_due',
           'pearnly_dms.booking.claim_recovery.schedule_due']
original = {}
try:
    assert frappe.conf.pearnly_line_channel_access_token == 'lab:' + frappe.local.site
    assert not frappe.conf.maintenance_mode and not frappe.conf.pause_scheduler
    enable_scheduler()
    frappe.clear_cache()
    frappe.db.commit()
    assert not is_scheduler_inactive(verbose=False)
    started = frappe.utils.now_datetime()
    jobs = []
    for method in methods:
        name = frappe.db.get_value('Scheduled Job Type', {'method': method}, 'name')
        assert name, method
        original[name] = frappe.db.get_value('Scheduled Job Type', name, 'create_log')
        frappe.db.set_value('Scheduled Job Type', name, 'create_log', 1, update_modified=False)
        jobs.append(name)
    frappe.db.commit()
    for name in jobs:
        frappe.get_doc('Scheduled Job Type', name).enqueue(force=True)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        frappe.db.rollback()
        logs = frappe.get_all('Scheduled Job Log', filters={
            'scheduled_job_type': ['in', jobs], 'creation': ['>=', started]},
            fields=['scheduled_job_type', 'status'])
        assert all(row.status != 'Failed' for row in logs), 'A native scheduled job failed'
        completed = {row.scheduled_job_type for row in logs if row.status == 'Complete'}
        if set(jobs) <= completed:
            break
        time.sleep(2)
    assert set(jobs) <= completed, 'Scheduled jobs did not complete through native RQ'
    print(json.dumps({'passed': True, 'native_jobs': len(completed), 'scheduler_enabled': True}))
finally:
    for name, value in original.items():
        frappe.db.set_value('Scheduled Job Type', name, 'create_log', value, update_modified=False)
    frappe.db.commit()
    frappe.destroy()
