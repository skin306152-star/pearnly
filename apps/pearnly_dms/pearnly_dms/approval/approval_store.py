# -*- coding: utf-8 -*-
"""DMS 改写审批申请的**状态机与存储**（数据层，不认识 LINE）。

照搬老产品 `services/line_dms/approval_store.py`（线上基准 `501589f0`，状态机未改），
async → 同步，Postgres 表 → Frappe DocType `DMS Change Request`。

## 状态机（一句话）

    待批 pending ──管理员点批准──▶ 执行中 processing ──写成功──▶ 已批 approved
                                        │
                                        ├──写失败──▶ 回到 pending（可再批，**清掉认领人**）
                                        └──崩了──▶ 超租约后视同 pending（自愈）
    待批 pending ──管理员点拒绝──▶ 已拒 rejected
    待批 pending ──超过 24 小时──▶ 已过期 expired（**读的时候才发现**，惰性判定）

## 三处照搬时最容易错的地方

1. **"谁先点谁认领"必须是原子的**。老产品是一条 Postgres CAS
   （`UPDATE … WHERE pending OR 租约过期 RETURNING *`）；**Frappe 的 DocType 没有
   条件更新**，所以这里换成"**Redis 锁 + 锁内读改写**"—— 判据与线上那条 WHERE 一一对应。
   抢不到锁直接回 None（说明另一个管理员正在处理），绝不放行。
2. **执行租约**：`processing` 超过 10 分钟视同回到 pending。这是给"执行方崩了"留的自愈口，
   不是重试机制 —— 判据在 `_claimable()`，claim 与读都用它。
3. **写失败要"回炉"而不是判失败**：`finish(..., "pending")` 会把状态退回并**清掉认领人**，
   这样任何管理员都能再点一次。DMS 拒绝（比如字段被下游规则挡住）时走的就是这条。

## 新平台的管道映射

· `tenant_id` 参数全部去掉 —— **一个站点就是一家租户**，存储位置本身就是隔离；
· 惰性过期（读时落库）保留：`get_request()` 会先把过期的 pending 标成 expired 再返回；
· DDL/自愈那一层不需要了：DocType 由 `bench migrate` 建表（老产品靠首用自愈）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import frappe

logger = logging.getLogger(__name__)

DT = "DMS Change Request"

# 执行租约窗：processing 超过此时长视同回到 pending（执行方崩溃自愈）。
PROCESSING_LEASE_MINUTES = 10
DEFAULT_TTL_HOURS = 24

_LOCK_PREFIX = "pearnly:dms:approval-lock:"
LOCK_SECONDS = 30


# ── 锁（与 `line_session` / `master_shared` 同一套写法） ─────────────────────
def _lock(request_id: str) -> Optional[str]:
    """抢这把申请锁。返回 owner（拿到）或 None（别人正在处理）。"""
    owner = frappe.generate_hash(length=32)
    try:
        ok = frappe.cache().set(
            _LOCK_PREFIX + str(request_id), owner, nx=True, px=LOCK_SECONDS * 1000
        )
    except Exception:  # noqa: BLE001
        logger.warning("[approval] 抢锁失败", exc_info=True)
        return None
    return owner if ok else None


def _unlock(request_id: str, owner: str) -> None:
    try:
        current = frappe.cache().get(_LOCK_PREFIX + str(request_id))
        # ⚠️ Redis 拿回来是 bytes，不解码就永远判成"不是自己的锁"，锁删不掉
        if isinstance(current, (bytes, bytearray)):
            current = current.decode("utf-8", "ignore")
        if current is not None and str(current) != str(owner):
            return  # 别人的锁不能删
        frappe.cache().delete(_LOCK_PREFIX + str(request_id))
    except Exception:  # noqa: BLE001
        logger.warning("[approval] 释锁失败（到点会自己过期）", exc_info=True)


# ── 判据 ─────────────────────────────────────────────────────────────────────
def _now() -> datetime:
    return frappe.utils.now_datetime()


def _as_dt(value: Any) -> Optional[datetime]:
    """DocType 的 Datetime 读回来可能是 str（也可能已经是 datetime）。"""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return frappe.utils.get_datetime(value)
    except Exception:  # noqa: BLE001
        return None


def _claimable(doc) -> bool:
    """能被认领吗：**待批**，或者**执行中但租约已过**（执行方崩了）。"""
    status = str(doc.status or "")
    if status == "pending":
        return True
    if status == "processing":
        started = _as_dt(doc.processing_at)
        if not started:
            return True  # 没有开始时间 = 数据不完整，按可认领处理（宁可自愈）
        return started < _now() - timedelta(minutes=PROCESSING_LEASE_MINUTES)
    return False


def _expired(doc) -> bool:
    due = _as_dt(doc.expires_at)
    return bool(due and due < _now())


def _row(doc) -> Dict[str, Any]:
    """申请 → 纯 dict（给流程层用；字段名与线上对齐，去掉 tenant）。"""
    return {
        "id": doc.name,
        "operator_user_id": doc.operator_user,
        "endpoint_id": doc.endpoint_id or "",
        "customer_id": doc.customer_id or "",
        "customer_name": doc.customer_name or "",
        "field_diffs": _load_json(doc.field_diffs, []),
        "draft": _load_json(doc.draft, {}),
        "status": doc.status,
        "target_user_id": doc.target_user or None,
        "decided_by": doc.decided_by or None,
        "decided_at": str(doc.decided_at or ""),
        "processing_at": str(doc.processing_at or ""),
        "created_at": str(doc.creation or ""),
        "expires_at": str(doc.expires_at or ""),
    }


def _load_json(raw: Any, default: Any) -> Any:
    """JSON 字段读回来可能是字符串（也可能是已经解好的对象）。"""
    if isinstance(raw, str):
        try:
            return json.loads(raw or "null") or default
        except ValueError:
            return default
    return raw if raw is not None else default


# ── 对外：建 / 读 / 改派 / 认领 / 收尾 ───────────────────────────────────────
def create_request(
    operator_user_id: str,
    *,
    endpoint_id: str = "",
    customer_id: str,
    customer_name: str = "",
    field_diffs: Optional[List[dict]] = None,
    draft: Optional[Dict[str, Any]] = None,
    ttl_hours: int = DEFAULT_TTL_HOURS,
) -> Optional[str]:
    """落一张待审申请，返回它的名字。**差异与草稿快照存下来** —— 批准时不再依赖会话。"""
    try:
        doc = frappe.new_doc(DT)
        doc.status = "pending"
        doc.operator_user = str(operator_user_id)
        doc.endpoint_id = str(endpoint_id or "")
        doc.customer_id = str(customer_id)
        doc.customer_name = str(customer_name or "")
        # ⚠️ JSON 字段要**写字符串**：直接塞 list/dict 会被 Frappe 的类型校验挡下
        # （报"值不能是列表"）。会话层 `line_session` 也是这么写的。
        doc.field_diffs = json.dumps(list(field_diffs or []), ensure_ascii=False)
        doc.draft = json.dumps(dict(draft or {}), ensure_ascii=False)
        doc.expires_at = _now() + timedelta(hours=int(ttl_hours))
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return doc.name
    except Exception:  # noqa: BLE001
        logger.error("[approval] create_request failed", exc_info=True)
        return None


def get_request(request_id: str) -> Optional[dict]:
    """读一张申请；**惰性过期**：待批的已经过点了 → 先落 expired 再返回。"""
    if not request_id or not frappe.db.exists(DT, request_id):
        return None
    try:
        doc = frappe.get_doc(DT, request_id)
        if doc.status == "pending" and _expired(doc):
            doc.status = "expired"
            doc.flags.ignore_permissions = True
            doc.save(ignore_permissions=True)
            frappe.db.commit()
        return _row(doc)
    except Exception:  # noqa: BLE001
        logger.error("[approval] get_request failed", exc_info=True)
        return None


def set_target(request_id: str, target_user_id: Optional[str]) -> bool:
    """定/改审批人（`None` = 发给所有管理员）。**只许对未过期的待批申请改派。**"""
    if not request_id:
        return False
    lock = _lock(request_id)
    if not lock:
        return False
    try:
        if not frappe.db.exists(DT, request_id):
            return False
        doc = frappe.get_doc(DT, request_id)
        if doc.status != "pending" or _expired(doc):
            return False
        doc.target_user = str(target_user_id) if target_user_id else None
        doc.flags.ignore_permissions = True
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        return True
    except Exception:  # noqa: BLE001
        logger.error("[approval] set_target failed", exc_info=True)
        return False
    finally:
        _unlock(request_id, lock)


def claim(request_id: str, decided_by: str) -> Optional[dict]:
    """管理员点批准的**原子认领**（谁先点谁拿到）。

    有效待批（或租约已过的执行中）→ 置 `processing` + 记认领人与开始时间，返回整行快照；
    否则回 `None` —— 调用方读回现状、按 status 回话（已被别人处理 / 已拒 / 已过期）。
    """
    if not request_id or not decided_by:
        return None
    lock = _lock(request_id)
    if not lock:
        # 抢不到锁 = 另一个管理员正在处理这一张，绝不放行
        logger.info("[approval] 申请被占用，拒绝认领：%s", request_id)
        return None
    try:
        if not frappe.db.exists(DT, request_id):
            return None
        doc = frappe.get_doc(DT, request_id)
        if not _claimable(doc) or _expired(doc):
            return None
        doc.status = "processing"
        doc.processing_at = _now()
        doc.decided_by = str(decided_by)
        doc.flags.ignore_permissions = True
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        return _row(doc)
    except Exception:  # noqa: BLE001
        logger.error("[approval] claim failed", exc_info=True)
        return None
    finally:
        _unlock(request_id, lock)


def finish(request_id: str, status: str, *, expected_claim: Optional[dict] = None) -> bool:
    """执行收尾：`approved` / `rejected`（记处理时间），或 `pending`（**写失败回炉**）。

    回炉那一条会**清掉认领人与租约** —— 任何管理员都能再点一次。
    只有 `processing` 能收尾：别处调过来一律 False（防把别人正在执行的单改掉）。
    """
    if status not in ("approved", "rejected", "pending"):
        return False
    if not request_id:
        return False
    lock = _lock(request_id)
    if not lock:
        return False
    try:
        # A worker may still hold a pre-login repeatable-read view. Redis only
        # serializes writers; a current locking read must also observe a newer
        # claim, without committing unrelated caller work just to refresh it.
        doc = frappe.get_doc(DT, request_id, for_update=True)
        if str(doc.status or "") != "processing":
            return False
        if expected_claim is not None and (
            str(doc.decided_by or "") != str(expected_claim.get("decided_by") or "")
            or str(doc.processing_at or "") != str(expected_claim.get("processing_at") or "")
        ):
            return False
        if status == "pending":
            doc.status = "pending"
            doc.processing_at = None
            doc.decided_by = None
        else:
            doc.status = status
            doc.decided_at = _now()
        doc.flags.ignore_permissions = True
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        return True
    except Exception:  # noqa: BLE001
        logger.error("[approval] finish failed", exc_info=True)
        return False
    finally:
        _unlock(request_id, lock)
