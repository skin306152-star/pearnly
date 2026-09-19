# -*- coding: utf-8 -*-
"""按顾问的**原生级联**解出订车单的组织归属。

DMS 订车表单的 `detailbooksell()` 在选完顾问后会读这个端点。它回的十二列同时填满
**订车组**和**销售组**两套组织，含四级可选主管。

⚠️ **分店列表的首行不是这个顾问的分店** —— 即使那份列表是刚现抓的也不行。
组织只能按顾问 id 问出来。

照搬老产品 `services/erp/mrerp_dms_booking_org.py`（线上基准 `501589f0`），逻辑未改。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_dms.contracts.models import BookingDefaults, DMSMasterRef

logger = logging.getLogger(__name__)
_PATH = "drfcbc/component/detailbooksell.php"


@dataclass(frozen=True)
class BookingOrganization:
    branch: DMSMasterRef
    team: DMSMasterRef
    form_fields: tuple[tuple[str, str], ...]


def _scalar(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise DmsClientError("DMS advisor organization malformed", "ERR_DMS_MASTER_UNAVAILABLE")
    return str(value).strip()


def parse_booking_org(body: str) -> BookingOrganization:
    """解析原生响应；**组织不完整绝不放行成草稿**。"""
    try:
        raw = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise DmsClientError(
            "DMS advisor organization unavailable", "ERR_DMS_MASTER_UNAVAILABLE"
        ) from exc
    if not isinstance(raw, list) or len(raw) < 12:
        raise DmsClientError("DMS advisor organization malformed", "ERR_DMS_MASTER_UNAVAILABLE")
    values = [_scalar(value) for value in raw[:12]]
    for offset in range(0, 12, 2):
        rid, name = values[offset : offset + 2]
        # 分店/组是原生表单的必填项。主管四级允许有空档，
        # 但**有 id 就必须有配套的显示名**。
        if rid in {"0", "null", "undefined"}:
            rid = values[offset] = ""
        if (offset < 4 and not rid) or bool(rid) != bool(name):
            raise DmsClientError("DMS advisor organization incomplete", "ERR_DMS_MASTER_UNMATCHED")

    fields = {}
    for side in ("book", "sell"):
        fields[f"branch_{side}val"] = values[0]
        fields[f"txtbranch_{side}"] = values[1]
        fields[f"team_{side}val"] = values[2]
        fields[f"txtteam_{side}"] = values[3]
        for level in range(2, 6):
            fields[f"usersposival{level}_{side}"] = values[level * 2]
            fields[f"txtusersposi{level}_{side}"] = values[level * 2 + 1]
    return BookingOrganization(
        branch=DMSMasterRef(id=values[0], code=values[0], name=values[1]),
        team=DMSMasterRef(id=values[2], code=values[2], name=values[3]),
        form_fields=tuple(fields.items()),
    )


def resolve_booking_org(client, advisor_id: str, defaults: BookingDefaults) -> BookingOrganization:
    """读**选中的这个顾问**当前的组织；失败时按配置走管理员回退。

    回退只在**同一个 DMS 端点**上读这一个顾问，不切换销售会话、也不提权写单。
    主档列表缓存一概不用，包括同一个会话里再解一次同一个顾问。
    """
    if not advisor_id:
        raise DmsClientError("booking advisor not pinned for operator", "ERR_DMS_ADVISOR_REQUIRED")
    try:
        org = parse_booking_org(client._post_text(_PATH, {"idusers": advisor_id}))
    except DmsClientError:
        # 统一走权威只读层判定（未配管理员 → 原样抛本会话的错；配了但登录失败 → 明确失败关闭）。
        from pearnly_dms.adapter.admin_read import authoritative_read_client

        reader = authoritative_read_client(client)
        if reader is client:
            raise
        # 另起一个 reader 是为了避免"换主体"时共用 client 的备忘状态；
        # transport 仍是适配器那套已登录的浏览器 API。
        org = parse_booking_org(reader._post_text(_PATH, {"idusers": advisor_id}))
    if (defaults.branch_id and defaults.branch_id != org.branch.id) or (
        defaults.team_id and defaults.team_id != org.team.id
    ):
        logger.warning("[dms] 端点默认的组织与顾问当前归属不一致")
    return org
