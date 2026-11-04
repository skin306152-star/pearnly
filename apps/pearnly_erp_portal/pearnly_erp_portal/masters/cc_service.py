"""ERP 版 CC 的公司（ADR-020 同一形状，2026-11-02）：CC 对话页的公司列表、公司页、知识库、待处理的服务端逻辑；网页接口是
`cc_companies`（薄入口），CC 的工具是 `cc_tools`，共用层的 provider 是 `cc_projects`。

共用层 `projects.listing` 把 provider 声明的前缀（`cc_projects.API`）交给页面，页面只调它声明的分区（`cc_projects.SECTIONS`）。
这里只出 Cowork 前端已有的形状（`frontend/assistant/src/lib/companies.ts`），字段是子集：

- 公司 = `ERP Company` 的一行（编号 = 文档名），只回本人可见（`company.allowed_companies`，已只含启用的）的；
- 改名、删、停用在 ERP 公司页（`/desk/pearnly-company`）；「添加公司」同 Cowork 从账套建（`candidates` / `create`，只给能管公司的人，
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
CANDIDATES_MAX = 200  # 同 Cowork 添加公司的候选上限
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
    out = [{"connection": "", "adapter": "express", "key": item["key"], "label": item.get("label") or item["key"],
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


def detail(company, period=None) -> dict:
    """公司页要的：公司、账套（新年度在前）、覆盖这个月的那份、知识库；ERP 没有的分区（文件夹、记住的选择、定时任务、草稿）
    给空。`desk_url` 是这家在 ERP 里的公司页。"""
    require_read()
    period = _period(period)
    row = get(company)
    sets = account_sets(row)
    cover = covering(sets, period)
    found = {
        "company": {**_row(row), "combined_vat": False, "created_by": row.owner or "", "created_at": when(row.creation),
                    "desk_url": f"{DESK}/{row.name}"},
        "account_sets": sets,
        "branches": [],
        "period_set": {"key": cover[0]["key"], "label": cover[0]["label"], "connection": ""} if cover else None,
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


def _mrerp_on() -> bool:
    from pearnly_erp_portal import mrerp
    from pearnly_erp_portal.mrerp import connection as connections

    return mrerp.enabled() and connections.is_admin()


def _mrerp_sets() -> list:
    """MR.ERP 各连接目录里的账套（只在 MR.ERP 账本开着、本人是管理员时；同 ERP 公司页选账本的那份目录）。"""
    if not _mrerp_on():
        return []
    from pearnly_erp_portal.mrerp import catalog
    from pearnly_erp_portal.mrerp import connection as connections

    out = []
    for name in frappe.get_all(connections.CONNECTION, pluck="name", order_by="name asc", limit_page_length=CONNECTIONS_MAX):
        out += [(name, row) for row in catalog.account_sets(name, 0, catalog.PAGE_MAX)["rows"]]
    return out


def candidates(search=None) -> list:
    """「添加公司」的候选（形状同 Cowork `companies.service.candidates`，页面用同一个弹窗）：Express = 在用电脑报的账套按
    「税号 + 分店」合并（`express.companies.catalog`，后台本来就会照它补建，这里多半是「已添加」）；MR.ERP = 各连接目录里的
    账套，一份一行（一家公司只接一份账本）。``company`` 是已有的那家（停用的不列，人停的不在这里重开）。只给能管公司的人。
    不调电脑、不扣费；纯 ERP 公司（不接账本）在 ERP 公司页建。"""
    require_manage()
    from pearnly_erp_portal.express import companies as express_companies
    from pearnly_erp_portal.express import contracts

    rows = _companies()
    by_tax = _by_tax(rows)
    by_set = {(row.mrerp_connection, row.mrerp_account_set): row for row in rows if row.mrerp_connection}
    out = []
    for (tax, branch), item in express_companies.catalog().items():
        found = by_tax.get((tax, branch))
        if found and not found.is_active:
            continue
        out.append({"identity": f"express:{tax}:{branch}", "title": item["name"], "legal_name": item["name"], "tax_id": tax,
                    "branch": branch, "branches": [branch], "adapter": "express", "years": item["years"], "connection": "",
                    "account_set_key": f"{tax}:{branch}", "company": found.name if found else None})
    for connection, row in _mrerp_sets():
        found = by_set.get((connection, row["key"]))
        if found and not found.is_active:
            continue
        name = str(row.get("company") or row["label"])
        out.append({"identity": f"mrerp:{connection}:{row['key']}", "title": name, "legal_name": name,
                    "tax_id": row.get("tax_id") or "", "branch": HEAD_OFFICE if row.get("tax_id") else "", "branches": [],
                    "adapter": "mrerp", "years": [str(row.get("period_start") or "")[:4]] if row.get("period_start") else [],
                    "connection": connection, "account_set_key": row["key"], "company": found.name if found else None})
    needle = " ".join(str(search or "").split()).casefold()
    if needle:
        out = [item for item in out if needle in " ".join([item["title"], item["tax_id"], *item["years"]]).casefold()]
    out.sort(key=lambda item: (item["company"] is not None, item["title"].casefold()))
    return out[:CANDIDATES_MAX]


def _companies() -> list:
    return frappe.get_all(COMPANY, fields=["name", "is_active", "tax_id", "branch", "mrerp_connection", "mrerp_account_set"],
                          limit_page_length=COMPANY_MAX)


def _by_tax(rows) -> dict:
    """现有公司按「税号 + 分店」（同 `express.companies.missing` 的写法规整，含停用的）。"""
    from pearnly_erp_portal.express import contracts

    return {(contracts.tax13(row.tax_id), contracts.branch5(row.branch)): row for row in rows if contracts.tax13(row.tax_id)}


def _not_in_catalog():
    frappe.throw(_("Invalid request"))


def create(connection, account_set_key, request_key=None) -> dict:
    """从候选里的一份账套建公司：服务端自己读目录算名称、税号，前端给的一律不收；已有就回现有的那家（同一组账套、同一份 MR.ERP
    账套只有一家，重复点、两人同时点都一样：同站点串行，建好在锁里提交）。``request_key`` 收下不用（形状同 Cowork）。"""
    require_manage()
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
                _not_in_catalog()
            if found:
                return {**_made(found.name), "created": False}
            name = express_companies.build(tax, branch, item["name"])
            masters.queue(name)
        else:
            found_set = next((row for conn, row in _mrerp_sets() if conn == connection and row["key"] == key), None)
            if not found_set:
                _not_in_catalog()
            found = frappe.db.get_value(COMPANY, {"mrerp_connection": connection, "mrerp_account_set": key},
                                        ["name", "is_active"], as_dict=True)
            if found and not found.is_active:
                _not_in_catalog()
            if found:
                return {**_made(found.name), "created": False}
            name = _build_mrerp(connection, found_set)
        frappe.db.commit()
    return {**_made(name), "created": True}


def _build_mrerp(connection, row) -> str:
    """MR.ERP 账套建一家公司并接上这份账本（同 ERP 公司页选账本 `mrerp.catalog.choose`）。"""
    from pearnly_erp_portal.inventory import warehouses
    from pearnly_erp_portal.mrerp import catalog

    tax = str(row.get("tax_id") or "")
    doc = frappe.get_doc({"doctype": COMPANY, "company_name": str(row.get("company") or row["label"])[:200], "tax_id": tax,
                          "branch": HEAD_OFFICE if tax else "", "is_active": 1, "pos_enabled": 0, "express_enabled": 0,
                          "company_type": "general"})
    doc.insert(ignore_permissions=True)
    warehouses.ensure_for_company(doc.name, "Main")
    catalog.choose(doc.name, connection, row["key"])
    return doc.name


def _made(name) -> dict:
    row = frappe.db.get_value(COMPANY, name, ["name", "company_name"], as_dict=True)
    return {"id": row.name, "title": row.company_name or row.name}


def context_text(company, period="", user=None) -> str:
    """给 CC 的公司说明（`cc_context.company_note`）。"""
    row = get(company, user)
    sets = account_sets(row)
    synced = str(row.express_synced_at or "")[:10]
    return cc_context.company_note({
        "id": row.name, "title": row.company_name or row.name, "short_name": row.short_name or "",
        "tax_id": row.tax_id or "", "branch": row.branch or "", "vat": row.vat_registered or "",
        "express": bool(row.express_enabled), "ledger": row.express_book or "", "account_sets": sets,
        "covering": [item["key"] for item in covering(sets, period)], "synced_on": synced,
        "sync_failed": row.express_sync_state == "failed", "period": period, "notes": company_notes.listing(row.name)})


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
