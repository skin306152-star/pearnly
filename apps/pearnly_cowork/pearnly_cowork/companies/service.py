"""公司主档（ADR-020）：全所共用，一家客户一份；只能从账套目录建，由服务端自己读目录、自己算身份。

- 主档只存名称、法定名、身份（`identity`，算法见 `identity.company_identity`）、合并申报标记、知识库（`notes`）与按电脑的
  文件夹绑定（`folder_intake.company_folders`）。账套、分店、定时任务、申报草稿、记住的选择都按各自现有的键现算，
  不迁数据、不加外键；删了主档再建，它们照样出现。
- 账套目录读连接最近一次的目录快照（`connections.service.account_set_facts`，权限同录入下拉），同一请求里每条连接只读
  一次（`frappe.local`）。不调电脑、不扣费。
- 权限：读（公司页、主档页）要能看录入或识别记录（`scope.READ_FEATURES`）；建、改名、删、知识库、文件夹、改选择要
  录入权限；账套只列本人能用的连接上的。Cowork 不做公司隔离：主档全所可见。
- 主档变了（建、改名、知识库、文件夹、选择、删）提交后按全所录入用户逐个推 `pearnly.project`（`announce`）。
"""
from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.utils import now_datetime

from pearnly_core.assistant import projects
from pearnly_cowork.access import scope
from pearnly_cowork.companies import context, identity, months, notes, rules
from pearnly_cowork.companies.access import COMPANY, NOTE, read, write
from pearnly_cowork.connections import service as connections
from pearnly_cowork.folder_intake import agent, company_folders
from pearnly_cowork.folder_intake import contracts as folder_contracts
from pearnly_cowork.folder_intake.access import RULE
from pearnly_cowork.targets.identity import normalize_express_account_key

COMPANY_MAX = 2000
PAGE_MAX = 50
CANDIDATES_MAX = 200
TITLE_MAX = 140
REQUEST_KEY = re.compile(r"^[A-Za-z0-9-]{8,64}$")
FIELDS = ["name", "identity", "adapter", "title", "legal_name", "tax_id", "branch", "name_key", "seed_connection",
          "seed_account_set", "mrerp_company", "combined_vat", "created_by", "creation", "request_key"]
ADAPTERS = ("express", "mrerp")
ANNOUNCE_ROLES = ("Cowork Intake", "Cowork Admin")
SAVEPOINT = "cowork_company_create"
TASKS_MAX = 50


def _error(code, exc=None):
    frappe.throw(_(code), exc or frappe.ValidationError)


def _missing():
    _error("cowork.company.not_found", frappe.DoesNotExistError)


def require_read():
    scope.require_read()


def require_write():
    scope.require("intake")


# ---------------- 账套目录（请求内缓存） ----------------


def _cache() -> dict:
    found = getattr(frappe.local, "cowork_company_cache", None)
    if found is None:
        found = {}
        frappe.local.cowork_company_cache = found
    return found


def usable_connections() -> list:
    """本人能用的 Express / MR.ERP 连接（同录入下拉，`connections.records`）：`[{name, adapter, label}]`。"""
    cache = _cache()
    if "connections" not in cache:
        try:
            rows = connections.records(None, None, 0, 100) or []
        except (frappe.PermissionError, frappe.ValidationError):
            frappe.clear_messages()
            rows = []
        cache["connections"] = [{"name": row["name"], "adapter": row.get("adapter"), "label": row.get("label") or ""}
                                for row in rows if row.get("enabled", True) and row.get("adapter") in ADAPTERS]
    return cache["connections"]


def facts(connection) -> dict:
    """这条连接最近一次的账套目录（`account_set_facts`）；读不到（没权限、没快照）是空的。"""
    cache = _cache().setdefault("facts", {})
    if connection not in cache:
        try:
            cache[connection] = connections.account_set_facts(connection)
        except (frappe.PermissionError, frappe.ValidationError, frappe.DoesNotExistError):
            frappe.clear_messages()
            cache[connection] = {"connection": connection, "account_sets": [], "status": "missing"}
    return cache[connection]


def _rows():
    """本人能用的全部目录行：`[(连接, 适配器, 行)]`。"""
    out = []
    for connection in usable_connections():
        found = facts(connection["name"])
        for row in found.get("account_sets") or []:
            if isinstance(row, dict) and row.get("active", True) is not False:
                out.append((connection["name"], connection["adapter"], row))
    return out


# ---------------- 主档读写 ----------------


def _company_row(name, for_update=False):
    if not isinstance(name, str) or not name or len(name) > 140:
        _missing()
    row = frappe.db.get_value(COMPANY, name, FIELDS, as_dict=True, for_update=for_update)
    if not row:
        _missing()
    return row


def get(name) -> dict:
    """主档一行（不核权限，调用方已核）；不在就抛「找不到」。"""
    return _company_row(name)


def exists(name) -> bool:
    return bool(isinstance(name, str) and name and len(name) <= 140 and frappe.db.exists(COMPANY, name))


def with_tax_id(tax_id) -> list:
    """税号（13 位数字）对应的主档编号（同一税号可能有几份主档，例如不同适配器）；不是 13 位数字回空。"""
    tax = str(tax_id or "").strip()
    if len(tax) != 13 or not tax.isdigit():
        return []
    return sorted(frappe.get_all(COMPANY, filters={"tax_id": tax}, pluck="name", limit_page_length=20))


def subtitle(row) -> str:
    return row.get("tax_id") or ""


def books(company, limit=identity.BOOKS_MAX) -> list:
    """这家公司有哪些账套（本人能用的连接上），每次现算、不存：新年度在前，最多 `limit` 份（页面与 CC 说明截到
    `identity.BOOKS_MAX`；删 / 改记住的选择要全集，给 None，否则多分店多年度删不干净、改不全）。
    按名称建的主档，同一连接同名称的行后来报了有效税号 → 顺手补上税号（那个 `tax:` 键没被别的主档占用时；占了就不动，
    只记日志）。"""
    company = dict(company)
    if not company.get("tax_id"):
        for connection, adapter, row in _rows():
            fill = identity.autofill(company, adapter, connection, row) if adapter == company.get("adapter") else None
            if not fill:
                continue
            if frappe.db.exists(COMPANY, {"identity": fill["identity"]}):
                _taken(company, fill)
            elif _autofill(company, fill):
                company.update(fill)
            break
    found = [identity.book(adapter, connection, row) for connection, adapter, row in _rows()
             if adapter == company.get("adapter") and identity.belongs(company, adapter, connection, row)]
    found = identity.newest_first(found)
    return found if limit is None else found[:limit]


def work_sets(company, work_scope="") -> list:
    """CC 在这家公司的对话里默认看的账套（`books` 全集：各年度、各分店）；对话指定了分店（`work_scope`）时只留这个分店的，
    这个分店没有自己的账套（一套账服务几个分店）就仍是全部，同给 CC 的公司说明（`context.company_note`）。"""
    sets = books(_company_row(company), None)
    work_scope = identity.scope_value(work_scope) or ""
    return (identity.scoped(sets, work_scope) or sets) if work_scope else sets


_TAKEN_LOGGED: set = set()


def _taken(company, filled) -> None:
    """要补的税号已被别的主档占用：不动，记一次日志（同一进程里同一对只记一次，免得每次打开主档页都记）。"""
    key = (frappe.local.site if hasattr(frappe.local, "site") else "", company.get("name"), filled["identity"])
    if key not in _TAKEN_LOGGED:
        _TAKEN_LOGGED.add(key)
        frappe.log_error(title="Cowork company tax ID taken", message=f"{company.get('name')} {filled['identity']}")


def _autofill(company, filled) -> bool:
    frappe.db.savepoint(SAVEPOINT)
    try:
        frappe.db.set_value(COMPANY, company["name"], filled, update_modified=False)
        return True
    except (frappe.DuplicateEntryError, frappe.UniqueValidationError,
            getattr(frappe.db, "IntegrityError", frappe.UniqueValidationError)):  # 并发时撞唯一键（1062）
        frappe.db.rollback(save_point=SAVEPOINT)
        frappe.clear_messages()
        _taken(company, filled)
        return False


def _period(value) -> str:
    text = str(value or "").strip()
    if text and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", text):
        _error("cowork.company.period_invalid")
    return text


def _years(sets) -> list:
    return list(dict.fromkeys(item["year_label"] for item in sets if item.get("year_label")))


def _view_row(row, sets=None, mine=None) -> dict:
    out = {"id": row.name, "title": row.title or "", "legal_name": row.legal_name or "", "tax_id": row.tax_id or "",
           "branch": row.branch or "", "adapter": row.adapter, "combined_vat": bool(row.get("combined_vat"))}
    if sets is not None:
        out["years"] = _years(sets)
        out["branches"] = [item["branch"] for item in identity.branches(sets)]
    if mine is not None:
        stat = mine.get(row.name) or {}
        out.update(mine_count=int(stat.get("count") or 0), mine_last_active=when(stat.get("last_active")))
    return out


def _index() -> dict:
    """目录行按身份分好：`{身份: [账套]}`，另给没有税号的行 `{(适配器, 连接, 名称键): [账套]}`（同 `identity.belongs`）。"""
    cache = _cache()
    if "index" not in cache:
        by_key, by_name = {}, {}
        for connection, adapter, row in _rows():
            found = identity.company_identity(adapter, connection, row)
            if not found:
                continue
            item = identity.book(adapter, connection, row)
            by_key.setdefault(found["key"], []).append(item)
            if not found["tax_id"] and found["name_key"]:
                by_name.setdefault((adapter, connection, found["name_key"]), []).append(item)
        cache["index"] = (by_key, by_name)
    return cache["index"]


def _sets_of(row) -> list:
    """列表页用的轻量版 `books`：从请求内的目录索引取，不补税号。"""
    by_key, by_name = _index()
    found = list(by_key.get(row.identity) or [])
    if row.adapter == "express" and row.seed_connection and row.name_key:
        seen = {(item["connection"], item["key"]) for item in found}
        found += [item for item in by_name.get((row.adapter, row.seed_connection, row.name_key)) or []
                  if (item["connection"], item["key"]) not in seen]
    return identity.newest_first(found)[:identity.BOOKS_MAX]


def _built(keys, named) -> dict:
    """候选里哪些已建主档：按身份；没有税号的候选（`named`：身份 → (连接, 名称键)）另按建档时的连接与名称键认
    按名称建、后来补了税号的那份。"""
    built = {row.identity: row.name for row in frappe.get_all(
        COMPANY, filters={"identity": ["in", list(keys)]}, fields=["name", "identity"], limit_page_length=0)} \
        if keys else {}
    if named:
        rows = frappe.get_all(COMPANY, filters={"adapter": "express",
                                                "seed_connection": ["in", sorted({c for c, _n in named.values()})]},
                              fields=["name", "seed_connection", "name_key"], limit_page_length=0)
        by_name = {(row.seed_connection, row.name_key): row.name for row in rows if row.name_key}
        for key, pair in named.items():
            if not built.get(key) and by_name.get(pair):
                built[key] = by_name[pair]
    return built


def candidates(search=None) -> list:
    """本人能用的连接下、按身份合并的候选：`[{identity, title, legal_name, tax_id, branch, branches, adapter, years,
    connection, account_set_key, company}]`。不可写的账套不列；`company` 是已建的主档编号（没建是 None）。"""
    require_write()
    groups = {}
    for connection, adapter, row in _rows():
        if not identity.usable(adapter, row):
            continue
        found = identity.company_identity(adapter, connection, row)
        if not found:
            continue
        groups.setdefault(found["key"], {"identity": found, "books": []})["books"].append(
            (identity.book(adapter, connection, row), row))
    if not groups:
        return []
    named = {key: (group["books"][0][0]["connection"], group["identity"]["name_key"]) for key, group in groups.items()
             if not group["identity"]["tax_id"] and group["identity"]["name_key"] and key.startswith("name:")}
    built = _built(list(groups), named)
    needle = str(search or "").strip().casefold()
    out = []
    for key, group in groups.items():
        sets = identity.newest_first([item for item, _row in group["books"]])
        newest = sets[0]
        found = group["identity"]
        item = {"identity": key, "title": found["name"] or newest["label"], "legal_name": found["name"], "tax_id": found["tax_id"],
                "branch": newest["branch"], "branches": [b["branch"] for b in identity.branches(sets)],
                "adapter": newest["adapter"], "years": _years(sets), "connection": newest["connection"],
                "account_set_key": newest["key"], "company": built.get(key)}
        text = " ".join([item["title"], item["legal_name"], item["tax_id"], *item["years"]]).casefold()
        if needle and needle not in text:
            continue
        out.append(item)
    out.sort(key=lambda item: (item["company"] is not None, item["title"].casefold()))
    return out[:CANDIDATES_MAX]


def listing(search=None, start=0, limit=PAGE_MAX) -> list:
    """全所主档：我有对话的按我最近的活动排在前，其余按名称排；一页最多 50 家（不满一页就是最后一页）。"""
    require_read()
    try:
        start, limit = max(0, int(start or 0)), max(1, min(int(limit or PAGE_MAX), PAGE_MAX))
    except (TypeError, ValueError):
        start, limit = 0, PAGE_MAX
    rows = frappe.get_all(COMPANY, fields=FIELDS, order_by="title asc", limit_page_length=COMPANY_MAX)
    needle = str(search or "").strip().casefold()
    if needle:
        rows = [row for row in rows if needle in " ".join(
            [row.title or "", row.legal_name or "", row.tax_id or ""]).casefold()]
    mine = projects.mine(frappe.session.user, [row.name for row in rows]) if rows else {}
    ours = sorted((row for row in rows if row.name in mine), key=lambda row: mine[row.name]["last_active"],
                  reverse=True)
    rows = ours + [row for row in rows if row.name not in mine]
    return [_view_row(row, _sets_of(row), mine) for row in rows[start:start + limit]]


def _request_key(value) -> str:
    text = str(value or "").strip()
    if text and not REQUEST_KEY.match(text):
        _error("cowork.company.not_in_catalog")
    return text


def _existing(found, connection, adapter, request_key=""):
    if request_key:
        name = frappe.db.get_value(COMPANY, {"request_key": request_key}, "name")
        if name:
            return name
    name = frappe.db.get_value(COMPANY, {"identity": found["key"]}, "name")
    if name:
        return name
    if adapter == "express" and found["name_key"]:
        # 同一连接、同名按名称建过的那份（还没补税号，或这一行本身没有税号）：同 `identity.belongs`
        for row in frappe.get_all(COMPANY, filters={"seed_connection": connection, "name_key": found["name_key"],
                                                    "adapter": "express"}, fields=["name", "tax_id"],
                                  limit_page_length=5):
            if not row.tax_id or not found["tax_id"] or row.tax_id == found["tax_id"]:
                return row.name
    return None


def create(connection, account_set_key, request_key=None) -> dict:
    """从目录里的一份账套建主档：服务端自己读目录、算身份；前端传的名称、税号一律不收。已有就回现有的那份
    （同一身份、同一幂等键、并发撞唯一键都一样）。"""
    require_write()
    request_key = _request_key(request_key)
    connection = str(connection or "").strip()
    if connection not in {row["name"] for row in usable_connections()}:
        _error("cowork.company.not_in_catalog")
    adapter = next(row["adapter"] for row in usable_connections() if row["name"] == connection)
    wanted = str(account_set_key or "").strip()
    keyed = normalize_express_account_key(wanted) if adapter == "express" else wanted
    row = next((item for item in facts(connection).get("account_sets") or []
                if isinstance(item, dict) and (normalize_express_account_key(item.get("source_id")) if adapter == "express"
                                               else str(item.get("source_id") or "")) == keyed), None)
    if not row or not identity.usable(adapter, row):
        _error("cowork.company.not_in_catalog")
    found = identity.company_identity(adapter, connection, row)
    if not found:
        _error("cowork.company.not_in_catalog")
    name = _existing(found, connection, adapter, request_key)
    if name:
        return {**summary(name), "created": False}
    if frappe.db.count(COMPANY) >= COMPANY_MAX:
        _error("cowork.company.too_many")
    book = identity.book(adapter, connection, row)
    doc = frappe.new_doc(COMPANY)
    doc.update({"workspace": scope.default_workspace(), "identity": found["key"], "adapter": adapter,
                # Express 取登录列表名（会计认它），MR.ERP 的账套名是年度，取公司名
                "title": (found["name"] or book["label"] or found["code"]
                          or book["key"])[:TITLE_MAX],
                "legal_name": (found["name"] or "")[:255], "tax_id": found["tax_id"], "branch": found["branch"],
                "name_key": (found["name_key"] or "")[:200], "seed_connection": connection,
                "seed_account_set": book["key"][:500], "mrerp_company": (found["code"] or "")[:40],
                "combined_vat": 0, "created_by": frappe.session.user, "request_key": request_key or None})
    frappe.db.savepoint(SAVEPOINT)
    try:
        write(doc)
    except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
        frappe.db.rollback(save_point=SAVEPOINT)
        frappe.clear_messages()
        name = _existing(found, connection, adapter, request_key)
        if not name:
            raise
        return {**summary(name), "created": False}
    announce("upsert", doc.name, part="master")
    return {**summary(doc.name), "created": True}


def summary(name) -> dict:
    row = _company_row(name)
    return _view_row(row, _sets_of(row), projects.mine(frappe.session.user, [row.name]))


def _title(value) -> str:
    text = " ".join(str(value or "").split())
    if not text or len(text) > TITLE_MAX or re.search(r"[\x00-\x1f\x7f-\x9f]", text):
        _error("cowork.company.title_invalid")
    return text


def rename(company, title) -> dict:
    require_write()
    _company_row(company)
    doc = read(COMPANY, company, for_update=True)
    doc.title = _title(title)
    write(doc)
    announce("upsert", company, part="master")
    return summary(company)


def set_combined_vat(company, combined) -> dict:
    """合并申报（ยื่นรวม，已获批的公司才勾）：这一批只做显示与说明，不改草稿算法。"""
    require_write()
    _company_row(company)
    doc = read(COMPANY, company, for_update=True)
    doc.combined_vat = 1 if frappe.utils.cint(combined) else 0
    write(doc)
    announce("upsert", company, part="master")
    return summary(company)


def delete(company) -> dict:
    """删主档（不确认，入口在 ⋯ 菜单里）：知识库、公司规则 → 文件夹绑定 → 对话归回「最近」→ 主档；账套记忆、任务、草稿
    都不动。"""
    require_write()
    row = _company_row(company, for_update=True)
    frappe.db.delete(NOTE, {"company": row.name})
    rules.forget_company(row.name)
    company_folders.forget(row.name)
    projects.release(row.name)
    frappe.db.delete(COMPANY, {"name": row.name})
    announce("delete", row.name)
    return {"id": row.name}


# ---------------- 主档页 ----------------


def _covering(sets, period, work_scope="") -> list:
    """覆盖这个月的账套：对话指定的分店在前，再是总公司，其余按新年度在前。"""
    found = identity.covering(sets, period) if period else []
    return sorted(found, key=lambda item: (item.get("branch") != work_scope if work_scope else False,
                                           item.get("branch") not in ("", identity.HEAD_OFFICE)))


def _month_parts(period):
    return (int(period[:4]), int(period[5:7])) if period else (0, 0)


def folders_view(company, period="") -> list:
    """各台电脑上的文件夹与这个月的子文件夹（按电脑已上报的文件夹名现算，不向电脑发请求）。"""
    year, month = _month_parts(period)
    out = []
    for item in company_folders.view(company, _cache().setdefault("agents", {})):
        picked = months.month_folder(item.get("reported"), item.get("folder") or "", year, month) \
            if period else {"folder": None, "candidates": []}
        out.append({**{key: value for key, value in item.items()
                       if key not in ("reported", "stored_label", "stored_device")},
                    "month_folder": picked["folder"], "month_candidates": picked["candidates"]})
    return out


def tasks_of(sets) -> list | None:
    """这家公司账套上的定时任务（有录入权限、连接能用的才列）；没有录入权限回 None。"""
    if not scope.allowed("intake"):
        return None
    wanted = {(item["connection"], item["key"].casefold()) for item in sets}
    usable = sorted({item["connection"] for item in sets})
    if not usable:
        return []
    rows = frappe.get_all(RULE, filters={"connection": ["in", usable]},
                          fields=["name", "title", "enabled", "connection", "reader", "account_set_key",
                                  "account_label", "root_id", "folder"],
                          order_by="creation asc", ignore_permissions=True, limit_page_length=0)
    states = _cache().setdefault("agents", {})
    out = []
    for row in rows:
        key = normalize_express_account_key(row.account_set_key) or str(row.account_set_key or "")
        if (row.connection, key.casefold()) not in wanted:
            continue
        reader = row.reader or row.connection
        if reader not in states:
            states[reader] = agent.read(reader)
        roots = states[reader].get("roots") or []
        out.append({"name": row.name, "title": row.title or "", "enabled": bool(row.enabled),
                    "account_label": row.account_label or "", "connection": row.connection,
                    "account_set_key": row.account_set_key or "",
                    "folder_label": folder_contracts.folder_label(roots, row.root_id, row.folder or "", "")})
        if len(out) >= TASKS_MAX:
            break
    return out


def drafts_of(row) -> list | None:
    """申报草稿（按税号；页面按分店分组）；没有报税权限回 None。"""
    from pearnly_cowork.tax import access as tax_access
    from pearnly_cowork.tax import service as tax_service

    if not tax_access.allowed():
        return None
    if not row.tax_id:
        return []
    return tax_service.drafts_of(row.tax_id, None)


def detail(company, period=None) -> dict:
    """主档页要的全部：主档、账套（新年度在前）、分店、覆盖这个月的账套、各台电脑的文件夹与月份子文件夹、知识库、
    记住的选择、定时任务、申报草稿。各区块按权限隐藏（没有权限的给 None）。"""
    from pearnly_cowork.companies import choices

    require_read()
    period = _period(period)
    row = _company_row(company)
    sets = books(row)
    row = _company_row(company)  # 可能刚补了税号
    covering = _covering(sets, period)
    found = {
        "company": {**_view_row(row), "created_by": row.created_by or "", "created_at": str(row.creation or "")},
        "account_sets": sets,
        "branches": identity.branches(sets),
        "period_set": ({"key": covering[0]["key"], "label": covering[0]["label"],
                        "connection": covering[0]["connection"]} if covering else None),
        "folders": folders_view(row.name, period),
        "notes": notes.listing(row.name),
        "choices": choices.listing(row, sets) if scope.allowed("intake") else None,
        "tasks": tasks_of(sets),
        "drafts": drafts_of(row),
    }
    # 库里的时间是站点时区的裸时间：给页面前换成带时区的 UTC（页面按这台设备的时区显示，不自己猜时区）
    found["company"]["created_at"] = when(found["company"]["created_at"])
    for key, field in (("notes", "updated_at"), ("choices", "set_at"), ("drafts", "updated_at")):
        for item in found[key] or ():
            item[field] = when(item.get(field))
    return found


def when(value) -> str:
    """站点时区的裸时间（或同格式的字符串）→ RFC3339 UTC（`2026-10-15T02:00:00Z`）；只有日期的（如历史学到的最后日期）、
    空的、认不出的原样回。"""
    from pearnly_core.assistant import contracts as shared

    text = str(value or "").strip()
    if len(text) <= 10:
        return text
    try:
        return shared.utc_iso(text, frappe.utils.get_system_timezone())
    except (TypeError, ValueError):
        return text


def context_text(company, period="", work_scope="") -> str:
    """给 CC 的公司说明（`context.company_note`）：文件夹用绑定时记下的显示路径与电脑名，不用会变的上报值。"""
    row = _company_row(company)
    sets = books(row)
    row = _company_row(company)
    year, month = _month_parts(period)
    folders = []
    for item in company_folders.view(row.name, _cache().setdefault("agents", {})):
        picked = months.month_folder(item.get("reported"), item.get("folder") or "", year, month) \
            if period else {"folder": None, "candidates": []}
        folders.append({"device": item.get("stored_device") or item.get("device"),
                        "label": item.get("stored_label") or item.get("label"), "reader": item["reader"],
                        "root_id": item["root_id"], "folder": item["folder"], "month_folder": picked["folder"],
                        "month_candidates": picked["candidates"]})
    covering = {(item["connection"], item["key"]) for item in _covering(sets, period)}
    return context.company_note({
        "id": row.name, "title": row.title, "legal_name": row.legal_name, "tax_id": row.tax_id,
        "combined_vat": bool(row.get("combined_vat")), "period": period, "scope": work_scope,
        "account_sets": sets, "branches": identity.branches(sets), "covering": covering, "folders": folders,
        "notes": notes.listing(row.name), "rules": rules.context_rows(row.name)})


# ---------------- 文件夹 ----------------


def folder_bind(company, binding) -> dict:
    require_write()
    _company_row(company)
    found = company_folders.bind(company, binding, frappe.session.user)
    announce("upsert", company, part="folders")
    return found


def folder_unbind(company, reader) -> dict:
    require_write()
    _company_row(company)
    removed = company_folders.unbind(company, reader)
    if removed:
        announce("upsert", company, part="folders")
    return {"removed": removed}


# ---------------- 单据归哪家（待处理用） ----------------


def attribution() -> tuple[dict, dict]:
    """单据归到哪家主档：`({(连接, 账套键小写): 主档}, {税号: 主档})`。账套按本人能用的连接现算（同 `books` 的判据），
    税号只认主档上有的；同一请求里只算一次。"""
    cache = _cache()
    if "attribution" not in cache:
        rows = frappe.get_all(COMPANY, fields=["name", "identity", "tax_id", "adapter", "seed_connection", "name_key"],
                              limit_page_length=0)
        by_identity = {row.identity: row.name for row in rows}
        by_name = {(row.seed_connection, row.name_key): row.name for row in rows
                   if row.adapter == "express" and row.name_key}
        by_set = {}
        for connection, adapter, row in _rows():
            found = identity.company_identity(adapter, connection, row)
            if not found:
                continue
            name = by_identity.get(found["key"]) or (
                by_name.get((connection, found["name_key"])) if not found["tax_id"] else None)
            if name:
                by_set[(connection, identity.book(adapter, connection, row)["key"].casefold())] = name
        cache["attribution"] = (by_set, {row.tax_id: row.name for row in rows if row.tax_id})
    return cache["attribution"]


# ---------------- 定时任务按公司（侧栏「定时任务 N」、总览分组、提醒点开去哪家） ----------------


def _rule_company(rule, by_set) -> str:
    key = normalize_express_account_key(rule.get("account_set_key")) or str(rule.get("account_set_key") or "")
    return by_set.get((rule.get("connection"), key.casefold())) or ""


def task_overview() -> dict:
    """定时任务归哪家公司（任务表不加字段，按账套现算，同 `attribution`）：
    ``{"companies": {主档: {"count", "alert"}}, "tasks": {任务: 主档}, "titles": {主档: 名称}}``。归不上的任务不在
    ``tasks`` 里（页面放「其他」）。任务表一次查询（本人能用的连接上的）；``alert`` = 有要人处理的票（停下待选 / 失败，
    `folder_intake.rules.results`，按文件行的戳缓存）或电脑报文件夹不在了。没有录入权限回空。"""
    from pearnly_cowork.folder_intake import rules as folder_rules

    empty = {"companies": {}, "tasks": {}, "titles": {}}
    if not scope.allowed("intake"):
        return empty
    usable = [row["name"] for row in usable_connections()]
    if not usable:
        return empty
    rows = frappe.get_all(RULE, filters={"connection": ["in", usable]},
                          fields=["name", "enabled", "connection", "reader", "account_set_key"],
                          order_by="creation asc", ignore_permissions=True, limit_page_length=0)
    by_set, _by_tax = attribution()
    states = _cache().setdefault("agents", {})
    companies, tasks = {}, {}
    for row in rows:
        company = _rule_company(row, by_set)
        if not company:
            continue
        tasks[row.name] = company
        found = companies.setdefault(company, {"count": 0, "alert": False})
        found["count"] += 1
        if found["alert"]:
            continue
        reader = row.reader or row.connection
        if reader not in states:
            states[reader] = agent.read(reader)
        health = (states[reader].get("health") or {}).get(row.name) if agent.fresh(states[reader]) else None
        attention = int((folder_rules.results(row.name) or {}).get("attention") or 0)
        found["alert"] = bool(attention or (row.enabled and health in ("root_missing", "folder_missing")))
    titles = {}
    if companies:
        titles = {row.name: row.title or "" for row in frappe.get_all(
            COMPANY, filters={"name": ["in", list(companies)]}, fields=["name", "title"], limit_page_length=0)}
    return {"companies": companies, "tasks": tasks, "titles": titles}


def company_of_rules(owner, rule_names) -> str:
    """一条提醒 / 完成简报涉及的这些任务都属于同一家公司时回它的编号（点开到这家的主档页），否则空串（hooks
    ``cowork_rules_company``，`folder_intake.notify` 叫）。按收件人能用的连接算；出错回空串，不挡提醒。"""
    from pearnly_cowork.companies import pending

    names = [name for name in dict.fromkeys(rule_names or ()) if isinstance(name, str) and name][:50]
    if not owner or not names:
        return ""
    try:
        rows = frappe.get_all(RULE, filters={"name": ["in", names]}, fields=["name", "connection", "account_set_key"],
                              ignore_permissions=True, limit_page_length=0)
        with pending._as(owner):
            by_set, _by_tax = attribution()
            found = {_rule_company(row, by_set) for row in rows}
    except Exception:
        frappe.log_error(title="Cowork rules company failed")
        return ""
    return found.pop() if len(found) == 1 and len(rows) == len(names) and "" not in found else ""


def forget_cache() -> None:
    """换了身份（后台按人算）时清掉请求内缓存：账套目录按人能用的连接读。"""
    frappe.local.cowork_company_cache = {}


# ---------------- 实时事件 ----------------


def recipients() -> list:
    """全所录入用户（有 Cowork Intake 或 Cowork Admin、且启用的），最多 `projects.ANNOUNCE_MAX` 人。"""
    return frappe.db.sql("""SELECT DISTINCT u.name FROM `tabUser` u JOIN `tabHas Role` r ON r.parent = u.name
        WHERE u.enabled = 1 AND r.parenttype = 'User' AND r.role IN %s ORDER BY u.name LIMIT %s""",
                         (ANNOUNCE_ROLES, projects.ANNOUNCE_MAX), pluck=True)


def announce(op, company, part=None) -> None:
    """主档变了：提交后按全所录入用户逐个推（只带编号与显示名，全所本来就能看）。推不出去不影响写入。"""
    try:
        users = recipients()
        if op == "delete":
            projects.announce(users, "delete", name=company)
            return
        row = _company_row(company)
        projects.announce(users, "upsert", project={"id": row.name, "title": row.title or "",
                                                    "subtitle": subtitle(row)}, part=part)
    except frappe.DoesNotExistError:
        frappe.clear_messages()
    except Exception:
        frappe.log_error(title="Cowork company announce failed")


__all__ = ["announce", "books", "candidates", "company_of_rules", "context_text", "create", "delete", "detail", "exists",
           "facts", "folder_bind", "folder_unbind", "get", "listing", "rename", "set_combined_vat", "subtitle", "summary",
           "task_overview", "usable_connections"]
