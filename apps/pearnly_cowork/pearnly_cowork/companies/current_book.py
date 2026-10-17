"""真有两份同期账套时按公司问一次哪份是现用的（数字会计员第 3 期，用户 2026-10-17 批）。

按单据日期选账套（`identity.for_day`，T1 定时任务、自动推的「推往的须是按日期选出的那份」共用）选出多份同期时（如 SINCERE
68SINCER / 69SINCER 的 ISPRD 本年都是 2025）：这家公司、这组分店、本年或次年段、这一组候选问一次，记住复用；新建了账套、组合
变了是另一个键，再问。不自动挑最近有录入的那份。键与取值的规则在 `contracts.document_policy`（``current_book|…``），存在工作空间
同一个 ``document_policy`` 字段里（同商品单入账方式 ``goods_posting``，不新建 DocType）。

- 读（这里）：:func:`remembered` 在选出多份时查这一组的选择，选的那份还在候选里才只留它；否则原样（调用方照旧停下）。接在
  `tasks.books_of`（T1）与 `automation.day_books`（自动推判档）两处；`identity.covering`（月结台账、主档页、档位统计按月取单）
  不接：它是列单、不是选推往哪份，两份都取才不漏人推进另一份的单。
- 写：选择只经 `history.document_policy.record` 写（companies 不导入 history）。复核页 / 待处理当场换账套、CC 工具都走
  `assistant.pending_api.choose_account_set`：这里的 :func:`key_of` 按同一组候选现算键，`companies.pending.current_book_choice`
  核过这张单确实停在多份同期、选的在候选里，这张推成了才记（推不成不记，下一张照旧停下再问）。已经记住的要改选：CC 工具对
  推送去向那份账套这一天有多份同期的单只改记（`companies.pending.current_book_rechoice`，同自动推判档的候选）。

公司身份同 `identity.company_identity` 的 ``key``（建了主档的用主档上的身份）；分店取候选账套报的分店号，本年 / 次年按
`targets.account_match.fiscal_part`。候选跨了分店（报了两个以上不同的分店号，如定时任务没记分店、两个分店各有自己的现用账套）
不问不记、照旧停下（第五轮后审查）：不然没印分店的单会被推进另一个分店的账套。同一个分店（含没报分店号的旧年度）两边算出的
候选一样（定时任务按任务记的分店、自动推按推送去向那一行的分店，`identity.for_branch`），键也一样。只读。

主档页「记住的选择」只列还起作用的键（:func:`live_keys`）：新建 / 删了账套、组合变了的旧键不会再被问到，不列（也不清）；
同一分店、同是本年（或次年）在不同年度各有一组时各列一行，行上写选中那份的年度（`choices.policy_rows`）。
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import frappe

from pearnly_cowork.companies import identity
from pearnly_cowork.contracts import document_policy as policy_rules
from pearnly_cowork.targets import account_match
from pearnly_cowork.targets.identity import normalize_express_account_key

WORKSPACE = "Cowork Workspace"
FIELD = "document_policy"
PARTS = {account_match.CURRENT: "cy", account_match.NEXT: "ny"}


def _key(item) -> str:
    return normalize_express_account_key(item.get("key")) or str(item.get("key") or "")


def key_of(company_identity, day, books) -> str:
    """这组同期候选（`identity.book` 的样子，`identity.for_day` 的结果）问「哪份是现用的」的键；不到两份、日期在各份里
    不同段（本年 / 次年）、跨了分店、认不出公司回空串。"""
    books = list(books or ())
    if len(books) < 2:
        return ""
    branches = {str(item.get("branch") or "") for item in books} - {""}
    if len(branches) > 1:
        return ""  # 跨分店：各分店各有现用的账套，不当成「两份同期」记
    parts = {account_match.fiscal_part(item.get("period_start"), item.get("period_end"), item.get("next_end"), day)
             for item in books}
    part = PARTS.get(parts.pop()) if len(parts) == 1 else None
    return policy_rules.current_book_key(company_identity, "".join(branches), part, [_key(item) for item in books])


def _starts(books) -> set:
    """按日期选出的账套只在这些日子变：各份本年起、本年止后一天、次年止后一天（`identity.for_day` 的分段）。"""
    days = set()
    for item in books:
        for value, shift in ((item.get("period_start"), 0), (item.get("period_end"), 1), (item.get("next_end"), 1)):
            try:
                days.add((date.fromisoformat(str(value or "")[:10]) + timedelta(days=shift)).isoformat())
            except ValueError:
                continue
    return days


def live_keys(company_identity, books) -> set:
    """这家公司现在的账套（``books``：`identity.book` 的样子、不截断）里，按日期选账套会选出多份同期的各组候选算出的键：
    各连接分开，分店同定时任务 / 自动推（不指定、各分店，`identity.for_branch`），日子取各段的起点（段内选出的一样）。
    主档页只列这些（组合已不成立的旧键不会再被问到）。"""
    out = set()
    by_connection = {}
    for item in books or ():
        by_connection.setdefault(item.get("connection"), []).append(item)
    for group in by_connection.values():
        days = sorted(_starts(group))
        for number in [""] + sorted({item.get("branch") for item in group if item.get("branch")}):
            mine = identity.for_branch(group, number)
            for day in days:
                key = key_of(company_identity, day, identity.for_day(mine, day))
                if key:
                    out.add(key)
    return out


def policy(workspace) -> dict:
    """这个工作空间存着的口径（认不得的丢掉，同 `history.document_policy.read`）。"""
    raw = frappe.db.get_value(WORKSPACE, workspace, FIELD) if workspace else None
    try:
        return policy_rules.normalize(json.loads(raw or "{}"))
    except (TypeError, ValueError):
        return {}


def remembered(workspace, company_identity, day, books) -> list:
    """按日期选出多份同期时，这家公司对这一组选过的那份 → 只留它；没选过、选的已不在候选里、不到两份原样回。"""
    books = list(books or ())
    key = key_of(company_identity, day, books)
    if not key:
        return books
    chosen = policy_rules.current_book(policy(workspace), key, [_key(item) for item in books])
    picked = [item for item in books if _key(item) == chosen] if chosen else []
    return picked[:1] or books


__all__ = ["key_of", "live_keys", "policy", "remembered"]
