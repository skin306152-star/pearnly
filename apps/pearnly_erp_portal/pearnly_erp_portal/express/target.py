"""ERP 的单写进哪一份 Express 账套（ADR-025）：按本公司税号 + 分店 + 单据日期，在 CC 桌面版报来的账套里选。

账套名单是共用机器通道记的（`pearnly_core.cc_desktop.books`：各台电脑心跳报的整份名单，带公司、税号、分店、会计期间、能不能写）。
按日期挑与 Cowork 推送、定时任务同一条规则（`express_rules.targets.account_match.for_day`）：本年期间覆盖这一天的；没有就用期间
含这一天的旧账套（次年段，新年度账套还没建）。

- 一份都没有：停下（``express.account_set_missing``），每分钟再看（电脑报了新年度账套就接着写）；
- 同期两份：这家公司这个会计年度选过就用选的，没选过停下等人在 ERP 里选一次（``account_set``，记住复用）；
- 选出的那份没有一台电脑能写：停下（``express.no_writer``），每分钟再看。
"""
from pearnly_core.cc_desktop import books

from pearnly_erp_portal.express import choices, contracts


def year_subject(day, book) -> str:
    """「这个会计年度」的记法：账套本年期间的起始日（同一份账套整年同一个键）；没报期间按单据日期的年份。"""
    start = str(book.get("period_start") or "")[:10]
    return f"year:{start or str(day)[:4]}"


def candidates(company_row, day) -> list:
    tax = contracts.tax13(company_row.get("tax_id"))
    if not tax:
        return []
    return books.for_day(books.for_company(tax, company_row.get("branch")), day)


def pick(company_row, day) -> dict:
    """``{book, reason, options}``：选出的账套（`books.for_company` 那一项），或停下的原因与可选的几份。"""
    if not contracts.tax13(company_row.get("tax_id")):
        return {"book": None, "reason": contracts.NO_TAX, "options": []}
    found = candidates(company_row, day)
    if not found:
        return {"book": None, "reason": contracts.NO_BOOK, "options": []}
    book = found[0]
    if len(found) > 1:
        subject = year_subject(day, found[0])
        wanted = choices.chosen(company_row["name"], choices.ACCOUNT_SET, subject)
        book = next((item for item in found if item["key"] == wanted), None)
        if book is None:
            return {"book": None, "reason": contracts.SEVERAL_BOOKS, "subject": subject,
                    "options": [{"code": item["key"], "name": item["label"]} for item in found]}
    if not book["writers"]:
        return {"book": book, "reason": contracts.NO_WRITER, "options": []}
    return {"book": book, "reason": "", "options": []}


def current(company_row, day) -> dict:
    """主档同步读哪一份：今天该写的那份（同期两份时按选过的，没选过取写得进的第一份，读主档不必停下）。"""
    found = candidates(company_row, day)
    if not found:
        return None
    if len(found) > 1:
        wanted = choices.chosen(company_row["name"], choices.ACCOUNT_SET, year_subject(day, found[0]))
        chosen = next((item for item in found if item["key"] == wanted), None)
        if chosen:
            return chosen
        found = [item for item in found if item["writable"]] or found
    return found[0]


__all__ = ["candidates", "current", "pick", "year_subject"]
