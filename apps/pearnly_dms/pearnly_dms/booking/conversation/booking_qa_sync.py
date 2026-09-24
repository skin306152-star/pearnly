"""Cache-only LINE booking choices; final confirmation still revalidates live.

Each step rechecks the current endpoint scope and complete generation. Saved
answers survive refresh; they are never authority to create an external order.
"""
from __future__ import annotations

from typing import Any, Dict, List

from pearnly_dms.snapshots.directory import Directory
from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_dms.masters.master_contract import (
    MasterSyncError,
    build_paint_snapshot,
    build_snapshot,
    snapshot_rows,
)


def get_masters(endpoint, *, require_complete=True):
    try:
        return Directory(endpoint).masters()
    except DmsClientError as exc:
        raise MasterSyncError(exc.error_code, "snapshot") from exc


def _endpoint(qa: Dict[str, Any]) -> str:
    return str((qa or {}).get("endpoint_id") or "")


def _snapshot(tenant_id, line_user_id, qa, *, persist) -> Dict[str, Any]:
    """每步读发布代际，保持旧选择；最终提交实时验证。"""
    endpoint = _endpoint(qa)
    if not endpoint:
        raise MasterSyncError("ERR_DMS_MASTER_UNAVAILABLE", "endpoint")
    masters = get_masters(endpoint)
    if not masters:
        raise MasterSyncError("ERR_DMS_MASTER_UNAVAILABLE", "snapshot")
    current = build_snapshot(masters)
    qa["master_snapshot"] = current
    qa.pop("masters_synced", None)
    persist(tenant_id, line_user_id, qa)
    return current


def masters(tenant_id, line_user_id, qa, key, *, persist) -> List[list]:
    """开局取一次有界新鲜共享主档；本轮按钮与分页复用该快照。"""
    return snapshot_rows(_snapshot(tenant_id, line_user_id, qa, persist=persist), key)


def paints(tenant_id, line_user_id, qa, *, persist) -> List[list]:
    """只读本地完整车型颜色目录；缺失不现场刷新。"""
    endpoint = _endpoint(qa)
    if not endpoint:
        raise MasterSyncError("ERR_DMS_MASTER_UNAVAILABLE", "endpoint")
    _snapshot(tenant_id, line_user_id, qa, persist=persist)
    car_id = str((qa.get("answers") or {}).get("car", {}).get("id") or "")
    if not car_id:
        return []
    try:
        view = Directory(endpoint)
        rows = view.paints(car_id)
    except DmsClientError as exc:
        raise MasterSyncError(exc.error_code, "paints") from exc
    paint_snapshot = build_paint_snapshot(car_id, rows, metadata=view.snapshot)
    qa.setdefault("paint_snapshots", {})[car_id] = paint_snapshot
    persist(tenant_id, line_user_id, qa)
    return list(paint_snapshot["rows"])
