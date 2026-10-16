"""推送前核对「用户选的账套」与票面有没有确定对不上（用户 2026-09-27），纯函数，不做 IO。

账套由用户在上传前选（网页下拉 / LINE 打字找），按用户的选择推；系统不自动挑。这里只拦**确定错了**的：
- 票上本方税号（进项看买方、销项看卖方）是有效的 13 位税号（校验位对），账套也报了税号，两者不同
  —— 这张票不是这家公司的（旧项目 07-23：票写进了别家公司的账套）；方向判不出时只在买方是别家有效税号、
  卖方也不是这份账套时拦；
- 单据日期落在这份账套整个会计期间（本年起 ~ 次年止）之外 —— 年度选错了；落在次年段、而同一家公司（同税号同分店，
  没有税号按目录公司名）已有本年期间覆盖这一天的新年度账套 —— 新年度账套已建，该推往它（用户 2026-10-16：新年度账套
  还没建时下一年的单记进旧账套的下一年期间，一建好就推往新账套；按日期选账套的同一规则 :func:`for_day`）。
- 分店（补充 甲.3，用户 2026-10-15）：同一法人在这条连接里有不止一个分店的账套时，进项票票面的买方分店（销项看卖方分店）
  只认这个分店的账套；票面没有本方分店（简易税票、收据）时用 CC 对话指定的分店（``work_branch``）。与所选账套的分店
  不同就拦，不静默改。只有一个分店的公司不按分店拦（分店号没有校验位、识别常读错一位，一套账服务所有分店时更不能拦）。
  「几个分店」只数同一期间的账套：覆盖单据日期的；票上没有日期时与所选账套期间重叠的（2026-10-15 验收 F16：
  一家 39 份账套都是分店 00001、只有 2014 年一份旧账套是 00000，被当成两个分店，当期的总公司票全被拦）。
读不出、账套没报的都不拦（判断不了就按用户的选择）。
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Mapping

from pearnly_core.tax_documents.amounts import valid_tax_id

TAX_MISMATCH = "account_tax_mismatch"
PERIOD_MISMATCH = "account_period_mismatch"
BRANCH_MISMATCH = "account_branch_mismatch"
# 推送拦下时给人看的话（词条原文；推送按当前语言翻、记原因时还原成原文，`history.push_state.source_text`）。
# 月结台账按这几句把记下的原因认回原因码（`companies.exceptions`），所以只在这里写一份。
CONFLICT_MESSAGES = {
    TAX_MISMATCH: "The own tax ID on this document does not match the chosen account set. "
                  "Check the account set and send it again.",
    PERIOD_MISMATCH: "The document date is outside the fiscal period of the chosen account set. "
                     "Check the year and send it again.",
    BRANCH_MISMATCH: "This document belongs to another branch than the chosen account set. "
                     "Choose that branch's account set and send it again.",
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


def _valid(tax: str) -> bool:
    return len(tax) == 13 and valid_tax_id(tax)


def _other_company(direction: str, fields: Mapping[str, Any], book_tax: str) -> bool:
    """票上的本方确定是别家：有方向时本方是买方（进项）/ 卖方（销项）；没有方向时只认买方
    （账套是卖方或买方任一都不冲突；买方是别家、卖方也不是这份账套才拦）。没有方向时不拿卖方当本方
    （2026-10-15：快递公司开的无买方简易税票，方向判不出，原先拿快递公司的税号比账套，报「本方税号不符」）。"""
    if len(book_tax) != 13:
        return False
    seller, buyer = _digits(fields.get("seller_tax")), _digits(fields.get("buyer_tax"))
    if direction in ("purchase", "sales"):
        own = buyer if direction == "purchase" else seller
        return _valid(own) and own != book_tax
    return book_tax not in (seller, buyer) and _valid(buyer)


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


def for_day(items: Any, day: Any, span) -> list:
    """按单据日期选账套（T1 定时任务跟随公司当年账套、自动推的「推往的须是按日期选出的那份」、上面的期间硬闸共用）：
    先找本年期间覆盖这一天的；没有，就用期间里含这一天的旧账套（次年段，新年度账套还没建）。``span(item)`` 回
    ``(period_start, period_end, next_end)``。两份同期照样都回（调用方按「多于一份」停下）。"""
    items = [item for item in items or ()]
    current = [item for item in items if fiscal_part(*span(item), day) == CURRENT]
    return current or [item for item in items if fiscal_part(*span(item), day) == NEXT]


def _row_span(row: Any) -> tuple:
    attributes = _attributes(row)
    return attributes.get("period_start"), attributes.get("period_end"), attributes.get("next_end")


def _same_company(row: Any, attributes: Mapping[str, Any]) -> bool:
    """目录这一行与所选账套是同一家公司的同一个分店（同税号同分店；所选账套没有 13 位税号时按目录里的公司名）。"""
    mine, other = _attributes(row), attributes
    tax = _digits(other.get("tax_id"))
    if len(tax) == 13:
        return _digits(mine.get("tax_id")) == tax and _branch5(mine.get("branch")) == _branch5(other.get("branch"))
    name = str(other.get("company") or other.get("company_name") or "").strip().casefold()
    return bool(name) and not _digits(mine.get("tax_id")) and \
        str(mine.get("company") or mine.get("company_name") or "").strip().casefold() == name


def newer_book_covers(attributes: Mapping[str, Any], catalog: Any, day: Any) -> bool:
    """单据日期在所选账套的次年段，而目录里同一家公司已有本年期间覆盖这一天的账套（新年度账套已建）。"""
    if fiscal_part(*_row_span({"attributes": attributes}), day) != NEXT:
        return False
    return any(isinstance(row, Mapping) and _same_company(row, attributes)
               and fiscal_part(*_row_span(row), day) == CURRENT for row in catalog or ())


def company_branches(catalog: Any, tax: str, *, day: dt.date | None = None,
                     period: tuple[dt.date | None, dt.date | None] | None = None) -> set:
    """这条连接的目录里同一税号的账套报的分店号。给了 ``day`` 只数覆盖那天的账套；没给 ``day`` 给了 ``period``（起，止）
    只数期间与它重叠的；都没给数全部。没报期间的账套照数（判不出就不排除）。"""
    found = set()
    start_of, end_of = period or (None, None)
    for row in catalog or ():
        if not isinstance(row, Mapping):
            continue
        attributes = _attributes(row)
        if _digits(attributes.get("tax_id")) != tax or not _digits(attributes.get("branch")):
            continue
        start, end = _period(attributes)
        if start and end:
            if day and not start <= day <= end:
                continue
            if not day and start_of and end_of and (end < start_of or start > end_of):
                continue
        found.add(_branch5(attributes.get("branch")))
    return found


def wrong_branch(attributes: Mapping[str, Any], catalog: Any, direction: str, fields: Mapping[str, Any],
                 work_branch: object = "") -> bool:
    """所选账套的分店与这张票该进的分店不同（见文件头）。"""
    tax, book_branch = _digits(attributes.get("tax_id")), attributes.get("branch")
    if len(tax) != 13 or not _digits(book_branch) or direction not in ("purchase", "sales"):
        return False
    if len(company_branches(catalog, tax, day=_day(fields.get("date")), period=_period(attributes))) < 2:
        return False
    own = "buyer" if direction == "purchase" else "seller"
    wanted = printed_branch(fields.get(f"{own}_branch"))
    if not wanted:
        # 对话指定的分店可以传一个取值函数：只有票面没印分店、这家又有几个分店时才去取（省一次钩子调用）
        branch = work_branch() if callable(work_branch) else work_branch
        wanted = _branch5(branch) if _digits(branch) else ""
    return bool(wanted) and wanted != _branch5(book_branch)


def conflict(adapter: object, account: Mapping[str, Any] | None, *, direction: object,
             fields: Mapping[str, Any] | None, catalog: Any = None, work_branch: object = "") -> str:
    """→ 对不上的原因码（`TAX_MISMATCH` / `BRANCH_MISMATCH` / `PERIOD_MISMATCH`），没有确定的冲突返回 ''。只核 Express。
    ``catalog``：这条连接的账套目录行（判同一法人有几个分店）；``work_branch``：CC 对话指定的分店，或取它的函数
    （只在用得着时才调用）。"""
    if str(adapter or "").strip().lower() != "express" or not isinstance(account, Mapping):
        return ""
    attributes = account.get("attributes") if isinstance(account.get("attributes"), Mapping) else {}
    fields = fields if isinstance(fields, Mapping) else {}
    if _other_company(str(direction or ""), fields, _digits(attributes.get("tax_id"))):
        return TAX_MISMATCH
    if wrong_branch(attributes, catalog, str(direction or ""), fields, work_branch):
        return BRANCH_MISMATCH
    day = _day(fields.get("date"))
    start, end = _period(attributes)
    if day and start and end and not start <= day <= end:
        return PERIOD_MISMATCH
    if day and newer_book_covers(attributes, catalog, day):
        return PERIOD_MISMATCH  # 次年段的单：新年度账套已建，推往新账套
    return ""


__all__ = ["BRANCH_MISMATCH", "CONFLICT_MESSAGES", "CURRENT", "NEXT", "PERIOD_MISMATCH", "TAX_MISMATCH", "company_branches",
           "conflict", "fiscal_part", "for_day", "newer_book_covers", "printed_branch", "wrong_branch"]
