"""Manual full address catalogues, resumed in account-lock-releasing chunks.

The legacy cascade checkpoint lacked English names/native parent fields and is
never resumed. Only a fully collected/rechecked catalogue becomes a generation.
"""
import math
import time

import frappe

from pearnly_dms.adapter.operations import run_logged_in
from pearnly_dms.client.catalogue_reader import start_catalogues, advance_catalogues, CatalogueLimits
from pearnly_dms.client.geo_catalogue import geo_from_catalogues
from pearnly_dms.snapshots import manual, store


CHECKPOINT_VERSION = 2
GEO_LIMITS = CatalogueLimits(max_seconds=30, max_total_seconds=5 * 3600,
    max_requests=40000, max_total_bytes=1024 * 1024 * 1024, max_rows=50000)


def refresh(endpoint, *, request_id=None, dataset="geo", params=None):
    if not request_id:
        return {"ok": False, "retired": True}
    manual.assert_active(endpoint, dataset, request_id)
    key = store.scope(endpoint)
    checkpoint = store.stage(endpoint)
    if checkpoint is not None and (not isinstance(checkpoint, dict)
            or checkpoint.get("version") != CHECKPOINT_VERSION
            or set(checkpoint) != {"version", "started_at", "catalogues"}
            or isinstance(checkpoint["started_at"], bool)
            or not isinstance(checkpoint["started_at"], (int, float))
            or not math.isfinite(checkpoint["started_at"])
            or not 0 < checkpoint["started_at"] <= time.time() + 1
            or time.time() - checkpoint["started_at"] > manual.REQUEST_AGE):
        checkpoint = None

    def capture(client, adapter):
        nonlocal checkpoint
        if checkpoint is None:
            checkpoint = {"version": CHECKPOINT_VERSION, "started_at": time.time(),
                          "catalogues": start_catalogues(client, "geo_aux", limits=GEO_LIMITS)}
        catalogues = advance_catalogues(client, checkpoint["catalogues"],
                                       max_nodes=100, limits=GEO_LIMITS)
        result = {"ok": True, "catalogues": catalogues}
        if catalogues["done"]:
            result["geo"] = geo_from_catalogues(catalogues["data"]["catalogues"])
        return result

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
        catalogues = result["catalogues"]
        complete = catalogues["done"]
        if complete:
            store.publish(endpoint, "geo", result["geo"], checkpoint["started_at"], expected_scope=key)
            store.checkpoint(endpoint, None, expected_scope=key)
        else:
            store.checkpoint(endpoint, {**checkpoint, "catalogues": catalogues}, expected_scope=key)
    frappe.db.commit()
    return {"ok": True, "complete": complete, "count": catalogues["processed"],
            "processed": catalogues["processed"], "total": catalogues["total"]}
