"""Native manual-read lane and scheduler retirement; isolated lab only.

Only UUID-owned harmless jobs are executed. The retired read schedulers do no
IO, and unrelated business recovery hooks/queues must remain intact.
"""
from datetime import datetime, timedelta
import os
from pathlib import Path
import runpy
import subprocess
import unittest
from unittest.mock import patch
import uuid

import frappe
from frappe.utils.background_jobs import get_job, get_queue
from frappe.utils.scheduler import get_scheduler_tick
import requests

lab = runpy.run_path(str(Path(__file__).with_name("test-dms-snapshot-store-native.py")))


class DedicatedQueue(unittest.TestCase):
    def test_native_scheduler_retires_reads_and_preserves_business_recovery(self):
        lab["connect"]()
        try:
            from pearnly_dms.snapshots import schedule
            from pearnly_dms.adapter import snapshot_sync, geo_sync
            for method in ("pearnly_dms.snapshots.schedule.sweep", "pearnly_dms.adapter.warm.sweep"):
                self.assertEqual(frappe.get_all("Scheduled Job Type",
                    filters={"method": method, "stopped": 0}, pluck="name"), [])
            for method in ("pearnly_dms.booking.recovery.reconcile_due",
                           "pearnly_dms.booking.delivery.schedule_due",
                           "pearnly_dms.booking.claim_recovery.schedule_due"):
                self.assertTrue(frappe.db.exists("Scheduled Job Type", {"method": method, "stopped": 0}))
            baseline = {name: get_queue(name).get_job_ids() for name in ("default", "long", "dms_sync")}
            self.assertTrue(schedule.sweep()["retired"])
            self.assertTrue(snapshot_sync.refresh("not-a-real-endpoint")["retired"])
            self.assertTrue(geo_sync.refresh("not-a-real-endpoint")["retired"])
            self.assertEqual({name: get_queue(name).get_job_ids() for name in baseline}, baseline)
            self.assertTrue(frappe.conf.pause_scheduler)
        finally:
            frappe.db.rollback()
            frappe.destroy()
            self.assertEqual(lab["HTTP_ATTEMPTS"], [])

    def test_native_burst_worker_does_not_consume_interactive_long(self):
        lab["connect"]()
        jobs = []
        try:
            self.assertEqual(frappe.conf.get("workers", {}).get("dms_sync", {}).get("timeout"), 420)
            self.assertTrue(frappe.conf.get("pause_scheduler"))
            queues = {name: get_queue(name) for name in ("long", "dms_sync")}
            self.assertEqual(queues["dms_sync"].count, 0, "Refusing a queue already containing work")
            baseline = set(queues["long"].get_job_ids())
            uid = "snapshot-lane-" + uuid.uuid4().hex
            for name in ("long", "dms_sync"):
                job = frappe.enqueue("frappe.utils.now", queue=name, job_id=uid + "-" + name,
                                     enqueue_after_commit=False, timeout=15)
                jobs.append((queues[name], job.id))
            self.assertEqual(queues["dms_sync"].get_job_ids(), [jobs[1][1]])
            worker = subprocess.run(["bench", "worker", "--queue", "dms_sync", "--burst"],
                cwd="/home/frappe/frappe-bench", capture_output=True, text=True, timeout=45,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            self.assertEqual(worker.returncode, 0, (worker.stdout + worker.stderr)[-2000:])
            completed = queues["dms_sync"].fetch_job(jobs[1][1])
            self.assertEqual(completed.get_status(refresh=True), "finished", completed.exc_info)
            self.assertTrue(completed.return_value())
            self.assertEqual(queues["dms_sync"].count, 0)
            self.assertEqual(set(queues["long"].get_job_ids()), baseline | {jobs[0][1]})
            self.assertEqual(queues["long"].fetch_job(jobs[0][1]).get_status(refresh=True), "queued")
        finally:
            for queue, job_id in jobs:
                job = queue.fetch_job(job_id)
                if job:
                    self.assertEqual(job.kwargs.get("site"), lab["SITE"])
                    self.assertEqual(job.kwargs.get("method"), "frappe.utils.now")
                    self.assertIn(uid, job.id)
                    queue.remove(job.id)
                    job.delete()
            frappe.db.rollback()
            frappe.destroy()
            self.assertEqual(lab["HTTP_ATTEMPTS"], [])


if __name__ == "__main__":
    with patch.object(requests.sessions.Session, "request", lab["deny_http"]):
        unittest.main(verbosity=2)
