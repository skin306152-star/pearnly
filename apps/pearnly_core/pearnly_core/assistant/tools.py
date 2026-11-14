"""智能体工具：声明、汇总与派发（ADR-009 / ADR-019）。

- 每个工具是一条声明 `{"name", "label", "description", "input_schema", "handler"}`，说明和实现放在一处，
  `label` 是对话里那一行步骤显示的英文原文（译文进声明它的 App 的术语表），
  智能体配置（给模型看的工具表）与服务端派发都从这同一份生成，不会两边对不上。
- 共用工具在 `CORE_TOOLS`；各产品在自己 `hooks.py` 声明
  `pearnly_assistant_tools = ["<app>.<module>.TOOLS"]`（指向本产品的声明列表）。
- 改全公司共用设置、定时安排的工具声明 `"approval"`：`True` 每次都要用户在对话里点「允许」才执行；
  也可以是参数名列表，带了其中任一个（即要写）才要（ADR-019，用户 2026-10-10：经授权后代为操作）。
  卡片上的问句是 `"ask"`；问法随参数变时另写 `"ask_when": {参数名: 问句}`，给了该参数就用那句。
  问句下面那一行事实另写 `"note": "<函数路径>"`（函数(参数) → 这一步会动到哪家公司、哪本账套，没有就回空）：只写名字、不写说明句，
  回空就不出那一行（用户 11-14：允许卡照 Claude 桌面应用的权限确认框，问句 → 一行事实 → 内容块 → 拒绝 / 允许）。
  只属于一种会话的工具写 `"modes"`：`["chat"]` 直写会话（例如直写推送）、`["operator"]` 代录会话；不符的调用在服务端拒绝
  （`toolrun._wrong_mode`，11-05 测试机：代录会话里 CC 用了直写推送，两种模式就没区别了）。
  出卡片前要先核对的另写 `"refuse": "<函数路径>"`（函数(参数) → 交回模型的拒绝结果文本或 None）：核出要拒绝的不出卡片、
  不执行工具，直接把那段结果交回模型（`refusal`；2026-10-16：存公司规则时模型说给用户的对方与单据对不上，不该先让人点「允许」）。
- 进行中的说法（对话底部的工作状态行、正在执行的那一步，如「正在识别单据」）另在 `pearnly_assistant_step_names`
  声明 `{工具名: 英文原文}`（共用工具在 `CORE_STEP_NAMES`），译文同样进声明它的 App 的术语表；没声明的显示 `label`。
- 产品可以声明工具执行时的上下文 `pearnly_assistant_tool_context = ["<点路径>"]`（无参函数，回一个 context manager），
  执行工具、出卡片前核对、卡片预览都套在里面（ERP：工具执行期间「当前公司」是对话挂的那家，`masters.company.acting`）。
- 工具一律在本站服务端、以当前登录用户身份执行：权限、租户、套账与数据范围沿用各产品现有服务的
  校验，工具只调已有服务，不绕过它们读库。业务拒绝用 `frappe.throw`，作为 `is_error` 结果交回模型。
"""
import json
from contextlib import ExitStack

import frappe
from frappe import _

from pearnly_core.account import names as person_names
from pearnly_core.assistant import config


def whoami(_tool_input) -> str:
    user = frappe.session.user
    return json.dumps({
        "user": user,
        "full_name": person_names.display_name(user),  # 姓名（共用显示名，用户 10-30）  # person-name: 值就是共用显示名
        "product": config.product(),
        "company": frappe.conf.get("pearnly_company_name") or "",
        "language": frappe.local.lang or "",
    }, ensure_ascii=False)


def tax_id_lookup(tool_input) -> str:
    """税务局公开登记信息（同 ERP 录对方档、pearnly.com 税号工具）：缓存 7 天，查不到给原因，不抛错。"""
    from pearnly_core.revenue_department import service

    result = service.lookup(tool_input.get("tax_id"), tool_input.get("branch") or 0)
    return json.dumps(result, ensure_ascii=False, default=str)


def file_conversation(tool_input) -> str:
    """CC 把「最近」里的这段对话挂到用户刚在提问卡上选的公司（`sessions.file_chosen`，服务端核：只从「最近」、只一次、
    只认用户选的）。之后这段对话的写入只进这家的账套（`write_scope`）。"""
    from pearnly_core.assistant import sessions

    session = str((getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id") or "")
    if not session:
        return json.dumps({"status": "refused", "reason": "no_conversation"})
    return json.dumps(sessions.file_chosen(session, tool_input.get("company")), ensure_ascii=False)


# 改不了这次代录月份的原因（只给模型）
MONTH_NOT_OPERATOR = "not_an_operator_conversation"
MONTH_STARTED = "task_started"
MONTH_NOT_AN_OPTION = "not_an_option"
MONTH_NOT_CHOSEN = "not_chosen_by_user"


def operator_month(tool_input) -> str:
    """CC 把这次代录改到用户刚在提问卡上选的月份（A 线 11-15）：只在代录对话、任务还在准备中；月份要是服务端数出来还有单在等的
    那几个（`products.elsewhere`），且是用户在本对话最近一张提问卡上选的。改法同对话上方的月份（`sessions.move`：任务跟着改）。"""
    from pearnly_core.assistant import ask, sessions, toolrun
    from pearnly_core.express_operator import products, tasks

    session = str((getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id") or "")
    row = frappe.db.get_value(sessions.SESSION, {"session_id": session, "user": frappe.session.user},
                              ["operator_task", "project", "project_scope"], as_dict=True) if session else None
    if not row or not row.get("operator_task"):
        return json.dumps({"status": "refused", "reason": MONTH_NOT_OPERATOR})
    task = tasks.read(row.get("operator_task"))
    if task.get("status") != "preparing":
        return json.dumps({"status": "refused", "reason": MONTH_STARTED})
    month = str((tool_input or {}).get("month") or "").strip()[:7]
    if month not in {item["month"] for item in products.elsewhere(task["company"], task.get("period"))}:
        return json.dumps({"status": "refused", "reason": MONTH_NOT_AN_OPTION})
    if not ask.names_one_of(toolrun.last_answers(session), (month,)):
        return json.dumps({"status": "refused", "reason": MONTH_NOT_CHOSEN})
    moved = sessions.move(session, row.get("project") or "", period=month, scope=row.get("project_scope") or "")
    return json.dumps({"status": "set", "month": moved["period"]})


CORE_TOOLS = [
    {"name": "pearnly_whoami",
     "label": "Checked your account",
     "description": "Return who the signed-in Pearnly user is: user id, full name, product, company and language. "
                    "Call it when you need to address the user or check which product and company you are working in.",
     "input_schema": {"type": "object", "properties": {}},
     "handler": "pearnly_core.assistant.tools.whoami"},
    {"name": "pearnly_tax_id_lookup",
     "label": "Looked up a tax ID",
     "description": "Look up a Thai 13-digit tax ID in the Revenue Department's public register: registered name, "
                    "head office or branch, address, VAT registration date. Use it whenever the user asks who a tax ID "
                    "belongs to or wants a counterparty's official name or address. branch is the branch number "
                    "(0 = head office). error not_vat_registered means the ID exists but is not VAT-registered; "
                    "rd_unreachable means the Revenue Department did not answer, so say so and suggest trying later.",
     "input_schema": {"type": "object", "properties": {
         "tax_id": {"type": "string"}, "branch": {"type": "integer", "minimum": 0}}, "required": ["tax_id"]},
     "handler": "pearnly_core.assistant.tools.tax_id_lookup"},
    {"name": "pearnly_precheck",
     "label": "Checked push choices",
     "description": "Before writing documents into Express or MR.ERP (direct write, hands-on entry, or documents waiting in "
                    "pending), get everything a person must decide first, for one company and its documents: groups "
                    "(book, purchase, sales, stock, service, payment) of items, each with kind, subject, label, state "
                    "(decided = already remembered for this company, do not ask; proposed = a preselected value; open = no "
                    "value yet), value / value_label, source, options (small lists, the recommended one first, with a note "
                    "saying why) or search (accounts / products / parties for big lists) and the documents it holds; plus "
                    "unknown (last write result unknown: the user must check Express first), not_writable (facts, not "
                    "choices), waiting and ready. Read-only: use it to explain what is decided or still open. Never ask these items "
                    "yourself (no ask_user for them): to get the user's decisions call pearnly_guide with the same documents, "
                    "the server puts the questions on a card. planned, when present, maps a document id to how that document will be "
                    "entered if it is written now (only for documents with nothing left to decide): before writing, "
                    "whenever you tell the user cash or credit or which account, take it from planned, never from the "
                    "document type. When the user said in the chat what kind of expense the documents are (e.g. "
                    "stationery), pass it as hint in their own words, with only the documents it is about: it only "
                    "reorders the suggested expense accounts and their reason.",
     "input_schema": {"type": "object", "properties": {
         "company": {"type": "string", "description": "The company id the documents belong to."},
         "doc_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 200},
         "mode": {"type": "string", "enum": ["direct", "operator", "pending"]},
         "hint": {"type": "string", "description": "The expense category the user stated, in their words."}},
         "required": ["company", "doc_ids"]},
     "handler": "pearnly_core.write_precheck.api.tool_check"},
    {"name": "pearnly_guide",
     "label": "Asked before writing",
     "approval": True,
     "prepare": "pearnly_core.assistant.guide_tool.prepare",
     "description": "Before writing documents into Express or MR.ERP, get the user's decisions for them: give the documents "
                    "(and the expense category the user stated, as hint). The server works out every question (payment, "
                    "party, items, warehouse, accounts, whether to enter a document, account set, ...), puts them on one "
                    "card with the recommended choice and the already decided items, and the user answers on the card. "
                    "You never write these questions yourself. The result is {status, answered, writable, held}: "
                    "nothing_to_ask means there is nothing to ask (only writable documents can be written; waiting / not_writable say why the others cannot); answered means the answers are saved and only writable "
                    "documents can be written now; skipped means the user closed the card and nothing was saved. When the "
                    "user answers in a chat message instead (for example 'this supplier is always cash'), save it with "
                    "pearnly_precheck_answer using the keys of that card. Then make the write plan (pearnly_write_plan).",
     "input_schema": {"type": "object", "properties": {
         "company": {"type": "string", "description": "The company id the documents belong to."},
         "doc_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 200},
         "hint": {"type": "string", "description": "The expense category the user stated, in their words."}},
         "required": ["company", "doc_ids"]},
     "handler": "pearnly_core.assistant.ask.unanswered"},
    {"name": "pearnly_precheck_answer",
     "label": "Saved push choices",
     "description": "Save a decision the user gave in a chat message instead of on the pearnly_guide card: answers is "
                    "[{key, value}] with the item key and the option code (or a code the user picked through search). "
                    "Choices are remembered for the company the same way as everywhere else, so the next time they come "
                    "back decided. Items left unanswered keep their documents back this time, proposed ones too (only an "
                    "answered item counts). Returns the card checked again with writable and held "
                    "documents, or errors (nothing saved) when a value is not offered. Saving writes nothing: write the "
                    "documents through the write plan (pearnly_write_plan) afterwards; resume does not write in a "
                    "conversation. An answer with doc_id changes that one document only and is not "
                    "remembered (only for payment: e.g. this supplier is usually on credit but this document was paid "
                    "in cash); the next check lists it as that document's own decided item, which can be changed again "
                    "the same way. Without doc_id a payment is remembered for the supplier (its later documents too).",
     "input_schema": {"type": "object", "properties": {
         "company": {"type": "string"},
         "doc_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 200},
         "answers": {"type": "array", "items": {"type": "object", "properties": {
             "key": {"type": "string"}, "value": {"type": "string"},
             "doc_id": {"type": "string", "description": "Only this document (payment only); not remembered."}},
             "required": ["key", "value"]}},
         "resume": {"type": "boolean"}},
         "required": ["company", "doc_ids", "answers"]},
     "handler": "pearnly_core.write_precheck.api.tool_answer"},
    {"name": "pearnly_write_plan",
     "label": "Made a write plan",
     "description": "Before entering any documents into Express or MR.ERP (direct push or an operator task), make the "
                    "write plan: the server works out which documents will be written now and which are skipped with "
                    "the reason (moved to not entered, already entered, last write result unknown, undecided items, "
                    "waiting for the computer, no_account_set: the company has no account set for it, ledger_needed: "
                    "this conversation's account set is not decided yet, not writable), and into which company and "
                    "account set(s). In an operator "
                    "conversation it always takes this task's whole waiting list (give again for documents already "
                    "entered that the user explicitly wants entered again); in a direct-write conversation give "
                    "documents, the ones the user wants written. Settle the questions first with pearnly_guide. Then say the plan back in one sentence and wait for the user's reply; "
                    "writing starts only with pearnly_write_start after the user answers. A new plan replaces the one "
                    "not yet started.",
     "input_schema": {"type": "object", "properties": {
         "documents": {"type": "array", "items": {"type": "string"}, "maxItems": 200,
                       "description": "Direct write: the documents to write."},
         "again": {"type": "array", "items": {"type": "string"},
                   "description": "Operator: already entered documents the user wants entered again."}}},
     "handler": "pearnly_core.assistant.write_plan.tool_plan"},
    {"name": "pearnly_write_start",
     "label": "Started writing",
     "description": "Start writing by the plan from pearnly_write_plan, only after you said that plan back and the user "
                    "answered in a new message that it can go ahead (e.g. go, yes, start, or in their words). The server "
                    "refuses (not_confirmed) when the user has not sent a message since the plan, (stopped) when the "
                    "user stopped after it, and leaves out documents that changed since (changed). In an operator "
                    "conversation it hands the documents to the computer; in a direct-write conversation push exactly "
                    "the returned documents next.",
     "input_schema": {"type": "object", "properties": {"plan": {"type": "string"}}, "required": ["plan"]},
     "handler": "pearnly_core.assistant.write_plan.tool_start"},
    {"name": "pearnly_file_conversation",
     "label": "Filed the conversation under a company",
     "modes": ["chat"],
     "description": "File this conversation under the company the user just chose, when it is not filed under one yet "
                    "(a write came back with reason no_company). Ask first with ask_user: one question listing the "
                    "user's companies, each option label exactly the company's name (at most 4; Other lets the user "
                    "type another). Then call this with company = that company's id. The server accepts only the "
                    "company the user picked or typed on the latest ask_user card, only once, and only while the "
                    "conversation is not filed: refused reasons are already_filed (a filed conversation never moves "
                    "to another company), not_chosen_by_user (ask again; never choose for the user) and "
                    "company_not_available. After status filed, this conversation writes only to that company's "
                    "account sets: continue with the write the user asked for.",
     "input_schema": {"type": "object", "properties": {
         "company": {"type": "string", "description": "The id of the company the user chose."}},
         "required": ["company"]},
     "handler": "pearnly_core.assistant.tools.file_conversation"},
    {"name": "pearnly_operator_month",
     "label": "Changed the month",
     "modes": ["operator"],
     "description": "Switch this operator task to another month, only after the write plan came back with other_months "
                    "and the user picked one of those months on the latest ask_user card. Call it with month = that "
                    "month (YYYY-MM). Refused reasons: task_started (the task was already handed in, so its month cannot "
                    "change: tell the user), not_an_option (not one of other_months), not_chosen_by_user (ask again; "
                    "never choose for the user). After status set, make the write plan again.",
     "input_schema": {"type": "object", "properties": {
         "month": {"type": "string", "description": "The month the user picked, YYYY-MM."}}, "required": ["month"]},
     "handler": "pearnly_core.assistant.tools.operator_month"},
    {"name": "pearnly_conversation_ledger",
     "label": "Set the conversation's account set",
     "description": "Set the account set this conversation writes to, when it is not decided yet (the company note says "
                    "so, or a write came back with reason ledger_needed) or the user asked here to switch. Ask first "
                    "with ask_user: one question listing the account sets the note or the refusal gives, each option "
                    "label exactly the account set's name (at most 4; Other lets the user type another). Then call "
                    "this with ledger = that account set's name exactly as given. The server accepts only the account set the user picked "
                    "or typed on the latest ask_user card: refused reasons are not_chosen_by_user (ask again; never "
                    "choose for the user), not_an_option and not_available (this conversation has no account set to "
                    "choose: it is not filed under a company, or the product has none). After status set, every write "
                    "in this conversation goes into it: continue with the write the user asked for.",
     "input_schema": {"type": "object", "properties": {
         "ledger": {"type": "string", "description": "The name of the account set the user chose."}},
         "required": ["ledger"]},
     "handler": "pearnly_core.assistant.ledger.tool_choose"},
    {"name": "ask_user",
     "label": "Asked questions",
     "approval": True,
     "refuse": "pearnly_core.assistant.guide_tool.refuse_ask",
     "description": "Ask the user 1-4 questions in one card and wait for the answers, for decisions outside writing "
                    "documents (questions about writing documents — payment, party, items, accounts, warehouse, account set, "
                    "whether to enter a document — come only from pearnly_guide and are refused here). "
                    "Write the question, header, labels and descriptions in the user's language; names from the data "
                    "keep their own spelling (account sets, companies, accounts), and reasons a tool gave in English are "
                    "retold in the user's language. Each question: "
                    "question (full sentence ending with ?), "
                    "header (at most 12 characters), multiSelect, options 2-4 each with label and description; the card "
                    "adds Other by itself. For big lists set search {company, doc_id, kind: accounts|products|parties} so "
                    "Other can search them. show_if {question, labels} asks a question only when an earlier question (its "
                    "number, from 1) got one of those labels; otherwise it is hidden and comes back as not_asked. The "
                    "result is {answers: [{header, question, labels, other?, skipped?, not_asked?}]} in "
                    "question order; skipped means the user left it undecided, so documents that depend on it are not "
                    "written this time; not_asked means it did not apply, so save nothing for it.",
     "input_schema": {"type": "object", "properties": {
         "questions": {"type": "array", "minItems": 1, "maxItems": 4, "items": {"type": "object", "properties": {
             "question": {"type": "string"},
             "header": {"type": "string", "maxLength": 12},
             "multiSelect": {"type": "boolean"},
             "options": {"type": "array", "minItems": 2, "maxItems": 4, "items": {"type": "object", "properties": {
                 "label": {"type": "string"}, "description": {"type": "string"}}, "required": ["label"]}},
             "search": {"type": "object", "properties": {
                 "company": {"type": "string"}, "doc_id": {"type": "string"},
                 "kind": {"type": "string", "enum": ["accounts", "products", "parties"]}}},
             "show_if": {"type": "object", "properties": {
                 "question": {"type": "integer", "minimum": 1},
                 "labels": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
                 "required": ["question", "labels"]}},
             "required": ["question", "header", "options"]}}},
         "required": ["questions"]},
     "handler": "pearnly_core.assistant.ask.unanswered"},
]


# 共用工具进行中的说法（见文件头）
CORE_STEP_NAMES = {
    "pearnly_whoami": "Checking your account",
    "pearnly_tax_id_lookup": "Looking up a tax ID",
    "pearnly_precheck": "Checking push choices",
    "pearnly_precheck_answer": "Saving push choices",
    "ask_user": "Waiting for answers",
    "pearnly_guide": "Waiting for answers",
    "pearnly_file_conversation": "Filing the conversation under a company",
    "pearnly_conversation_ledger": "Setting the conversation's account set",
    "pearnly_operator_month": "Changing the month",
    "pearnly_write_plan": "Making a write plan",
    "pearnly_write_start": "Starting to write",
}


def _context() -> ExitStack:
    """产品声明的执行上下文（见文件头）叠在一起；没声明就是空的。"""
    stack = ExitStack()
    for path in frappe.get_hooks("pearnly_assistant_tool_context") or []:
        stack.enter_context(frappe.get_attr(path)())
    return stack


def declared() -> list[dict]:
    found = {tool["name"]: tool for tool in CORE_TOOLS}
    for path in frappe.get_hooks("pearnly_assistant_tools") or []:
        for tool in frappe.get_attr(path) or []:
            found[tool["name"]] = tool
    return [found[name] for name in sorted(found)]


def labels() -> dict[str, str]:
    """对话里步骤行显示的名字（按当前用户语言）；没写 label 的工具显示原名。"""
    return {t["name"]: _(t["label"]) if t.get("label") else t["name"] for t in declared()}


def step_names() -> dict[str, str]:
    """各工具进行中的说法（英文原文）：共用的加上各产品 `pearnly_assistant_step_names` 声明的，只留已声明的工具。"""
    found = dict(CORE_STEP_NAMES)
    for path in frappe.get_hooks("pearnly_assistant_step_names") or []:
        found.update(frappe.get_attr(path) or {})
    names = {t["name"] for t in declared()}
    return {name: text for name, text in found.items() if name in names and text}


def steps() -> dict[str, dict[str, str]]:
    """对话页的步骤名（按当前用户语言）：`done` 做完的说法（同 `labels`），`running` 进行中的说法，`ask` 要点「允许」的工具
    卡片上那句问话（`ask`，没写用步骤名）——点了「拒绝」的那一步照它写成没做的样子，不写成做完的说法（第 8 轮测试机：
    拒绝存公司规则后，步骤仍写「记下了公司选择」）；`gate` 是带了哪些参数才要「允许」的工具（`approval` 是参数名列表，
    同 `needs_approval`）：重开对话时页面据此认出哪一步出过卡片，点完的卡片留在原处（用户 10-17，照 Claude）；`ask_when` 是问法随参数
    变的工具 {参数: 问句}（按声明顺序，同 `approval_view`）：重开对话时卡片上的问话与当场一样。"""
    return {"done": labels(), "running": {name: _(text) for name, text in step_names().items()},
            "ask": {t["name"]: _(t.get("ask") or t.get("label") or t["name"]) for t in declared() if t.get("approval")},
            "gate": {t["name"]: list(t["approval"]) for t in declared() if isinstance(t.get("approval"), (list, tuple))},
            "ask_when": {t["name"]: {key: _(text) for key, text in t["ask_when"].items()}
                         for t in declared() if t.get("approval") and t.get("ask_when")}}


def _given(values: dict, key: str) -> bool:
    """这次调用给了这个参数（False / 0 也算给了；空串、空表不算）。"""
    return values.get(key) not in (None, "", {}, [])


def mode_allowed(name: str, mode: str) -> bool:
    """这个工具能不能在这种会话里用（工具声明 ``"modes": ["chat"]`` 只在直写会话、``["operator"]`` 只在代录会话；没声明的两种都行）。"""
    tool = next((t for t in declared() if t["name"] == name), None)
    modes = (tool or {}).get("modes")
    return not modes or mode in modes


def needs_approval(name: str, tool_input) -> bool:
    tool = next((t for t in declared() if t["name"] == str(name or "")), None)
    gate = (tool or {}).get("approval")
    if isinstance(gate, (list, tuple)):
        values = tool_input if isinstance(tool_input, dict) else {}
        return any(_given(values, key) for key in gate)
    return bool(gate)


# 卡片只显示前几行，其余合成一行「还有 N 项」（对话页 approval-bar，卡片规矩：4–5 项、卡内不滚动，用户 2026-10-13）：
# 这里照实给全，N 才数得对；上限只挡异常大的列表。
PREVIEW_ROWS = 100


def _note(tool: dict, values: dict) -> str:
    """卡片问句下面那一行事实（工具声明的 `note`）：会动到哪家公司、哪本账套。没声明、算不出或回空都是空串（那一行不出）。"""
    if not tool.get("note"):
        return ""
    try:
        with _context():
            text = frappe.get_attr(tool["note"])(values)
    except Exception:
        frappe.db.rollback()
        frappe.clear_messages()
        frappe.log_error(title="Assistant approval note failed")
        return ""
    return str(text or "").strip()


def approval_view(name: str, tool_input) -> dict:
    """等「允许」时卡片上显示的：这一步要做什么（`ask_when` 里给了参数的那句，否则 `ask`，没写就用步骤名）、一行事实（`note`，
    会动到哪家公司、哪本账套；没有就不给）和要改哪几项（`preview`：[{field, before, after}]，由工具的 `preview` 按当前设置现算；
    没写或算不出就列参数）。不写任何东西。"""
    tool = next((t for t in declared() if t["name"] == str(name or "")), None) or {}
    values = tool_input if isinstance(tool_input, dict) else {}
    rows = None
    if tool.get("preview"):
        try:
            with _context():
                rows = frappe.get_attr(tool["preview"])(values)
        except Exception:
            frappe.db.rollback()
            frappe.clear_messages()
            frappe.log_error(title="Assistant approval preview failed")
    if rows is None:
        rows = [{"field": key, "before": "", "after": value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}
                for key, value in values.items()]
    # 问句跟着这次改的内容走：`ask_when` 是 {参数: 问句}，给了哪个参数就问哪句（按声明顺序取第一个），
    # 都没给才用 `ask`。例：定时任务只改提醒时刻，不该问「启用还是停用」（2026-10-13）。
    ask = next((text for key, text in (tool.get("ask_when") or {}).items() if _given(values, key)), None)
    ask = ask or tool.get("ask") or tool.get("label") or str(name or "")
    note = _note(tool, values)
    return {"ask": _(ask), **({"note": note} if note else {}), "preview": rows[:PREVIEW_ROWS]}


def refusal(name: str, tool_input) -> str | None:
    """要「允许」的工具出卡片前的核对（工具声明的 `refuse`）：要拒绝回交给模型的结果文本（按出错交回），否则 None。
    只读、不写；核对本身出错按不拒绝处理（照常出卡片，工具执行时自己会再核一遍）。"""
    tool = next((t for t in declared() if t["name"] == str(name or "")), None) or {}
    if not tool.get("refuse"):
        return None
    try:
        with _context():
            text = frappe.get_attr(tool["refuse"])(tool_input if isinstance(tool_input, dict) else {})
    except Exception:
        frappe.db.rollback()
        frappe.clear_messages()
        frappe.log_error(title="Assistant approval check failed")
        return None
    return str(text) if text else None


def prepares(name: str) -> bool:
    """这个工具声明了出卡前准备（``prepare``）。"""
    return bool((next((t for t in declared() if t["name"] == str(name or "")), None) or {}).get("prepare"))


def prepare(name: str, tool_input) -> dict | None:
    """要「允许」的工具出卡前由服务端准备输入（工具声明的 ``prepare``，引导卡 ``pearnly_guide``）：回 ``{"input": 卡}`` 换掉模型给的输入、
    ``{"result": 文本}`` 不出卡直接交回结果；没声明回 None。出错照抛（调用方按出错交回模型）。"""
    tool = next((t for t in declared() if t["name"] == str(name or "")), None) or {}
    if not tool.get("prepare"):
        return None
    with _context():
        found = frappe.get_attr(tool["prepare"])(tool_input if isinstance(tool_input, dict) else {})
    return found if isinstance(found, dict) else None


REFUSED = ("not_saved", "not_started", "not_exported", "failed")

# 出卡片前就核出要拒绝、没问过用户的结果上带的标记（`not_asked`）：重开对话时对话页据此把它画成步骤组里没做成的一步，
# 而点了「允许」后没做成的（同样是 not_saved / failed）画成卡片「允许 · 失败」，两种不再分不清（第 10 轮审查）。
NOT_ASKED = {"approval": "not_asked"}


def not_asked(text: str) -> str:
    """出卡片前核出的拒绝结果带上 `NOT_ASKED`：结果是 JSON 对象就并进去；不是就包成 {status: failed, reason: 原文}。"""
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        data = {"status": "failed", "reason": text}
    return json.dumps({**data, **NOT_ASKED}, ensure_ascii=False)


def _refused(text: str) -> bool:
    """业务上没办成（工具回 status not_saved 等）也按出错交回：对话里那一步显示失败，不只靠模型转述。"""
    try:
        data = json.loads(text)
    except ValueError:
        return False
    return isinstance(data, dict) and data.get("status") in REFUSED


def run(name: str, tool_input) -> tuple[str, bool]:
    tool = next((t for t in declared() if t["name"] == str(name or "")), None)
    if not tool:
        return _("assistant.tool.unavailable"), True
    try:
        with _context():
            text = str(frappe.get_attr(tool["handler"])(tool_input if isinstance(tool_input, dict) else {}))
        return text, _refused(text)
    except (frappe.ValidationError, frappe.PermissionError) as err:
        frappe.db.rollback()
        return str(err) or _("assistant.tool.refused"), True
