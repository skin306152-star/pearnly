"""Explicit, independently published masters and resumable customer reads."""
import time

import frappe

from pearnly_dms.adapter.operations import run_logged_in
from pearnly_dms.client.snapshot_reader import (
    advance_customers, collect_masters, start_customers, SnapshotLimits,
)
from pearnly_dms.masters.company_banks import fetch_payment_bank_masters
from pearnly_dms.snapshots import manual, store


def refresh(endpoint, *, request_id=None, dataset="directory", params=None):
    # Persisted old jobs are inert; a current explicit request authorizes login.
    if not request_id:
        return {"ok": False, "retired": True}
    manual.assert_active(endpoint, dataset, request_id)
    expected = store.scope(endpoint)
    started = time.time()
    checkpoint = store.stage(endpoint, dataset) if dataset == "customers" else None
    if checkpoint and started - checkpoint["started_at"] > manual.REQUEST_AGE:
        checkpoint = None

    def capture(client, adapter):
        nonlocal checkpoint
        if dataset == "masters":
            banks = fetch_payment_bank_masters(adapter, client=client)
            data = collect_masters(client, bank_masters=banks, limits=SnapshotLimits(max_seconds=30))
            return {"ok": True, "data": data}
        if dataset != "customers":
            raise ValueError("Invalid shared DMS dataset")
        if checkpoint is None:
            checkpoint = {"started_at": started, "tree": start_customers(client,
                extra_customer_ids=store.overlay_customer_ids(endpoint),
                limits=SnapshotLimits(max_seconds=30))}
        tree = advance_customers(client, checkpoint["tree"], max_nodes=20,
                                 limits=SnapshotLimits(max_seconds=30))
        return {"ok": True, "tree": tree}

    result = run_logged_in(endpoint, capture, authoritative_read=True, background=True, read_only_batch=True)
    frappe.db.commit()
    with manual.publish_guard(endpoint, dataset, request_id):
        if not result.get("ok"):
            if result.get("error_code") == "ERR_DMS_SNAPSHOT_BUSY":
                return {**result, "busy": True}
            if dataset == "customers" and result.get("error_code") == "ERR_DMS_SNAPSHOT_INCOMPLETE":
                # Pagination drift invalidates the checkpoint, not the last good
                # generation. The next explicit retry must start with a fresh list.
                store.checkpoint(endpoint, None, dataset=dataset, expected_scope=expected)
            store.failed(endpoint, dataset, result.get("error_code"), expected_scope=expected)
            frappe.db.commit()
            return result
        if dataset == "masters":
            store.publish(endpoint, dataset, result["data"], started, expected_scope=expected)
            count = sum(len(rows) for rows in result["data"]["masters"].values() if isinstance(rows, list))
            complete = True
        else:
            tree = result["tree"]
            complete = tree["phase"] == "done"
            count = len(tree["customers"])
            if complete:
                store.publish(endpoint, dataset, {"customers": tree["customers"]}, checkpoint["started_at"],
                              expected_scope=expected)
                store.checkpoint(endpoint, None, dataset=dataset, expected_scope=expected)
            else:
                store.checkpoint(endpoint, {**checkpoint, "tree": tree}, dataset=dataset, expected_scope=expected)
    frappe.db.commit()
    return {"ok": True, "complete": complete, "count": count, "processed": count,
            "total": count if complete else tree["count"]}
