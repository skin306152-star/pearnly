"""Cowork 公司资料的引导式（CC 对话里点「公司资料」，总表 CW-D-CO；评估 docs/work/2026-11-16-cc-guided-settings-评估.md）。

``GUIDES`` 是纯字面量（闸门按源码读）。业务 id、菜单字、图标、顺序、笔记那几步的 key 与 ERP 的公司资料（`pearnly_erp_portal.masters.cc_guide`）相同。
公司 = 本对话公司。做什么：加笔记 / 改笔记 / 删笔记 / 设为全公司规则（记住的选择里能提升的那几行）/ 删规则 → 对象 → 正文 → 复核 → 确定。

只转调 CC 工具同一组函数（`assistant.company_tools` 的 ``note_save / note_remove / choice_save``（带 key = 设为全公司）/
``choice_remove``，它们调 `companies` 的公开入口，按本对话公司认、写时只认本对话公司），选项来自公司资料（`companies.service.detail`）
与公司规则（`companies.rules.listing`）。从一张单据新建规则属于采购 / 销售判定卡（主会话 11-16），不在这里。
删笔记、删规则要画成允许卡：等引擎按答案区分确定样式（约定 11-16）后补 ``consent``。
"""
from __future__ import annotations

import contextlib
import hashlib
import json

import frappe
from frappe import _

NOTE_ADD, NOTE_EDIT, NOTE_DELETE, RULE_PROMOTE, RULE_DELETE = "note_add", "note_edit", "note_delete", "rule_promote", "rule_delete"
ACTIONS = ((NOTE_ADD, "guide.option.company.note_add"), (NOTE_EDIT, "guide.option.company.note_edit"),
           (NOTE_DELETE, "guide.option.company.note_delete"), (RULE_PROMOTE, "guide.option.company.rule_promote"),
           (RULE_DELETE, "guide.option.company.rule_delete"))
NO_COMPANY = "guide.fact.company.none"
RULES_MAX = 200
DRAFT_SEP = "@"


@contextlib.contextmanager
def _conversation(ctx):
    """调 CC 工具 / 公司服务时带上这段对话（本对话公司按它认），出来还原。"""
    before = getattr(frappe.local, "pearnly_assistant", None)
    frappe.local.pearnly_assistant = {**(before or {}), "session_id": str(ctx.get("session") or "")}
    try:
        yield
    finally:
        frappe.local.pearnly_assistant = before


def allowed(user) -> bool:
    """菜单上出不出「公司资料」：能写公司资料的人（`companies.service.require_write`，同公司页）。"""
    from pearnly_cowork.companies import service

    if not user or user == "Guest":
        return False
    try:
        service.require_write()
    except frappe.PermissionError:
        frappe.clear_messages()
        return False
    return True


def _answer(ctx, key, default=""):
    return (ctx.get("answers") or {}).get(key) or default


def _company(ctx) -> str:
    return str(ctx.get("company") or "")


def _only(rows, codes):
    wanted = {str(code) for code in codes}
    return {"options": [row for row in rows if row["code"] in wanted], "more": False}


def _page(rows, txt="", start=0, page_length=20):
    text = str(txt or "").strip().casefold()
    rows = [row for row in rows if text in f"{row['name']} {row.get('note') or ''}".casefold()] if text else rows
    start, size = max(0, int(start or 0)), max(1, int(page_length or 20))
    return {"options": rows[start:start + size], "more": len(rows) > start + size}


def action_options(ctx, txt="", start=0, page_length=20, codes=None):
    rows = [{"code": code, "name": _(label)} for code, label in ACTIONS]
    return _only(rows, codes) if codes is not None else {"options": rows, "more": False}


def _detail(ctx) -> dict:
    from pearnly_cowork.companies import service

    return service.detail(_company(ctx)) if _company(ctx) else {}


def _notes(ctx) -> list:
    return [{"code": note["id"], "name": note.get("text") or ""} for note in _detail(ctx).get("notes") or ()]


def _promotable(ctx) -> list:
    """记住的选择里能设为全公司的行（同公司页的「设为全公司」，`companies.choices` 标 promotable 的）。"""
    return [{"code": row["key"], "name": " → ".join(part for part in (row.get("subject_label"), row.get("value_label") or row.get("value"))
                                                    if part), **({"note": row["year_label"]} if row.get("year_label") else {})}
            for row in _detail(ctx).get("choices") or () if row.get("promotable") and row.get("key")]


def _rules(ctx) -> list:
    """这家公司的规则（`companies.rules.listing`，同公司页「公司规则」），码 = 公司规则的 key（同 CC 工具）。"""
    from pearnly_cowork.companies import choices as saved_choices, rules

    if not _company(ctx):
        return []
    return [{"code": saved_choices.encode("rule", row["id"]),
             "name": " → ".join(part for part in (row.get("subject_label") or row.get("subject"), row.get("value_label") or row.get("value"))
                                if part)}
            for row in rules.listing(_company(ctx), RULES_MAX)]


TARGETS = {NOTE_EDIT: _notes, NOTE_DELETE: _notes, RULE_PROMOTE: _promotable, RULE_DELETE: _rules}


def target_options(ctx, txt="", start=0, page_length=20, codes=None):
    """要改 / 删的那条笔记、要设为全公司的记住的选择、要删的规则（都是有界的一份：笔记每家最多 50 条，规则最多 200 条）。"""
    if not _company(ctx):
        return {"options": [], "more": False, "facts": [_(NO_COMPANY)]}
    rows = TARGETS.get(_answer(ctx, "action"), lambda ctx: [])(ctx)
    return _only(rows, codes) if codes is not None else _page(rows, txt, start, page_length)


def text_default(ctx):
    """改笔记：推荐现文（点了才算）。"""
    if _answer(ctx, "action") != NOTE_EDIT:
        return None
    found = next((row for row in _notes(ctx) if row["code"] == _answer(ctx, "note")), None)
    return {"value": found["name"], "value_label": found["name"], "source": "current"} if found else None


def text_check(ctx, value) -> str:
    """同知识库服务的口径（`team_notes`：规整后非空、不超长），不过就退回正文。"""
    from pearnly_core.assistant import team_notes

    return "" if team_notes.valid(team_notes.normalize(value)) else "cowork.company.note_invalid"


def draft(ctx, values) -> dict:
    digest = hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return {"draft": DRAFT_SEP.join((values["action"], digest))}


def _line(key, label_key, value, step=None):
    return {"key": key, "label_key": label_key, "value": value, **({"step": step} if step else {})}


def review(ctx, draft) -> dict:
    from pearnly_cowork.companies import service

    action = _answer(ctx, "action")
    title = service.get(_company(ctx)).title if _company(ctx) else ""
    lines = [_line("company", "Company", title), _line("action", "guide.step.company.action", _(dict(ACTIONS).get(action, "")), "action")]
    if action in TARGETS:
        found = next((row for row in TARGETS[action](ctx) if row["code"] == _answer(ctx, "note")), {})
        lines.append(_line("note", "guide.step.company.note", found.get("name") or "", "note"))
    if action in (NOTE_ADD, NOTE_EDIT):
        lines.append(_line("text", "guide.step.company.text", " ".join(str(_answer(ctx, "text")).split()), "text"))
    return {"version": draft, "ready": bool(title), "problem": "" if title else "no_company",
            "problem_text": "" if title else _(NO_COMPANY), "lines": lines, "tables": [], "total": ""}


def confirm(ctx, values, draft, consent) -> dict:
    """转调 CC 工具同一个函数（按本对话公司认；拒绝回 not_saved 或抛业务错）。"""
    from pearnly_cowork.assistant import company_tools

    action = values["action"]
    step = "text" if action == NOTE_ADD else "note"
    calls = {NOTE_ADD: ("note_save", {"text": values.get("text")}),
             NOTE_EDIT: ("note_save", {"text": values.get("text"), "note": values.get("note")}),
             NOTE_DELETE: ("note_remove", {"note": values.get("note")}),
             RULE_PROMOTE: ("choice_save", {"key": values.get("note")}),
             RULE_DELETE: ("choice_remove", {"key": values.get("note")})}
    name, payload = calls[action]
    with _conversation(ctx):
        try:
            found = json.loads(getattr(company_tools, name)(payload))
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
            frappe.db.rollback()
            frappe.clear_messages()
            return {"returned": {step: {"reason": "company_refused", "text": str(error)[:300]}}}
    if found.get("status") not in ("saved", "removed"):
        return {"returned": {step: {"reason": "company_refused", "text": str(found.get("reason") or "")[:300]}}}
    return {"status": "entered", **{key: found[key] for key in ("note", "removed") if key in found}}


GUIDES = [
    {"id": "company", "label_key": "guide.business.company", "icon": "building", "order": 80,
     "modes": ["chat"], "ledgers": ["express_local", "express_cloud", "mrerp"],
     "allowed": "pearnly_cowork.assistant.guided.company.allowed",
     "opening_key": "guide.open.company", "source": "form", "writes": True,
     "steps": [
         {"key": "action", "input": "choice", "rule": "always", "options": "pearnly_cowork.assistant.guided.company.action_options"},
         {"key": "note", "input": "choice", "rule": "always", "search": True, "after": ["action"],
          "when": {"key": "action", "in": ["note_edit", "note_delete", "rule_promote", "rule_delete"]},
          "options": "pearnly_cowork.assistant.guided.company.target_options"},
         {"key": "text", "input": "text", "rule": "always", "after": ["action"],
          "when": {"key": "action", "in": ["note_add", "note_edit"]},
          "default": "pearnly_cowork.assistant.guided.company.text_default", "check": "pearnly_cowork.assistant.guided.company.text_check"},
     ],
     "draft": "pearnly_cowork.assistant.guided.company.draft", "review": "pearnly_cowork.assistant.guided.company.review",
     "confirm": "pearnly_cowork.assistant.guided.company.confirm",
     "tool": "cowork_company_note_save", "expires": ["company"]},
]
