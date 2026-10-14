"""CC 的公司主档工具（ADR-020）：列出公司、读一家公司的资料、记 / 删知识库。

只调 `companies` 的公开入口，权限、归属由它们判。知识库的两件写都要用户在对话里点「允许」（`approval`），卡片上先给
改前、改后；CC 写入的记 `origin = cc` 与这段对话（只作审计）。缺省的公司取本对话挂的那家（`projects.of_session`）。
单独成模块（不并进 `tools.py`）：两边各自登记在 hooks `pearnly_assistant_tools` / `pearnly_assistant_guidance`。
"""
import json

import frappe
from frappe import _

from pearnly_core.assistant import projects
from pearnly_cowork.access import scope
from pearnly_cowork.companies import identity, notes, service

LIST_MAX = 20
CHOICES_MAX = 200

GUIDANCE = [
    "When a <company> note is present, work for that company by default: use its account set covering the work "
    "month, its documents folder and month folder for cowork_folder_files, cowork_folder_fetch and new scheduled "
    "tasks; ask before working on another company. Do not move existing scheduled tasks to another folder unless the "
    "user asks.",
    "When the user states or confirms a lasting fact about how this client's books are kept that is not already in "
    "the team notes (who is paid rent and the withholding rate, how POS days are closed, how a kind of receipt is "
    "booked), offer once in one short sentence to save it with cowork_company_note_save. Do not save one-off details, "
    "amounts of a single document, passwords, ID card or bank account numbers; if the user declines, do not offer it "
    "again in this conversation.",
    "Remembered push choices are applied automatically when pushing; to change one, use the push choice tools as usual.",
    "In a conversation filed under a company, cowork_list_documents, cowork_push_results and cowork_folder_tasks "
    "cover only that company's account sets (all its years, or only the work branch); scope in the result names the "
    "company. Answer for that company only and never list other companies' documents or tasks there. Pass "
    "all_companies true only when the user asks about all companies or other companies (" + "ทุกเจ้า, เจ้าอื่น" + "); "
    "for one other company pass its company id.",
]


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _session() -> str:
    return (getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id") or ""


def _filed() -> tuple:
    """本对话挂的公司、月份、分店（没挂回空串）。"""
    session = _session()
    return projects.of_session(session) if session else ("", "", "")


def _company(tool_input):
    """要读 / 写的那家：参数给了用参数，否则用本对话挂的那家；都没有回 None。"""
    wanted = str(tool_input.get("company") or "").strip() or _filed()[0]
    if not wanted or not service.exists(wanted):
        return None
    return service.get(wanted)


def _no_company() -> str:
    return _json({"status": "failed", "reason": "no company: pass company (an id from cowork_companies)"})


# ---------------- 公司对话的默认范围 ----------------

# 2026-10-15 测试机：在「T.8 ซินเซียร์ไอซ์」的 9 月对话里问「这个月哪些单进了 Express」，CC 调 cowork_list_documents 没带账套，
# 答出整个工作空间 9 月推送的 128 张（别家三份账套的）。按公司可筛的读工具（单据列表、推送结果、定时任务）在服务端兜底：
# 调用没点账套、连接或公司时，挂在公司下的对话默认只看这家的账套；会计员问所有公司 / 别家时才由 all_companies 放开。


def default_scope(tool_input) -> dict | None:
    """这次读哪家公司的账套。回 ``{"scope": 给模型看的范围, "sets": 账套（`service.book` 的样子）或 None = 不限}``：

    - 给了 ``company``（别的那家）：那家的全部账套（各年度、各分店）。
    - 本对话挂在某家公司：没给 ``all_companies`` → 这家的账套（对话指定了分店只看该分店，`service.work_sets`）；
      给了 → 不限，``scope`` 标 ``all_companies``，照「所有公司」逐家答。
    - 没挂公司、也没给 ``company``：回 None，照旧不限、不加 ``scope``。

    ``company`` 给错回 ``{"failed": 原因}``。调用自己点了账套 / 连接的不走这里（由调用方判），照旧按点的取。"""
    filed, _period, work_scope = _filed()
    filed = filed if filed and service.exists(filed) else ""
    wanted = str(tool_input.get("company") or "").strip()
    if wanted and wanted != filed:
        if not service.exists(wanted):
            return {"failed": "unknown company: pass an id from cowork_companies"}
        row = service.get(wanted)
        sets = service.work_sets(row.name)
        return {"scope": {"company": row.title, "company_id": row.name, "account_sets": len(sets)}, "sets": sets}
    if not filed:
        return None
    if tool_input.get("all_companies") and not wanted:
        return {"scope": {"all_companies": True}, "sets": None}
    row = service.get(filed)
    branch = identity.scope_value(work_scope) or ""
    sets = service.work_sets(row.name, branch)
    view = {"company": row.title, "company_id": row.name, "conversation_company": True, "account_sets": len(sets)}
    if branch and any(item.get("branch") == branch for item in sets):
        view["branch"] = branch
    return {"scope": view, "sets": sets}


def companies(tool_input) -> str:
    scope.require_read()
    found = service.listing(tool_input.get("search"), 0, LIST_MAX)
    return _json({"companies": [{"id": row["id"], "title": row["title"], "legal_name": row["legal_name"],
                                 "tax_id": row["tax_id"], "branches": row.get("branches") or [],
                                 "years": row.get("years") or []} for row in found]})


def profile(tool_input) -> str:
    row = _company(tool_input)
    if not row:
        return _no_company()
    filed, filed_period, filed_scope = _filed()
    period = str(tool_input.get("period") or (filed_period if filed == row.name else "") or "")
    found = service.detail(row.name, period or None)
    if found.get("choices"):
        found["choices"] = [{key: value for key, value in item.items() if key != "key"}
                            for item in found["choices"][:CHOICES_MAX]]
    if filed == row.name and filed_scope:
        found["work_branch"] = filed_scope
    return _json(found)


def note_save(tool_input) -> str:
    service.require_write()
    row = _company(tool_input)
    if not row:
        return _no_company()
    saved = notes.save(row.name, tool_input.get("text"), tool_input.get("note") or None,
                       workspace=scope.default_workspace(), origin="cc", session=_session())
    service.announce("upsert", row.name, part="notes")
    return _json({"status": "saved", "company": row.title, "note": saved})


def note_remove(tool_input) -> str:
    service.require_write()
    row = _company(tool_input)
    if not row:
        return _no_company()
    removed = notes.delete(row.name, str(tool_input.get("note") or ""))
    service.announce("upsert", row.name, part="notes")
    return _json({"status": "removed", "company": row.title, "note": removed["id"]})


def _before(row, note) -> str:
    if not note or not row:
        return ""
    try:
        return notes.one(row.name, str(note)).text or ""
    except frappe.DoesNotExistError:
        frappe.clear_messages()
        return ""


def preview_note_save(tool_input) -> list:
    row = _company(tool_input)
    return [{"field": _("Company"), "before": "", "after": row.title if row else ""},
            {"field": _("cowork.company.notes"), "before": _before(row, tool_input.get("note")),
             "after": " ".join(str(tool_input.get("text") or "").split())}]


def preview_note_remove(tool_input) -> list:
    row = _company(tool_input)
    return [{"field": _("cowork.company.notes"), "before": _before(row, tool_input.get("note")), "after": ""}]


COMPANY_PROP = {"type": "string", "description": "Company id from cowork_companies; leave out for the company this "
                                                 "conversation is filed under."}

TOOLS = [
    {"name": "cowork_companies", "label": "Listed companies",
     "description": "List the firm's client companies (the company masters shared by the whole firm): id, title, "
                    "legal name, tax ID, branches and account set years, up to 20; search matches the name or tax ID. "
                    "Use the id with cowork_company_profile.",
     "input_schema": {"type": "object", "properties": {"search": {"type": "string"}}},
     "handler": "pearnly_cowork.assistant.company_tools.companies"},
    {"name": "cowork_company_profile", "label": "Read company details",
     "description": "Read one client company: its account sets (newest first, with branch and fiscal period), "
                    "branches, the account set covering the work month (period YYYY-MM), its documents folder on each "
                    "computer with its state and month folder, all team notes (with ids), remembered push choices, "
                    "scheduled tasks and tax drafts. Without company it reads the company this conversation is filed "
                    "under; work_branch is the branch this conversation works on.",
     "input_schema": {"type": "object", "properties": {"company": COMPANY_PROP, "period": {"type": "string"}}},
     "handler": "pearnly_cowork.assistant.company_tools.profile"},
    {"name": "cowork_company_note_save", "label": "Saved a company note",
     "description": "Add a line to a client company's team notes, or change one (give note, its id from "
                    "cowork_company_profile). One short sentence per note, up to 500 characters; 50 notes per "
                    "company. The user sees Allow / Deny first.",
     "input_schema": {"type": "object", "properties": {
         "company": COMPANY_PROP, "text": {"type": "string"}, "note": {"type": "string"}}, "required": ["text"]},
     "approval": True,
     "ask": "Save this to the company's notes?",
     "ask_when": {"note": "Update this company note?"},
     "preview": "pearnly_cowork.assistant.company_tools.preview_note_save",
     "handler": "pearnly_cowork.assistant.company_tools.note_save"},
    {"name": "cowork_company_note_remove", "label": "Removed a company note",
     "description": "Remove one line from a client company's team notes (note is its id from cowork_company_profile). "
                    "The user sees Allow / Deny first.",
     "input_schema": {"type": "object", "properties": {"company": COMPANY_PROP, "note": {"type": "string"}},
                      "required": ["note"]},
     "approval": True,
     "ask": "Remove this company note?",
     "preview": "pearnly_cowork.assistant.company_tools.preview_note_remove",
     "handler": "pearnly_cowork.assistant.company_tools.note_remove"},
]
