"""Real native dms_sync queue consumption, isolated snapshot lab only.

Enqueues UUID-owned frappe.utils.now jobs and one exact native sweep job. A real bounded burst worker must
consume dms_sync while leaving an owned long-queue sentinel queued. No scheduler,
collector, third-party request or business write runs. Native scheduler checks
read registration, compute due boundaries in memory, then enqueue but never
perform the exact sweep job. They preserve last_execution and precisely remove
only their new job. Queue must initially be empty for the burst test; foreign
jobs are never consumed or cleaned.
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
    def test_native_scheduler_registration_and_five_minute_due_boundaries(self):
        lab["connect"]()
        method = "pearnly_dms.snapshots.schedule.sweep"
        try:
            fields = ["name", "method", "frequency", "cron_format", "stopped", "last_execution", "modified"]
            rows = frappe.get_all("Scheduled Job Type", filters={"method": method}, fields=fields)
            self.assertEqual(len(rows), 1, "Hooks must be synchronized to the native scheduler table")
            persisted = rows[0]
            self.assertEqual(persisted.frequency, "Cron")
            self.assertEqual(persisted.cron_format, "*/5 * * * *")
            self.assertFalse(persisted.stopped)
            self.assertEqual(frappe.get_all("Scheduled Job Type", filters={
                "method": "pearnly_dms.adapter.warm.sweep", "stopped": 0}, pluck="name"), [],
                "The legacy one-minute warm sweep must not remain active")
            self.assertEqual(get_scheduler_tick(), 60)
            self.assertTrue(frappe.conf.pause_scheduler, "The lab scheduler must remain disabled")
            before_queues = {name: get_queue(name).get_job_ids() for name in ("default", "long", "dms_sync")}
            job = frappe.get_doc("Scheduled Job Type", persisted.name)
            self.assertEqual(job.get_queue_name(), "dms_sync")
            start = datetime(2026, 9, 24, 0, 0, 0)  # Fixed naive UTC, matching native scheduler timestamps.
            job.last_execution = start
            first = job.get_next_execution()
            self.assertEqual(first, start + timedelta(seconds=300))
            self.assertFalse(job.is_event_due(first - timedelta(microseconds=1)))
            self.assertTrue(job.is_event_due(first))
            self.assertTrue(job.is_event_due(first + timedelta(microseconds=1)))
            job.last_execution = first
            second = job.get_next_execution()
            self.assertEqual(second - first, timedelta(seconds=300))
            job.last_execution = start + timedelta(minutes=7, seconds=12)
            self.assertEqual(job.get_next_execution(), start + timedelta(minutes=10),
                             "Cron follows five-minute wall-clock boundaries, not time since completion")
            job.last_execution = datetime(2026, 9, 24, 23, 55, 0)
            self.assertEqual(job.get_next_execution(), datetime(2026, 9, 25, 0, 0, 0))
            self.assertEqual(frappe.get_all("Scheduled Job Type", filters={"method": method}, fields=fields), rows)
            self.assertEqual({name: get_queue(name).get_job_ids() for name in before_queues}, before_queues)
        finally:
            frappe.db.rollback()
            frappe.destroy()
            self.assertEqual(lab["HTTP_ATTEMPTS"], [])

    def test_native_scheduler_enqueue_routes_only_snapshot_sweep_to_dedicated_queue(self):
        lab["connect"]()
        method = "pearnly_dms.snapshots.schedule.sweep"
        runner = "frappe.core.doctype.scheduled_job_type.scheduled_job_type.run_scheduled_job"
        created = None
        try:
            from frappe.core.doctype.scheduled_job_type.scheduled_job_type import ScheduledJobType
            from pearnly_dms.snapshots.scheduler_job import DmsSnapshotScheduler
            name = frappe.db.get_value("Scheduled Job Type", {"method": method}, "name")
            self.assertTrue(name)
            native = frappe.get_doc("Scheduled Job Type", name)
            self.assertIn(DmsSnapshotScheduler, type(native).__mro__)
            self.assertIn(ScheduledJobType, type(native).__mro__)
            self.assertEqual(native.get_queue_name(), "dms_sync")
            # Resolve the actual extended native class for other jobs too;
            # ordinary Cron and Long routing must still come from super().
            other = frappe.get_doc({"doctype": "Scheduled Job Type", "method": "frappe.utils.now",
                                    "frequency": "Cron", "cron_format": "*/5 * * * *"})
            self.assertIn(DmsSnapshotScheduler, type(other).__mro__)
            self.assertEqual(other.get_queue_name(), "default")
            other.frequency = "Daily Long"
            self.assertEqual(other.get_queue_name(), "long")
            queues = {key: get_queue(key) for key in ("default", "long", "dms_sync")}
            baseline = {key: queue.get_job_ids() for key, queue in queues.items()}
            before = frappe.db.get_value("Scheduled Job Type", name, ["last_execution", "modified"])
            self.assertIsNone(get_job(native.rq_job_id), "Refusing to replace any existing native sweep job")
            self.assertTrue(native.enqueue(force=True))
            created = get_job(native.rq_job_id)
            self.assertIsNotNone(created)
            self.assertEqual(created.func_name, "frappe.utils.background_jobs.execute_job")
            self.assertEqual(created.kwargs.get("site"), lab["SITE"])
            self.assertEqual(created.kwargs.get("method"), runner)
            self.assertEqual(created.kwargs.get("kwargs"), {"job_type": method, "scheduled_job_type": name})
            self.assertEqual(created.origin, queues["dms_sync"].name)
            self.assertEqual(created.timeout, 420)
            self.assertEqual(created.get_status(refresh=True), "queued")
            self.assertEqual(queues["dms_sync"].get_job_ids(), baseline["dms_sync"] + [created.id])
            for key in ("default", "long"):
                self.assertEqual(queues[key].get_job_ids(), baseline[key])
            self.assertEqual(frappe.db.get_value("Scheduled Job Type", name, ["last_execution", "modified"]), before)
        finally:
            if created:
                # Recheck exact ownership before removing the never-executed job.
                self.assertEqual(created.kwargs.get("site"), lab["SITE"])
                self.assertEqual(created.kwargs.get("method"), runner)
                self.assertEqual(created.kwargs.get("kwargs", {}).get("scheduled_job_type"), name)
                self.assertEqual(created.get_status(refresh=True), "queued")
                queues["dms_sync"].remove(created.id)
                created.delete()
                self.assertEqual({key: queue.get_job_ids() for key, queue in queues.items()}, baseline)
            frappe.db.rollback()
            frappe.destroy()
            self.assertEqual(lab["HTTP_ATTEMPTS"], [])

    def test_native_burst_worker_does_not_consume_interactive_long(self):
        lab["connect"]()
        jobs = []
        try:
            self.assertEqual(frappe.conf.get("scheduler_tick_interval"), 60)
            self.assertEqual(get_scheduler_tick(), 60)
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
