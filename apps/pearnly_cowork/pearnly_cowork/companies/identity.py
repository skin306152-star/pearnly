"""公司身份与分店（ADR-020），纯函数，不连库：账套目录的一行 → 这家公司是谁、这份账套是哪个分店。

主档按法人算一家（用户 2026-10-15「主档公司或者供应商存在分店的可能性」）：
- 有 13 位有效税号（校验位对）：`tax:{税号}`，不带分店。同税号、不同分店号（ISINFO.ORGNUM，`00000` = 总公司）的账套
  都归到同一份主档；分店清单从账套现算（`branches`），主档不存分店表。
- Express 没有有效税号、但有公司名：`name:express:{连接}:{名称键}`（跨连接同名不串成一家）。
- MR.ERP（目录里没有税号）：`mrerp:{连接}:{公司代码}`。
- 都不满足：None（不进候选，也不能建）。

名称键同 `targets.identity.account_set_company`（去首尾空白、小写）：POS 版式、收款科目按它认同一家公司。
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from pearnly_core.tax_documents.amounts import valid_tax_id
from pearnly_cowork.targets import account_match
from pearnly_cowork.targets.catalog_groups import year_rank
from pearnly_cowork.targets.identity import account_set_company, normalize_express_account_key

HEAD_OFFICE = "00000"
IDENTITY_MAX = 200
BOOKS_MAX = 20


def digits(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))


def tax_id(value: object) -> str:
    """有效的 13 位税号；不是就空串。"""
    raw = digits(value)
    return raw if len(raw) == 13 and valid_tax_id(raw) else ""


def branch(value: object) -> str:
    """分店号归一成 5 位（空 / 读不出 = 总公司 00000）。"""
    raw = digits(value)
    return (raw[-5:] if raw else "0").zfill(5)


def _attributes(row: Any) -> Mapping[str, Any]:
    if not isinstance(row, Mapping):
        return {}
    attributes = row.get("attributes")
    return attributes if isinstance(attributes, Mapping) else {}


def _text(value: object) -> str:
    return " ".join(str(value or "").split())


def name_identity(connection: object, name_key: object) -> str:
    """Express 没有有效税号的账套按名称认的公司身份 ``name:express:{连接}:{名称键}``（同 `push.express.goods_posting.company_key`）。"""
    return f"name:express:{_text(connection)}:{name_key}"[:IDENTITY_MAX]


def identities(company: Mapping[str, Any]) -> list:
    """这家主档在按公司记的口径（工作空间 ``document_policy``）里可能用到的身份：主档身份在前；按名称建档、后来补了税号的，
    再加建档连接上的名称身份（旧年度没报税号的账套也算这家，`belongs`；推送按推往那一行现算身份，记在这个键下）。"""
    out = [str(company.get("identity") or "")] if company.get("identity") else []
    if company.get("adapter") == "express" and company.get("seed_connection") and company.get("name_key"):
        alias = name_identity(company["seed_connection"], company["name_key"])
        if alias not in out:
            out.append(alias)
    return out


def company_identity(adapter: object, connection: object, row: Any) -> dict | None:
    """目录一行 → `{key, tax_id, branch, name, name_key, code}`；认不出是 None。`branch` 是这份账套的分店号（Express 有
    税号时才有意义）；`name` 是目录里的公司法定名（THINAM / MR.ERP 的 company_name）。"""
    kind = _text(adapter).lower()
    attributes = _attributes(row)
    connection = _text(connection)
    if not connection:
        return None
    if kind == "express":
        name = _text(attributes.get("company"))
        name_key = account_set_company(row)
        tax = tax_id(attributes.get("tax_id"))
        if tax:
            return {"key": f"tax:{tax}", "tax_id": tax, "branch": branch(attributes.get("branch")), "name": name,
                    "name_key": name_key, "code": ""}
        if name_key:
            return {"key": name_identity(connection, name_key), "tax_id": "", "branch": "", "name": name,
                    "name_key": name_key, "code": ""}
        return None
    if kind == "mrerp":
        code = _text(attributes.get("company"))
        if not code:
            return None
        name = _text(attributes.get("company_name")) or code
        # 身份照旧按连接 + 公司代码（税号是刷新账套时读到的总店税号，2026-10-19 起才有；改身份会把已建的主档拆成两家），
        # 税号只用来补主档（`service.books`）、建档时写上
        return {"key": f"mrerp:{connection}:{code}"[:IDENTITY_MAX], "tax_id": tax_id(attributes.get("tax_id")),
                "branch": "", "name": name, "name_key": account_set_company(row), "code": code}
    return None


def usable(adapter: object, row: Any) -> bool:
    """能建主档、能列进候选的账套：启用、有键；Express 的还要小助手写得进（同录入下拉，`catalog_groups._usable`）。"""
    if not isinstance(row, Mapping) or row.get("active") is False or not _text(row.get("source_id")):
        return False
    return not (_text(adapter).lower() == "express" and _attributes(row).get("writable") is False)


def belongs(company: Mapping[str, Any], adapter: object, connection: object, row: Any) -> bool:
    """这一行账套是不是这家公司的：身份相同；或这一行没有有效税号、而与主档建档时的连接相同、名称键相同（按名称建的
    主档补了税号以后，旧年度没报税号的账套也还认得出，见 `autofill`）。"""
    found = company_identity(adapter, connection, row)
    if not found:
        return False
    if found["key"] == company.get("identity"):
        return True
    return (not found["tax_id"] and _text(adapter).lower() == "express" == company.get("adapter")
            and company.get("seed_connection") == _text(connection) and bool(found["name_key"])
            and found["name_key"] == company.get("name_key"))


def autofill(company: Mapping[str, Any], adapter: object, connection: object, row: Any) -> dict | None:
    """按名称建的主档，同一连接、同名称键的行后来上报了有效税号 → 要补上的 `{tax_id, branch, identity}`；否则 None。
    这个 `tax:` 键有没有被别的主档占用由调用方查。"""
    if company.get("tax_id"):
        return None
    found = company_identity(adapter, connection, row)
    if not found or not found["tax_id"] or company.get("seed_connection") != _text(connection) \
            or not company.get("name_key") or found["name_key"] != company.get("name_key"):
        return None
    return {"tax_id": found["tax_id"], "branch": found["branch"], "identity": found["key"]}


def book(adapter: object, connection: object, row: Mapping[str, Any]) -> dict:
    """一份账套给页面与 CC 的样子：`{connection, adapter, key, label, year_label, branch, period_start, period_end,
    next_end, writable}`（``next_end`` 是次年段止，Express ISPRD 的 NY 第 12 期；没报为空串）。"""
    attributes = _attributes(row)
    kind = _text(adapter).lower()
    key = _text(row.get("source_id"))
    if kind == "express":
        key = normalize_express_account_key(key) or key
        year = _text(attributes.get("root_label"))
    else:
        year = _text(row.get("label"))
    found = company_identity(kind, connection, row) or {}
    return {"connection": _text(connection), "adapter": kind, "key": key, "label": _text(row.get("label")) or key,
            "year_label": year, "branch": found.get("branch") or "",
            # 本年起止（不含次年）：「覆盖这个月」按本年算，次年的单据进下一年度的账套
            "period_start": _text(attributes.get("period_start"))[:10],
            "period_end": _text(attributes.get("period_end"))[:10],
            "next_end": _text(attributes.get("next_end"))[:10],
            "writable": attributes.get("writable") is not False}


def newest_first(books: Iterable[Mapping[str, Any]]) -> list:
    """新年度在前：先按会计期间起始日，再按年度标签里的数字；同一年度按名称（登录列表名）从小到大。"""
    ordered = sorted(books, key=lambda item: str(item.get("label") or "").casefold())
    return sorted(ordered, key=lambda item: (str(item.get("period_start") or ""), year_rank(item.get("year_label"))),
                  reverse=True)


def _span(item: Mapping[str, Any]) -> tuple:
    return item.get("period_start"), item.get("period_end"), item.get("next_end")


def covering(books: Iterable[Mapping[str, Any]], period: str) -> list:
    """覆盖这个月（`YYYY-MM`）的账套：会计期间起止都报了、且这个月落在本年期间里；某个分店没有本年覆盖这个月的账套时，
    用这个分店次年段含这个月的旧账套（新年度账套还没建，与按日期选账套 :func:`for_day` 同一规则，审查处理 10-16：
    月结台账、主档页、档位统计原先只认本年，次年那几个月归不到账套）。按原顺序回。"""
    if not re.fullmatch(r"\d{4}-\d{2}", str(period or "")):
        return []
    books = list(books)
    year, month = int(period[:4]), int(period[5:7])
    if not 1 <= month <= 12:
        return []
    first = f"{period}-01"
    last = f"{year + month // 12:04d}-{month % 12 + 1:02d}-01"
    last = (date.fromisoformat(last) - timedelta(days=1)).isoformat()
    chosen = []
    for number in dict.fromkeys(item.get("branch") or "" for item in books):
        group = [item for item in books if (item.get("branch") or "") == number]
        chosen.extend(account_match.for_month(group, first, last, _span))
    return [item for item in books if any(item is found for found in chosen)]


def for_day(books: Iterable[Mapping[str, Any]], day: object) -> list:
    """这一天（`YYYY-MM-DD`）该推往的账套（定时任务跟随公司当年账套 T1、自动推的「推往的须是按日期选出的那份」）：先找本年
    期间覆盖这一天的（会计期间从月中起止时核到日，不把相邻两份都算上）；没有，就用期间里含这一天的旧账套（次年段，到
    ``next_end``：新年度账套还没建时下一年的单记进旧账套的下一年期间，用户 2026-10-16）。两份同期都回（调用方停下）。
    规则在 `targets.account_match.for_day`（推送的期间硬闸同一份）。日期不是这个形状回空表。"""
    day = str(day or "")[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return []
    return account_match.for_day(books, day, lambda item: (item.get("period_start"), item.get("period_end"),
                                                           item.get("next_end")))


def for_branch(books: Iterable[Mapping[str, Any]], number: object) -> list:
    """定时任务记下的分店（T1）：只留这个分店的账套；这家公司只报过这一个分店号时，没报分店号的账套（旧年度没税号的）
    也算。没记分店原样回。"""
    books = list(books)
    number = str(number or "")
    if not number:
        return books
    known = {item.get("branch") for item in books if item.get("branch")}
    return [item for item in books
            if item.get("branch") == number or (not item.get("branch") and known <= {number})]


def branches(books: Iterable[Mapping[str, Any]]) -> list:
    """分店清单（主档不存，按账套现算）：`[{branch, label, account_sets: [账套…]}]`，总公司在前，再按分店号；
    每组里新年度在前，`label` 是这个分店最新那份账套的登录列表名。没有分店号的账套（MR.ERP、无税号）不分组，回空表。"""
    groups: dict[str, list] = {}
    for item in books:
        if item.get("branch"):
            groups.setdefault(item["branch"], []).append(item)
    out = []
    for number in sorted(groups):
        sets = newest_first(groups[number])
        out.append({"branch": number, "label": sets[0].get("label") or "", "account_sets": sets})
    return out


def scoped(books: Iterable[Mapping[str, Any]], scope: str) -> list:
    """对话指定了分店（`scope`）时只留这个分店的账套；没指定原样。"""
    books = list(books)
    if not scope:
        return books
    return [item for item in books if item.get("branch") == scope]


def route_branch(fields: Mapping[str, Any] | None, *, direction: str, company_branches: Iterable[str],
                 scope: str = "") -> dict:
    """推送时这张单据该进哪个分店的账套（补充 甲.3）：
    - 进项看买方分店、销项看卖方分店（我方开票的分店），票面读出了就只认这个分店（`source = document`）；
      公司没有这个分店时 `known = False`（照硬闸提示，不静默改）；
    - 票面没有（简易税票、收据）→ 用对话的分店（`conversation`）；
    - 对话也没定：公司只有一个分店就是它（`only`），有几个就要问一次（`ask`）。
    回 `{branch, source, known}`；`branch` 为空表示要问。"""
    fields = fields if isinstance(fields, Mapping) else {}
    own = "buyer" if str(direction or "") == "purchase" else "seller"
    known = sorted({branch(item) for item in company_branches or () if str(item or "").strip()})
    printed = digits(fields.get(f"{own}_branch"))
    if printed:
        number = branch(printed)
        return {"branch": number, "source": "document", "known": number in known or not known}
    if scope:
        return {"branch": branch(scope), "source": "conversation", "known": branch(scope) in known or not known}
    if len(known) <= 1:
        return {"branch": known[0] if known else "", "source": "only", "known": True}
    return {"branch": "", "source": "ask", "known": True}


def scope_value(value: object) -> str:
    """对话的分店范围（共用层 `project_scope`）：5 位分店号；空 = 不指定；别的形状不认（回 None）。"""
    text = str(value or "").strip()
    if not text:
        return ""
    return text if re.fullmatch(r"\d{5}", text) else None


__all__ = ["BOOKS_MAX", "HEAD_OFFICE", "autofill", "belongs", "book", "branch", "branches", "company_identity",
           "covering", "digits", "for_branch", "for_day", "identities", "name_identity", "newest_first", "route_branch",
           "scope_value", "scoped", "tax_id", "usable"]
