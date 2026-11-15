"""ERP 版 CC 的公司（ADR-020 同一形状，2026-11-02）：CC 对话页的公司列表、公司页、知识库、待处理的服务端逻辑；网页接口是
`cc_companies`（薄入口），CC 的工具是 `cc_tools`，共用层的 provider 是 `cc_projects`。

共用层 `projects.listing` 把 provider 声明的前缀（`cc_projects.API`）交给页面，页面只调它声明的分区（`cc_projects.SECTIONS`）。
这里只出 Cowork 前端已有的形状（`frontend/assistant/src/lib/companies.ts`），字段是子集：

- 公司 = `ERP Company` 的一行（编号 = 文档名），只回本人可见（`company.allowed_companies`，已只含启用的）的；
- 改名、删、停用在 ERP 公司页（`/desk/pearnly-company`）；选择公司弹窗同 Cowork（`choices` 列已建的与账套目录里还没建的，`create` 选中即建，只给能建公司的人 `can_build`，
  2026-11-04：以前打开 ERP 新建公司页，CC 桌面版里会交给系统浏览器、要重新登录），不接账本的纯 ERP 公司在 ERP 公司页建；
  公司页的「在浏览器中打开」去这家的公司页（`detail.company.desk_url`）；
- 账套：CC 桌面版报来的 Express 账套按「税号 + 分店」对上（共用通道 `books.for_company`，与 ERP 写账选账套同一条）；
- 知识库：`company_notes`；待处理：`express.pending`。
- 读用 GET、写只收 POST；权限全在服务端判：读要能看公司（`company.read`）且这家在本人可见公司里；写知识库另要有开单权或能管公司。
- 公司或知识库变了（建、改名、停用、启用、删、记 / 删一条）：提交后给能看这家的人逐个推 ``pearnly.project``（`announce`）。
"""
from __future__ import annotations

import frappe
from frappe import _

from pearnly_core.assistant import projects
from pearnly_erp_portal.access import guard
from pearnly_erp_portal.masters import cc_context, company_notes
from pearnly_erp_portal.masters import company as companies

COMPANY = "ERP Company"
PAGE_MAX = 50
COMPANY_MAX = 2000  # 一个租户的公司数上限（按账套自动建公司的大租户几十家）
HEAD_OFFICE = "00000"
WRITE_CAPABILITIES = ("purchase.write", "sales.write", "company.manage")
FIELDS = ["name", "company_name", "short_name", "tax_id", "branch", "vat_registered", "express_enabled", "express_book",
          "express_synced_at", "express_sync_state", "is_active", "owner", "creation"]
DESK = "/desk/pearnly-company"
CONNECTIONS_MAX = 50


# ---------------- 权限 ----------------


def require_read(user=None) -> str:
    user = user or frappe.session.user
    if not user or user == "Guest" or not guard.has("company.read", user=user):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    return user


def require_write(user=None) -> str:
    user = require_read(user)
    if not any(guard.has(capability, user=user) for capability in WRITE_CAPABILITIES):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    return user


def visible(user=None) -> list:
    """本人能看的公司（启用的，有 User Permission 按它），默认公司在前。"""
    return list(companies.allowed_companies(user or frappe.session.user))


def can_see(company, user=None) -> bool:
    return isinstance(company, str) and bool(company) and len(company) <= 140 and company in visible(user)


def get(company, user=None):
    """本人能看的这家（不能看、停用、不存在一律回「找不到」）。"""
    if not can_see(company, user):
        frappe.throw(_("Company not found"), frappe.DoesNotExistError)
    return frappe.db.get_value(COMPANY, company, FIELDS, as_dict=True)


# ---------------- 形状 ----------------


def subtitle(row) -> str:
    """侧栏组头的副标题：税号；不是总公司的带上分店号。"""
    branch = row.get("branch") or ""
    return " · ".join(part for part in (row.get("tax_id") or "", branch if branch and branch != HEAD_OFFICE else "")
                      if part)


def _row(row, mine=None) -> dict:
    out = {"id": row.name, "title": row.company_name or row.name, "legal_name": "", "tax_id": row.tax_id or "",
           "branch": row.branch or "", "adapter": "express" if row.express_enabled else ""}
    if mine is not None:
        stat = mine.get(row.name) or {}
        out.update(mine_count=int(stat.get("count") or 0), mine_last_active=when(stat.get("last_active")))
    return out


def when(value) -> str:
    """站点时区的裸时间 → RFC3339 UTC（页面按设备时区显示，同 Cowork `companies.service.when`）；只有日期的、空的原样回。"""
    from pearnly_core.assistant import contracts as shared

    text = str(value or "").strip()
    if len(text) <= 10:
        return text
    try:
        return shared.utc_iso(text, frappe.utils.get_system_timezone())
    except (TypeError, ValueError):
        return text


def _year(book) -> str:
    return str(book.get("period_start") or "")[:4]


def account_sets(row) -> list:
    """这家公司的 Express 账套（CC 桌面版报来的，按税号 + 分店），新年度在前；形状同 Cowork 公司页的账套行。"""
    from pearnly_core.cc_desktop import books
    from pearnly_erp_portal.express import contracts

    tax = contracts.tax13(row.get("tax_id"))
    found = books.for_company(tax, row.get("branch")) if tax else []
    out = [{"connection": "", "adapter": "express", "key": item["key"], "label": item.get("label") or item.get("path") or "",
            "year_label": _year(item), "branch": item.get("branch") or "",
            "period_start": str(item.get("period_start") or "")[:10], "period_end": str(item.get("period_end") or "")[:10],
            "next_end": str(item.get("next_end") or "")[:10], "writable": bool(item.get("writable"))} for item in found]
    out.sort(key=lambda item: str(item["label"]).casefold())
    return sorted(out, key=lambda item: item["period_start"], reverse=True)


def covering(sets, period) -> list:
    """覆盖这个月的账套（本年起止含这个月一号）。"""
    if not period:
        return []
    day = f"{period}-01"
    return [item for item in sets if item["period_start"] and item["period_end"]
            and item["period_start"] <= day <= item["period_end"]]


def _period(value) -> str:
    import re

    text = str(value or "").strip()
    if text and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", text):
        frappe.throw(_("Invalid value"))
    return text


# ---------------- 读 ----------------


def listing(search=None, start=0, limit=PAGE_MAX) -> list:
    """本人可见的公司：我有对话的按我最近的活动排在前，其余按名称排；按名称、简称、税号搜（服务端）；一页最多 50 家。"""
    user = require_read()
    try:
        start, limit = max(0, int(start or 0)), max(1, min(int(limit or PAGE_MAX), PAGE_MAX))
    except (TypeError, ValueError):
        start, limit = 0, PAGE_MAX
    names = visible(user)
    if not names:
        return []
    needle = " ".join(str(search or "").split())[:140]
    or_filters = [[field, "like", f"%{needle}%"] for field in ("company_name", "short_name", "tax_id")] if needle else None
    rows = frappe.get_all(COMPANY, filters={"name": ["in", names], "is_active": 1}, or_filters=or_filters, fields=FIELDS,
                          order_by="company_name asc, name asc", limit_page_length=COMPANY_MAX)
    mine = projects.mine(user, [row.name for row in rows]) if rows else {}
    ours = sorted((row for row in rows if row.name in mine), key=lambda row: mine[row.name]["last_active"], reverse=True)
    rows = ours + [row for row in rows if row.name not in mine]
    return [_row(row, mine) for row in rows[start:start + limit]]


def _month_books(row, period) -> dict | None:
    from pearnly_erp_portal.masters import cc_projects

    return cc_projects.ledgers(row.name, period, "", frappe.session.user) if period else None


def period_book(row, sets, period, found=None) -> dict | None:
    """这个月的账套（右栏 / 公司页的 ``period_set``）：本对话账套的同一个函数（`cc_projects.ledgers` 的默认：覆盖本月恰好一本、
    多本按记过的、都不覆盖退最新可写的；A 线 11-12 A8，不再取覆盖本月的第一份）。没月份、定不了一本回 None。
    ``found``：调用方已取的 `cc_projects.ledgers` 结果（:func:`detail` 同时要覆盖标记，不取两遍）。"""
    found = found if found is not None else _month_books(row, period)
    key = (found or {}).get("default") or ""
    item = next((item for item in sets if item["key"] == key), None) if key else None
    return {"key": item["key"], "label": item["label"], "connection": ""} if item else None


def detail(company, period=None) -> dict:
    """公司页要的：公司、账套（新年度在前）、这个月的那份（:func:`period_book`）、知识库；ERP 没有的分区（文件夹、记住的选择、
    定时任务、草稿）给空。`desk_url` 是这家在 ERP 里的公司页。"""
    require_read()
    period = _period(period)
    row = get(company)
    month = _month_books(row, period)
    # 「全部账套」里覆盖这个月的高亮（A 线 11-12）：同本对话账套的可选项（`cc_projects.ledgers` 的 covers），页面不按日期现算
    covered = {item["id"] for item in (month or {}).get("options") or () if item.get("covers")}
    sets = [{**item, "covers": item["key"] in covered} for item in account_sets(row)]
    found = {
        "company": {**_row(row), "combined_vat": False, "created_by": row.owner or "", "created_at": when(row.creation),
                    "desk_url": f"{DESK}/{row.name}"},
        "account_sets": sets,
        "branches": [],
        "period_set": period_book(row, sets, period, month or {}),
        "folders": [],
        "notes": company_notes.listing(row.name),
        "choices": [],
        "tasks": None,
        "drafts": None,
    }
    for item in found["notes"]:
        item["updated_at"] = when(item.get("updated_at"))
    return found


# ---------------- 添加公司（从账套建，同 Cowork 的弹窗） ----------------


def require_manage(user=None) -> str:
    user = require_read(user)
    if not guard.has("company.manage", user=user):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    return user


def can_build(user=None) -> bool:
    """能在对话里选中没建过的公司顺手建（主会话 11-07 定案 #1）：能管公司的人；或有开单权限、看得到全部公司（没被 User
    Permission 限定到某几家）的人——ERP 本来就按电脑报来的账套自动建公司，员工选中只是提前建好那一行。被限定公司的员工
    看不到新建出来的那家，不给建；改名、停用、删除仍只在 ERP Desk。"""
    user = user or frappe.session.user
    if not user or user == "Guest" or not guard.has("company.read", user=user):
        return False
    if guard.has("company.manage", user=user):
        return True
    if not any(guard.has(capability, user=user) for capability in WRITE_CAPABILITIES):
        return False
    # 同 `masters.company.allowed_companies`：有 User Permission 就是被限定了
    return not frappe.get_all("User Permission", filters={"user": user, "allow": COMPANY}, pluck="name",
                              limit_page_length=1)


def require_build(user=None) -> str:
    user = require_read(user)
    if not can_build(user):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    return user


def _mrerp_on(check_admin=True) -> bool:
    from pearnly_erp_portal import mrerp
    from pearnly_erp_portal.mrerp import connection as connections

    return mrerp.enabled() and (not check_admin or connections.is_admin())


def _mrerp_sets(check_admin=True) -> list:
    """MR.ERP 各连接目录里的账套（只在 MR.ERP 账本开着、本人是管理员时；同 ERP 公司页选账本的那份目录）。
    ``check_admin`` 为假只给后台算「有没有新账套」（`express.companies.announce`，不回给任何人看）。"""
    if not _mrerp_on(check_admin):
        return []
    from pearnly_erp_portal.mrerp import catalog
    from pearnly_erp_portal.mrerp import connection as connections

    out = []
    for name in frappe.get_all(connections.CONNECTION, pluck="name", order_by="name asc", limit_page_length=CONNECTIONS_MAX):
        out += [(name, item) for item in catalog._sets(name)]  # loop-query: 一个租户的 MR.ERP 连接（CONNECTIONS_MAX 内），每条读一次目录快照
    return out


NEW, INACTIVE, ADDED = "new", "inactive", "added"  # 目录一行的状态：还要人勾 / 对应公司已停用（勾了就重新启用）/ 已有公司
STATE_ORDER = {NEW: 0, INACTIVE: 1, ADDED: 2}


def _state(found, linkable) -> str:
    if found is None or linkable:
        return NEW
    return ADDED if found.is_active else INACTIVE


def _mrerp_companies(check_admin=True) -> dict:
    """MR.ERP 目录按公司归组（各年度账套归一家，`mrerp.catalog.identity`）：``{标识: {connection, attributes, books}}``，
    各年度按期间排（期间读不到的排后）。同一家出现在几条连接里只取第一条。"""
    from pearnly_erp_portal.mrerp import catalog

    groups, hosts = {}, {}
    for connection, item in _mrerp_sets(check_admin):
        attributes = item.get("attributes") or {}
        if connection not in hosts:
            hosts[connection] = catalog.site_host(connection)  # loop-query: 每条连接只取一次（连接数有界）
        group = groups.setdefault(catalog.identity(connection, attributes, hosts[connection]),
                                  {"connection": connection, "attributes": attributes, "books": []})
        if group["connection"] == connection:
            group["books"].append(catalog._book(connection, item))
    for group in groups.values():
        group["books"].sort(key=lambda book: (not book["period_start"], book["period_start"], book["comidyear"]))
    return groups


def _year_label(book) -> str:
    """弹窗行里一个年度库怎么写：账套名 + 期间年份；期间读不到的写「—」（写账遇到它覆盖不了的日期照「没有账套」停下）。"""
    start, end = book["period_start"][:4], book["period_end"][:4]
    return f"{book['label']} {start}–{end}" if start and end and start != end else \
        f"{book['label']} {start}" if start else f"{book['label']} —"


def _catalog(search=None, check_admin=True) -> list:
    """账套目录里的公司（`choices` 里还没建的那些行从这里来；不截断、不判权限，调用方判）：Express = 在用电脑报的账套按
    「税号 + 分店」合并（`express.companies.catalog`）；MR.ERP = 各年度账套归一家公司，一家一行。
    ``company`` 是已有的那家，``state`` 见 :data:`NEW`（停用的也列：「从连接添加」里勾它就是重新启用那家，主会话 11-15）。
    顺序：还要人勾的在前，停用的其次，已有的最后，各自按名称。不调电脑、不扣费；纯 ERP 公司（不接账本）在 ERP 公司页建。"""
    from pearnly_erp_portal.express import companies as express_companies
    from pearnly_erp_portal.express import contracts

    rows = _companies()
    by_tax = _by_tax(rows)
    by_company = {row.mrerp_company: row for row in rows if row.mrerp_company}
    out = []
    for (tax, branch), item in express_companies.catalog().items():
        found = by_tax.get((tax, branch))
        # 已有同税号同分店、但没接账本的启用公司（ERP 里手工建的）：选中就是把它接上，不另建一家（`create`）
        linkable = bool(found and found.is_active and not found.express_enabled and not found.mrerp_connection)
        out.append({"identity": f"express:{tax}:{branch}", "title": item["name"], "legal_name": item["name"], "tax_id": tax,
                    "branch": branch, "branches": [branch], "adapter": "express", "years": item["years"],
                    "folders": item.get("folders") or [], "connection": "",
                    "account_set_key": f"{tax}:{branch}", "company": found.name if found else None,
                    "linkable": linkable, "state": _state(found, linkable)})
    from pearnly_erp_portal.mrerp import catalog

    for ident, group in _mrerp_companies(check_admin).items():
        found = by_company.get(ident)
        attributes = group["attributes"]
        name = catalog.legal_name(attributes, group["books"][0]["label"])
        short = str(attributes.get("company") or "").strip()
        tax = contracts.tax13(attributes.get("tax_id"))
        # 一家 MR.ERP 公司一行（各年度账套归一家，主会话 11-14）：法定名称、简称辅助显示、各年度账套与期间
        out.append({"identity": f"mrerp:{ident}", "title": name, "legal_name": name, "tax_id": tax,
                    "branch": HEAD_OFFICE if tax else "", "branches": [], "book": short if short != name else "",
                    "adapter": "mrerp", "years": [_year_label(book) for book in group["books"]],
                    "connection": group["connection"], "account_set_key": ident,
                    "folders": [], "company": found.name if found else None, "linkable": False,
                    "state": _state(found, False)})
    needle = " ".join(str(search or "").split()).casefold()
    if needle:
        out = [item for item in out
               if needle in " ".join([item["title"], item.get("book") or "", item["tax_id"], *item["years"]]).casefold()]
    out.sort(key=lambda item: (STATE_ORDER[item["state"]], item["title"].casefold()))
    return out


ADAPTERS = ("express", "mrerp", "erp")


def _adapter(row) -> str:
    """已建公司接的账本：Express、MR.ERP，或不接账本的纯 ERP 公司（弹窗里只在有这类公司时出「ERP」标签，定案 #3）。"""
    if row.get("express_enabled"):
        return "express"
    if row.get("mrerp_connection"):
        return "mrerp"
    return "erp"


def choices(search=None, adapter=None, start=0, limit=PAGE_MAX) -> dict:
    """对话里选公司的弹窗（形状同 Cowork `companies.service.choices`）：本人可见的公司与目录里还没建的账套合成一张表，服务端
    分页。没建的只给能建公司的人（`can_build`）；``adapters`` 是不按标签筛时有哪几种。顺序：我有对话的按最近活动在前，其余按名称。"""
    user = require_read()
    try:
        start, limit = max(0, int(start or 0)), max(1, min(int(limit or PAGE_MAX), PAGE_MAX))
    except (TypeError, ValueError):
        start, limit = 0, PAGE_MAX
    wanted = str(adapter or "").strip()
    if wanted not in ADAPTERS:
        wanted = ""
    needle = " ".join(str(search or "").split()).casefold()
    names = visible(user)
    built = frappe.get_all(COMPANY, filters={"name": ["in", names], "is_active": 1},
                           fields=[*FIELDS, "mrerp_connection", "mrerp_account_set"],
                           order_by="company_name asc, name asc", limit_page_length=COMPANY_MAX) if names else []
    rows = [{"identity": row.name, "title": row.company_name or row.name, "legal_name": row.company_name or "",
             "tax_id": row.tax_id or "", "branch": row.branch or "", "branches": [row.branch] if row.branch else [],
             "adapter": _adapter(row), "years": [], "connection": row.mrerp_connection or "",
             "account_set_key": row.mrerp_account_set or "", "company": row.name} for row in built]
    if can_build(user):
        rows += [item for item in _catalog() if item["state"] == NEW and item["company"] is None]
    adapters = sorted({row["adapter"] for row in rows})
    rows = [row for row in rows if (not wanted or row["adapter"] == wanted)
            and (not needle or needle in " ".join([row["title"], row["tax_id"], *row["years"]]).casefold())]
    mine = projects.mine(user, [row["company"] for row in rows if row["company"]]) if rows else {}
    ours = sorted((row for row in rows if row["company"] in mine), key=lambda row: mine[row["company"]]["last_active"],
                  reverse=True)
    rest = sorted((row for row in rows if row["company"] not in mine), key=lambda row: row["title"].casefold())
    page = (ours + rest)[start:start + limit]
    for row in page:
        row["mine_last_active"] = when((mine.get(row["company"]) or {}).get("last_active"))
    return {"rows": page, "adapters": adapters}


def _companies() -> list:
    return frappe.get_all(COMPANY, fields=["name", "is_active", "tax_id", "branch", "express_enabled", "mrerp_connection",
                                           "mrerp_account_set", "mrerp_company"], limit_page_length=COMPANY_MAX)


def waiting(row) -> bool:
    """目录里这一行还要人勾：没建公司，或有同税号的手工公司还没接账本。"""
    return row["state"] == NEW


def pending(search=None, adapter=None, start=0, limit=PAGE_MAX) -> dict:
    """ERP 公司页「从连接添加」的选择窗（公司接入重做第 2 批，2026-11-14）：连接里还没接进 ERP 的公司（同 CC 对话选公司弹窗
    同一份目录 `_catalog`），按标签（Express / MR.ERP）服务端分页。只给能建公司的人。回 ``{rows, adapters, total}``。"""
    require_build()
    try:
        start, limit = max(0, int(start or 0)), max(1, min(int(limit or PAGE_MAX), PAGE_MAX))
    except (TypeError, ValueError):
        start, limit = 0, PAGE_MAX
    rows = [row for row in _catalog(search) if waiting(row)]
    adapters = [name for name in ("express", "mrerp") if any(row["adapter"] == name for row in rows)]
    wanted = str(adapter or "").strip()
    if wanted in adapters:
        rows = [row for row in rows if row["adapter"] == wanted]
    return {"rows": rows[start:start + limit], "adapters": adapters, "total": len(rows)}


def connection_list(search=None, adapter=None, start=0, limit=PAGE_MAX) -> dict:
    """ERP 公司页「从连接添加」的选择窗（主会话 11-15）：连接上报的全部账套，带状态（:data:`NEW` / :data:`INACTIVE` /
    :data:`ADDED`），按标签（Express / MR.ERP）服务端分页。``connected``：有没有连着的电脑或 MR.ERP 连接（一行都没有时，
    页面据此显示「CC 桌面版未连接」还是「无数据」）。只给能建公司的人。回 ``{rows, adapters, total, connected}``。"""
    require_build()
    from pearnly_erp_portal.express import companies as express_companies
    from pearnly_erp_portal.mrerp import connection as connections

    try:
        start, limit = max(0, int(start or 0)), max(1, min(int(limit or PAGE_MAX), PAGE_MAX))
    except (TypeError, ValueError):
        start, limit = 0, PAGE_MAX
    every = _catalog()
    adapters = [name for name in ("express", "mrerp") if any(row["adapter"] == name for row in every)]
    wanted = str(adapter or "").strip()
    rows = [row for row in every if row["adapter"] == wanted] if wanted in adapters else every
    needle = " ".join(str(search or "").split()).casefold()
    if needle:
        rows = [row for row in rows if needle in " ".join([row["title"], row.get("book") or "", row["tax_id"], *row["years"], *row["folders"]]).casefold()]
    connected = bool(every) or bool(express_companies.devices()) or (
        _mrerp_on() and bool(frappe.get_all(connections.CONNECTION, pluck="name", limit_page_length=1)))
    return {"rows": rows[start:start + limit], "adapters": adapters, "total": len(rows), "connected": connected}


def rescan() -> dict:
    """「从连接添加」右上的刷新（主会话 11-15）：请此刻在线的每台电脑立刻整份重扫并上报账套（`cc_desktop.rescan`，同 Cowork
    「重新读取」那条指令；同一台没回执的不再下发）。回 ``{devices: [...]}``；没有在线电脑回空，页面直接显示「CC 桌面版未连接」。
    只给能建公司的人。"""
    require_build()
    from pearnly_core.cc_desktop import rescan as desktop_rescan
    from pearnly_erp_portal.express import companies as express_companies

    return {"devices": sorted(desktop_rescan.request(express_companies.online_devices()))}


def rescan_pending(devices) -> dict:
    """这批重扫的进度（页面边等边出）：``pending`` 还有几台没回执、``online`` 其中还在线、也来领了指令（或刚下发、还在领单周期内）的几台（都不是就不再等，页面显示未连接）、``stamp``
    这几台报上来的账套名单的指纹（变了页面就重取列表，扫得慢时「进行中」快照也先出来）。只认本租户在用的电脑。"""
    require_build()
    from pearnly_core.cc_desktop import rescan as desktop_rescan
    from pearnly_erp_portal.express import companies as express_companies

    if isinstance(devices, str):
        devices = frappe.parse_json(devices) if devices.strip().startswith("[") else [devices]
    known = set(express_companies.devices())
    wanted = [str(name) for name in (devices if isinstance(devices, list) else []) if str(name) in known]
    waiting = desktop_rescan.pending(wanted)
    online = set(express_companies.online_devices())
    latest = frappe.get_all(express_companies.BOOK, filters={"device": ["in", wanted or [""]]}, fields=["creation"],
                            order_by="creation desc", limit_page_length=1)
    count = frappe.db.count(express_companies.BOOK, {"device": ["in", wanted or [""]]})
    gone = set(desktop_rescan.unreached(wanted))  # 下发后迟迟没来领（刚退出的 CC 还算在线那几分钟）：当不在线
    return {"pending": len(waiting), "online": len([name for name in waiting if name in online and name not in gone]),
            "stamp": f"{count}|{latest[0].creation if latest else ''}"}


def pending_identities() -> list:
    """（后台）还要人勾的那些账套的标识：自动弹窗、启动数据据此判断有没有新的（不判权限，不回给前端看内容）。"""
    return sorted(row["identity"] for row in _catalog(check_admin=False) if waiting(row))


def create_many(items) -> list:
    """「从连接添加」勾了几家一次建：逐家走 :func:`create`（同 CC 选中即建，锁内幂等、各自提交）。最多一页。"""
    require_build()
    if isinstance(items, str):
        items = frappe.parse_json(items)
    if not isinstance(items, list) or not items:
        _not_in_catalog()
    out = []
    for item in items[:PAGE_MAX]:
        if not isinstance(item, dict):
            _not_in_catalog()
        out.append(create(item.get("connection"), item.get("account_set_key")))  # loop-query: 一次最多一页（PAGE_MAX）家，逐家锁内幂等建
    return out


def _by_tax(rows) -> dict:
    """现有公司按「税号 + 分店」（同 `express.companies.missing` 的写法规整，含停用的）。"""
    from pearnly_erp_portal.express import contracts

    return {(contracts.tax13(row.tax_id), contracts.branch5(row.branch)): row for row in rows if contracts.tax13(row.tax_id)}


def _not_in_catalog():
    frappe.throw(_("Invalid request"))


def create(connection, account_set_key, request_key=None) -> dict:
    """从候选里的一份账套建公司：服务端自己读目录算名称、税号，前端给的一律不收；已有就回现有的那家（同一组账套、同一份 MR.ERP
    账套只有一家，重复点、两人同时点都一样：同站点串行，建好在锁里提交）。``request_key`` 收下不用（形状同 Cowork）。
    能建公司的人才能建（`can_build`，11-07 定案 #1）。"""
    require_build()
    from pearnly_core.execution import state as pearnly_state
    from pearnly_erp_portal.express import companies as express_companies
    from pearnly_erp_portal.express import contracts, masters

    connection, key = str(connection or "").strip(), str(account_set_key or "").strip()
    store = pearnly_state.store()
    with store.lock(store.make_key("erp-cc-company-create"), timeout=60, blocking_timeout=20):
        if not connection:
            tax, _sep, branch = key.partition(":")
            item = express_companies.catalog().get((contracts.tax13(tax), contracts.branch5(branch))) if contracts.tax13(tax) else None
            if not item:
                _not_in_catalog()
            tax, branch = contracts.tax13(tax), contracts.branch5(branch)
            found = _by_tax(_companies()).get((tax, branch))
            if found and not found.is_active:
                # 停用的那家：勾它就是重新启用（不另建一家）；没接账本的顺手接上
                _reactivate(found.name)
                if not found.express_enabled and not found.mrerp_connection:
                    link_express(found.name)
                else:
                    masters.queue(found.name)
                _pending_changed()
                return {**_made(found.name), "created": False, "reactivated": True}
            if found and not found.express_enabled and not found.mrerp_connection:
                # 手工建过同税号同分店的公司：接上它，不另建一家（来源由选账套决定，11-14 去掉了「接 Express」勾选）
                link_express(found.name)
                _pending_changed()
                return {**_made(found.name), "created": False, "linked": True}
            if found:
                return {**_made(found.name), "created": False}
            blank = _blank_default()
            if blank:
                # 开户时那家空的默认公司：第一家添加的账套接到它上面，不并存一家空公司（主会话 11-15）
                _rename(blank, item["name"], tax, branch)
                link_express(blank)
                _pending_changed()
                return {**_made(blank), "created": False, "adopted": True}
            name = express_companies.build(tax, branch, item["name"])
            masters.queue(name)
        else:
            # MR.ERP：``account_set_key`` 是公司标识（`_catalog` 一家一行）；旧调法给的是一本账套的键，按它所属的公司认
            groups = _mrerp_companies()
            group = groups.get(key) or next((value for value in groups.values() if value["connection"] == connection
                                              and any(book["key"] == key for book in value["books"])), None)
            if not group:
                _not_in_catalog()
            ident = next(name for name, value in groups.items() if value is group)
            found = frappe.db.get_value(COMPANY, {"mrerp_company": ident}, ["name", "is_active"], as_dict=True)
            if found and not found.is_active:
                _reactivate(found.name)
                frappe.db.commit()
                _pending_changed()
                return {**_made(found.name), "created": False, "reactivated": True}
            if found:
                return {**_made(found.name), "created": False}
            name = _build_mrerp(group, _blank_default())
        frappe.db.commit()
    _pending_changed()
    return {**_made(name), "created": True}


def link_express(company):
    """把一家已有的公司接上 Express（来源由账套决定，公司接入重做第 2 批）：记「接 Express」、清接上时刻（第一次落库时记）、排主档
    同步，提交。从连接添加选中手工公司、回归实验室 / 测试夹具建站都走这一处，不再改字段（表单交来的 ``express_enabled`` 一律不收，
    `masters.company._apply`）。权限由调用方判。"""
    from pearnly_erp_portal.express import masters

    frappe.db.set_value(COMPANY, company, {"express_enabled": 1, "express_linked_at": None})
    masters.queue(company)
    frappe.db.commit()


def _pending_changed():
    """建了 / 接上一家：还要人勾的名单变了，下次启动数据重算（`express.companies.pending`）。"""
    from pearnly_erp_portal.express import companies as express_companies

    frappe.cache.delete_value(express_companies.PENDING_KEY)


# 开户时建的默认公司（`company.ensure_default`）有没有被用过：这几类挂着它的记录有一条就算用过（库存流水另按它的仓查）。
# 客户 / 供应商 / 物料不在内：没接过账本的公司没有账本档，本地档不归哪家公司，接管后照样各家能用
USED_BY = ("Purchase Document", "ERP Sales Document", "ERP Collection", "ERP LINE Intake", "ERP POS Day Summary",
           "ERP Serial No", "ERP Company Note")


def _blank_default():
    """开户时那家空的默认公司（启用、没税号、没接 Express / MR.ERP、没有任何单据 / 库存流水）：第一次从连接添加时接管它，
    不新建（不然新用户看到的是「My Company + 新公司」两家，当前公司还停在空的那家）。不是就回 ``None``。"""
    row = frappe.db.get_value(COMPANY, {"is_default": 1, "is_active": 1},
                              ["name", "tax_id", "express_enabled", "mrerp_connection", "mrerp_account_set"], as_dict=True)
    if not row or row.tax_id or row.express_enabled or row.mrerp_connection or row.mrerp_account_set:
        return None
    for doctype in USED_BY:
        if frappe.get_all(doctype, filters={"company": row.name}, pluck="name", limit_page_length=1):  # loop-query: 固定七类记录各查一条
            return None
    held = frappe.get_all("ERP Warehouse", filters={"company": row.name}, pluck="name", limit_page_length=COMPANY_MAX)
    if held:
        from pearnly_erp_portal.inventory import stock

        if frappe.get_all(stock.MOVEMENT, filters={"warehouse": ["in", held]}, pluck="name", limit_page_length=1):
            return None
    return row.name


def _rename(name, title, tax, branch):
    """把接管的默认公司改成账套的名称、税号、分店（同按账套新建那家写的字段，`express.companies.build`）。"""
    doc = frappe.get_doc(COMPANY, name)
    doc.company_name, doc.tax_id, doc.branch = str(title or "")[:200], tax, branch
    doc.save(ignore_permissions=True)


def _reactivate(name):
    """停用的公司重新启用（同公司页「启用」那一条保存，公告与启动数据照常刷新）。"""
    doc = frappe.get_doc(COMPANY, name)
    doc.is_active = 1
    doc.save(ignore_permissions=True)


def _build_mrerp(group, blank=None) -> str:
    """一家 MR.ERP 公司建一家 ERP 公司并接上（同 ERP 公司页选账本 `mrerp.catalog.choose`）：名称用全称去掉内部编号；接上时记的
    那本是今天该写的（`catalog.current`；之后按单据日期选，`catalog.pick`）；``blank`` 是要接管的空默认公司。"""
    from pearnly_erp_portal.express import contracts
    from pearnly_erp_portal.inventory import warehouses
    from pearnly_erp_portal.mrerp import catalog

    attributes = group["attributes"]
    tax = contracts.tax13(attributes.get("tax_id"))
    books = group["books"]
    today = frappe.utils.nowdate()
    covering = [book for book in books if book["period_start"] and book["period_start"] <= today <= book["period_end"]]
    pick = (covering or sorted(books, key=lambda book: (book["period_start"], book["comidyear"]))[-1:])[0]
    if blank:
        _rename(blank, catalog.legal_name(attributes, pick["label"]), tax, HEAD_OFFICE if tax else "")
        catalog.choose(blank, group["connection"], pick["key"])
        return blank
    doc = frappe.get_doc({"doctype": COMPANY, "company_name": catalog.legal_name(attributes, pick["label"]), "tax_id": tax,
                          "branch": HEAD_OFFICE if tax else "", "is_active": 1, "pos_enabled": 0, "express_enabled": 0,
                          "company_type": "general"})
    doc.insert(ignore_permissions=True)
    warehouses.ensure_for_company(doc.name, "Main")
    catalog.choose(doc.name, group["connection"], pick["key"])
    return doc.name


def _made(name) -> dict:
    row = frappe.db.get_value(COMPANY, name, ["name", "company_name"], as_dict=True)
    return {"id": row.name, "title": row.company_name or row.name}


def context_text(company, period="", user=None) -> str:
    """给 CC 的公司说明（`cc_context.company_note`）。接 Express 的公司有本对话账套（共用层在说明后接它那一段，A 线 11-12）：
    账套只列出、不标「覆盖这个月」，写进哪本由那一段定。"""
    row = get(company, user)
    sets = account_sets(row)
    synced = str(row.express_synced_at or "")[:10]
    return cc_context.company_note({
        "id": row.name, "title": row.company_name or row.name, "short_name": row.short_name or "",
        "tax_id": row.tax_id or "", "branch": row.branch or "", "vat": row.vat_registered or "",
        "express": bool(row.express_enabled), "ledger": row.express_book or "", "account_sets": sets,
        "covering": [item["key"] for item in covering(sets, period)], "synced_on": synced,
        "sync_failed": row.express_sync_state == "failed", "period": period, "notes": company_notes.listing(row.name),
        "conversation_ledger": bool(row.express_enabled)})


# ---------------- 知识库 ----------------


def note_save(company, text, note=None, *, origin="person", session="") -> dict:
    require_write()
    row = get(company)
    saved = company_notes.save(row.name, text, note or None, origin=origin, session=session)
    saved["updated_at"] = when(saved.get("updated_at"))
    announce(row.name, part="notes")
    return saved


def note_delete(company, note) -> dict:
    require_write()
    row = get(company)
    found = company_notes.delete(row.name, note)
    announce(row.name, part="notes")
    return found


# ---------------- 实时事件 ----------------


def viewers(company) -> list:
    """能看这家公司的人（在职、系统用户）：没有按公司收窄过的（没有 ``ERP Company`` 的 User Permission）都能看，收窄过的只有
    名单里有这家的。最多 `projects.ANNOUNCE_MAX` 人。"""
    narrowed = set(frappe.get_all("User Permission", filters={"allow": COMPANY}, pluck="user",  # bounded: 每人一行 × 公司数
                                  limit_page_length=projects.ANNOUNCE_MAX * 20))
    listed = set(frappe.get_all("User Permission", filters={"allow": COMPANY, "for_value": company}, pluck="user",
                                limit_page_length=projects.ANNOUNCE_MAX))
    users = frappe.get_all("User", filters={"enabled": 1, "user_type": "System User"}, pluck="name",
                           order_by="name asc", limit_page_length=projects.ANNOUNCE_MAX * 2)
    return [user for user in users if user not in narrowed or user in listed][:projects.ANNOUNCE_MAX]


def announce(company, op="upsert", part=None) -> None:
    """这家变了：提交后给能看这家的人逐个推（只带编号与显示名）；停用、删除推 ``delete``。推不出去不影响写入。"""
    try:
        users = viewers(company)
        if op == "delete":
            projects.announce(users, "delete", name=company)
            return
        row = frappe.db.get_value(COMPANY, company, ["name", "company_name", "tax_id", "branch"], as_dict=True)
        if not row:
            return
        projects.announce(users, "upsert", project={"id": row.name, "title": row.company_name or row.name,
                                                    "subtitle": subtitle(row)}, part=part)
    except Exception:
        frappe.log_error(title="ERP company announce failed")


# 显示名、税号、分店、启用变了才推（别的字段改了侧栏不用变）
WATCHED = ("company_name", "tax_id", "branch", "is_active")


def company_changed(doc, method=None) -> None:
    """（hooks doc_events，ERP Company 建、存之后）显示名或启用变了：推给能看这家的人；停用 = 从侧栏拿掉（挂在它下面的对话
    回到「最近」，共用层 provider 不再认它），重新启用 = 再出现。"""
    before = doc.get_doc_before_save() if hasattr(doc, "get_doc_before_save") else None
    if before is not None and all(before.get(field) == doc.get(field) for field in WATCHED):
        return
    announce(doc.name, "upsert" if doc.get("is_active") else "delete")


def company_removed(doc, method=None) -> None:
    """（hooks doc_events，ERP Company 删之后）挂在它下面的对话回到「最近」，知识库一起删；推 ``delete``。"""
    projects.release(doc.name)
    frappe.db.delete(company_notes.NOTE, {"company": doc.name})
    announce(doc.name, "delete")
