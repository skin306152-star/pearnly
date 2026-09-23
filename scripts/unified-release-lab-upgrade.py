"""Seed/read back fixed old-production DMS records across native Site migration.

Run seed only in a container with old-production App mounts; run verify only
after that container exits and the candidate bench has migrated the same Site.
Neither phase performs external IO or dispatches a background worker.
"""
import json
import os
from pathlib import Path
import sys
import uuid

import frappe


BASELINE = "7128a1e5decf9b79ee19e995c0bf9ead938e5a50"
SITE = "release-dms-upgrade.localhost"
SITE_ROOT = Path("/home/frappe/frappe-bench/sites")
FIELDS = (
    "name", "status", "trigger", "business_key", "request_body", "response_body",
    "attempt", "actor", "http_status", "retry_count", "max_retries", "lease_owner",
    "lease_expires_at", "next_retry_at", "records_deleted_at", "records_deleted_by",
)


def snapshot(name):
    doc = frappe.get_doc("Pearnly Push Log", name)
    return json.loads(json.dumps({field: doc.get(field) for field in FIELDS}, default=str))


def main(phase):
    if os.environ.get("PEARNLY_RELEASE_LAB") != "1":
        raise SystemExit("Requires dedicated release laboratory")
    if not (SITE_ROOT / ".unified-release-lab").is_file():
        raise SystemExit("Missing dedicated Site volume marker")
    os.chdir(SITE_ROOT)
    frappe.init(site=SITE)
    frappe.connect()
    if not frappe.conf.pearnly_release_lab or frappe.conf.db_host != "db":
        raise SystemExit("Refusing foreign Site")
    frappe.set_user("Administrator")
    evidence = SITE_ROOT / SITE / "private/release-upgrade-fixture.json"
    module = frappe.db.get_value("DocType", "Pearnly Push Log", "module")
    if phase == "seed":
        if os.environ.get("PEARNLY_RELEASE_LAB_BASELINE_SHA") != BASELINE:
            raise SystemExit("Missing exact old-production source marker")
        assert module == "Pearnly Core", module
        assert not evidence.exists(), "Fixture already seeded; do not overwrite evidence"
        from pearnly_core.push_log import record

        token = uuid.uuid4().hex
        names = []
        for index, status in enumerate(("success", "retrying", "pending")):
            name = record(
                status, trigger="line", business_key=f"release-lab-{token}-{index}",
                request_body={"lab_only": True, "sequence": index},
                response_body=f"fixture-response-{index}", http_status=200 if index == 0 else 0,
                attempt=index + 1, actor="Administrator",
            )
            if status == "retrying":
                frappe.db.set_value("Pearnly Push Log", name, {
                    "retry_count": 2, "max_retries": 5, "lease_owner": "lab-lease-owner",
                    "lease_expires_at": "2030-01-02 03:04:05", "next_retry_at": "2030-01-02 04:05:06",
                })
            if status == "success":
                frappe.db.set_value("Pearnly Push Log", name, {
                    "records_deleted_at": "2026-09-24 01:02:03", "records_deleted_by": "Administrator",
                })
            names.append(name)
        frappe.db.commit()
        evidence.write_text(json.dumps({"baseline": BASELINE, "records": [snapshot(n) for n in names]}, indent=2))
        print(f"PASS old-production fixture: {BASELINE}; module={module}; rows={len(names)}")
    elif phase == "verify":
        assert module == "Pearnly DMS", module
        saved = json.loads(evidence.read_text())
        assert saved["baseline"] == BASELINE
        for expected in saved["records"]:
            assert snapshot(expected["name"]) == expected, expected["name"]
        from pearnly_dms.records.push_log import status_of

        history = {row["name"]: row["status"] for row in status_of(None, None)["history"]}
        for expected in saved["records"]:
            assert history[expected["name"]] == expected["status"]
        print(f"PASS candidate upgrade: module={module}; {len(saved['records'])} rows x {len(FIELDS)} fields unchanged")
        print("PASS moved DMS service reads migrated success/retrying/pending records")
    else:
        raise SystemExit("Use seed or verify")
    frappe.destroy()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) == 2 else "")
