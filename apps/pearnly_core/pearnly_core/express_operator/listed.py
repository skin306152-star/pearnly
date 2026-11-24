"""代录任务准备中列出过的单（快照，多产品共用；主会话 11-24 口径）：列出阶段不锁；列出过的单之后被直写写进了账本，卡上与给模型的
清单里如实显示「已入账 · 单号」、开始时跳过，不从清单里凭空消失（L3 首跑：产品 ``sources`` 现查只给还没交进写账的单，ERP 每分钟那一轮
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
    doc = tasks._own(task, for_update=True)
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


def entered(company: str, snapshot: dict, current, period: str = "") -> list:
    """快照里有、现查里没有、产品说已入账的单：``[{doc_id, group, ref, party, docnum}]``，按快照顺序。``current`` 是这次现查到的
    单号；只是一页（给了 ``period``）时，先按全月清单核一遍，只对真不在清单里的读现状。"""
    present = {str(doc_id) for doc_id in current or ()}
    gone = [doc_id for doc_id in snapshot if doc_id not in present]
    if gone and period:
        present |= _all_ids(company, period)
        gone = [doc_id for doc_id in gone if doc_id not in present]
    if not gone:
        return []
    found = {str(row.get("doc_id")): row for row in products.status(company, gone) if isinstance(row, dict)}
    out = []
    for doc_id in gone:
        row = found.get(doc_id) or {}
        if row.get("state") != "saved":
            continue
        out.append({"doc_id": doc_id, "group": snapshot.get(doc_id) or "", "ref": str(row.get("ref") or ""),
                    "party": str(row.get("party") or ""), "docnum": str(row.get("docnum") or "")})
    return out
