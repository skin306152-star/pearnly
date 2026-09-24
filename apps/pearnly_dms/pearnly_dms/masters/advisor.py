# -*- coding: utf-8 -*-
"""把操作员的 DMS 登录账号匹配成订车单的销售顾问（ที่ปรึกษาการขาย）。

**为什么这件事必须做对**：DMS 按订车单上的顾问栏算销售提成，而那一栏的值是**我们
POST 上去的**（真机取证："填谁存谁"，与登录账号无关；三个键留空则整单被拒收）。
老实现恒填顾问主档第一行 → **全公司的单都记到同一个人头上**。

自动匹配只使用员工表 login → 员工 ID → 有效顾问 ID。员工编号和姓名不是登录身份，
取数失败、零命中或重复登录名都不能借它们猜测归属。管理员明确指定的顾问 ID 仍可使用。

账号不在顾问名册（老板借号、代录员）时，可以在端点上**钉死归属**，钉死优先于一切匹配。
钉死只认那个 **id**：文字（name）每次按 id 从传入的完整名册重解，存下来的名字不作数。
LINE 开局使用已发布快照；最终提交仍在写服务里独立实时核对，不拿旧配置放行。

本模块只读取本地端点配置并匹配调用方传入的主档；未传主档明确拒绝，
不登录 DMS，不刷新或回退旧缓存。缺失、空表或匹配失败均不能猜测顾问归属。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from pearnly_dms.masters import employees as dms_employees
from pearnly_dms.client.client_ops import row_by_id
from pearnly_dms.contracts.endpoint import endpoint_name

logger = logging.getLogger(__name__)

_COL_CODE = 1
_COL_NAME = 2


def _cell(row: list, idx: int) -> str:
    if not row or len(row) <= idx or row[idx] is None:
        return ""
    return str(row[idx]).strip()


def _ref(row: list) -> Dict[str, str]:
    """会话里只留 id（DMS 认人的键）+ name（预览卡/回执给人看）。

    code/tel 不存：建单层会按 id 从实时名册重解一次（`_advisor_ref_strict`），
    存进会话只会等主档改了以后变陈旧，还多两处要维护。
    """
    return {"id": _cell(row, 0), "name": _cell(row, _COL_NAME)}


def match_advisor(advisors: List[list], dms_username: str) -> Optional[Dict[str, str]]:
    """启发层：DMS 登录名 → 顾问主档行；**认不准就弃权（返回 None），不猜**。

    保留的纯匹配辅助函数；当前自动归属不以它作为员工表缺失时的回退。
    先比 code 列，没有再比 name 列（有些站把登录名直接当顾问名）。
    同一层命中多行 = 名册里有重名/重号，此时填错提成要到月底对账才炸 ——
    拦下来反而当场有人喊，所以**多命中一律弃权**。
    """
    needle = (dms_username or "").strip().casefold()
    if not needle:
        return None
    for col in (_COL_CODE, _COL_NAME):
        hits = [r for r in advisors or [] if _cell(r, col).casefold() == needle]
        if len(hits) == 1:
            return _ref(hits[0])
        if hits:
            return None
    return None


def match_in_masters(masters: Optional[Dict[str, Any]], username: str) -> Optional[Dict[str, str]]:
    """只有登录名唯一匹配、且同一 ID 在顾问名册中，才允许自动归属。"""
    advisors = (masters or {}).get("advisors") or []
    employee = dms_employees.match_by_login((masters or {}).get("employees") or [], username)
    if employee is not None:
        row = row_by_id(advisors, employee.get("id"))
        return _ref(row) if row is not None else None
    return None


def resolve_operator_advisor(
    endpoint: Any, *, masters: Optional[Dict[str, Any]] = None
) -> Tuple[Optional[Dict[str, str]], str]:
    """端点 → `(顾问, DMS 登录名)`。返回登录名只用于拦截话术，
    好让销售知道**是哪个账号**没对上。

    仅使用本地端点配置与显式传入的主档；缺少主档时拒绝，不触发外部读取。
    """
    pinned = booking_defaults(endpoint)
    if str(pinned.get("advisor_id") or "").strip():
        return _pinned_advisor(endpoint, pinned, masters=masters), ""
    username = dms_username(endpoint)
    if masters is None:
        logger.warning("[dms advisor] explicit masters required; refusing resolution")
        return None, username
    if not username:
        return None, ""
    return match_in_masters(masters, username), username


def _pinned_advisor(
    endpoint: Any,
    pinned: Dict[str, Any],
    *,
    masters: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, str]]:
    """端点上钉死的归属（账号不在顾问名册时的出路）。

    指定 ID 必须在传入主档存在；未传或空主档均拒绝，不使用旧配置放行。

    存下来的 `advisor_name` 只当线索、**不作数**：客户端能改的名字直接决定月底提成表上
    印谁，一律按 id 从传入名册重解。名册里没有指定 id → None（拦截）；
    最终提交的实时复核由写服务负责。
    """
    if masters is None:
        logger.warning("[dms advisor] explicit masters required; refusing resolution")
        return None
    advisor_id = str(pinned.get("advisor_id") or "").strip()
    row = row_by_id(masters.get("advisors"), advisor_id)
    return _ref(row) if row is not None else None


def booking_defaults(endpoint: Any) -> Dict[str, Any]:
    """端点上存的建单默认值（`booking_defaults`，JSON 字段）。读不到 → 空 dict。"""
    import frappe

    name = endpoint_name(endpoint)
    if not name:
        return {}
    try:
        raw = frappe.db.get_value("Pearnly ERP Endpoint", name, "booking_defaults")
    except Exception:
        logger.warning("[dms advisor] 读 booking_defaults 失败", exc_info=True)
        return {}
    if isinstance(raw, str):
        import json

        try:
            raw = json.loads(raw or "{}")
        except ValueError:
            raw = {}
    return dict(raw or {}) if isinstance(raw, dict) else {}


def dms_username(endpoint: Any) -> str:
    """这位操作员自己的 DMS 登录名（端点上存的账号）。解不出 → 空串。

    **解不出就当没匹配上**（调用方发拦截话术），绝不猜账号 —— 猜错 = 提成算到别人头上。
    """
    import frappe

    name = endpoint_name(endpoint)
    if not name:
        return ""
    try:
        return str(frappe.db.get_value("Pearnly ERP Endpoint", name, "username") or "").strip()
    except Exception:
        logger.warning("[dms advisor] 解析 DMS 登录名失败", exc_info=True)
        return ""
