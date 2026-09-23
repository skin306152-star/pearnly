# -*- coding: utf-8 -*-
"""客户档案的字段规则（纯逻辑，部署版口径）。

来源：ec2e5c957e6b ``services/clients/store.py`` 的 ``create_client`` / ``update_client``
（`eff506ba…`? 见差分测试里的固定哈希）—— 字段长度上限、"空串即未填"、可更新字段白名单、
颜色默认值。这些规则决定档案里存进去的是什么，所以逐条照搬。

**这是纯模块**：不碰 Frappe、数据库、时钟。存储层（`masters/clients.py`）拿这里清洗过的值
去写 DocType；差分测试把旧侧那两个函数放进假游标里跑，比对本模块清洗出来的每一列。

口径（部署版逐条）：
· 可写字段白名单与长度上限：name 200 / short_name 80 / tax_id 20 / address 500 /
  contact_person 100 / contact_phone 50 / contact_email 200 / notes 1000 / color 20 /
  party_type 20 / branch 120 / promptpay_id 40；
· 字符串先 strip，再截到上限，**截完为空就是未填**（存 None，不是空串）；
· 颜色没给就用 ``#3b82f6``；
· name 是唯一必填：空名不建档（旧侧直接返回 None）；
· 更新时 **只有给了值的字段才写**（`None` 表示"这次不动它"），一个可写字段都没给就不写；
· **建档与更新的清洗不完全一样**（部署版如此）：建档那一路的 `color` 原样收下（真值即用），
  清洗与截断只发生在更新那一路；本模块照此保留。
"""

from typing import Any, Dict, Optional

# 部署版的字段长度上限（create 与 update 两处，值相同）。
FIELD_LIMITS = {
    "name": 200,
    "short_name": 80,
    "tax_id": 20,
    "address": 500,
    "contact_person": 100,
    "contact_phone": 50,
    "contact_email": 200,
    "notes": 1000,
    "color": 20,
    "party_type": 20,
    "branch": 120,
    "promptpay_id": 40,
}
# 旧侧 create_client 用的、update 不认的字段：建档时的字段集比更新白名单多一个 name 之外
# 的同集；更新白名单见下（含 is_active，且不含 workspace/tenant 这类归属字段）。
CREATE_FIELDS = ("name", "short_name", "tax_id", "address", "contact_person",
    "contact_phone", "contact_email", "notes", "color", "party_type", "branch",
    "promptpay_id")
UPDATE_FIELDS = ("name", "short_name", "tax_id", "address", "contact_person",
    "contact_phone", "contact_email", "notes", "color", "is_active", "party_type",
    "branch", "promptpay_id")
DEFAULT_COLOR = "#3b82f6"


def clean_text(value: Any, field: str, *, limit: Optional[int] = None) -> Optional[str]:
    """去空白 → 截断 → 空即未填（None）。非字符串一律按字符串看待（部署版同一行为）。"""
    if value is None:
        return None
    text = str(value).strip()
    if limit is None:
        limit = FIELD_LIMITS.get(field)
    if limit is not None:
        text = text[:limit]
    return text or None


def create_values(values: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """建档字段：清洗白名单内的字段；**没有名字就返回 None**（旧侧不建档）。"""
    name = clean_text((values or {}).get("name"), "name")
    if not name:
        return None
    result: Dict[str, Any] = {"name": name}
    for field in CREATE_FIELDS:
        if field == "name":
            continue
        result[field] = clean_text((values or {}).get(field), field)
    # The deployed create keeps the colour as given whenever it is truthy (no strip,
    # no length cut there — only the update path cleans it); mirroring that exactly is
    # what makes the differential pass, odd as it is.
    result["color"] = (values or {}).get("color") or DEFAULT_COLOR
    return result


def update_values(values: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """更新字段：只取白名单里**给了值**的字段（None = 这次不动），清洗后可能变 None。

    返回 ``None`` 表示"没有任何要写的字段"（旧侧返回 False，不写库）。
    """
    result: Dict[str, Any] = {}
    for field in UPDATE_FIELDS:
        if field not in (values or {}):
            continue
        raw = values[field]
        if raw is None:
            continue
        if field == "is_active":
            result[field] = bool(raw)
            continue
        result[field] = clean_text(raw, field)
    return result or None
