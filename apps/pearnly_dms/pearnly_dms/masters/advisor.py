# -*- coding: utf-8 -*-
"""把操作员的 DMS 登录账号匹配成订车单的销售顾问（ที่ปรึกษาการขาย）。

**为什么这件事必须做对**：DMS 按订车单上的顾问栏算销售提成，而那一栏的值是**我们
POST 上去的**（真机取证："填谁存谁"，与登录账号无关；三个键留空则整单被拒收）。
老实现恒填顾问主档第一行 → **全公司的单都记到同一个人头上**。

匹配分两层，精确优先：

1. **精确层**（`dms_employees.py`）：登录名 ↔ 员工表 `login` 列 → 员工 id 回顾问下拉校验。
   员工表是唯一带登录名的表 —— 顾问下拉的 `code` 列其实是员工**编号**（测试站两者相等
   只是录入习惯，真机取证过）。
2. **启发层**（`match_advisor`）：只在员工表拿不到（权限/接口变更）时才用，拿登录名去猜
   顾问下拉的 code/name 列。

账号不在顾问名册（老板借号、代录员）时，可以在端点上**钉死归属**，钉死优先于一切匹配。
钉死只认那个 **id**：文字（name）每次按 id 从**实时**名册重解，存下来的名字不作数 ——
名字直接决定月底提成表上印谁，客户端能改的值不能信；名册里已删/改名 → 拦下来，
不拿旧配置放行。

照搬老产品 `services/erp/dms_advisor.py`（线上基准 `501589f0`）。两处容易搬错的
地方按线上 `b78697f3`（2026-09-12，"实时校验完整主档关系与变更，禁止旧值及首项兜底"）：

1. 无传入快照时**一律现抓** `get_masters(force_refresh=True, require_complete=True)`，
   不认缓存；
2. `_pinned_advisor` 的 id 必须在当前名册里，**名字取实时值、读取失败不放行**。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from pearnly_dms.masters import employees as dms_employees
from pearnly_dms.masters import masters_cache as dms_masters_cache
from pearnly_dms.client.client_ops import row_by_id
from pearnly_dms.intake.service import endpoint_name

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

    只在员工表拿不到时用。先比 code 列，没有再比 name 列（有些站把登录名直接当顾问名）。
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
    """主档快照 + 登录名 → 顾问；认不准弃权。**精确层优先，员工表缺料才回落启发层。**

    精确层认出人就地定论：该员工在顾问下拉里 → 归属他；不在 → None（拦截）。
    "不在下拉" = 职位/团队不够当顾问，是**合法状态、不是取数失败**；此时再让启发层去比
    code/name，只会把单记到别人头上 —— 在"编号 ≠ 登录名"的车行里，
    A 的编号完全可能正是 B 的登录名。
    """
    advisors = (masters or {}).get("advisors") or []
    employee = dms_employees.match_by_login((masters or {}).get("employees") or [], username)
    if employee is not None:
        row = row_by_id(advisors, employee.get("id"))
        return _ref(row) if row is not None else None
    return match_advisor(advisors, username)


def resolve_operator_advisor(
    endpoint: Any, *, masters: Optional[Dict[str, Any]] = None
) -> Tuple[Optional[Dict[str, str]], str]:
    """端点 → `(顾问, DMS 登录名)`。返回登录名只用于拦截话术，
    好让销售知道**是哪个账号**没对上。

    阻塞函数（可能触发一次 DMS 登录去抓主档），异步调用方要放线程里跑。
    """
    pinned = booking_defaults(endpoint)
    if str(pinned.get("advisor_id") or "").strip():
        return _pinned_advisor(endpoint, pinned, masters=masters), ""
    username = dms_username(endpoint)
    if not username:
        return None, ""
    if masters is not None:
        return match_in_masters(masters, username), username
    # 一律现抓（force + require_complete），**不认缓存**：顾问栏直接决定月底提成算给谁，
    # 拿一份可能缺行/陈旧的名单去匹配，等于把人记错。抓不到就空表 → 下面的 None 走拦截。
    fresh = dms_masters_cache.get_masters(endpoint, force_refresh=True, require_complete=True) or {}
    return match_in_masters(fresh, username), username


def _pinned_advisor(
    endpoint: Any,
    pinned: Dict[str, Any],
    *,
    masters: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, str]]:
    """端点上钉死的归属（账号不在顾问名册时的出路）。

    指定 ID 仍必须在**当前主档**存在；文字采用实时值，**读取失败不得使用旧配置放行**。

    存下来的 `advisor_name` 只当线索、**不作数**：客户端能改的名字直接决定月底提成表上
    印谁，一律按 id 从实时名册重解。名册里已删/改名的钉子 → None（拦截），
    不拿旧配置继续放行。
    """
    advisor_id = str(pinned.get("advisor_id") or "").strip()
    fresh = (
        masters
        if masters is not None
        else dms_masters_cache.get_masters(endpoint, force_refresh=True, require_complete=True) or {}
    )
    row = row_by_id(fresh.get("advisors"), advisor_id)
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
