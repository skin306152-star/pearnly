"""定时任务跟随公司当年账套（T1，2026-10-15）：任务记下公司主档与分店，到点按单据日期取这家公司覆盖那天的账套。

folder_intake 不导入本域（companies 依赖 folder_intake，反过来会成环），经 hooks 问这里：
- `company_of`（``cowork_task_company``）：保存任务时，所选 Express 账套属于唯一一家主档 → 记下主档编号与这份账套的分店；
  对不上或多于一家回 None（任务照旧用固定账套）。补丁 `v0_0_23_folder_task_company` 给旧任务回填也用它。
- `books_of`（``cowork_task_books``）：这家公司在这条连接上的账套，给了日期只留覆盖那天的、给了分店按分店筛
  （纯规则 `identity.for_day` / `identity.for_branch`）；覆盖那天的有多份同期时，这家公司对这一组选过现用的那份就只留它
  （`current_book.remembered`，用户 2026-10-17「按公司问一次」）。主档已删回 None（任务照旧用固定账套）。

都按当前用户能用的连接上的目录现算（同 `service.books`），只读；选哪份、要不要停下由调用方定。后台推进在同一个任务里
换着负责人推（`folder_intake.pump._as` 不清本域的请求内缓存），所以每次先清掉缓存，不拿上一个人能用的连接来算。
"""
from __future__ import annotations

import frappe

from pearnly_cowork.companies import current_book, identity, service
from pearnly_cowork.companies.access import COMPANY
from pearnly_cowork.targets.identity import normalize_express_account_key

FIELDS = ["name", "identity", "adapter", "seed_connection", "name_key"]


def _catalog_row(connection, key):
    """这条连接（本人能用的 Express 连接）目录里这份账套那一行；没有回 None。"""
    if not any(row["name"] == connection and row["adapter"] == "express" for row in service.usable_connections()):
        return None
    return next((row for row in service.facts(connection).get("account_sets") or []
                 if isinstance(row, dict) and row.get("active", True) is not False
                 and normalize_express_account_key(row.get("source_id")) == key), None)


def company_of(connection, account_set_key) -> dict | None:
    """这份 Express 账套属于哪家主档：唯一一家时 ``{"company": 编号, "branch": 这份账套的分店号（没有税号是空串）}``，
    对不上或多于一家回 None。判据同 `identity.belongs`（身份相同；或没有税号、与按名称建的主档同连接同名称键）。"""
    connection = str(connection or "").strip()
    key = normalize_express_account_key(account_set_key)
    if not connection or not key:
        return None
    service.forget_cache()
    row = _catalog_row(connection, key)
    found = identity.company_identity("express", connection, row) if row else None
    if not found:
        return None
    rows = frappe.get_all(COMPANY, filters={"identity": found["key"]}, fields=FIELDS, limit_page_length=0)
    if not found["tax_id"] and found["name_key"]:
        rows += frappe.get_all(COMPANY, filters={"adapter": "express", "seed_connection": connection,
                                                 "name_key": found["name_key"]}, fields=FIELDS, limit_page_length=0)
    names = {item.name for item in rows if identity.belongs(item, "express", connection, row)}
    if len(names) != 1:
        return None
    return {"company": names.pop(), "branch": identity.book("express", connection, row)["branch"]}


def books_of(company, connection, day=None, branch="", *, remembered=True) -> list | None:
    """这家公司在这条连接上的账套（`identity.book` 的样子，新年度在前）；``day``（`YYYY-MM-DD`）给了只留覆盖那天的，
    ``branch`` 给了按分店筛。覆盖那天的有多份同期时按这家公司选过的现用账套只留那份（``remembered``；列「可换的账套」时
    给 False，列全这一组）。主档不在回 None。"""
    if not service.exists(company):
        return None
    service.forget_cache()
    row = service.get(company)
    sets = [item for item in service.books(row, None) if item["connection"] == connection]
    sets = identity.for_branch(sets, branch)
    if day is None:
        return sets
    found = identity.for_day(sets, day)
    if not remembered or len(found) < 2:
        return found
    workspace = frappe.db.get_value(COMPANY, company, "workspace")
    return current_book.remembered(workspace, row.get("identity"), day, found)


__all__ = ["books_of", "company_of"]
