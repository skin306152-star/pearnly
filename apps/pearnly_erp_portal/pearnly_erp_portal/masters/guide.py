"""ERP 公司资料（知识库笔记）与添加公司的引导式：声明 `masters.cc_guide.GUIDES` 点名的产品函数。

只转调公司页同一组函数（`masters.cc_service`：`detail` 的笔记、`note_save / note_delete`、`pending / create_many`），CC 工具
``erp_company_note_*``、``erp_connection_companies / erp_company_add`` 也是这一组，不另查库、不另写规则：

- 公司资料：公司 = 本对话公司（对话没挂公司就不上这张卡的选项，题目下一行事实）；笔记选项 = 这家知识库的各条（有界：每家最多 50 条），
  改时正文推荐现文；存时来源记 ``cc``、带这段对话（同 CC 工具）。
- 添加公司：选项 = 连接里还没接进 ERP 的公司（服务端按页、搜索，同公司页弹窗）；码 = 目录里的标识（Express 税号 + 分店、
  MR.ERP 按公司各年度一行），确定时按标识找回连接与账套交 `create_many`（逐家锁内幂等，建好排主档同步）。
"""
from __future__ import annotations

import hashlib
import json

import frappe
from frappe import _

NOTE_ADD, NOTE_EDIT, NOTE_DELETE = "note_add", "note_edit", "note_delete"
ACTIONS = ((NOTE_ADD, "guide.option.company.note_add"), (NOTE_EDIT, "guide.option.company.note_edit"),
           (NOTE_DELETE, "guide.option.company.note_delete"))
NO_COMPANY = "guide.fact.company.none"
SCAN_PAGES = 40   # 添加公司按标识找回：目录按页（每页 PAGE_MAX）最多翻这么多页
DRAFT_SEP = "@"


def _service():
    from pearnly_erp_portal.masters import cc_service

    return cc_service


def _answer(ctx, key, default=""):
    return (ctx.get("answers") or {}).get(key) or default


def _company(ctx) -> str:
    return str(ctx.get("company") or "")


def _only(rows, codes):
    wanted = {str(code) for code in codes}
    return {"options": [row for row in rows if row["code"] in wanted], "more": False}


# ── 公司资料：笔记 ─────────────────────────────────────────────────────────────


def action_options(ctx, txt="", start=0, page_length=20, codes=None):
    rows = [{"code": code, "name": _(label)} for code, label in ACTIONS]
    return _only(rows, codes) if codes is not None else {"options": rows, "more": False}


def _notes(ctx) -> list:
    company = _company(ctx)
    if not company:
        return []
    return _service().detail(company)["notes"]


def note_options(ctx, txt="", start=0, page_length=20, codes=None):
    """这家知识库的各条（每家最多 50 条，已取全）；对话没挂公司时没有选项、题目下一行事实。"""
    if not _company(ctx):
        return {"options": [], "more": False, "facts": [_(NO_COMPANY)]}
    rows = [{"code": note["id"], "name": note["text"]} for note in _notes(ctx)]
    if codes is not None:
        return _only(rows, codes)
    text = str(txt or "").strip().casefold()
    rows = [row for row in rows if text in row["name"].casefold()] if text else rows
    start, size = max(0, int(start or 0)), max(1, int(page_length or 20))
    return {"options": rows[start:start + size], "more": len(rows) > start + size}


def text_default(ctx):
    """改笔记：推荐现文（点了才算）；加笔记没有推荐。"""
    if _answer(ctx, "action") != NOTE_EDIT:
        return None
    found = next((note for note in _notes(ctx) if note["id"] == _answer(ctx, "note")), None)
    return {"value": found["text"], "value_label": found["text"], "source": "current"} if found else None


def text_check(ctx, value) -> str:
    """同知识库服务的口径（`company_notes.clean`：去空白后非空、不超长），不过就退回正文。"""
    from pearnly_erp_portal.masters import company_notes

    try:
        company_notes.clean(value)
    except frappe.ValidationError:
        frappe.clear_messages()
        return company_notes.INVALID
    return ""


def draft(ctx, values) -> dict:
    digest = hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return {"draft": DRAFT_SEP.join((values["action"], str(values.get("note") or ""), digest))}


def _line(key, label_key, value, step=None):
    return {"key": key, "label_key": label_key, "value": value, **({"step": step} if step else {})}


def review(ctx, draft) -> dict:
    """复核卡：公司、做什么、笔记（改前 / 删的那条）、正文。"""
    action = _answer(ctx, "action")
    row = _service().get(_company(ctx)) if _company(ctx) else None
    lines = [_line("company", "Company", (row.company_name or row.name) if row else ""),
             _line("action", "guide.step.company.action", _(dict(ACTIONS).get(action, "")), "action")]
    if action in (NOTE_EDIT, NOTE_DELETE):
        found = next((note for note in _notes(ctx) if note["id"] == _answer(ctx, "note")), {})
        lines.append(_line("note", "guide.step.company.note", found.get("text") or "", "note"))
    if action in (NOTE_ADD, NOTE_EDIT):
        lines.append(_line("text", "guide.step.company.text", " ".join(str(_answer(ctx, "text")).split()), "text"))
    return {"version": draft, "ready": bool(row), "problem": "" if row else "no_company",
            "problem_text": "" if row else _(NO_COMPANY), "lines": lines, "tables": [], "total": ""}


def _session(ctx) -> str:
    return str(ctx.get("session") or "")


def _short(text) -> str:
    return " ".join(str(text or "").split())[:80]


def _joiner() -> str:
    """几家公司名连写：中文、日文用「、」，泰文、英文用逗号（同收款期间那句，`collection.texts.periods_text`）。"""
    return "、" if str(getattr(frappe.local, "lang", "") or "")[:2] in ("zh", "ja") else ", "


def confirm(ctx, values, draft, consent) -> dict:
    service, company = _service(), _company(ctx)
    action = values["action"]
    try:
        if action == NOTE_DELETE:
            # 结果卡给人看的名字（`guide.form_result` 的 label）：删的那条的正文（删之前取），截到 80 字；写删了几条
            shown = next((row["name"] for row in note_options(ctx, codes=[values["note"]])["options"]), "")
            found = service.note_delete(company, values["note"])
            return {"status": "entered", "note": found["id"], "label": _short(shown), "deleted": 1}
        saved = service.note_save(company, values["text"], values.get("note") if action == NOTE_EDIT else None,
                                  origin="cc", session=_session(ctx))
        return {"status": "entered", "note": saved.get("id") or "", "label": _short(values["text"])}
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        step = "text" if action == NOTE_ADD else "note"
        return {"returned": {step: {"reason": "company_refused", "text": str(error)[:300]}}}


# ── 添加公司 ──────────────────────────────────────────────────────────────────


def _row(item) -> dict:
    note = " · ".join(part for part in (item.get("tax_id"), ", ".join(item.get("years") or ())) if part)
    return {"code": item["identity"], "name": item["title"], **({"note": note} if note else {})}


def _catalog_rows(search=None):
    """连接里还没接进 ERP 的公司（`cc_service.pending`，按页取全；目录本身是有界的一份，同公司页弹窗）。"""
    service, out = _service(), []
    for number in range(SCAN_PAGES):
        page = service.pending(search, None, number * service.PAGE_MAX, service.PAGE_MAX)  # loop-query: 目录按页取，最多 40 页
        out.extend(page["rows"])
        if len(out) >= int(page["total"] or 0) or not page["rows"]:
            break
    return out


def pending_options(ctx, txt="", start=0, page_length=20, codes=None):
    service = _service()
    if codes is not None:
        wanted = {str(code) for code in codes}
        return {"options": [_row(item) for item in _catalog_rows() if item["identity"] in wanted], "more": False}
    size = max(1, min(int(page_length or 20), service.PAGE_MAX))
    page = service.pending(str(txt or "").strip() or None, None, max(0, int(start or 0)), size)
    return {"options": [_row(item) for item in page["rows"]], "more": int(start or 0) + size < int(page["total"] or 0)}


def add_draft(ctx, values) -> dict:
    digest = hashlib.sha256(json.dumps(sorted(values["companies"])).encode()).hexdigest()[:16]
    return {"draft": f"add{DRAFT_SEP}{digest}"}


def add_review(ctx, draft) -> dict:
    """复核卡：勾的几家（名称、税号、年度）。"""
    wanted = list(_answer(ctx, "companies", []))
    rows = {item["identity"]: item for item in _catalog_rows() if item["identity"] in set(wanted)}
    return {"version": draft, "ready": True, "problem": "", "problem_text": "", "lines": [],
            "tables": [{"key": "companies", "label_key": "guide.step.company_add.companies", "step": "companies",
                        "rows": [{**_row(rows[code]), "amount": ""} for code in wanted if code in rows]}], "total": ""}


def add_confirm(ctx, values, draft, consent) -> dict:
    """确定：按标识找回连接与账套，一次交 `create_many`（同公司页「添加」）；目录里已没有的退回勾选那一步。"""
    wanted = list(values["companies"])
    rows = {item["identity"]: item for item in _catalog_rows() if item["identity"] in set(wanted)}
    if len(rows) != len(set(wanted)):
        return {"returned": {"companies": {"reason": "not offered"}}}
    try:
        made = _service().create_many([{"connection": rows[code]["connection"], "account_set_key": rows[code]["account_set_key"]}
                                       for code in wanted])
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return {"returned": {"companies": {"reason": "company_refused", "text": str(error)[:300]}}}
    companies = [{"id": item.get("id"), "title": item.get("title") or item.get("label"),
                  "created": item.get("created"), "linked": bool(item.get("linked"))} for item in made]
    # 结果卡：名字是加的几家（标题连写，截到 80 字）；主档还在同步的写「同步中」（公司页同一个词，`masters.queue` 排上之后
    # 读取 / 落库都算同步中，同公司页的同步状态）
    syncing = _service().syncing([item["id"] for item in companies if item["id"]])
    return {"status": "entered", "companies": companies,
            "label": _short(_joiner().join(str(item["title"] or "") for item in companies if item["title"])),
            **({"state_text": _("company.syncExpress.syncing")} if syncing else {})}
