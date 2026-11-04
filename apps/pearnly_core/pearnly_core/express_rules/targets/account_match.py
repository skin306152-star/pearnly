"""推送前核对「用户选的账套」与票面有没有确定对不上（用户 2026-09-27），纯函数，不做 IO。

账套由用户在上传前选（网页下拉 / LINE 打字找），按用户的选择推；系统不自动挑。这里只拦 Express 自己也会拒的：单据日期落在
这份账套整个会计期间（本年起 ~ 次年止，即 Express 的 CY + NY 两段，`companion.fiscal_period`）之外 —— Express 录单报
「วันที่ไม่อยู่ในงวด」（Message#. 70）。读不出、账套没报期间的不拦（判断不了就按用户的选择）。

Express 不拦的我们也不拦（用户 2026-11-04，直写、代录、自动推同步）：票面本方税号、本方分店与所选账套不同；单据在旧账套的
次年段、而新年度账套已建（Express 的期间窗含次年段，照收）。本方税号仍用来判进 / 销项（`contracts.push.resolve_account_direction`）、
判单据归哪家公司；按日期选账套（:func:`for_day`，T1 定时任务、自动推的「推往的须是按日期选出的那份」）照旧先选新年度账套。
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Mapping

PERIOD_MISMATCH = "account_period_mismatch"
# 推送拦下时给人看的话（词条原文；推送按当前语言翻、记原因时还原成原文，`history.push_state.source_text`）。
# 月结台账按这句把记下的原因认回原因码（`companies.exceptions`），所以只在这里写一份。
CONFLICT_MESSAGES = {
    PERIOD_MISMATCH: "The document date is outside the fiscal period of the chosen account set. "
                     "Check the year and send it again.",
}
# 票面「สำนักงานใหญ่ / Head office」写的是总公司，没有分店号
HEAD_OFFICE = "00000"
_HEAD_WORDS = ("สำนักงานใหญ่", "สนญ", "head office", "headoffice")


def _digits(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _day(value: object) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _branch5(value: object) -> str:
    number = _digits(value)
    return (number[-5:] if number else "0").zfill(5)


def printed_branch(value: object) -> str:
    """票面上的分店：有数字取 5 位，写「总公司」的是 00000，没写回空串。"""
    text = str(value or "").strip()
    if _digits(text):
        return _branch5(text)
    folded = text.casefold().replace(" ", "")
    return HEAD_OFFICE if any(word.replace(" ", "") in folded for word in _HEAD_WORDS) else ""


def _attributes(row: Any) -> Mapping[str, Any]:
    found = row.get("attributes") if isinstance(row, Mapping) else None
    return found if isinstance(found, Mapping) else {}


def _period(attributes: Mapping[str, Any]) -> tuple[dt.date | None, dt.date | None]:
    return _day(attributes.get("period_start")), _day(attributes.get("next_end")) or _day(attributes.get("period_end"))


CURRENT, NEXT = "current", "next"


def fiscal_part(start: Any, end: Any, next_end: Any, day: Any) -> str:
    """这一天落在账套会计期间的哪一段：本年（``period_start``～``period_end``）回 `CURRENT`；本年之后、次年止（``next_end``）
    之内回 `NEXT`；都不在（或期间没报全、日期认不出）回空串。参数是 `YYYY-MM-DD` 字符串或日期。"""
    start, end, next_end, day = (_day(value) for value in (start, end, next_end, day))
    if not (start and end and day):
        return ""
    if start <= day <= end:
        return CURRENT
    return NEXT if next_end and end < day <= next_end else ""


def _newer_unknown(items: list, chosen: list, span) -> bool:
    """「新年度账套还没建」核不了：同一组里（调用方给的同一家公司同一个分店的账套）有会计期间没报全的账套（CC 桌面版读
    ISPRD 失败、文件缺失时目录里没有起止，可能正是新年度那份），或有本年起始晚于所选旧账套本年止的账套（新年度账套已在）。"""
    ends = [_day(span(item)[1]) for item in chosen]
    for item in items:
        if item in chosen:
            continue
        start, end, _next_end = span(item)
        start, end = _day(start), _day(end)
        if not (start and end):
            return True
        if any(last and start > last for last in ends):
            return True
    return False


def for_day(items: Any, day: Any, span) -> list:
    """按单据日期选账套（T1 定时任务跟随公司当年账套、自动推的「推往的须是按日期选出的那份」、上面的期间硬闸共用）：
    先找本年期间覆盖这一天的；没有，就用期间里含这一天的旧账套（次年段，新年度账套还没建）。``span(item)`` 回
    ``(period_start, period_end, next_end)``。两份同期照样都回（调用方按「多于一份」停下）。退到次年段前要确认新年度账套
    确实还没建（`_newer_unknown`）：同组里有期间没报全的、或本年起始更晚的账套就回空表（调用方停下、交人，审查处理 10-16）。"""
    items = [item for item in items or ()]
    current = [item for item in items if fiscal_part(*span(item), day) == CURRENT]
    if current:
        return current
    following = [item for item in items if fiscal_part(*span(item), day) == NEXT]
    return [] if following and _newer_unknown(items, following, span) else following


def for_month(items: Any, first: Any, last: Any, span) -> list:
    """按月取账套（月结台账、主档页、档位统计按期间取单）：本年期间与这个月（``first``～``last``）有重叠的；没有，就用次年段与
    这个月有重叠的旧账套（同 :func:`for_day` 的次年段规则与「新年度账套确实还没建」的核对）。"""
    items = [item for item in items or ()]
    first, last = _day(first), _day(last)
    if not (first and last):
        return []

    def overlaps(item, part):
        start, end, next_end = (_day(value) for value in span(item))
        if not (start and end):
            return False
        if part == CURRENT:
            return start <= last and end >= first
        return bool(next_end) and end < last and next_end >= first and next_end > end

    current = [item for item in items if overlaps(item, CURRENT)]
    if current:
        return current
    following = [item for item in items if overlaps(item, NEXT)]
    return [] if following and _newer_unknown(items, following, span) else following


def conflict(adapter: object, account: Mapping[str, Any] | None, *, fields: Mapping[str, Any] | None) -> str:
    """→ 对不上的原因码（`PERIOD_MISMATCH`：单据日期不在账套整个会计期间里），没有确定的冲突返回 ''。只核 Express。"""
    if str(adapter or "").strip().lower() != "express" or not isinstance(account, Mapping):
        return ""
    attributes = account.get("attributes") if isinstance(account.get("attributes"), Mapping) else {}
    fields = fields if isinstance(fields, Mapping) else {}
    day = _day(fields.get("date"))
    start, end = _period(attributes)
    if day and start and end and not start <= day <= end:
        return PERIOD_MISMATCH
    return ""


__all__ = ["CONFLICT_MESSAGES", "CURRENT", "NEXT", "PERIOD_MISMATCH", "conflict", "fiscal_part", "for_day",
           "printed_branch"]
