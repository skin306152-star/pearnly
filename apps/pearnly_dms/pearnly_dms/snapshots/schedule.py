"""Native five-minute dispatcher; only published product collector hooks run.

No login or caller-triggered warming here. Stable jobs deduplicate queued/running
work by current reader scope. Account locks remain in the transport.
"""
import logging

import frappe
from pearnly_dms.snapshots import store
from pearnly_dms.contracts.client_base import DmsClientError

logger = logging.getLogger(__name__)
QUEUE = "dms_sync"


def collectors():
    return frappe.get_hooks("pearnly_dms_snapshot_collectors") or []


def enqueue(endpoint, collector):
    key = store.scope(endpoint, reader=collector["reader"])
    method = collector["method"]
    if not str(method).startswith("pearnly_dms."):
        raise ValueError("Invalid DMS collector")
    return frappe.enqueue(method, endpoint=endpoint, queue=QUEUE,
        timeout=300 if collector["reader"] == "sales" else 420,
        job_id=f"dms-snapshot-{collector['dataset']}-{key}", deduplicate=True,
        enqueue_after_commit=True)


def sweep():
    seen, queued, skipped = set(), 0, 0
    endpoints = frappe.get_all("Pearnly ERP Endpoint",
        filters={"enabled": 1, "adapter": "mrerp_dms"}, pluck="name")
    for endpoint in endpoints:
        for collector in collectors():
            try:
                key = (collector["dataset"], store.scope(endpoint, reader=collector["reader"]))
                if key in seen:
                    continue
                seen.add(key)
                if collector["dataset"] == "geo" and not store.stage(endpoint):
                    try:
                        if not store.read(endpoint, "geo", max_age=3 * 86400,
                                          refresh_interval=86400)["stale"]:
                            continue
                    except store.SnapshotUnavailable:
                        pass
                enqueue(endpoint, collector)
                queued += 1
            except DmsClientError:
                skipped += 1  # Disabled identity / no query rights / incomplete credentials.
            except Exception:
                logger.exception("DMS snapshot dispatch failed")
                skipped += 1
    return {"queued": queued, "skipped": skipped}
