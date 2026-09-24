"""Admin-only manual read requests for this Site's fixed primary DMS connection.

Status is local metadata only. Neither this RPC nor page polling logs in to DMS.
The snapshots domain owns queue deduplication, scope validation and collection.
"""
from __future__ import annotations

import json
import math
import re

import frappe
from frappe import _

from pearnly_dms.connections.api import ADAPTER, ENDPOINT, _require_admin

# 地址（geo）**不在**这里：全国四级目录是租户可编辑的字典，但一次完整读取只要几十个
# 请求、约 8 秒，且每天自动刷新一次就够（见 `snapshots/geo_daily.py`）。把它放进"更新
# 数据"按钮只会让操作员为一个通常不需要的动作等待。手动刷新仍由管理员入口负责。
DATASETS = ("vehicles", "customers", "company")
STATES = frozenset(("idle", "queued", "running", "waiting", "succeeded", "failed"))


def _primary_endpoint(*, required=False):
    rows = frappe.get_all(
        ENDPOINT, filters={"adapter": ADAPTER, "owner_user": ["is", "not set"]},
        fields=["name", "enabled"], limit_page_length=2,
    )
    if len(rows) > 1:
        frappe.throw(_("Multiple primary DMS connections exist; ask an administrator to check the configuration"))
    if not rows:
        if required:
            frappe.throw(_("No DMS connection has been configured yet"))
        return None
    if required and not rows[0].get("enabled"):
        frappe.throw(_("Enable the DMS connection before reading data"))
    return rows[0]["name"]


def _number(value, *, integer=False, nullable=False):
    if value is None and nullable:
        return None
    try:
        number = float(value)
        if not math.isfinite(number) or number < 0:
            return None if nullable else 0
        return int(number) if integer else number
    except (ValueError, TypeError, OverflowError):
        return None if nullable else 0


def _public_status(dataset, value):
    """Never return domain internals, credentials, customer fields or raw errors."""
    value = value if isinstance(value, dict) else {}
    progress = value.get("progress") or {}
    progress = progress if isinstance(progress, dict) else {}
    error = str(value.get("error_code") or "")
    request_id = str(value.get("request_id") or "")
    state = value.get("state")
    return {
        "dataset": dataset,
        "state": state if state in STATES else "idle",
        "request_id": request_id if re.fullmatch(r"[a-f0-9]{32}", request_id) else "",
        "progress": {
            "processed": _number(progress.get("processed"), integer=True),
            "total": _number(progress.get("total"), integer=True, nullable=True),
            "unit": dataset,
        },
        "count": _number(value.get("count"), integer=True, nullable=True),
        "as_of": _number(value.get("as_of")),
        "completed_at": _number(value.get("completed_at")),
        "stale": bool(value.get("stale")),
        "error_code": error if re.fullmatch(r"ERR_[A-Z0-9_]{1,100}", error) else "",
    }


def _datasets(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            value = None
    if (not isinstance(value, list) or not 1 <= len(value) <= len(DATASETS)
            or any(not isinstance(item, str) or item not in DATASETS for item in value)):
        frappe.throw(_("Select one or more DMS data groups to read"))
    return [item for item in DATASETS if item in value]


@frappe.whitelist(methods=["GET"])
def get_status():
    _require_admin()
    endpoint = _primary_endpoint()
    if endpoint is None:
        return {"configured": False, "datasets": []}
    from pearnly_dms.snapshots import manual

    return {"configured": True, "datasets": [
        _public_status(dataset, manual.status(endpoint, dataset, reader="authority"))
        for dataset in DATASETS
    ]}


@frappe.whitelist(methods=["POST"])
def request_refresh(datasets):
    actor = _require_admin()
    selected = _datasets(datasets)
    endpoint = _primary_endpoint(required=True)
    from pearnly_dms.snapshots import manual

    results = []
    for dataset in selected:
        try:
            value = manual.request(endpoint, dataset, reader="authority", actor=actor)
        except frappe.PermissionError:
            raise
        except Exception as exc:
            # Each registration has its own committed queue state. A failure in
            # one selection must not hide the others or expose raw exception data.
            value = {"state": "failed", "error_code": getattr(exc, "error_code", None)
                     or "ERR_DMS_SNAPSHOT_INTERRUPTED"}
        results.append(_public_status(dataset, value))
    return {"configured": True, "datasets": results}
