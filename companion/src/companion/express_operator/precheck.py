# -*- coding: utf-8 -*-
"""开录前一次查完要人决定的事（评估 §10 准备第 4 步「预检」的电脑这一侧；识别存疑、记账口径在服务端准备时已列出）。

读这本账套（只读）查：
- 单种有没有配方（unsupported）；
- 对方码在不在主档（party_missing：第 3 期前预检里让人选「直写建档 / 选已有 / 跳过」，默认直写建档）；
- 每行品码在不在商品主档（item_missing）；
- 主档税务设置与票面不符（tax_setting_mismatch）：票面有税、对方主档税率是 0；
- 单据日期在不在账套期间里、那一期关账没有（period_outside / period_locked）；
- 疑似重复（duplicate_ref，只提示不拦：推送不查重不拦）：同对方、同对方单号的单已在账套里，或同一批里出现两次。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Callable, Dict, Iterable, List, Optional

from .book import Book
from .draft import DOC_TYPES, Draft
from .recipe import Recipe

MASTERS = {"purchase": ("APMAS", "SUPCOD"), "sales": ("ARMAS", "CUSCOD")}


def _rate(value) -> Optional[Decimal]:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def check(drafts: Iterable[Draft], book: Book, recipe: Recipe, *,
          period: Callable[[object], Optional[str]] = lambda _day: None) -> Dict[str, List[Dict]]:
    """回 ``{doc_id: [{"reason", ...}]}``（只列有问题的单）。``period(day)`` 回 None / period_outside / period_locked。"""
    drafts = list(drafts)
    wanted = {DOC_TYPES[d.doc_type]["side"] for d in drafts if d.doc_type in DOC_TYPES}
    masters: Dict[str, Dict[str, Dict]] = {}
    for side in wanted:
        table, column = MASTERS[side]
        rows = book.rows_since({}, [table]).get(table, [])
        masters[side] = {str(r.get(column) or "").strip(): r for r in rows if not r.get("_del")}
    items = book.codes("STMAS", "STKCOD") if drafts else set()
    heads = book.rows_since({}, ["APTRN", "ARTRN"]) if drafts else {}
    existing = set()
    for name in ("APTRN", "ARTRN"):
        party, ref = ("SUPCOD", "REFNUM") if name == "APTRN" else ("CUSCOD", "YOUREF")
        for r in heads.get(name, []):
            if not r.get("_del") and str(r.get(ref) or "").strip():
                existing.add((name, str(r.get(party) or "").strip(), str(r.get(ref) or "").strip()))
    seen_in_batch = set()
    out: Dict[str, List[Dict]] = {}
    for d in drafts:
        issues: List[Dict] = []
        if not recipe.supported(d.doc_type):
            issues.append({"reason": "unsupported", "doc_type": d.doc_type, "phase": recipe.phase(d.doc_type)})
            out[d.doc_id] = issues
            continue
        spec = DOC_TYPES[d.doc_type]
        master = masters.get(spec["side"], {}).get(d.party_code)
        if not d.party_code or master is None:
            issues.append({"reason": "party_missing", "code": d.party_code})
        elif d.totals()["vat"] != 0 and "VATRAT" in master and _rate(master.get("VATRAT")) == 0:
            issues.append({"reason": "tax_setting_mismatch", "code": d.party_code})
        for index, line in enumerate(d.lines, start=1):
            if not line.item_code or line.item_code not in items:
                issues.append({"reason": "item_missing", "line": index, "code": line.item_code})
        problem = period(d.day)
        if problem:
            issues.append({"reason": problem, "date": d.date})
        if d.ref_no:
            key = (spec["head"], d.party_code, d.ref_no)
            if key in existing or key in seen_in_batch:
                issues.append({"reason": "duplicate_ref", "ref": d.ref_no})
            seen_in_batch.add(key)
        if issues:
            out[d.doc_id] = issues
    return out


def account_period(account_dir) -> Callable[[object], Optional[str]]:
    """真账套的期间判断（``fiscal_period``，与直写同一份 ISPRD 读法）。"""
    from .. import fiscal_period

    calendar = fiscal_period.load(account_dir)

    def judge(day) -> Optional[str]:
        if calendar is None:
            return None  # 读不到期间表：判不出，不拦（录的时候 Express 自己会弹 70）
        if calendar.locate(day) is None:
            return "period_outside"
        return "period_locked" if fiscal_period.locked(account_dir, day) else None

    return judge
