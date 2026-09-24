"""Manual address tree, resumed in short account-lock-releasing chunks."""
import time

import frappe

from pearnly_dms.adapter.operations import run_logged_in
from pearnly_dms.client.snapshot_reader import start_geo, advance_geo, SnapshotLimits
from pearnly_dms.snapshots import manual, store


def refresh(endpoint, *, request_id=None, dataset="geo", params=None):
    if not request_id:
        return {"ok": False, "retired": True}
    manual.assert_active(endpoint, dataset, request_id)
    key = store.scope(endpoint)
    checkpoint = store.stage(endpoint)
    if checkpoint and time.time() - checkpoint["started_at"] > manual.REQUEST_AGE:
        checkpoint = None

    def capture(client, adapter):
        nonlocal checkpoint
        if checkpoint is None:
            checkpoint = {"started_at": time.time(),
                          "tree": start_geo(client, limits=SnapshotLimits(max_seconds=30))}
        tree = advance_geo(client, checkpoint["tree"], max_nodes=40,
                           limits=SnapshotLimits(max_seconds=30))
        return {"ok": True, "tree": tree}

    result = run_logged_in(endpoint, capture, authoritative_read=True, background=True, read_only_batch=True)
    frappe.db.commit()
    with manual.publish_guard(endpoint, dataset, request_id):
        if not result.get("ok"):
            if result.get("error_code") == "ERR_DMS_SNAPSHOT_BUSY":
                return {**result, "busy": True}
            if result.get("error_code") == "ERR_DMS_SNAPSHOT_INCOMPLETE":
                store.checkpoint(endpoint, None, expected_scope=key)
            store.failed(endpoint, "geo", result.get("error_code"), expected_scope=key)
            frappe.db.commit()
            return result
        tree = result["tree"]
        complete = not tree["todo"]
        if complete:
            store.publish(endpoint, "geo", tree["geo"], checkpoint["started_at"], expected_scope=key)
            store.checkpoint(endpoint, None, expected_scope=key)
        else:
            store.checkpoint(endpoint, {**checkpoint, "tree": tree}, expected_scope=key)
    frappe.db.commit()
    return {"ok": True, "complete": complete, "count": tree["processed"],
            "processed": tree["processed"], "total": tree["processed"] + len(tree["todo"])}
