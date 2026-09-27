"""推送前核对「用户选的账套」与票面有没有确定对不上（用户 2026-09-27），纯函数，不做 IO。

账套由用户在上传前选（网页下拉 / LINE 打字找），按用户的选择推；系统不自动挑。这里只拦**确定错了**的：
- 票上本方税号（进项看买方、销项看卖方）是有效的 13 位税号（校验位对），账套也报了税号，两者不同
  —— 这张票不是这家公司的（旧项目 07-23：票写进了别家公司的账套）；
- 单据日期落在这份账套整个会计期间（本年起 ~ 次年止）之外 —— 年度选错了。
读不出、账套没报的都不拦（判断不了就按用户的选择）。分店号没有校验位、识别常读错一位，不拿来拦。
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Mapping

from pearnly_core.tax_documents.amounts import valid_tax_id

TAX_MISMATCH = "account_tax_mismatch"
PERIOD_MISMATCH = "account_period_mismatch"


def _digits(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _day(value: object) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def conflict(adapter: object, account: Mapping[str, Any] | None, *, direction: object,
             fields: Mapping[str, Any] | None) -> str:
    """→ 对不上的原因码（`TAX_MISMATCH` / `PERIOD_MISMATCH`），没有确定的冲突返回 ''。只核 Express。"""
    if str(adapter or "").strip().lower() != "express" or not isinstance(account, Mapping):
        return ""
    attributes = account.get("attributes") if isinstance(account.get("attributes"), Mapping) else {}
    fields = fields if isinstance(fields, Mapping) else {}
    own = "buyer" if str(direction or "") == "purchase" else "seller"
    tax = _digits(fields.get(f"{own}_tax"))
    book_tax = _digits(attributes.get("tax_id"))
    if len(tax) == 13 and valid_tax_id(tax) and len(book_tax) == 13 and tax != book_tax:
        return TAX_MISMATCH
    day = _day(fields.get("date"))
    start = _day(attributes.get("period_start"))
    end = _day(attributes.get("next_end")) or _day(attributes.get("period_end"))
    if day and start and end and not start <= day <= end:
        return PERIOD_MISMATCH
    return ""


__all__ = ["PERIOD_MISMATCH", "TAX_MISMATCH", "conflict"]
