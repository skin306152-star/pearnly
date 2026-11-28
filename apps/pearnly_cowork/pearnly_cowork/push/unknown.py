"""推送结果不明（写没写进去不知道）的票：录入列表的状态与筛选、复核页、公司待处理同一个判据（主会话 11-25：结果不明不得显示成
失败，写成失败会让人重录、重复入账）。

结果不明 =
- 票停在转人工（``last_push_status`` = manual）且它的推送记录标了 ``result_unknown``（电脑领单途中断了、MR.ERP 导入发出后报错或查不到，
  原因栏可能是 ``ERR_TECHNICAL`` 之类），或
- 票停在转人工或没有推送状态（旧票只记了原因，主会话 11-28：这些票原来两档都筛不到）、原因栏写的是结果不明的码（``…RESULT_UNKNOWN…``、
  ``confirmed_push_unacked``，与 `push.lifecycle.result_unknown` 同一批码）。
"""
from __future__ import annotations

import frappe

from pearnly_cowork.push.lifecycle import CONFIRMED_UNACKED

RECORD = "Cowork Push Record"
MANUAL = "manual"
OPEN = (MANUAL, "")   # 停着的：转人工，或旧票没有推送状态只有原因
SETTLED = ("success", "pending")
MARKERS = ("RESULT_UNKNOWN", CONFIRMED_UNACKED.upper())   # 原因栏里认的码（不分大小写）
LIKE = ("%result_unknown%", "%" + CONFIRMED_UNACKED + "%")  # 同 MARKERS，给列表筛选（库的默认排序规则不分大小写）
NAMES_MAX = 500   # 一个站点同时停在结果不明、等人核对的票（要人逐张核对，实际是个位数）
SHOWN = 1


def by_reason(status, reason) -> bool:
    text = str(reason or "").upper()
    return str(status or "") in OPEN and any(marker in text for marker in MARKERS)


def records(names) -> frozenset:
    """这几条推送记录里标了结果不明的（一次查完）。"""
    names = [name for name in names or () if name]
    if not names:
        return frozenset()
    return frozenset(frappe.get_all(RECORD, filters={"name": ["in", names], "result_unknown": 1}, pluck="name",
                                    limit_page_length=len(names)))


def is_unknown(row, unknown_records=frozenset()) -> bool:
    status = str(row.get("last_push_status") or "")
    return (status == MANUAL and row.get("last_push_record") in unknown_records) or by_reason(status, row.get("last_push_reason"))


def mark(rows):
    """票的行（录入列表的一页、详情的一张，`history.push_state.marked` 的钩子）：结果不明的多带只读的 ``push_unknown``。"""
    open_rows = [row for row in rows or () if str(row.get("last_push_status") or "") in OPEN]
    manual = [row.get("last_push_record") for row in open_rows if row.get("last_push_status") == MANUAL]
    unknown = records(manual) if manual else frozenset()
    for row in open_rows:
        if is_unknown(row, unknown):
            row["push_unknown"] = SHOWN
    return rows


def histories() -> list:
    """推送记录标了结果不明、还停在转人工的票名（列表「结果不明」筛选用；有界，见 :data:`NAMES_MAX`）。"""
    return frappe.get_all(RECORD, filters={"result_unknown": 1, "status": MANUAL, "history": ["is", "set"]}, pluck="history",
                          order_by="updated_at desc", limit_page_length=NAMES_MAX)


def list_filters(state) -> tuple[list, list]:
    """录入列表「推送状态」筛选 → (且条件, 或条件)。``unknown``：转人工里结果不明的；``failed``：失败里去掉结果不明的。
    其它状态不在这里（回两个空表）。"""
    if state == "unknown":
        # 停着的（转人工 / 旧票没有状态；`not in` 框架自动套 IFNULL，空状态也算）里：推送记录标了的，或原因是结果不明的码
        names = histories()
        return ([["last_push_status", "not in", list(SETTLED)]],
                [["name", "in", names or [""]], *[["last_push_reason", "like", like] for like in LIKE]])
    if state == "failed":
        names = histories()
        return ([*([["name", "not in", names]] if names else []),
                 *[["last_push_reason", "not like", like] for like in LIKE]], [])
    return [], []


__all__ = ["MARKERS", "NAMES_MAX", "by_reason", "histories", "is_unknown", "list_filters", "mark", "records"]
