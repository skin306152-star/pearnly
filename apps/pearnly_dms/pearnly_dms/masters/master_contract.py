# -*- coding: utf-8 -*-
"""订车所用 DMS 主档的"会话快照"与**提交前复核**。

为什么要复核：操作员选车、选色、选分店是**几分钟前**的事。等他点确认的时候，DMS 那边
主档可能已经改了（车型下架、颜色停用、银行账户换号）。拿旧选择直接建单，轻则填错，
重则把提成/转账指向不存在的东西。

所以确认的瞬间再拉一次实时主档，逐项核对：**对得上** → 放行（并在 qa 里记下版本）；
**对不上** → 不建单，把 qa 回退到那一步重问。

照搬老产品 `services/line_dms/master_contract.py`。
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from pearnly_dms.masters.company_banks import company_bank_label
from pearnly_dms.booking.conversation.qa_util import car_label, find_row, row_name

SNAPSHOT_KEYS = (
    "place_books",
    "cars",
    "term_sales",
    "regis_behalfs",
    "company_banks",
)
REQUIRED_NONEMPTY_KEYS = frozenset(SNAPSHOT_KEYS[:-1])
_ROW_WIDTHS = {"company_banks": 5}

# 逐问的答案项 → 主档键 / 标签键 / 取标签的函数
_ANSWER_SPECS = (
    ("place", "place_books", "place", "name", row_name),
    ("car", "cars", "car_search", "label", car_label),
    ("term", "term_sales", "term", "name", row_name),
    ("regis", "regis_behalfs", "regis", "name", row_name),
)
# Only model -> color is dependent. Dates, payments and customer input survive.
_REPAIR_STEPS = {"place": "place", "car": "car_search", "paint": "paint",
                 "term": "term", "regis": "regis"}


class MasterSyncError(RuntimeError):
    def __init__(self, code: str, key: str = ""):
        super().__init__(f"{code}:{key}" if key else code)
        self.code = code
        self.key = key


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _compact(rows: Iterable[list], width: int = 3) -> List[list]:
    """行只留前 `width` 列（够比对/显示就行），并丢掉没有 id 的脏行。"""
    compact = []
    for row in rows or []:
        if not row or row[0] is None:
            continue
        compact.append(
            [
                str(row[index]) if len(row) > index and row[index] is not None else ""
                for index in range(width)
            ]
        )
    return compact


def _version(rows: Dict[str, List[list]]) -> str:
    """快照版本：内容哈希（排序键固定，所以同样的主档得同样的版本）。"""
    canonical = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def build_snapshot(
    masters: Dict[str, Any],
    captured_at: Optional[str] = None,
    *,
    require_nonempty: bool = True,
) -> Dict[str, Any]:
    metadata = masters.get("_snapshot") or {}
    if not captured_at and metadata.get("as_of"):
        captured_at = datetime.fromtimestamp(float(metadata["as_of"]), timezone.utc).isoformat(timespec="seconds")
    rows: Dict[str, List[list]] = {}
    for key in SNAPSHOT_KEYS:
        source = masters.get(key)
        if not isinstance(source, list):
            raise MasterSyncError("ERR_DMS_MASTER_UNAVAILABLE", key)
        rows[key] = _compact(source, _ROW_WIDTHS.get(key, 3))
        if require_nonempty and key in REQUIRED_NONEMPTY_KEYS and not rows[key]:
            raise MasterSyncError("ERR_DMS_MASTER_EMPTY", key)
    return {
        "version": _version(rows),
        "captured_at": captured_at or _now(),
        "source_generation": metadata.get("generation", ""),
        "counts": {key: len(value) for key, value in rows.items()},
        "rows": rows,
    }


def snapshot_rows(snapshot: Dict[str, Any], key: str) -> List[list]:
    rows = (snapshot or {}).get("rows") or {}
    value = rows.get(key)
    if not isinstance(value, list):
        raise MasterSyncError("ERR_DMS_MASTER_UNAVAILABLE", key)
    if key in REQUIRED_NONEMPTY_KEYS and not value:
        raise MasterSyncError("ERR_DMS_MASTER_EMPTY", key)
    return value


def build_paint_snapshot(car_id: str, rows: List[list], *, metadata=None) -> Dict[str, Any]:
    compact = _compact(rows)
    if not compact:
        raise MasterSyncError("ERR_DMS_MASTER_EMPTY", "paints")
    payload = {"car_id": str(car_id), "rows": compact}
    return {
        **payload,
        "version": _version({"paints": compact}),
        "captured_at": (datetime.fromtimestamp(float(metadata["as_of"]), timezone.utc).isoformat(timespec="seconds")
                        if metadata and metadata.get("as_of") else _now()),
        "source_generation": (metadata or {}).get("generation", ""),
        "count": len(compact),
    }


def _change(changes: List[dict], field: str, before: str, after: str) -> None:
    if before != after:
        changes.append({"field": field, "before": before, "after": after})


def _missing_result(qa: dict, field: str, snapshot: dict, paints: List[list]) -> Dict[str, Any]:
    """某项对不上：不建单，把 qa 退回到那一步重问。"""
    updated = copy.deepcopy(qa)
    updated["master_snapshot"] = snapshot
    updated.pop("masters_synced", None)
    updated.pop("paint_snapshots", None)
    answers = updated.setdefault("answers", {})

    if field == "advisor":
        # 顾问对不上**不是重问**能解决的 —— 得去 DMS 名册补账号，所以单独一个码
        return {
            "status": "unmatched",
            "field": field,
            "qa": updated,
            "code": "ERR_DMS_ADVISOR_UNMATCHED",
        }
    if field == "bank":
        # 转账目的地没了：把这笔转账摘出来重问，其余保留
        transfers = [p for p in updated.get("payments") or [] if p.get("channel") == "transfer"]
        missing = transfers[0] if transfers else {"channel": "transfer", "amount": ""}
        updated["payments"] = [p for p in updated.get("payments") or [] if p is not missing]
        updated["pending_channel"] = missing
        updated["step"] = "pay_dst"
        return {
            "status": "unmatched",
            "field": field,
            "qa": updated,
            "code": "ERR_DMS_MASTER_UNMATCHED",
        }

    repair_fields = ["car", "paint"] if field == "car" else [field]
    for answer_key in repair_fields:
        answers.pop(answer_key, None)
    updated["repair_fields"] = repair_fields
    updated.pop("car_search", None)
    updated.pop("pages", None)
    updated["step"] = _REPAIR_STEPS[field]
    if field == "paint" and paints:
        updated["paint_snapshots"] = {
            str((answers.get("car") or {}).get("id") or ""): build_paint_snapshot(
                str((answers.get("car") or {}).get("id") or ""), paints
            )
        }
    return {
        "status": "unmatched",
        "field": field,
        "qa": updated,
        "code": "ERR_DMS_MASTER_UNMATCHED",
    }


def reconcile(qa: dict, masters: Dict[str, Any], paints: Optional[List[list]]) -> Dict[str, Any]:
    """用**确认瞬间**的 DMS 主档复核选择。不创建单据。"""
    updated = copy.deepcopy(qa)
    snapshot = build_snapshot(masters, require_nonempty=False)
    answers = updated.setdefault("answers", {})
    changes: List[dict] = []

    advisor = updated.get("advisor") or {}
    advisor_row = find_row(masters.get("advisors"), str(advisor.get("id") or ""))
    if advisor_row is None:
        return _missing_result(updated, "advisor", snapshot, paints or [])
    advisor_name = row_name(advisor_row)
    _change(changes, "advisor", str(advisor.get("name") or ""), advisor_name)
    updated["advisor"] = {**advisor, "name": advisor_name}

    for answer_key, master_key, _, label_key, label_fn in _ANSWER_SPECS:
        selected = answers.get(answer_key) or {}
        row = find_row(masters.get(master_key), str(selected.get("id") or ""))
        if row is None:
            return _missing_result(updated, answer_key, snapshot, paints or [])
        current_label = label_fn(row)
        _change(changes, answer_key, str(selected.get(label_key) or ""), current_label)
        answers[answer_key] = {**selected, label_key: current_label}

    paint_selected = answers.get("paint") or {}
    paint_row = find_row(paints, str(paint_selected.get("id") or ""))
    if paint_row is None:
        return _missing_result(updated, "paint", snapshot, paints or [])
    paint_name = row_name(paint_row)
    _change(changes, "paint", str(paint_selected.get("name") or ""), paint_name)
    answers["paint"] = {**paint_selected, "name": paint_name}

    banks = masters.get("company_banks") or []
    for payment in updated.get("payments") or []:
        if payment.get("channel") != "transfer":
            continue
        extra = payment.setdefault("extra", {})
        bank = find_row(banks, str(extra.get("dst_id") or ""))
        if bank is None:
            return _missing_result(updated, "bank", snapshot, paints or [])
        label = company_bank_label(bank)
        _change(changes, "bank", str(extra.get("dst") or ""), label)
        extra["dst"] = label

    car_id = str((answers.get("car") or {}).get("id") or "")
    paint_snapshot = build_paint_snapshot(car_id, paints or [])
    updated["master_snapshot"] = snapshot
    updated["paint_snapshots"] = {car_id: paint_snapshot}
    updated.pop("masters_synced", None)
    updated["master_validation"] = {
        "validated_at": _now(),
        "version": snapshot["version"],
        "paint_version": paint_snapshot["version"],
        "changes": changes,
    }
    # status=changed 表示"能建，但有值变了"—— 上层据此在回执里说明，不是失败
    return {"status": "changed" if changes else "ok", "qa": updated, "changes": changes}
