"""ERP 版 CC 的公司工具（ADR-020，2026-11-02）：列出公司、读一家公司的资料、经「允许」记 / 删知识库。

照 Cowork 的公司工具（`pearnly_cowork.assistant.company_tools` 的 companies / profile / note_save / note_remove）：只调
`cc_service` 的公开入口，权限、归属由它判；知识库的写都要用户在对话里点「允许」（`approval`），卡片上先给改前、改后；CC 写的
记 `origin = cc` 与当时的对话（只作审计）。缺省的公司取本对话挂的那家（`projects.of_session`）。登记在 hooks
`pearnly_assistant_tools` / `pearnly_assistant_guidance`。
"""
import json
import re

import frappe
from frappe import _

from pearnly_core.assistant import direct_push, projects
from pearnly_erp_portal.masters import cc_service as service
from pearnly_erp_portal.masters import company_notes

LIST_MAX = 20

GUIDANCE = [
    "When a <company> note is present, work for that company by default and write only into this conversation's "
    "account set (<conversation_account_set>, never one chosen by the document date); ask before working on another "
    "company.",
    "When the user states or confirms a lasting fact about how this company's books are kept that is not already in "
    "the team notes, offer once in one short sentence to save it with erp_company_note_save. Do not save one-off "
    "details, amounts of a single document, passwords, ID card or bank account numbers; if the user declines, do not "
    "offer it again in this conversation.",
    "When a team note decided how you handled something in this chat, say which one in a few words.",
]


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _session() -> str:
    return (getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id") or ""


def acting_filed():
    """hooks ``pearnly_assistant_tool_context``（共用层 `assistant.tools`）：CC 工具执行期间，ERP 的「当前公司」是本对话挂的那家
    （`company.acting`，盘点 2026-11-08 ①）；没挂公司、或这家已不在本人可用公司里，照本人当前公司（工具自己的公司核对照旧拒）。"""
    from contextlib import nullcontext

    from pearnly_erp_portal.masters import company as companies

    company = filed()
    return companies.acting(company) if company and company in companies.allowed_companies() else nullcontext()


def filed() -> str:
    """本对话挂的公司（没挂回空串）；对话直写工具（`express.cc_tools`）也按它取缺省公司。"""
    session = _session()
    return projects.of_session(session)[0] if session else ""


def resolve(wanted, filed="", write=False) -> tuple:
    """模型给的 ``company`` → (公司编号, 认不出的原因)。没给回 (``filed``, None)；是本人可见的公司编号照用；13 位数字按税号认：
    是本对话公司的税号就当没给，本人可见的只有一家是这个税号就用那家（写时只认本对话公司的税号，别家的回失败并列出编号），
    几家同税号回失败；都不是回 unknown company。"""
    wanted = str(wanted or "").strip()
    if not wanted:
        return filed, None
    seen = service.visible()
    if wanted in seen:
        return wanted, None
    tax = re.sub(r"[\s-]", "", wanted)
    if len(tax) == 13 and tax.isdigit():
        rows = frappe.get_all(service.COMPANY, filters={"tax_id": tax, "name": ["in", seen or [""]]}, pluck="name",
                              limit_page_length=20)
        if filed and filed in rows:
            return filed, None
        if write and rows:
            return "", (f"{tax} is the tax ID of company {', '.join(rows)}, not of the company this conversation is "
                        "filed under. Nothing was changed. For this conversation's company leave company out; a "
                        "supplier's or customer's tax ID never goes in company. To change that other company, check "
                        "with the user first, then pass its id as company.")
        if len(rows) == 1:
            return rows[0], None
        if rows:
            return "", f"several companies have tax ID {tax}: pass one company id from erp_companies ({', '.join(rows)})"
    return "", "unknown company: pass an id from erp_companies"


def _company(tool_input, write=False):
    wanted, _reason = resolve(tool_input.get("company"), filed(), write)
    if not wanted or not service.can_see(wanted):
        return None
    return service.get(wanted)


def _no_company(tool_input=None, write=False) -> str:
    reason = resolve(tool_input.get("company"), filed(), write)[1] if (tool_input or {}).get("company") else None
    return _json({"status": "failed", "reason": reason or "no company: pass company (an id from erp_companies)"})


def company_refusal(tool_input) -> str | None:
    """写知识库的工具出「允许」卡片前：给的 company 认不出、或是别家的税号就不出卡片，直接回失败与原因。"""
    if not tool_input.get("company"):
        return None
    _wanted, reason = resolve(tool_input.get("company"), filed(), write=True)
    return _json({"status": "failed", "reason": reason}) if reason else None


def companies(tool_input) -> str:
    found = service.listing(tool_input.get("search"), 0, LIST_MAX)
    return _json({"companies": [{"id": row["id"], "title": row["title"], "tax_id": row["tax_id"],
                                 "branch": row["branch"], "express": row["adapter"] == "express"} for row in found]})


def profile(tool_input) -> str:
    service.require_read()
    row = _company(tool_input)
    if not row:
        return _no_company(tool_input)
    period = str(tool_input.get("period") or "")
    if not period and filed() == row.name:
        session = _session()
        period = projects.of_session(session)[1] if session else ""
    found = service.detail(row.name, period or None)
    company = found["company"]
    out = {"company": {key: company.get(key) for key in ("id", "title", "tax_id", "branch", "created_at")},
           "vat_registered": row.vat_registered or "", "express": bool(row.express_enabled),
           "account_sets": found["account_sets"], "period": period, "period_set": found["period_set"], "notes": found["notes"]}
    if filed() == row.name:
        _with_conversation_book(out, row)
    # 页面要 UTC（按设备时区显示）；交给 CC 的时间点换成用户当地时间，同对话里别的工具（共用层 `direct_push.local_time`）
    return direct_push.dumps(out, utc=("created_at", "updated_at"))


CONVERSATION_BOOK_NOTE = ("conversation_account_set is where every write in this conversation goes (the account set of "
                          "this conversation); account_sets lists only it. Never choose another by the document date.")
CONVERSATION_BOOK_NEEDED = ("conversation_account_set is not decided yet: before the first write ask the user once with "
                            "ask_user which of account_sets to write to and set it with pearnly_conversation_ledger.")


def _with_conversation_book(out, row) -> None:
    """本对话公司的资料（A 线 11-12 A8，同 Cowork `company_tools._with_conversation_book`）：已定本对话账套时账套只给它、明说它是
    写入目标（这个月的那一份也换成它）；未定时照列，另说先问一次。"""
    from pearnly_erp_portal.express import conversation

    pin = conversation.pinned({"name": row.name})
    if not pin:
        return
    if not pin["key"]:
        out["conversation_account_set"] = {"status": "needed", "note": CONVERSATION_BOOK_NEEDED}
        return
    sets = out.get("account_sets") or []
    book = next((item for item in sets if item.get("key") == pin["key"]), None) or {
        "connection": "", "key": pin["key"], "label": pin["label"]}
    out["account_sets_total"] = len(sets)
    out["account_sets"] = [book]
    out["period_set"] = {"key": book.get("key"), "label": book.get("label"), "connection": ""}
    out["conversation_account_set"] = {**out["period_set"], "note": CONVERSATION_BOOK_NOTE}


def note_save(tool_input) -> str:
    service.require_write()
    row = _company(tool_input, write=True)
    if not row:
        return _no_company(tool_input, write=True)
    saved = service.note_save(row.name, tool_input.get("text"), tool_input.get("note") or None, origin="cc",
                              session=_session())
    return _json({"status": "saved", "company": row.company_name or row.name, "note": saved})


def note_remove(tool_input) -> str:
    service.require_write()
    row = _company(tool_input, write=True)
    if not row:
        return _no_company(tool_input, write=True)
    removed = service.note_delete(row.name, str(tool_input.get("note") or ""))
    return _json({"status": "removed", "company": row.company_name or row.name, "note": removed["id"]})


def _before(row, note) -> str:
    if not note or not row:
        return ""
    try:
        return company_notes.one(row.name, str(note)).text or ""
    except frappe.DoesNotExistError:
        frappe.clear_messages()
        return ""


def preview_note_save(tool_input) -> list:
    row = _company(tool_input, write=True)
    return [{"field": _("Company"), "before": "", "after": (row.company_name or row.name) if row else ""},
            {"field": _("erp.company.notes"), "before": _before(row, tool_input.get("note")),
             "after": " ".join(str(tool_input.get("text") or "").split())}]


def preview_note_remove(tool_input) -> list:
    row = _company(tool_input, write=True)
    return [{"field": _("erp.company.notes"), "before": _before(row, tool_input.get("note")), "after": ""}]


COMPANY_PROP = {"type": "string", "description": "Company id from erp_companies; leave out for the company this "
                                                 "conversation is filed under."}

TOOLS = [
    {"name": "erp_companies", "label": "Listed companies",
     "description": "List the companies in this ERP that the user can see: id, title, tax ID, branch and whether its "
                    "books are kept in Express, up to 20; search matches the name or tax ID. Use the id with "
                    "erp_company_profile.",
     "input_schema": {"type": "object", "properties": {"search": {"type": "string"}}},
     "handler": "pearnly_erp_portal.masters.cc_tools.companies"},
    {"name": "erp_company_profile", "label": "Read company details",
     "description": "Read one company: tax ID, branch, VAT registration, whether its books are kept in Express, its "
                    "Express account sets (newest first, with fiscal period), the account set of the work month "
                    "(period YYYY-MM) and all team notes (with ids). Without company it reads the company this "
                    "conversation is filed under; for it, conversation_account_set is the one account set every write "
                    "goes into. Times are the user's local time.",
     "input_schema": {"type": "object", "properties": {"company": COMPANY_PROP, "period": {"type": "string"}}},
     "handler": "pearnly_erp_portal.masters.cc_tools.profile"},
    {"name": "erp_company_note_save", "label": "Saved a company note",
     "description": "Add a line to a company's team notes, or change one (give note, its id from erp_company_profile). "
                    "One short sentence per note, up to 500 characters; 50 notes per company. The user sees Allow / "
                    "Deny first.",
     "input_schema": {"type": "object", "properties": {
         "company": COMPANY_PROP, "text": {"type": "string"}, "note": {"type": "string"}}, "required": ["text"]},
     "approval": True,
     "ask": "Save this to the company's notes?",
     "ask_when": {"note": "Update this company note?"},
     "refuse": "pearnly_erp_portal.masters.cc_tools.company_refusal",
     "preview": "pearnly_erp_portal.masters.cc_tools.preview_note_save",
     "handler": "pearnly_erp_portal.masters.cc_tools.note_save"},
    {"name": "erp_company_note_remove", "label": "Removed a company note",
     "description": "Remove one line from a company's team notes (note is its id from erp_company_profile). The user "
                    "sees Allow / Deny first.",
     "input_schema": {"type": "object", "properties": {"company": COMPANY_PROP, "note": {"type": "string"}},
                      "required": ["note"]},
     "approval": True,
     "ask": "Remove this company note?",
     "refuse": "pearnly_erp_portal.masters.cc_tools.company_refusal",
     "preview": "pearnly_erp_portal.masters.cc_tools.preview_note_remove",
     "handler": "pearnly_erp_portal.masters.cc_tools.note_remove"},
]
