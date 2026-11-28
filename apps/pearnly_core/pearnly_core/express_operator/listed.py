"""代录任务准备中列出过的单（快照，多产品共用；主会话 11-24 口径）：列出阶段不锁；列出过的单之后被直写写进了账本，卡上与给模型的
清单里如实显示「成功 · 单号」、开始时跳过，不从清单里凭空消失（L3 首跑：产品 ``sources`` 现查只给还没交进写账的单，ERP 每分钟那一轮
把排队中的单直写掉后，代录清单时有时无）。

- 快照记在任务上（隐藏字段 ``listed``：``{单号: 分组}``，最多 :data:`LISTED_MAX` 条，先到先记），只在任务还是「准备中」时记；
  开始后的占用照旧由产品的写账件管（行锁 + 写账件 + 代录归属，直写不再碰），这里不加锁、不写单据、不排写账件。
- 现查（``sources``）里还在的照旧显示；快照里有、现查里没有的，按产品 ``status`` 读现状：已入账的作为一行补出来（带单号），
  别的（在队列、没录成、取消、读不到）不补——它们要么在别的页、要么不再是这次代录的单。
- 产品 ``sources`` 不改（只补新单）。
"""
from __future__ import annotations

import json

import frappe

from pearnly_core.express_operator import products, tasks

FIELD = "listed"
LISTED_MAX = 500
ENTERED = "already_entered"  # 写入计划 / 开始跳过的原因（同一张票已写进账本，`assistant.write_plan.SKIP_ENTERED`）
LEFT = "no_longer_waiting"  # 列出过、已不在待录清单里、也不是已入账（没录成 / 已取消 / 在别处排着 / 读不到）：跳过并带现状


def _load(raw) -> dict:
    try:
        found = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return {str(key): str(value or "") for key, value in found.items()} if isinstance(found, dict) else {}


def _rows(rows) -> dict:
    return {str(row["doc_id"]): str(row.get("group") or row.get("kind") or "")
            for row in rows or () if isinstance(row, dict) and row.get("doc_id") and row.get("group") != "skip"}


def remember(task: str, company: str, rows) -> dict:
    """把这次现查到的单记进任务的快照（本人、同一家公司、还在准备中的任务才记；别的情况什么都不记，回空）。回记完后的快照。
    调用方都在会提交的路径上（资料清单接口只收 POST、CC 工具、写入计划、交单），这里不自己提交。"""
    if not task:
        return {}
    name = tasks._own_name(task, for_update=True)
    # 快照在加锁读里取：调用方的事务早有读视图，普通读读到的是旧快照，合并后写回会丢掉另一路刚记进去的单（11-28，B ③）
    doc = frappe.db.get_value(tasks.DOCTYPE, {"name": name}, ["name", "company", "status", FIELD], as_dict=True,
                              for_update=True)
    if doc.company != company or doc.status != "preparing":
        return {}
    snapshot = _load(doc.get(FIELD))
    before = dict(snapshot)
    for doc_id, group in _rows(rows).items():
        if doc_id not in snapshot and len(snapshot) >= LISTED_MAX:
            continue
        snapshot[doc_id] = group or snapshot.get(doc_id, "")
    if snapshot != before:
        frappe.db.set_value(tasks.DOCTYPE, doc.name, FIELD, json.dumps(snapshot, ensure_ascii=False), update_modified=False)
    return snapshot


PAGE = 100  # 全月清单逐页取（产品 sources 的服务端分页，同 `api.PAGE_MAX`）


def _all_ids(company: str, period: str) -> set:
    """这家公司这个月此刻现查到的全部单号（逐页取，最多 LISTED_MAX * 2 张）：只看一页时，快照里在后几页的单会被当成「不在了」、
    逐张去读现状（code-review 11-24）。产品 sources 是成批查询，比逐张读状态便宜。"""
    ids, offset = set(), 0
    while len(ids) <= LISTED_MAX * 2:
        found = products.sources(company, period, offset, PAGE)
        page = [str(row["doc_id"]) for row in found.get("documents") or [] if isinstance(row, dict) and row.get("doc_id")]
        ids.update(page)
        following = found.get("next_offset")
        if not page or following is None or int(following) <= offset:
            break
        offset = int(following)
    return ids


STATES = ("saved", "queued", "not_done", "cancelled")  # 产品 status 的现状；别的（unknown）照中性显示并记日志


def gone(company: str, snapshot: dict, current, period: str = "") -> list:
    """快照里有、现查里没有的单，**一律**按现状给出（主会话 11-24：列出过的单不许凭空消失）：
    ``[{doc_id, group, ref, party, state, docnum}]``，按快照顺序。``state`` 是产品 status 的 saved / queued / not_done / cancelled，
    读不到（unknown、产品没回这张）给 ``unknown`` 并记一条错误日志。``current`` 是这次现查到的单号；只是一页（给了 ``period``）时，
    先按全月清单核一遍，只对真不在清单里的读现状。"""
    present = {str(doc_id) for doc_id in current or ()}
    left = [doc_id for doc_id in snapshot if doc_id not in present]
    if left and period:
        present |= _all_ids(company, period)
        left = [doc_id for doc_id in left if doc_id not in present]
    if not left:
        return []
    found = {str(row.get("doc_id")): row for row in products.status(company, left) if isinstance(row, dict)}
    out, unread = [], []
    for doc_id in left:
        row = found.get(doc_id) or {}
        state = str(row.get("state") or "")
        if state not in STATES:
            state = "unknown"
            unread.append(doc_id)
        out.append({"doc_id": doc_id, "group": snapshot.get(doc_id) or "", "ref": str(row.get("ref") or ""),
                    "party": str(row.get("party") or ""), "state": state,
                    "docnum": str(row.get("docnum") or "") if state == "saved" else "",
                    # 没写成里的「结果不明」（账本里可能有）：页面单独显示「结果不明」，不显示「失败」
                    "result_unknown": state == "not_done" and str(row.get("reason") or "") == tasks.RESULT_UNKNOWN})
    if unread:
        frappe.log_error(title="Operator listed document state unknown",
                         message=f"company={company} docs={unread[:20]}")
    return out


def entered(company: str, snapshot: dict, current, period: str = "") -> list:
    """:func:`gone` 里已入账的那几张（写入计划与交单按「同一张票已写进账本」跳过）。"""
    return [row for row in gone(company, snapshot, current, period) if row["state"] == "saved"]
