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
  推送去向那份账套这一天有多份同期的单只改记（`companies.pending.current_book_rechoice`，同自动推判档的候选）。两处都同时
  记下这一组候选当时的最新录入（:func:`seen_of`，见下「重问」）。

公司身份同 `identity.company_identity` 的 ``key``（建了主档的用主档上的身份）；分店取候选账套报的分店号，本年 / 次年按
`targets.account_match.fiscal_part`。候选跨了分店（报了两个以上不同的分店号，如定时任务没记分店、两个分店各有自己的现用账套）
不问不记、照旧停下（第五轮后审查）：不然没印分店的单会被推进另一个分店的账套。同一个分店（含没报分店号的旧年度）两边算出的
候选一样（定时任务按任务记的分店、自动推按推送去向那一行的分店，`identity.for_branch`），键也一样。只读。

主档页「记住的选择」只列还起作用的键（:func:`live_keys`）：新建 / 删了账套、组合变了的旧键不会再被问到，不列（也不清）；
同一分店、同是本年（或次年）在不同年度各有一组时各列一行，行上写选中那份的年度（`choices.policy_rows`）。

重问（用户 2026-10-18 同意）：记住回答时同时记下当时各候选的「最新录入」（:func:`seen_of`，存在同一组的 ``current_book_seen|…``，
`contracts.document_policy.book_marks`）；之后**未被选中**的某份此刻的最新录入晚于记下的，:func:`remembered` 当作没选过、原样回
（照旧停下再问，仍不自动挑最近有录入的那份）。选中的那份自己有新录入不算；旧存储没有这一项的回答照旧用、不重问。

「最新录入」取两样：池登记上这份账套最近一次经 CC 桌面版写成的时刻（`connections.service.account_sets_written`：回执成功时由
服务端记，每份登记了的物理账套都有、随每次写入更新），和 CC 桌面版 1.9.1 起在账套目录每行另报的录入标记（第 3 期 E3，
`connections.service.account_sets_entered`：Express ``GLJNL.DBF`` 表头的最后更新日期与记录数，存在池登记上、不进目录），会计不经
Pearnly 直接在 Express 里录进另一份的也看得到。各按连接一次主键查询，只在这一组有记住的回答、且回答时记了标记时才查。比法见
`contracts.document_policy.current_book_newer`（录入标记两边都有才比：记录数变了或日期更晚）。

已知限制与偏差：录入标记跟着 CC 桌面版的整套重扫走（每天一次兜底、登记表变了、网页「重新读取」、重启），不另加常驻读取，最晚约
一天后才看到；表头日期只到天，同一天里的新录入靠记录数看出来；回答前、上次重扫后录进另一份的单，下一次重扫后也算回答之后的新录入，
会多问一次。改选时旧那份还在排队、回答之后才写成的单，也算「回答之后的新录入」，会多问一次。
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import frappe

from pearnly_cowork.companies import identity
from pearnly_cowork.contracts import document_policy as policy_rules
from pearnly_core.express_rules.targets import account_match
from pearnly_core.express_rules.targets.identity import normalize_express_account_key

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


def marks(books) -> dict:
    """各候选此刻的「最新录入」：``{规范账套键: 池登记上最近一次经 CC 桌面版写成的时刻，带上 CC 桌面版报的录入标记
    （`book_mark` 写法），都没有为空串}``。每条连接两次主键查询。"""
    from pearnly_cowork.connections import service as connections  # 只在这一组记了标记时才用：判档的轻路径不加载连接服务

    by_connection = {}
    for item in books or ():
        by_connection.setdefault(item.get("connection") or "", []).append(_key(item))
    out = {}
    for connection, keys in by_connection.items():
        written = connections.account_sets_written(connection, keys) if connection else {}
        written = {normalize_express_account_key(key) or key: value for key, value in (written or {}).items()}
        entered = connections.account_sets_entered(connection, keys) if connection else {}
        entered = {normalize_express_account_key(key) or key: value for key, value in (entered or {}).items()}
        out.update({key: policy_rules.book_mark(written.get(key), entered.get(key) or "") for key in keys})
    return out


def seen_of(books) -> str:
    """记住回答时一起存的各候选最新录入（``current_book_seen|…`` 的值，`contracts.document_policy.book_marks`）；记不下回空串。"""
    return policy_rules.book_marks(marks(books))


def remembered(workspace, company_identity, day, books) -> list:
    """按日期选出多份同期时，这家公司对这一组选过的那份 → 只留它；没选过、选的已不在候选里、不到两份原样回。回答时记了
    各候选的最新录入、而此刻**未被选中**的某份有了更晚的录入：当作没选过，原样回（调用方照旧停下再问，用户 2026-10-18）。"""
    books = list(books or ())
    key = key_of(company_identity, day, books)
    if not key:
        return books
    stored, candidates = policy(workspace), [_key(item) for item in books]
    chosen = policy_rules.current_book(stored, key, candidates)
    seen = policy_rules.current_book_seen(stored, key, candidates) if chosen else None
    if seen is not None and policy_rules.current_book_newer(seen, marks(books), chosen):
        chosen = None
    picked = [item for item in books if _key(item) == chosen] if chosen else []
    return picked[:1] or books


def pick(workspace, company_identity, day, books) -> list:
    """按日期选出的这一组（`identity.for_day`）该推往哪份：这家公司对这一组选过现用的那份就是它（写不进也是它，调用方按
    「没有电脑能访问」停下，不改推没选的那份）；没选过时写不进的不算候选（`identity.writable_first`，10-20 测试机：目录里一份
    写不进的 T8 让两张票停在「多份账套」）。定时任务（`tasks.books_of`）与自动推（`automation.day_books`）共用。"""
    found = remembered(workspace, company_identity, day, books)
    return found if len(found) < 2 else identity.writable_first(found)


__all__ = ["key_of", "live_keys", "marks", "pick", "policy", "remembered", "seen_of"]
