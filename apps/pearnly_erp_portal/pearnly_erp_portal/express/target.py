"""ERP 的单写进哪一份 Express 账套（ADR-025）：按本公司税号 + 分店 + 单据日期，在 CC 桌面版报来的账套里选。

账套名单是共用机器通道记的（`pearnly_core.cc_desktop.books`：各台电脑心跳报的整份名单，带公司、税号、分店、会计期间、能不能写）。
按日期挑与 Cowork 推送、定时任务同一条规则（`express_rules.targets.account_match.for_day`）：本年期间覆盖这一天的；没有就用期间
含这一天的旧账套（次年段，新年度账套还没建）。

- 一份都没有：停下（``express.account_set_missing``），每分钟再看（电脑报了新年度账套就接着写）；
- 同期两份：这家公司这个会计年度选过就用选的，没选过停下等人在 ERP 里选一次（``account_set``，记住复用）；
- 选出的那份没有一台电脑能写：停下（``express.no_writer``），每分钟再看。

CC 对话 / 代录任务里（A 线 11-12）：调用方经 `conversation.wanted` 传 ``wanted``（本对话账套的账套键）：就用这家名单里的那一本，
不按单据日期另判、不另选别的账套；不在这家名单里 → 没有账套；空串（本对话账套未定）→ ``ledger_needed``。单据日期落在这本的整个
会计期间（本年起 ~ 次年止）之外 → 期间不符（``express.account_period_mismatch``，不写）：Express 原生录单同样拒（Message#70），
判据同 Cowork 推送（`account_match.conflict`）；账套没报期间的不拦。
"""
import frappe
from frappe import _
from pearnly_core.cc_desktop import books
from pearnly_core.express_rules.targets import account_match

from pearnly_erp_portal.express import choices, contracts, conversation


def year_subject(day, book) -> str:
    """「这个会计年度」的记法：账套本年期间的起始日（同一份账套整年同一个键）；没报期间按单据日期的年份。"""
    start = str(book.get("period_start") or "")[:10]
    return f"year:{start or str(day)[:4]}"


def option_name(book) -> str:
    """同期几份账套让人选时每一份怎么写：账套名 + 所在位置（同名的副本与真账只有位置不同，如 C 盘副本与 S 盘的年度文件夹）。"""
    return " · ".join(part for part in (book.get("label"), book.get("path")) if part)


def candidates(company_row, day) -> list:
    tax = contracts.tax13(company_row.get("tax_id"))
    if not tax:
        return []
    return books.for_day(books.for_company(tax, company_row.get("branch")), day)


def named(company_row, wanted) -> dict | None:
    """这家公司（税号 + 分店）名单里的那一本（``wanted`` 是账套键）；不在名单里回 None。"""
    tax = contracts.tax13(company_row.get("tax_id"))
    every = books.for_company(tax, company_row.get("branch")) if tax and wanted else []
    return next((item for item in every if item["key"] == wanted), None)


def pick(company_row, day, wanted=None) -> dict:
    """``{book, reason, options}``：选出的账套（`books.for_company` 那一项），或停下的原因与可选的几份。``wanted``：本对话账套
    （见文件头；None = 按日期选）。"""
    if not contracts.tax13(company_row.get("tax_id")):
        return {"book": None, "reason": contracts.NO_TAX, "options": []}
    if wanted is not None:
        if not wanted:
            return {"book": None, "reason": conversation.LEDGER_NEEDED, "options": []}
        book = named(company_row, wanted)
        if book is None:
            return {"book": None, "reason": contracts.NO_BOOK, "options": []}
        if account_match.conflict("express", {"attributes": book}, fields={"date": day}):
            return {"book": book, "reason": contracts.PERIOD_OUTSIDE, "options": []}
        return {"book": book, "reason": "" if book["writers"] else contracts.NO_WRITER, "options": []}
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
                    "options": [{"code": item["key"], "name": option_name(item)} for item in found]}
    if not book["writers"]:
        return {"book": book, "reason": contracts.NO_WRITER, "options": []}
    return {"book": book, "reason": "", "options": []}


def masters_choice(company_row, day) -> dict | None:
    """主档同步要人选一次的那种情况（11-15：同期几份账套没选过时不再默认读第一份——可能读到 C 盘副本）：
    ``{subject, options}``，与写单同一份记忆（`choices.ACCOUNT_SET`，按公司 × 会计年度）；不用选回 None。"""
    found = candidates(company_row, day)
    if len(found) < 2:
        return None
    subject = year_subject(day, found[0])
    if choices.chosen(company_row["name"], choices.ACCOUNT_SET, subject) in {item["key"] for item in found}:
        return None
    return {"subject": subject, "options": [{"code": item["key"], "name": option_name(item)} for item in found]}


def current(company_row, day, wanted=None) -> dict:
    """今天该写的那份（同期两份时按选过的，没选过取写得进的第一份；主档同步先看 :func:`masters_choice`，没选过就停下不读）。还没有今年的账套
    （只有往年的）就读会计期间最近的那份：主档按公司认，不因为没开今年的账套就读不到（10-24 测试机：按账套自动建的公司里
    有只剩往年账套的）。``wanted`` 给了（本对话账套）就是名单里那一本（不在名单里、未定回 None）。"""
    if wanted is not None:
        return named(company_row, wanted)
    found = candidates(company_row, day)
    if not found:
        tax = contracts.tax13(company_row.get("tax_id"))
        every = books.for_company(tax, company_row.get("branch")) if tax else []
        latest = max(every, key=lambda item: str(item.get("period_end") or ""), default=None)
        return latest
    if len(found) > 1:
        wanted = choices.chosen(company_row["name"], choices.ACCOUNT_SET, year_subject(day, found[0]))
        chosen = next((item for item in found if item["key"] == wanted), None)
        if chosen:
            return chosen
        found = [item for item in found if item["writable"]] or found
    return found[0]


NO_BOOK_LISTED = 5  # 说出几本已连接账套的期间（再多的不列）


def no_book_text(company_row, day, every=None) -> str:
    """「没有账套」给人看的一句（收款与 ERP 单据共用，用户 11-14 定）：已连接的几本都不覆盖这天 → 说出日期与各本的期间；
    一本都没连 → 这家公司没有已连接的账套。期间写到次年段末（选账套也认次年段，`for_day`）。"""
    if every is None:
        every = books_of(company_row)
    if not every:
        return _("This company has no connected Express account set")
    every = sorted(every, key=lambda item: str(item.get("period_start") or ""), reverse=True)[:NO_BOOK_LISTED]
    joiner = "、" if str(getattr(frappe.local, "lang", "") or "")[:2] in ("zh", "ja") else ", "
    listed = joiner.join(
        " ".join(part for part in (item.get("label") or item.get("key") or "", "–".join(
            str(value)[:10] for value in (item.get("period_start"), item.get("next_end") or item.get("period_end"))
            if value)) if part) for item in every)
    return _("{0} is outside the periods of the connected account sets: {1}").format(str(day or "")[:10], listed)


def books_of(company_row) -> list:
    """这家公司（税号 + 分店）已连接的全部账套，不按日期挑。"""
    tax = contracts.tax13((company_row or {}).get("tax_id"))
    return books.for_company(tax, (company_row or {}).get("branch")) if tax else []


def company_no_book_text(company, day) -> str:
    """同 :func:`no_book_text`，按 ERP 公司名取税号与分店。"""
    row = frappe.db.get_value("ERP Company", company, ["tax_id", "branch"], as_dict=True) if company else None
    return no_book_text(row or {}, day)


def no_book_texts(companies) -> dict:
    """列表用（待处理）：几家公司各查一次账套名单，回 ``{公司: 按日期出那句的函数}``，不逐行查。"""
    if not companies:
        return {}
    rows = frappe.get_all("ERP Company", filters={"name": ["in", sorted(companies)]}, fields=["name", "tax_id", "branch"],
                          limit_page_length=len(companies))
    listed = {row.name: (row, books_of(row)) for row in rows}  # loop-query: 待处理里出现的几家公司，各一次
    return {name: (lambda day, found=found: no_book_text(found[0], day, every=found[1])) for name, found in listed.items()}


__all__ = ["books_of", "candidates", "company_no_book_text", "current", "named", "no_book_text", "no_book_texts",
           "option_name", "pick", "year_subject"]
