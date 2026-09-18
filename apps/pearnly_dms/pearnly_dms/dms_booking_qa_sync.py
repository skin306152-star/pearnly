# -*- coding: utf-8 -*-
"""LINE 订车会话的 DMS 主档快照（会话内复用一份；提交前仍做权威实时复核）。

照搬老产品 `services/line_dms/booking_qa_sync.py`（线上基准 `501589f0`，逻辑未改），
async → 同步（新平台是 Frappe 的同步栈，没有事件循环）。

两处**架构性**差异（不是规则改动）：

1. **端点不再现解**。老产品每轮取数前按 LINE 绑定解一次 DMS 端点（`qa_endpoint`）；
   新平台**一个站点就是一家租户、一条 DMS 连接**，端点就在会话里
   （`qa["endpoint_id"]`，绑定/建档时写入）。解不出端点仍按老产品抛
   `ERR_DMS_MASTER_UNAVAILABLE` —— 发问层据此出"主档读不到"卡，不炸会话。
2. **有界新鲜那一层换了实现**：老产品是 `masters_cache.qa_snapshot` →
   `dms_master_shared.get_session_masters`；新平台同口径（会话 90 秒）在
   `adapter/master_shared.get_session_masters`，用 Redis 租约代替 Postgres 锁表。

⚠️ 快照**只在会话内复用**；提交前那次权威复核走 `dms_booking_preflight`，
与本文件无关 —— 不要拿这里的快照当"可以提交"的凭据。
"""

from __future__ import annotations

from typing import Any, Dict, List

from pearnly_dms.adapter import master_shared
from pearnly_dms.dms_master_contract import (
    MasterSyncError,
    build_paint_snapshot,
    build_snapshot,
    snapshot_rows,
)


def _endpoint(qa: Dict[str, Any]) -> str:
    return str((qa or {}).get("endpoint_id") or "")


def _snapshot(tenant_id, line_user_id, qa, *, persist) -> Dict[str, Any]:
    """本轮会话共用一份快照；没有就在有界新鲜口径内取一次**完整**主档。"""
    current = qa.get("master_snapshot")
    if current:
        return current
    endpoint = _endpoint(qa)
    if not endpoint:
        raise MasterSyncError("ERR_DMS_MASTER_UNAVAILABLE", "endpoint")
    masters = master_shared.get_session_masters(endpoint, require_complete=True)
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
    """颜色按车型保存独立快照；DMS 读取失败或空表**不落假快照**。

    先落地整份会话快照（同老产品：颜色步骤的前提是主档已取到），再取该车型
    的颜色。`force_refresh=False` —— 首发用有界新鲜的颜色缓存，提交前那次复核才实时读。
    """
    endpoint = _endpoint(qa)
    if not endpoint:
        raise MasterSyncError("ERR_DMS_MASTER_UNAVAILABLE", "endpoint")
    _snapshot(tenant_id, line_user_id, qa, persist=persist)
    car_id = str((qa.get("answers") or {}).get("car", {}).get("id") or "")
    if not car_id:
        return []
    current = (qa.get("paint_snapshots") or {}).get(car_id)
    if current:
        return list(current.get("rows") or [])
    rows = master_shared.get_session_paints(endpoint, car_id, require_complete=True) or []
    paint_snapshot = build_paint_snapshot(car_id, rows)
    qa.setdefault("paint_snapshots", {})[car_id] = paint_snapshot
    persist(tenant_id, line_user_id, qa)
    return list(paint_snapshot["rows"])
