"""Isolated real Redis + real DMS read concurrency. No business writes/OCR.

10/20 concurrent readers share the authorized test reader, not 20 independent
external accounts. This proves refresh coalescing, not mixed workload capacity.
"""
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import frappe
from pearnly_dms.adapter import master_shared as shared
from pearnly_dms.masters import masters_cache as cache
from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint

SITE = "dms-line-lab.localhost"
os.chdir("/home/frappe/frappe-bench/sites")

def connect():
    frappe.init(site=SITE, sites_path=".")
    frappe.connect()
    frappe.set_user("Administrator")

connect()
try:
    operator = frappe.get_doc("DMS Operator", {"display_name": "E2E DMS operator"})
    endpoint = resolve_dms_endpoint(operator.user, None)
    assert endpoint
    scope = shared._scope(endpoint)
finally:
    frappe.destroy()

reports = []
for count in (10, 20):
    connect()
    try:
        frappe.cache().delete_value(cache._key(scope))
    finally:
        frappe.destroy()
    barrier = threading.Barrier(count)
    mutex = threading.Lock()
    calls = [0]
    actual_fetch = cache._fetch_masters_via_login

    def measured_fetch(*args, **kwargs):
        with mutex:
            calls[0] += 1
        return actual_fetch(*args, **kwargs)

    def contender(index):
        connect()
        try:
            # Seed each request-local cache with the miss before simultaneous reads.
            assert cache._read(endpoint) is None
            barrier.wait(timeout=20)
            started = time.monotonic()
            masters = shared.get_session_masters(endpoint)
            assert all(isinstance(masters.get(key), list) for key in cache._COMPLETE_KEYS)
            return time.monotonic() - started, masters
        finally:
            frappe.destroy()

    with patch.object(cache, "_fetch_masters_via_login", side_effect=measured_fetch):
        with ThreadPoolExecutor(max_workers=count) as pool:
            results = list(pool.map(contender, range(count)))
    assert calls[0] == 1, ("Duplicate external refreshes", calls[0])
    assert all(result[1] == results[0][1] for result in results)
    durations = sorted(result[0] for result in results)
    reports.append({"readers": count, "real_dms_refreshes": calls[0], "succeeded": len(results),
                    "p50_seconds": round(durations[count // 2], 2),
                    "p95_seconds": round(durations[max(0, int(count * .95) - 1)], 2)})
    print(json.dumps(reports[-1]))

# Atomic color merge on an entirely synthetic local cache key.
scope = "shared-v2:local-cache-concurrency-fixture"
connect()
try:
    cache._write(scope, {"cars": [[str(i), "code", "test"] for i in range(20)]})
    stamp = frappe.cache().get_value(cache._key(scope), use_local_cache=False)["ts"]
finally:
    frappe.destroy()

def paint_writer(index):
    connect()
    try:
        cache._write(scope, {"paints_by_car": {str(index): [[str(index), "paint", "test"]]},
                             "paints_refreshed_at": {str(index): time.time()}}, touch_refreshed_at=False)
    finally:
        frappe.destroy()

with ThreadPoolExecutor(max_workers=20) as pool:
    list(pool.map(paint_writer, range(20)))
connect()
try:
    blob = frappe.cache().get_value(cache._key(scope), use_local_cache=False)
    assert blob["ts"] == stamp, "Color refresh extended whole-master freshness"
    assert len(blob["masters"]["paints_by_car"]) == 20, "Concurrent colors lost"
    cache._write_full_preserving_paints(scope, {"cars": [["1", "code", "test"]]})
    assert set(cache._read(scope)["masters"]["paints_by_car"]) == {"1"}, "Deleted cars resurrected"
    owner = shared._acquire(scope)
    assert owner
    shared._release(scope, "stale")
    assert shared._acquire(scope) is None
    shared._release(scope, owner)
    frappe.cache().delete_value(cache._key(scope))
    path = frappe.get_site_path("private", "line-lab-masters-concurrency.json")
    with open(path, "w") as handle:
        json.dump({"real_readers": reports, "redis_color_merge": 20, "lost_colors": 0}, handle)
    os.chmod(path, 0o600)
    print("PASS 20 concurrent color updates; timestamp preserved; removed cars pruned; owner unlock guarded")
finally:
    frappe.destroy()
