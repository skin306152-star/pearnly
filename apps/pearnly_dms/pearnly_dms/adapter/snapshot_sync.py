"""Background-only complete authority directory capture; no external writes."""
import time

import frappe

from pearnly_dms.adapter.operations import run_logged_in
from pearnly_dms.client.snapshot_reader import collect_business, SnapshotLimits
from pearnly_dms.masters.company_banks import fetch_payment_bank_masters
from pearnly_dms.snapshots import store
from pearnly_dms.contracts.client_base import DmsClientError


def refresh(endpoint):
    started = time.time()
    expected = store.scope(endpoint)
    # Capture before login; any concurrent later write is merged under row lock.
    extra_ids = store.overlay_customer_ids(endpoint)

    def capture(client, adapter):
        banks = fetch_payment_bank_masters(adapter, client=client)
        remaining = 300 - (time.time() - started)
        if remaining <= 0:
            raise DmsClientError("Directory budget exhausted", "ERR_DMS_SNAPSHOT_INCOMPLETE")
        data = collect_business(client, bank_masters=banks, extra_customer_ids=extra_ids,
                                 limits=SnapshotLimits(max_seconds=min(240, remaining)))
        return {"ok": True, "data": data}

    result = run_logged_in(endpoint, capture, authoritative_read=True, background=True)
    # Release MariaDB's pre-login read snapshot: credential/permission changes in
    # another connection must be visible before expected_scope is checked.
    frappe.db.commit()
    if result.get("ok"):
        published = store.publish(endpoint, "directory", result["data"], started, expected_scope=expected)
    else:
        store.failed(endpoint, "directory", result.get("error_code"), expected_scope=expected)
        published = False
    frappe.db.commit()
    return {"ok": bool(published), "error_code": result.get("error_code", ""),
            "elapsed_ms": int((time.time() - started) * 1000)}
