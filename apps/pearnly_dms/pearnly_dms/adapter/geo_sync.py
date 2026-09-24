"""Resumable full address-tree generation. Publish only a finished tree, daily.

Each bounded read-only session releases the external account lock between chunks.
Checkpoints are private; LINE never consumes a half-tree. Credentials fence every
checkpoint/publication. The timestamp remains the first chunk's source time.
"""
import time

import frappe

from pearnly_dms.adapter.operations import run_logged_in
from pearnly_dms.client.snapshot_reader import start_geo, advance_geo, SnapshotLimits
from pearnly_dms.snapshots import store
from pearnly_dms.contracts.client_base import DmsClientError

MAX_BUILD_AGE = 6 * 3600


def refresh(endpoint):
    key = store.scope(endpoint)
    lock = frappe.cache().lock(f"dms-geo-build:{key}", timeout=420, blocking_timeout=0)
    if not lock.acquire(blocking=False):
        return {"ok": False, "busy": True}
    try:
        return _refresh(endpoint, key)
    finally:
        lock.release()


def _refresh(endpoint, key):
    checkpoint = store.stage(endpoint)
    if not checkpoint:
        try:
            if not store.read(endpoint, "geo", max_age=3 * 86400, refresh_interval=86400)["stale"]:
                return {"ok": True, "fresh": True}
        except store.SnapshotUnavailable:
            pass
    now = time.time()
    if checkpoint and now - checkpoint["started_at"] > MAX_BUILD_AGE:
        checkpoint = None  # Restart abandoned partial work; preserve published tree.

    def capture(client, adapter):
        nonlocal checkpoint
        if checkpoint is None:
            checkpoint = {"started_at": time.time(), "step": 0,
                          "tree": start_geo(client, limits=SnapshotLimits(max_seconds=60))}
        tree = advance_geo(client, checkpoint["tree"], max_nodes=300,
                           limits=SnapshotLimits(max_seconds=90))
        return {"ok": True, "checkpoint": {**checkpoint, "step": checkpoint["step"] + 1, "tree": tree}}

    result = run_logged_in(endpoint, capture, authoritative_read=True, background=True)
    # Fresh permissions/credentials after external reads, not an old RR snapshot.
    frappe.db.commit()
    if not result.get("ok"):
        store.failed(endpoint, "geo", result.get("error_code"), expected_scope=key)
        frappe.db.commit()
        return {"ok": False, "error_code": result.get("error_code")}
    checkpoint = result["checkpoint"]
    tree = checkpoint["tree"]
    if tree["todo"]:
        store.checkpoint(endpoint, checkpoint, expected_scope=key)
        # A stable chunk identity coalesces retries. The account lock is released
        # before this job ends; writes and business sync can run between chunks.
        frappe.enqueue("pearnly_dms.adapter.geo_sync.refresh", endpoint=endpoint,
            queue="dms_sync", timeout=420, deduplicate=True, enqueue_after_commit=True,
            job_id=f"dms-geo-{key}-{checkpoint['started_at']}-{checkpoint['step']}")
        complete = False
    else:
        store.publish(endpoint, "geo", tree["geo"], checkpoint["started_at"], expected_scope=key)
        store.checkpoint(endpoint, None, expected_scope=key)
        complete = True
    frappe.db.commit()
    return {"ok": True, "complete": complete, "processed": tree["processed"]}
