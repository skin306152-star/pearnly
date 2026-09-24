"""Native OCR metadata + real log files in the guarded local snapshot Site.

No OCR execution, queue dispatch, network, billing or real identity. One UUID
owned succeeded Work fixture is deleted precisely; its safe timing log remains
in the task's retained log volume as local test evidence. The test does not
change logger levels to make INFO magically available.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch
import uuid

import frappe
import requests
from pearnly_dms.ocr import timing

lab = runpy.run_path(str(Path(__file__).with_name("test-dms-snapshot-store-native.py")))


class NativeOcrTiming(unittest.TestCase):
    def test_native_utc_metadata_and_real_private_logger(self):
        lab["connect"]()
        secret = "SYNTHETIC_PRIVATE_INPUT_" + uuid.uuid4().hex
        work = hashlib.sha256(secret.encode()).hexdigest()
        old_flag = frappe.flags.get("pearnly_ocr_work")
        try:
            with patch.object(requests.sessions.Session, "request", lab["deny_http"]):
                eligible = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=7)
                frappe.get_doc({"doctype": "Pearnly OCR Work", "name": work,
                    "product": "dms", "kind": "dms.id_card", "reference": secret,
                    "user": "Administrator", "status": "succeeded", "attempts": 3,
                    "next_run": eligible, "arguments": json.dumps({"private": secret}),
                    "checkpoints": "{}"}).insert(ignore_permissions=True, set_name=work)
                # Deliberately not UTC-eligible time: creation must never be used
                # for the ready-wait interval.
                frappe.db.set_value("Pearnly OCR Work", work, "creation", eligible - timedelta(hours=7))
                frappe.db.commit()
                frappe.flags.pearnly_ocr_work = work
                row = frappe.db.get_value("Pearnly OCR Work", work, ["next_run", "attempts"], as_dict=True)
                self.assertEqual(row.next_run, eligible)
                self.assertEqual(row.attempts, 3)
                metadata = timing._queue_metadata()
                self.assertEqual(metadata["work"], work)
                self.assertEqual(metadata["attempt"], 3)
                self.assertTrue(6000 <= metadata["ready_wait_ms"] <= 20000, metadata)
                logger = frappe.logger("pearnly.dms.ocr_timing", allow_site=True)
                paths = [Path(handler.baseFilename) for handler in logger.handlers if hasattr(handler, "baseFilename")]
                self.assertEqual(len(paths), 2, "Native bench + Site log handlers expected")
                offsets = {path: path.stat().st_size for path in paths}

                @timing.traced("line_id_card")
                def synthetic_attempt(private_input):
                    with timing.stage("local_lookup"):
                        result = {"private": private_input}
                    timing.completed_checkpoint(True)
                    return result

                self.assertEqual(synthetic_attempt(secret), {"private": secret})
                for handler in logger.handlers:
                    handler.flush()
                for path in paths:
                    with path.open("rb") as log:
                        log.seek(offsets[path])
                        emitted = log.read().decode()
                    self.assertNotIn(secret, emitted)
                    lines = [line for line in emitted.splitlines() if work in line]
                    self.assertEqual(len(lines), 1, "Timing INFO must reach each real native log")
                    self.assertIn("INFO", lines[0])
                    payload = json.loads(lines[0][lines[0].index("{"):])
                    self.assertEqual(set(payload), {"version", "kind", "outcome", "stages_ms", "stage_calls",
                        "work", "attempt", "ready_wait_ms", "attempt_ms", "completed_checkpoint"})
                    self.assertEqual((payload["kind"], payload["outcome"], payload["attempt"]),
                                     ("line_id_card", "completed", 3))
                    self.assertEqual(payload["stage_calls"], {"local_lookup": 1})
                    self.assertTrue(payload["completed_checkpoint"])
                    self.assertTrue(6000 <= payload["ready_wait_ms"] <= 20000)
                    self.assertEqual(set(payload["stages_ms"]), {"local_lookup"})
                self.assertFalse(lab["HTTP_ATTEMPTS"])
        finally:
            frappe.flags.pearnly_ocr_work = old_flag
            frappe.db.rollback()
            frappe.db.delete("Pearnly OCR Work", {"name": work, "reference": secret})
            frappe.db.commit()
            self.assertFalse(frappe.db.exists("Pearnly OCR Work", work))
            frappe.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
