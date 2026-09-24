"""Private, bounded timings for the DMS ID-card path; never log business inputs.

One record per attempt, not a second OCR execution or a business checkpoint.
Nested recognition contributes to the current LINE trace. Logging failure must
not change billing, retries, identity checks or the result returned to the caller.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import json
import logging
import re
import time

import frappe
from pearnly_core.execution.errors import Deferred, Uncertain

_active = ContextVar("dms_id_card_timing", default=None)
_STAGES = frozenset({"loading", "download", "endpoint", "billing_gate", "prepare_image",
                     "recognition", "billing_charge", "record", "local_lookup", "line_reply"})


@contextmanager
def stage(name):
    trace = _active.get()
    if trace is None or name not in _STAGES:
        yield
        return
    started = time.monotonic()
    try:
        yield
    finally:
        durations = trace["stages_ms"]
        durations[name] = durations.get(name, 0) + max(0, round((time.monotonic() - started) * 1000))
        counts = trace["stage_calls"]
        counts[name] = counts.get(name, 0) + 1


def completed_checkpoint(value):
    trace = _active.get()
    if trace is not None:
        trace["completed_checkpoint"] = bool(value)


def _queue_metadata():
    """next_run is UTC; creation is site-local and must not be subtracted from it."""
    name = frappe.flags.get("pearnly_ocr_work")
    if not isinstance(name, str) or not re.fullmatch(r"[a-f0-9]{64}", name):
        return {}
    row = frappe.db.get_value("Pearnly OCR Work", name, ["attempts", "next_run"], as_dict=True)
    if not row:
        return {}
    result = {"work": name, "attempt": int(row.get("attempts") or 0)}
    eligible = row.get("next_run")
    if isinstance(eligible, datetime):
        eligible = eligible.replace(tzinfo=timezone.utc) if eligible.tzinfo is None else eligible
        result["ready_wait_ms"] = max(0, round((datetime.now(timezone.utc) - eligible).total_seconds() * 1000))
    return result


def traced(kind):
    if kind not in {"line_id_card", "recognize_id_card"}:
        raise ValueError("Unknown ID-card trace")

    def decorate(fn):
        @wraps(fn)
        def run(*args, **kwargs):
            if _active.get() is not None:
                return fn(*args, **kwargs)
            trace = {"version": 1, "kind": kind, "outcome": "error", "stages_ms": {}, "stage_calls": {}}
            started = time.monotonic()
            try:
                trace.update(_queue_metadata())
            except Exception:
                pass  # Telemetry cannot block OCR if metadata is unavailable.
            token = _active.set(trace)
            try:
                result = fn(*args, **kwargs)
                trace["outcome"] = ("cancelled" if isinstance(result, dict) and result.get("cancelled")
                                    else "failed" if isinstance(result, dict) and result.get("failed")
                                    else "completed")
                return result
            except Deferred:
                trace["outcome"] = "deferred"
                raise
            except Uncertain:
                trace["outcome"] = "uncertain"
                raise
            finally:
                _active.reset(token)
                trace["attempt_ms"] = max(0, round((time.monotonic() - started) * 1000))
                try:
                    # No actor, message ID, filename, photo, result, exception or credential.
                    log = frappe.logger("pearnly.dms.ocr_timing", allow_site=True)
                    # Frappe defaults to ERROR outside developer mode. Enable
                    # only this fixed private timing logger, never global logs.
                    log.setLevel(logging.INFO)
                    log.info(json.dumps(trace, sort_keys=True))
                except Exception:
                    pass
        return run
    return decorate
