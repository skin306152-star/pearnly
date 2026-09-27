"""识别后自动挑推送账套（用户 2026-09-27），纯函数，不做 IO。

LINE 里只选目标 ERP、进项 / 销项和入账方式，账套等识别出来再定：
- Express：本方税号（进项看买方、销项看卖方）对上账套的税号（ISINFO）；本方分店号对得上账套的
  分店号时再收窄；再按单据日期落在哪份账套的会计期间里（ISPRD，本年优先、其次次年）；
  账套没报会计期间的，退而看年度文件夹名的年份（69EXP ↔ 佛历 2569）。
- MR.ERP：目录里没有税号，只有一份就是它；否则按账套名里的年份对单据年份。
剩下一份就选它；还有几份（审计版 / 数据版、分厂）先看这条连接最近推过的，仍定不下就留空，
由用户在编辑页选。选不出来绝不猜。
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Iterable, Mapping

from .catalog_groups import year_rank

_BUDDHIST_OFFSET = 543


def _digits(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _branch(value: object) -> str:
    """分店号统一成 5 位；「สำนักงานใหญ่ / 总公司 / head office」算 00000。空 → ''。"""
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.casefold()
    if any(word in lowered for word in ("สำนักงานใหญ่", "head office", "headoffice", "总公司", "總公司")):
        return "00000"
    digits = _digits(text)
    return digits[-5:].zfill(5) if digits else ""


def _day(value: object) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _attributes(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get("attributes")
    return value if isinstance(value, Mapping) else {}


def _usable(adapter: str, rows: Iterable[Any]) -> list[Mapping[str, Any]]:
    result = []
    for row in rows or []:
        if not isinstance(row, Mapping) or row.get("active") is False or not str(row.get("source_id") or "").strip():
            continue
        if adapter == "express" and _attributes(row).get("writable") is False:
            continue
        result.append(row)
    return result


def _within(day: dt.date, start: object, end: object) -> bool:
    first, last = _day(start), _day(end)
    return bool(first and last and first <= day <= last)


def _by_date(rows: list[Mapping[str, Any]], day: dt.date | None) -> list[Mapping[str, Any]]:
    """单据日期落在哪几份账套里：本年期间 → 次年期间 → （没报期间的）年度文件夹名年份。"""
    if day is None:
        return rows
    current = [r for r in rows if _within(day, _attributes(r).get("period_start"), _attributes(r).get("period_end"))]
    if current:
        return current
    following = [r for r in rows if _within(day, _attributes(r).get("period_end"), _attributes(r).get("next_end"))]
    if following:
        return following
    short_year = (day.year + _BUDDHIST_OFFSET) % 100
    return [r for r in rows if not _attributes(r).get("period_start")
            and year_rank(_attributes(r).get("root_label")) % 100 == short_year]


def _by_label_year(rows: list[Mapping[str, Any]], day: dt.date | None) -> list[Mapping[str, Any]]:
    """MR.ERP 的账套名就是年度（佛历或公历都有）：对上单据年份的那几份。"""
    if day is None:
        return rows
    years = {day.year, day.year + _BUDDHIST_OFFSET}
    return [r for r in rows if year_rank(r.get("label")) in years]


def _decide(candidates: list[Mapping[str, Any]], recent: Iterable[object], reason: str) -> dict[str, Any]:
    keys = [str(row.get("source_id")) for row in candidates]
    if len(keys) == 1:
        return {"account_set_key": keys[0], "reason": reason, "candidates": 1}
    for key in recent or []:
        if str(key or "") in keys:
            return {"account_set_key": str(key), "reason": "recent", "candidates": len(keys)}
    return {"account_set_key": "", "reason": "ambiguous" if keys else "no_match", "candidates": len(keys)}


def suggest(adapter: object, account_sets: Iterable[Any] | None, *, direction: object,
            fields: Mapping[str, Any] | None, recent: Iterable[object] = ()) -> dict[str, Any]:
    """→ `{account_set_key, reason, candidates}`；`account_set_key` 为空表示要用户自己选。

    `recent`：这条连接最近推过的账套键（新的在前），只用来在同一公司的几份账套里挑。
    """
    kind = str(adapter or "").strip().lower()
    fields = fields if isinstance(fields, Mapping) else {}
    rows = _usable(kind, account_sets)
    day = _day(fields.get("date"))
    if kind == "mrerp":
        if len(rows) == 1:
            return _decide(rows, (), "only_one")
        return _decide(_by_label_year(rows, day), recent, "year")
    if kind != "express":
        return {"account_set_key": "", "reason": "unsupported", "candidates": 0}
    own = "buyer" if str(direction or "") == "purchase" else "seller"
    tax = _digits(fields.get(f"{own}_tax"))
    if len(tax) != 13:
        return {"account_set_key": "", "reason": "no_tax", "candidates": 0}
    matched = [row for row in rows if _digits(_attributes(row).get("tax_id")) == tax]
    if not matched:
        return {"account_set_key": "", "reason": "no_tax_match", "candidates": 0}
    # 同一税号下总公司和分店各记一本账时，按票上本方的分店号收窄；账套没报分店号就不收窄。
    branch = _branch(fields.get(f"{own}_branch"))
    if branch:
        same_branch = [row for row in matched if _branch(_attributes(row).get("branch")) == branch]
        if same_branch:
            matched = same_branch
    return _decide(_by_date(matched, day), recent, "tax_date")


__all__ = ["suggest"]
