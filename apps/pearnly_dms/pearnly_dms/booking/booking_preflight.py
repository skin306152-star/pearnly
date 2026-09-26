# -*- coding: utf-8 -*-
"""订车提交前的**权威主档复核**（只读），写路径不受它控制。

提交前必须按 DMS **当下**的主档判能否继续，而销售账号常看不到车型/颜色/收款银行的
全表 —— 用销售的不完整视图复核，会把「DMS 里明明还在」的选择判成"主档变更"。
所以复核走**权威只读会话**（配了独立管理员凭据组时由 `dms_admin_read` 借管理员，
未配即销售），整块只解析一次管理员 transport。

本模块**只做读取与结论，不写任何 DMS 单据**：`readonly_preflight` 抓一份实时主档
（+ 选中车型的颜色）再交给 `master_contract.reconcile` 判。建单 / 客户写入 / 附件挂载
仍由订车流程在**原销售 transport** 上执行，且只提交一次。

照搬老产品 `services/line_dms/booking_preflight.py`（线上基准 `501589f0`），逻辑未改。
"""

from __future__ import annotations

from typing import Any, Dict, List

from pearnly_dms.masters import master_contract as master_contract
from pearnly_dms.booking.conversation.qa_util import find_row


def readonly_preflight(
    client: Any,
    adapter: Any,
    qa: dict,
    *,
    fetch_masters,
    fetch_payment_banks,
) -> Dict[str, Any]:
    """实时复核主档：返回 `{status, field, code, qa}` 或 `{status: "ok", masters, paints, qa}`。

    `client` 是权威读取会话上的 `DmsClient`（`fetch_masters` / `_bshsd_all` 走它的
    transport）。颜色读不到（回 `None`）按 `ERR_DMS_MASTER_UNAVAILABLE` 处理 ——
    **不拿空表冒充"这车没颜色"**。
    """
    from pearnly_dms.contracts.client_base import DmsClientError

    answers = (qa.get("answers") or {}) if isinstance(qa, dict) else {}
    live_masters = {**fetch_masters(client), **fetch_payment_banks(adapter)}
    selected_car_id = str((answers.get("car") or {}).get("id") or "")
    selected_car = find_row(live_masters.get("cars"), selected_car_id)
    live_paints: List[list] = []
    if selected_car is not None:
        live_paints = client._bshsd_all("txtcarpaint", idcar=selected_car_id)
        if live_paints is None:
            raise DmsClientError(
                f"DMS paint master unavailable for car {selected_car_id!r}",
                "ERR_DMS_MASTER_UNAVAILABLE",
            )
    preflight = master_contract.reconcile(qa, live_masters, live_paints)
    if preflight["status"] != "ok":
        return {
            "status": preflight["status"],
            "field": preflight.get("field", ""),
            "code": preflight.get("code", "ERR_DMS_MASTER_CHANGED"),
            "qa": preflight["qa"],
        }
    return {
        "status": "ok",
        "masters": live_masters,
        "paints": live_paints,
        "qa": preflight["qa"],
    }


def local_preflight(qa: dict, directory: Any) -> Dict[str, Any]:
    """提交前对照**本地完整快照**复核主档（ADR-013，用户 2026-09-27：主档不常变，不再为此
    借管理员去 DMS 实时读）。返回形状与 `readonly_preflight` 相同。

    快照由白天每分钟的增量同步与凌晨全量保持新鲜；这里不访问 DMS、不占任何账号。
    选中车型已不在快照里 → 由 `reconcile` 按"主档已变"退回重选；颜色目录缺失 → 明确报
    `ERR_DMS_MASTER_UNAVAILABLE`，不拿空表冒充"这车没颜色"。
    """
    from pearnly_dms.contracts.client_base import DmsClientError

    answers = (qa.get("answers") or {}) if isinstance(qa, dict) else {}
    masters = directory.masters()
    selected_car_id = str((answers.get("car") or {}).get("id") or "")
    paints: List[list] = []
    if find_row(masters.get("cars"), selected_car_id) is not None:
        try:
            paints = directory.paints(selected_car_id)
        except DmsClientError as exc:
            raise DmsClientError(
                f"DMS paint snapshot unavailable for car {selected_car_id!r}",
                "ERR_DMS_MASTER_UNAVAILABLE",
            ) from exc
    preflight = master_contract.reconcile(qa, masters, paints)
    if preflight["status"] != "ok":
        return {
            "status": preflight["status"],
            "field": preflight.get("field", ""),
            "code": preflight.get("code", "ERR_DMS_MASTER_CHANGED"),
            "qa": preflight["qa"],
        }
    return {"status": "ok", "masters": masters, "paints": paints, "qa": preflight["qa"]}
