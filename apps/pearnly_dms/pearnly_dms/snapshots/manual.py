"""Explicit, scoped read requests. Status reads never enqueue or contact DMS.

The private native snapshot row holds a bounded request, not an authority token
from the browser. Product entrypoints validate their caller before requesting;
workers recheck actor, credentials and the exact request before external IO.
"""
import json
from contextlib import contextmanager
import logging
import time
import uuid

import frappe

from pearnly_dms.contracts.endpoint import endpoint_name
from pearnly_dms.snapshots import store

logger = logging.getLogger(__name__)
QUEUE = "dms_sync"
ACTIVE = {"queued", "running"}
REQUEST_AGE = 6 * 3600
LEASE_AGE = 15 * 60
# 可续读的失败自动接着排下一步：断点还在，所以那是真的往前推进，不是从零重来。
# 连失败这么多次就停手，交给下一次显式请求或夜间任务，免得对着坏掉的上游空转。
MAX_RETRIES = 5
RETRYABLE = "ERR_DMS_SNAPSHOT_RETRY"


def _collector(dataset, reader):
    for item in frappe.get_hooks("pearnly_dms_snapshot_collectors") or []:
        prefix = item.get("dataset_prefix")
        if item["reader"] == reader and (item.get("dataset") == dataset or (
                prefix and str(dataset).startswith(prefix))):
            if not str(item["method"]).startswith("pearnly_dms."):
                break
            return item
    raise ValueError("Unknown manual DMS dataset")


def _actor(endpoint, reader, actor):
    if not actor or actor == "Guest" or not frappe.db.get_value("User", actor, "enabled"):
        raise frappe.PermissionError("Manual DMS reader is unavailable")
    if reader == "sales":
        # Accept the `{"id": …, "name": …}` shape the LINE query path passes in:
        # a bare dict reached `get_value` before 2026-09-25 and produced
        # `Unknown column 'id'`, which is what menu 5 reported as a read failure.
        owner = frappe.db.get_value("Pearnly ERP Endpoint", endpoint_name(endpoint), "owner_user")
        if owner != actor:
            raise frappe.PermissionError("Manual DMS reader does not own this connection")
    elif not {"DMS Admin", "System Manager"}.intersection(frappe.get_roles(actor)):
        raise frappe.PermissionError("Only a DMS admin may refresh shared data")


def _load(endpoint, dataset, reader):
    key = store.scope(endpoint, reader=reader)
    row = frappe.db.get_value(store.DT, store._name(key, dataset),
        ["refresh_request", "generation", "source_started_at", "completed_at", "last_error"], as_dict=True)
    req = json.loads(row.refresh_request or "{}") if row else {}
    return key, row, req


def _state(req):
    state = req.get("state", "idle")
    if state in ACTIVE and (time.time() - req.get("updated_at", 0) > LEASE_AGE
                            or time.time() - req.get("created_at", 0) > REQUEST_AGE):
        return "failed"
    return state


def status(endpoint, dataset, *, reader="authority"):
    """Metadata only. Entry-point authorization is still required by the caller."""
    _collector(dataset, reader)
    _, row, req = _load(endpoint, dataset, reader)
    stamp = float(row.source_started_at or 0) if row else 0
    return {"dataset": dataset, "state": _state(req), "request_id": req.get("id", ""),
        "progress": req.get("progress", {"processed": 0, "total": None, "unit": "records"}),
        "count": req.get("count"), "as_of": stamp,
        "completed_at": float(row.completed_at or 0) if row else 0,
        "stale": bool(stamp and (time.time() - stamp > (86400 if dataset == "geo" else 1800)
                                 or row.last_error)),
        "error_code": ("ERR_DMS_SNAPSHOT_INTERRUPTED" if _state(req) != req.get("state", "idle")
                       else req.get("error_code", ""))}


def request(endpoint, dataset, *, reader="authority", params=None, actor=None):
    """Register one user-requested read; duplicate active requests share work."""
    # One canonical name from here on: it is stored in `refresh_request` and passed
    # to the queue, and `assert_active` compares both against each other.
    endpoint = endpoint_name(endpoint)
    _collector(dataset, reader)
    actor = actor or frappe.session.user
    _actor(endpoint, reader, actor)
    params = params or {}
    if not isinstance(params, dict) or len(store._dump(params).encode()) > 4096:
        raise ValueError("Invalid manual DMS read parameters")
    if reader == "sales":
        store.request(endpoint, dataset, params, reader=reader)
    with store._cache_connection() as database:
        key = store.scope(endpoint, reader=reader, current=True, _database=database)
        name = store._name(key, dataset)
        store._ensure(name, key, dataset, _database=database, exclusive=True)
        rows = database.sql(f"SELECT refresh_request FROM `tab{store.DT}` WHERE name=%s FOR UPDATE",
                            name, as_dict=True)
        previous = json.loads(rows[0].refresh_request or "{}")
        if (_state(previous) in ACTIVE and previous.get("params") == params
                and previous.get("actor") == actor):
            pending = None
        else:
            now = time.time()
            pending = {"id": uuid.uuid4().hex, "state": "queued", "actor": actor,
                "endpoint": endpoint, "expected_scope": key, "reader": reader,
                "params": params, "created_at": now, "updated_at": now, "step": 0,
                "retries": 0,
                "count": previous.get("count"), "error_code": "",
                "progress": {"processed": 0, "total": None, "unit": "records"}}
            database.sql(f"UPDATE `tab{store.DT}` SET refresh_request=%s, modified=UTC_TIMESTAMP(6) "
                         "WHERE name=%s", (store._dump(pending), name))
    if pending:
        try:
            _enqueue(endpoint, dataset, reader, pending["id"], 0)
        except Exception:
            _update(endpoint, dataset, reader, pending["id"], {
                "state": "failed", "error_code": "ERR_DMS_SNAPSHOT_INTERRUPTED"})
            raise
    # The caller may hold an older RR view. Return the newly registered metadata
    # without committing its transaction just to refresh this local projection.
    out = status(endpoint, dataset, reader=reader)
    if pending:
        out.update(state="queued", request_id=pending["id"], progress=pending["progress"], error_code="")
    return out


def _enqueue(endpoint, dataset, reader, request_id, step):
    frappe.enqueue("pearnly_dms.snapshots.manual.execute", endpoint=endpoint, dataset=dataset,
        reader=reader, request_id=request_id, step=step, queue=QUEUE, timeout=420,
        # Registration/step claim is already committed on a separate connection.
        # A surrounding RPC rollback must not strand an independently queued row.
        job_id=f"dms-manual-{request_id}-{step}", deduplicate=True, enqueue_after_commit=False)


def assert_active(endpoint, dataset, request_id, *, reader="authority"):
    if not request_id:
        raise store.SnapshotUnavailable("ERR_DMS_SNAPSHOT_MANUAL_REQUIRED")
    key, _, req = _load(endpoint, dataset, reader)
    if (req.get("id") != request_id or _state(req) not in ACTIVE
            or req.get("expected_scope") != key or req.get("endpoint") != endpoint):
        raise store.SnapshotUnavailable("ERR_DMS_SNAPSHOT_SCOPE_CHANGED")
    _actor(endpoint, reader, req.get("actor"))
    return req


@contextmanager
def publish_guard(endpoint, dataset, request_id, *, reader="authority"):
    """Lock the exact request until the caller commits its local publication.

    A replacement request uses the same row, so cannot pass this fence between
    token validation and payload/checkpoint writes. No external IO under the lock.
    """
    key = store.scope(endpoint, reader=reader)
    rows = frappe.db.sql(f"SELECT refresh_request FROM `tab{store.DT}` WHERE name=%s FOR UPDATE",
                         store._name(key, dataset), as_dict=True)
    req = json.loads(rows[0].refresh_request or "{}") if rows else {}
    if (req.get("id") != request_id or _state(req) not in ACTIVE
            or req.get("expected_scope") != key or req.get("endpoint") != endpoint):
        raise store.SnapshotUnavailable("ERR_DMS_SNAPSHOT_SCOPE_CHANGED")
    _actor(endpoint, reader, req.get("actor"))
    yield req


def _update(endpoint, dataset, reader, request_id, values, *, expected_step=None, expected_state="queued"):
    with store._cache_connection() as database:
        key = store.scope(endpoint, reader=reader, current=True, _database=database)
        name = store._name(key, dataset)
        rows = database.sql(f"SELECT refresh_request FROM `tab{store.DT}` WHERE name=%s FOR UPDATE",
                            name, as_dict=True)
        req = json.loads(rows[0].refresh_request or "{}") if rows else {}
        if req.get("id") != request_id or (expected_step is not None and (
                req.get("step") != expected_step or req.get("state") != expected_state)):
            return False
        req.update(values, updated_at=time.time())
        database.sql(f"UPDATE `tab{store.DT}` SET refresh_request=%s, modified=UTC_TIMESTAMP(6) "
                     "WHERE name=%s", (store._dump(req), name))
    return True


def execute(endpoint, dataset, reader, request_id, step=0):
    """One bounded chunk. Continuations belong to this explicit request only."""
    claimed = False
    try:
        req = assert_active(endpoint, dataset, request_id, reader=reader)
        collector = _collector(dataset, reader)
        if not _update(endpoint, dataset, reader, request_id, {"state": "running"}, expected_step=step):
            return {"ok": False, "obsolete": True}
        claimed = True
        frappe.db.commit()  # End worker's pre-claim RR view before its collector.
        result = frappe.get_attr(collector["method"])(endpoint, request_id=request_id,
                                                      dataset=dataset, params=req["params"])
        frappe.db.commit()
        assert_active(endpoint, dataset, request_id, reader=reader)
        progress = {"processed": int(result.get("processed", result.get("count", 0))),
                    "total": result.get("total", result.get("count")), "unit": "records"}
        retries = int(req.get("retries") or 0)
        if result.get("busy"):
            state = "waiting"
        elif not result.get("ok"):
            if result.get("error_code") == RETRYABLE and retries < MAX_RETRIES:
                # 传输抖动、单步租约用完这类失败可以接着读：检查点没有被丢掉。
                state = "queued"
                retries += 1
            else:
                state = "failed"
        elif result.get("complete", True):
            state = "succeeded"
            retries = 0
        else:
            state = "queued"
            retries = 0
        values = {"state": state, "progress": progress,
                  "error_code": result.get("error_code", ""), "step": step + 1, "retries": retries}
        if state == "succeeded":
            values["count"] = result.get("count", progress["processed"])
        if _update(endpoint, dataset, reader, request_id, values) and state == "queued":
            try:
                _enqueue(endpoint, dataset, reader, request_id, step + 1)
            except Exception:
                _update(endpoint, dataset, reader, request_id,
                    {"state": "failed", "error_code": "ERR_DMS_SNAPSHOT_INTERRUPTED"}, expected_step=step + 1)
                raise
        return {"ok": state not in {"failed", "waiting"}, "state": state}
    except Exception as exc:
        code = getattr(exc, "error_code", None) or "ERR_DMS_SNAPSHOT_INCOMPLETE"
        try:
            # This read-only worker owns the transaction; release any publication
            # fence before recording failure on the independent connection.
            frappe.db.rollback()
            if claimed:
                _update(endpoint, dataset, reader, request_id, {"state": "failed", "error_code": code},
                        expected_step=step, expected_state="running")
        except Exception:
            pass  # Credential revocation deliberately makes the old scope inaccessible.
        logger.warning("DMS manual read failed (%s)", type(exc).__name__)
        return {"ok": False, "error_code": code}
