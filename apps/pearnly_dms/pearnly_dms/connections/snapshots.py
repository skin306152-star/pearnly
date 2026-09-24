"""Status of this Site's fixed primary DMS connection, plus the first-read bootstrap.

**没有手动触发器**：连接页不再有"更新数据"按钮。读取只发生在两个时点——
① 管理员绑定连接成功时，自动建一次缓存底座（`bootstrap`，含四组）；
② 日常由操作动作顺手带回的增量（见 `snapshots/change_sync`）。

`get_status` 只读本地元数据，既不登录 DMS 也不入队；界面用它显示进度。
队列去重、scope 校验与采集本身归 snapshots 域。
"""
from __future__ import annotations

import logging
import math
import re

import frappe
from frappe import _

from pearnly_dms.connections.api import ADAPTER, ENDPOINT, _require_admin

logger = logging.getLogger(__name__)

#: 读取顺序即界面展示顺序。四组都要 —— 地址也是底座的一部分（全国四级目录）。
DATASETS = ("vehicles", "customers", "company", "geo")
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


def bootstrap(endpoint: str, actor: str) -> list:
    """首次绑定成功后建缓存底座：把四组依次排队读取。

    **内部入口，不是 RPC**：只有 `connections.api.save_connection` 在绑定成功时调用。
    凭据一换，缓存 scope 就变（scope 含凭据指纹），所以这里总是要重读一遍底座。

    单组登记失败不影响其它组：登记本身是各自提交的队列状态，界面上该组会显示失败，
    管理员能看见，不需要在这里吞掉或放大成整体失败。

    四组之前先排一个**基线**任务（`change_sync.seed`）：它只记下每个模块此刻的
    使用记录水位，不补任何东西。有了它，"本次绑定之后发生的改动"才有一条明确的
    起算线；底座由这四组全量读负责。
    """
    from pearnly_dms.snapshots import manual

    try:
        frappe.enqueue("pearnly_dms.adapter.change_sync.seed", endpoint=endpoint,
                       actor=actor, queue=manual.QUEUE, timeout=420,
                       job_id=f"dms-changes-seed-{endpoint}", deduplicate=True,
                       enqueue_after_commit=False)
    except Exception:
        # 基线排不上不影响底座；操作动作那一路发现没有基线会自己补一次。
        logger.warning("[dms changes] 基线任务排队失败（操作时会自动重建）")

    queued = []
    for dataset in DATASETS:
        try:
            value = manual.request(endpoint, dataset, reader="authority", actor=actor)
        except frappe.PermissionError:
            raise
        except Exception as exc:
            queued.append({"dataset": dataset, "state": "failed",
                           "error_code": getattr(exc, "error_code", None)
                           or "ERR_DMS_SNAPSHOT_INTERRUPTED"})
            continue
        queued.append({"dataset": dataset, "state": value.get("state", ""),
                       "request_id": value.get("request_id", "")})
    return queued
