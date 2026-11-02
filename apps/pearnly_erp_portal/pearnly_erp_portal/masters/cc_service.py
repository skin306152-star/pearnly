"""ERP 版 CC 的公司（ADR-020 同一形状，2026-11-02）：CC 对话页的公司列表、公司页、知识库、待处理的服务端逻辑；网页接口是
`cc_companies`（薄入口），共用层的 provider 是 `cc_projects`。

共用层 `projects.listing` 把 provider 声明的前缀（`cc_projects.API`）交给页面，页面只调它声明的分区（`cc_projects.SECTIONS`）。
这里只出 Cowork 前端已有的形状（`frontend/assistant/src/lib/companies.ts`），字段是子集：

- 公司 = `ERP Company` 的一行（编号 = 文档名），只回本人可见（`company.allowed_companies`，已只含启用的）的；
- ERP **不提供**建、改名、删：公司在 ERP 里建、改、停用（公司页 `/desk/pearnly-company`）。页面的「添加公司」打开 ERP 新建公司页
  （`desk`，只给能管公司的人），公司页的「在浏览器中打开」去这家的公司页（`detail.company.desk_url`）；
- 账套：CC 桌面版报来的 Express 账套按「税号 + 分店」对上（共用通道 `books.for_company`，与 ERP 写账选账套同一条）；
- 读用 GET；权限全在服务端判：读要能看公司（`company.read`）且这家在本人可见公司里。
- 公司变了（建、改名、停用、启用、删）：提交后给能看这家的人逐个推 ``pearnly.project``（`announce`）。
"""
from __future__ import annotations

import frappe
from frappe import _

from pearnly_core.assistant import projects
from pearnly_erp_portal.access import guard
from pearnly_erp_portal.masters import cc_context
from pearnly_erp_portal.masters import company as companies

COMPANY = "ERP Company"
PAGE_MAX = 50
COMPANY_MAX = 2000  # 一个租户的公司数上限（按账套自动建公司的大租户几十家）
HEAD_OFFICE = "00000"
FIELDS = ["name", "company_name", "short_name", "tax_id", "branch", "vat_registered", "express_enabled", "express_book",
          "express_synced_at", "express_sync_state", "is_active", "owner", "creation"]
DESK = "/desk/pearnly-company"


# ---------------- 权限 ----------------


def require_read(user=None) -> str:
    user = user or frappe.session.user
    if not user or user == "Guest" or not guard.has("company.read", user=user):
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
    """公司页要的：公司、账套（新年度在前）、覆盖这个月的那份；ERP 没有的分区（文件夹、知识库、记住的选择、定时任务、草稿）
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
        "notes": [],
        "choices": [],
        "tasks": None,
        "drafts": None,
    }
    return found


def desk() -> dict:
    """「添加公司」去哪：ERP 的新建公司页（只给能管公司的人，其余回空，页面不出这个按钮）。"""
    require_read()
    return {"url": f"{DESK}/new" if guard.has("company.manage") else ""}


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
        "sync_failed": row.express_sync_state == "failed", "period": period})


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
    """（hooks doc_events，ERP Company 删之后）挂在它下面的对话回到「最近」；推 ``delete``。"""
    projects.release(doc.name)
    announce(doc.name, "delete")
