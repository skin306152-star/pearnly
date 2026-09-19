"""Persistent DEMO rows for the isolated preview only; no dispatch, endpoints or jobs.

Run on stdin using the auth-preview container's bench Python (cwd: sites).
Idempotent by business key. These rows deliberately stay for user acceptance.
"""
import json

import frappe
from frappe.utils import add_to_date, now_datetime

SITE = "auth-preview.localhost"
USER = "preview@example.test"
PREFIX = "DEMO-PUSH-20260920-"
STATUSES = ("success", "failed", "skipped_dup", "pending", "retrying", "manual")

frappe.init(site=SITE)
frappe.connect()
try:
    assert frappe.local.site == SITE
    assert frappe.conf.get("pearnly_tenant") == "LOCAL-REVIEW"
    assert frappe.conf.get("pearnly_product") == "dms"
    assert frappe.conf.get("pause_scheduler")
    assert frappe.db.exists("User", USER)
    stamp = now_datetime()
    created = 0
    for index in range(26):
        key = f"{PREFIX}{index + 1:03d}"
        if frappe.db.exists("Pearnly Push Log", {"business_key": key}):
            continue
        status = STATUSES[index % len(STATUSES)]
        doc = frappe.get_doc({
            "doctype": "Pearnly Push Log", "business_key": key,
            "status": status, "trigger": "manual", "actor": USER,
            "request_body": json.dumps({"adapter": "mrerp_dms", "local_preview": True}),
            "response_body": "DEMO: layout sample only; no external request was sent.",
            "error_msg": "DEMO_TIMEOUT" if status == "failed" else "",
            "attempt": 2 if status == "retrying" else 1,
            "max_retries": 0,
        })
        doc.insert(ignore_permissions=True)
        # Native insert stamps creation itself; set fixture time afterward for date filters.
        frappe.db.set_value("Pearnly Push Log", doc.name, "creation",
                            add_to_date(stamp, days=-(index // 13), minutes=-index * 7),
                            update_modified=False)
        assert not doc.endpoint and not doc.next_retry_at and not doc.reference_name
        created += 1
    frappe.db.commit()
    print(f"Preview only: created {created}; DEMO rows retained for visual acceptance.")
except Exception:
    frappe.db.rollback()
    raise
finally:
    frappe.destroy()
