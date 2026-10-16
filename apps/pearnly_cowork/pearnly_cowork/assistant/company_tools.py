"""CC 的公司主档工具（ADR-020）：列出公司、读一家公司的资料、记 / 删知识库、存 / 删公司规则（第 2 期统一客户规则）。

只调 `companies` 的公开入口，权限、归属由它们判。知识库与公司规则的写都要用户在对话里点「允许」（`approval`），卡片上先给
改前、改后；CC 写入的记 `origin = cc` / 规则的对话（只作审计）。缺省的公司取本对话挂的那家（`projects.of_session`）。
公司规则的归类键由服务端按单据算（`_subject_of`，同推送的 `push.express.choices`），也可传资料里一行的 key 做提升。
单独成模块（不并进 `tools.py`）：两边各自登记在 hooks `pearnly_assistant_tools` / `pearnly_assistant_guidance`。
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import frappe
from frappe import _

from pearnly_core.assistant import projects
from pearnly_cowork.access import scope
from pearnly_cowork.companies import choices as saved_choices
from pearnly_cowork.companies import identity, notes, rules, service
from pearnly_cowork.push.express import choices as choice_rules

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
    "When a client choice (<client_choices>) or a team note decided how you handled something, say which one in a few "
    "words.",
    "A choice the user wants for the whole company (or one branch) rather than one account set goes in with "
    "cowork_company_choice_save; a choice said for this document only is saved with cowork_save_push_choices as usual.",
    # 数字会计员第 3 期第 13 条：档位只由数据升降，没有改档位的工具；为什么没自动推按票上的原因码照实说
    "Automatic pushing for a company (automation in cowork_company_profile: L0 propose only, L1 by rules, L2 whole "
    "month) moves only by data (qualified month readbacks, write checks); there is no tool or setting to change it, so "
    "never offer to. When asked why a document was not pushed automatically, answer from automation.reasons in "
    "cowork_document_detail as they are, in plain words.",
    "In a conversation filed under a company, cowork_list_documents, cowork_push_results and cowork_folder_tasks "
    "cover only that company's account sets (all its years, or only the work branch); scope in the result names the "
    "company. Answer for that company only and never list other companies' documents or tasks there. Pass "
    "all_companies true only when the user asks about all companies or other companies (" + "ทุกเจ้า, เจ้าอื่น" + "); "
    "for one other company pass its company id.",
]


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


# 主档给页面的时刻是 UTC（`service.when`：`2026-10-15T14:08:00Z`，页面按设备时区显示）；交给 CC 前换成站点时区并带上偏移
# （`2026-10-15T21:08:00+07:00`）。测试窗口第 3 轮：CC 把记住选择的 14:08Z 当曼谷时间说成「14:08 记下的」。
PROFILE_TIMES = (("company", "created_at"), ("notes", "updated_at"), ("choices", "set_at"), ("drafts", "updated_at"),
                 ("automation", "since"))


def local_iso(value) -> str:
    """带时区的 ISO 时刻（含 ``Z``）→ 站点时区、带偏移的 ISO（到秒）；只有日期的、空的、读不出的、没带时区的原样回。"""
    text = str(value or "").strip()
    if len(text) <= 10:
        return value
    try:
        moment = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return value
    if moment.tzinfo is None:
        return value
    return moment.astimezone(ZoneInfo(frappe.utils.get_system_timezone())).isoformat(timespec="seconds")


def _local_profile(found) -> dict:
    for key, field in PROFILE_TIMES:
        block = found.get(key)
        for item in ([block] if isinstance(block, dict) else block or ()):
            if isinstance(item, dict) and item.get(field):
                item[field] = local_iso(item[field])
    return found


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
        # key 只留给能「设为全公司」的行与公司规则（cowork_company_choice_save / _remove 用），其余去掉免得结果太长
        found["choices"] = [{key: value for key, value in item.items()
                             if key != "key" or item.get("promotable") or item.get("source") == "rule"}
                            for item in found["choices"][:CHOICES_MAX]]
    if found.get("choices") is not None:
        found["rules"] = [{**item, "key": saved_choices.encode("rule", item["id"]), "set_at": service.when(item["set_at"])}
                          for item in rules.listing(row.name, CHOICES_MAX)]
    if filed == row.name and filed_scope:
        found["work_branch"] = filed_scope
    # 自动推送的档位（第 3 期第 13 条）：档位、自何时、原因码；有录入权限时带近两期统计（`automation.view`）
    from pearnly_cowork.companies import automation  # 档位要读报税期间（`tax.periods`），只在读资料时加载

    found["automation"] = automation.view(row.name)
    return _json(_local_profile(found))


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


# ---------------- 公司规则（第 2 期） ----------------

RULE_KINDS = ("party", "item", "account", "payment", "vat", "department")


def _subject_of(tool_input) -> tuple:
    """按单据算规则的归类键（同推送 `push.express.choices` 的键）→ (kind, subject, 写法)；认不出抛 choice_invalid。
    付款、抵进项、费用科目按卖方（进项）；对方档按方向（进项卖方 / 销项买方，带分店）；存货要给票面品名 ``item``；部门整份一个。"""
    from pearnly_cowork.contracts.history import primary_page_fields
    from pearnly_cowork.history import storage

    kind = str(tool_input.get("kind") or "")
    if kind not in RULE_KINDS:
        frappe.throw(_("cowork.company.choice_unsupported"))
    if kind == "department":
        return kind, "*", ""
    doc = storage.read(str(tool_input.get("document") or ""))
    fields = primary_page_fields(storage.project(doc).get("pages") or [])
    direction = "sales" if tool_input.get("direction") == "sales" and kind in ("party", "item") else "purchase"
    side = "buyer" if direction == "sales" else "seller"
    # 同推送 `choices._party` 的取法（只认 ``{side}_tax``），否则按税号存的规则推送时按名字找、永远对不上
    name, tax = fields.get(f"{side}_name") or "", fields.get(f"{side}_tax") or ""
    if not str(name).strip() and not choice_rules.digits(tax):
        frappe.throw(_("cowork.company.choice_unsupported"))
    if kind == "party":
        return kind, choice_rules.party_subject(direction, tax, fields.get(f"{side}_branch") or "", name), name
    if kind == "item":
        item = str(tool_input.get("item") or "").strip()[:50]
        if not item:
            frappe.throw(_("cowork.company.choice_unsupported"))
        return kind, choice_rules.item_subject(direction, tax, name, item), item
    return kind, choice_rules.supplier_subject("purchase", tax, name), name


def _period(row, tool_input) -> str:
    filed, filed_period, _scope = _filed()
    return str(tool_input.get("period") or (filed_period if filed == row.name else "") or "")


def choice_save(tool_input) -> str:
    service.require_write()
    row = _company(tool_input)
    if not row:
        return _no_company()
    sets = service.books(row, limit=None)
    branch, period = str(tool_input.get("branch") or ""), _period(row, tool_input)
    if tool_input.get("key"):
        saved = saved_choices.promote(row, sets, str(tool_input["key"]), branch, period, session=_session())
    else:
        kind, subject, label = _subject_of(tool_input)
        saved = saved_choices.save_rule(row, sets, kind, subject, str(tool_input.get("value") or ""), branch, period,
                                        subject_label=label, session=_session(),
                                        evidence={"from": "cc", "document": str(tool_input.get("document") or "")})
    service.announce("upsert", row.name, part="choices")
    rule = saved["rule"]
    return _json({"status": "saved", "company": row.title, "rule": {
        "key": saved["key"], "kind": rule["kind"], "subject_label": rule["subject_label"], "value": rule["value"],
        "value_label": rule["value_label"], "branch": rule["branch"] or "whole company",
        "removed_account_set_choices": rule.get("removed_memories", 0)}})


def choice_remove(tool_input) -> str:
    service.require_write()
    row = _company(tool_input)
    if not row:
        return _no_company()
    key = str(tool_input.get("key") or "")
    if saved_choices.decode(key)[0] != "rule":
        frappe.throw(_("cowork.company.choice_not_found"), frappe.DoesNotExistError)
    removed = saved_choices.forget(row, service.books(row, limit=None), key)
    service.announce("upsert", row.name, part="choices")
    return _json({"status": "removed", "company": row.title, "removed": removed.get("removed", 0)})


def _scope_text(branch) -> str:
    return _("cowork.company.branch_n").format(branch) if branch else _("cowork.company.whole_company")


def _described(row, tool_input) -> tuple:
    """卡片上这条规则的「对象 → 值」与范围（只读、不写库；认不出的照参数显示）。按 key 找时不经列表的每类截断
    （`saved_choices.find`）；找不到（已删、不是这家的）回 None 当范围，卡上不露编码后的 key，执行时报找不到。"""
    key = str(tool_input.get("key") or "")
    if key and row:
        parts = saved_choices.decode(key)
        item = saved_choices.find(row, service.books(row, limit=None), key)
        if item:
            value = item.get("value_label") or item.get("value") or ""
            return f"{item.get('subject_label') or ''} → {value}", item.get("branch") or "", parts[0]
        return "", None, parts[0]
    value = str(tool_input.get("value") or "")
    label = {"cash": "Cash", "credit": "Credit", "claim": "Claim VAT", "expense": "Expense it"}.get(value)
    try:
        subject = _subject_of(tool_input)[2]
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError):
        frappe.clear_messages()
        subject = ""
    return " → ".join(part for part in (subject, _(label) if label else value) if part), "", "document"


def _choice_rows(row, text, branch) -> list:
    """存 / 删公司规则的卡片行：公司、范围、这一条（对象 → 值），只列一次、不写「改前 → 改后」。``branch`` 为 None
    （按 key 找不到这一条）时不列范围，值那一行写「这条记住的选择已不在」，不像要删全公司的一条空规则。"""
    company = {"field": _("Company"), "before": "", "after": row.title if row else ""}
    if branch is None:
        return [company, {"field": _("Value"), "before": "", "after": _("cowork.company.choice_not_found")}]
    return [company, {"field": _("Scope"), "before": "", "after": _scope_text(branch)},
            {"field": _("Value"), "before": "", "after": text}]


def preview_choice_save(tool_input) -> list:
    row = _company(tool_input)
    text, branch, _source = _described(row, tool_input)
    return _choice_rows(row, text, None if branch is None else str(tool_input.get("branch") or ""))


def preview_choice_remove(tool_input) -> list:
    """删哪一条（第 5 轮测试 5）：同保存卡的三行，问句「删除这条公司规则？」已说明是删。原来公司写成「改前 → 改后」两遍、
    这一条写在「改前」后面跟一个空的「→」，卡上看着乱。找不到这一条（已删、不是这家的）时只写公司与「已不在」，执行时报找不到。"""
    row = _company(tool_input)
    text, branch, _source = _described(row, tool_input)
    return _choice_rows(row, text, branch)


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
                    "computer with its state and month folder, all team notes (with ids), remembered push choices "
                    "(account set choices, choices learned from Express history and company choices), rules (the "
                    "company choices with their keys), scheduled tasks and tax drafts. Without company it reads the "
                    "company this conversation is filed under; work_branch is the branch this conversation works on. "
                    "Times (set_at, updated_at, created_at) are the user's local time with its UTC offset: say them as "
                    "local time. Describe where a remembered choice or rule came from in the user's own words, never "
                    "by its source code.",
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
    {"name": "cowork_company_choice_save", "label": "Saved a company choice",
     "description": "Save a push choice for a whole client company (or one branch with branch, its 5-digit number), "
                    "applied automatically to all its account sets; a choice saved in one account set still overrides "
                    "it there, and saving this removes the same choice saved in its account sets. Either pass key, a "
                    "row from cowork_company_profile choices marked promotable (an account set choice or one learned "
                    "from Express history, which only goes company-wide, without branch), to make it company-wide; "
                    "or pass document, kind (party, item, account, "
                    "payment, vat, department) and value (an account, product or party code from cowork_push_choices, "
                    "or cash / credit, claim / expense), with item (the product name as on the document) for item "
                    "and direction sales for a customer. The value is checked against the account set covering the "
                    "work month. Only what the user asked for; the user sees Allow / Deny first.",
     "input_schema": {"type": "object", "properties": {
         "company": COMPANY_PROP, "key": {"type": "string"}, "document": {"type": "string"},
         "kind": {"type": "string", "enum": list(RULE_KINDS)}, "value": {"type": "string"},
         "item": {"type": "string"}, "direction": {"type": "string", "enum": ["purchase", "sales"]},
         "branch": {"type": "string"}, "period": {"type": "string"}}},
     "approval": True,
     "ask": "Save this choice for the company?",
     "preview": "pearnly_cowork.assistant.company_tools.preview_choice_save",
     "handler": "pearnly_cowork.assistant.company_tools.choice_save"},
    {"name": "cowork_company_choice_remove", "label": "Removed a company choice",
     "description": "Remove one company choice (key from cowork_company_profile rules). Pushes then use the account set's "
                    "own choice, or ask again. The user sees Allow / Deny first.",
     "input_schema": {"type": "object", "properties": {"company": COMPANY_PROP, "key": {"type": "string"}},
                      "required": ["key"]},
     "approval": True,
     "ask": "Remove this company choice?",
     "preview": "pearnly_cowork.assistant.company_tools.preview_choice_remove",
     "handler": "pearnly_cowork.assistant.company_tools.choice_remove"},
]
