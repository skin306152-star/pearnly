"""ERP 版 CC 对话里录收款（收款第 4 步，评估 docs/work/2026-11-11-collection-receipt-assessment.md §12-1、审定结论①②）。

工具只调收款的服务函数（`collection.service`，LINE 收款同一组）：找客户、未收清发票、收款方式、建 / 改草稿（回确认卡）、补转账凭证、
放行、查结果、本人最近的收款、撤回。权限、公司、账套、金额规则、预留、版本都在服务函数里判，这里不另写规则。

- **公司**：本对话挂的那家（`masters.cc_tools.filed`）；没挂公司回 ``no_company``（同写入计划，先问录到哪家、挂上）。
- **放行走写入计划**（`pearnly_core.assistant.write_plan`，与单据同一套）：草稿规则全过时结果带 ``document``（``R:<记录>@<版本>``，
  `collection.plan`）→ CC 调 ``pearnly_write_plan`` 把它放进计划（服务端按确认卡再判一遍）、一句话复述（公司·账套·客户·各张发票收
  多少·方式·折扣·合计）、等用户下一句 → ``pearnly_write_start`` → :func:`push`：只放已开始计划里的那张（`direct_push.permitted`），
  按单号里的版本放（`service.release`，同意 = 本对话的用户消息序号）。复述后草稿又改过 → 计划不写、放行回 ``changed``。
- **只在直写对话**（用户 11-11：代录不做收款）：每个工具只声明给直写会话（modes chat），代录会话给模型的工具表里没有它们
  （共用层 `tools.mode_allowed`），各模式只用自己的工具。
- **凭证**：对话里的附件（`assistant.files.attachment`）存成私有凭证（`service.store_voucher`），挂到草稿的那种转账上。
- 业务拒绝回 ``not_saved`` 与原因码（``reason``）、给人看的一句（``message``，已翻译）和数（``detail``），不抛给模型。
"""
import json
import time

import frappe
from frappe import _

from pearnly_core.assistant import direct_push
from pearnly_erp_portal.collection import contracts, plan, service, texts

LIST_MAX = 20
CUSTOMERS_MAX = 50
NO_COMPANY = "no_company"
NOT_PERMITTED = "not_permitted"
NOT_IN_CONVERSATION = "not_in_conversation"
# 放行之后的结果（同直写推送的词：entered / queued / failed）
ENTERED, QUEUED, FAILED, WITHDRAWN = "entered", "queued", "failed", "withdrawn"
RESULT = {contracts.WRITTEN: ENTERED, contracts.QUEUED: QUEUED, contracts.WRITING: QUEUED, contracts.FAILED: FAILED,
          contracts.WITHDRAWN: WITHDRAWN}

# 给模型的说明（不经 _()）
NO_COMPANY_NOTE = ("This conversation is not filed under a company. Ask the user with ask_user which company the "
                   "collection is for, file this conversation under it (pearnly_file_conversation), then go on.")
READY_NOTE = ("Ready to record. Next call pearnly_write_plan with documents [document]; then say back in one sentence the "
              "company, account set (book), customer, each invoice and how much is collected on it, the methods with "
              "amounts, the discount and the total, and ask whether to record it. Only after the user agrees in a new "
              "message call pearnly_write_start, then erp_collection_push with this document. If the user changes "
              "anything, change the draft with erp_collection_draft (collection and version) and say it back again.")
NOT_READY_NOTE = ("Not ready to record yet: problem says what is missing (voucher_missing: ask the user to attach a photo "
                  "of the transfer slip in this conversation, then erp_collection_voucher). Tell the user in plain words.")
CHANGED_NOTE = ("The collection changed after it was said back, so it was not recorded: show it again with "
                "erp_collection_card, say it back again and wait for the user's reply.")
EXPRESS_OPEN_NOTE = (" Express has this account set open on the computer that writes it: it is written automatically once "
                     "Express is closed. Tell the user that.")
LEDGER_NEEDED_NOTE = ("ledger_needed: this conversation's account set is not decided yet, so nothing was saved. Ask the "
                      "user once with ask_user which account set to record collections in (one question, each option "
                      "label exactly the label of one of allowed_account_sets), set it with pearnly_conversation_ledger "
                      "(ledger = its id), then go on; do not ask again after that.")
QUEUED_NOTE = ("queued: sent, but Express has not reported a result yet (CC Desktop on the computer that writes it may "
               "be offline: it is recorded once CC Desktop is back). Tell the user it is queued; check later with "
               "erp_collection_status when they ask. Do not wait. A queued collection is not in Express yet.")


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _session() -> str:
    return str((getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id") or "")


def _company() -> str:
    from pearnly_erp_portal.masters import cc_tools

    return cc_tools.filed()


def _refused(error) -> dict:
    frappe.db.rollback()
    frappe.clear_messages()
    if isinstance(error, contracts.CollectionError) and error.code == service.LEDGER_NEEDED:
        # 本对话账套未定（A 线 11-12）：形状同单据推送的 ``ledger_needed``（`express.cc_tools._ledger_refusal`）
        return {"status": "not_saved", "reason": error.code, "note": LEDGER_NEEDED_NOTE,
                "allowed_account_sets": [{"id": item["code"], "label": item["name"]}
                                         for item in (error.detail or {}).get("options") or ()]}
    if isinstance(error, contracts.CollectionError):
        return {"status": "not_saved", "reason": error.code, "message": texts.explain(error),
                **({"detail": error.detail} if error.detail else {})}
    if isinstance(error, frappe.PermissionError):
        return {"status": "not_saved", "reason": NOT_PERMITTED}
    return {"status": "not_saved", "reason": str(error)[:300]}


def _call(fn, *args, **kwargs) -> dict:
    """调收款的服务函数；规则不过、没权限回 not_saved 与原因，不抛给模型。"""
    try:
        result = fn(*args, **kwargs)
        frappe.db.commit()
        return {"result": result}
    except (contracts.CollectionError, frappe.ValidationError, frappe.PermissionError) as error:
        return _refused(error)


def _in_company(run) -> str:
    company = _company()
    if not company:
        return _json({"status": "not_saved", "reason": NO_COMPANY, "note": NO_COMPANY_NOTE})
    return _json(run(company))


def _card(card) -> dict:
    """确认卡 → 工具结果：规则全过的草稿带写入计划的单号与下一步。"""
    out = dict(card)
    if card.get("status") == contracts.DRAFT and card.get("ready"):
        return {**out, "document": plan.doc_id(card["name"], card["version"]), "note": READY_NOTE}
    if card.get("status") == contracts.DRAFT:
        out["note"] = NOT_READY_NOTE
    return out


# ── 查 ───────────────────────────────────────────────────────────────────────


def customers(tool_input) -> str:
    limit = max(1, min(int(tool_input.get("limit") or 12), CUSTOMERS_MAX))
    offset = max(0, int(tool_input.get("offset") or 0))

    def run(company):
        found = _call(service.customers, company, tool_input.get("search") or "", offset, limit)
        if "result" not in found:
            return found
        items = found["result"]["items"]
        return {"total": found["result"]["total"], "customers": items,
                "next_offset": offset + len(items) if len(items) == limit else None}

    return _in_company(run)


def invoices(tool_input) -> str:
    customer = str(tool_input.get("customer") or "").strip()

    def run(company):
        found = _call(service.open_invoices, company, customer)
        if "result" not in found:
            return found
        data = found["result"]
        out = {"customer": customer, "invoices": data["items"], "refresh": data["refresh"]}
        if data["refresh"] == "reading":
            out["note"] = ("CC Desktop is reading this customer's invoices again now; these are from the last read. "
                           "Call again in a moment for the latest.")
        return out

    return _in_company(run)


def methods(_tool_input) -> str:
    def run(company):
        found = _call(service.methods, company)
        return found.get("result", found)

    return _in_company(run)


def card(tool_input) -> str:
    found = _call(service.card, str(tool_input.get("collection") or ""))
    return _json(_card(found["result"]) if "result" in found else found)


def status(tool_input) -> str:
    found = _call(service.status, str(tool_input.get("collection") or ""))
    if "result" not in found:
        return _json(found)
    view = found["result"]
    return _json({**view, "result": RESULT.get(view["status"], view["status"])})


def recent(tool_input) -> str:
    limit = max(1, min(int(tool_input.get("limit") or 10), LIST_MAX))
    offset = max(0, int(tool_input.get("offset") or 0))
    found = _call(service.mine, offset, limit)
    if "result" not in found:
        return _json(found)
    items = found["result"]["items"]
    return _json({"total": found["result"]["total"], "collections": items,
                  "next_offset": offset + len(items) if len(items) == limit else None})


# ── 草稿与凭证 ───────────────────────────────────────────────────────────────


def _voucher(file_ref) -> str:
    """对话里的附件 → 私有凭证文件名（`service.store_voucher`）。"""
    from pearnly_core.assistant import files

    filename, content = files.attachment(file_ref)
    return service.store_voucher(filename, content)["file"]


def _methods(rows) -> list:
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            frappe.throw(_("Invalid request"))
        item = {"prefix": str(row.get("prefix") or ""), "amount": row.get("amount")}
        if row.get("file"):
            item["voucher"] = _voucher(row["file"])
        out.append(item)
    return out


def draft(tool_input) -> str:
    name = str(tool_input.get("collection") or "")
    rows = [row if isinstance(row, dict) else {"docnum": row} for row in tool_input.get("invoices") or []]

    def make(company):
        if name:
            return service.update_draft(name, tool_input.get("version"), rows, cash=tool_input.get("cash") or 0,
                                        methods=_methods(tool_input.get("methods")), amount=tool_input.get("amount"),
                                        salesman=tool_input.get("salesman"), department=tool_input.get("department"))
        return service.create_draft(company, str(tool_input.get("customer") or ""), rows, cash=tool_input.get("cash") or 0,
                                    methods=_methods(tool_input.get("methods")), amount=tool_input.get("amount"),
                                    doc_date=tool_input.get("date") or None, source="cc",
                                    salesman=tool_input.get("salesman"), department=tool_input.get("department"))

    def run(company):
        found = _call(make, company)
        return _card(found["result"]) if "result" in found else found

    return _in_company(run)


def voucher(tool_input) -> str:
    def attach():
        return service.set_voucher(str(tool_input.get("collection") or ""), tool_input.get("version"),
                                   str(tool_input.get("prefix") or ""), _voucher(tool_input.get("file")))

    found = _call(attach)
    return _json(_card(found["result"]) if "result" in found else found)


# ── 放行、撤回 ───────────────────────────────────────────────────────────────


def _settle(name) -> dict:
    """放行后等写入结论（同直写推送的时限，只在能等的地方等）：写成带 RE 号、失败带原因、到点仍在排队回 queued。"""
    frappe.db.commit()
    deadline = time.monotonic() + direct_push.PUSH_WAIT_SECONDS
    view = service.status(name)
    while RESULT.get(view["status"]) == QUEUED and direct_push.may_wait() and time.monotonic() < deadline:
        time.sleep(max(0.0, min(direct_push.PUSH_WAIT_STEP, deadline - time.monotonic())))
        frappe.db.rollback()  # 回执在别的请求里提交：换一份新快照再读
        view = service.status(name)
    return view


def push(tool_input) -> str:
    """放行一笔收款：只认用户同意了的写入计划里的那张（单号带着复述时的版本）。"""
    from pearnly_core.assistant import sessions

    document = str(tool_input.get("document") or "")
    name, version = plan.parse(document)
    if not name:
        return _json({"document": document, "status": "not_found"})
    session_id = _session()
    if not session_id:
        return _json({"document": document, "status": "not_pushed", "reason": NOT_IN_CONVERSATION})
    allowed, refused = direct_push.permitted([document])
    if not allowed:
        return _json(refused[0])
    consent = f"cc:{session_id}:{sessions.user_turn(session_id)}"  # 同意 = 用户回的那一句（本对话的用户消息序号）
    found = _call(service.release, name, version, consent)
    if "result" not in found:
        return _json({"document": document, **found})
    released = found["result"]
    if released["status"] == "changed":
        return _json({"document": document, "status": "changed", "version": released["version"], "note": CHANGED_NOTE})
    if released["status"] != "released":
        return _json({"document": document, "status": "not_saved",
                      "reason": released.get("code") or contracts.NOT_DRAFT, **(
                          {"message": texts.explain(contracts.CollectionError(released["code"], released.get("detail")))}
                          if released.get("code") else {})})
    view = _settle(name)
    result = RESULT.get(view["status"], view["status"])
    out = {"document": document, "status": result, "collection": view}
    if result == ENTERED:
        out["docnum"] = view["docnum"]
    elif result == FAILED:
        out["reason"] = view["reason"]
    elif result == QUEUED:
        out["note"] = QUEUED_NOTE
        if service.express_waiting(name):  # 这本账套此刻在等 Express 关闭（共用通道读的时候算）
            out.update(express_wait=True, note=QUEUED_NOTE + EXPRESS_OPEN_NOTE)
    return _json(out)


def withdraw(tool_input) -> str:
    found = _call(service.withdraw, str(tool_input.get("collection") or ""))
    return _json({"status": "withdrawn", "collection": found["result"]} if "result" in found else found)


# ── 声明 ─────────────────────────────────────────────────────────────────────

COLLECTION_PROP = {"type": "string", "description": "The collection's name (COL…) from an earlier result."}
VERSION_PROP = {"type": "integer", "description": "The version from the collection's latest result."}
FILE_PROP = {"type": "string", "description": "A file attached in this conversation (its id or name): the transfer slip."}
INVOICES_PROP = {"type": "array", "maxItems": contracts.MAX_INVOICES, "items": {"type": "object", "properties": {
    "docnum": {"type": "string"}, "amount": {"type": "string", "description": "Collected on this invoice, e.g. 1200.50."}},
    "required": ["docnum"]}}
METHODS_PROP = {"type": "array", "maxItems": contracts.MAX_METHODS, "items": {"type": "object", "properties": {
    "prefix": {"type": "string", "description": "The transfer or discount prefix from erp_collection_methods."},
    "amount": {"type": "string"}, "file": FILE_PROP}, "required": ["prefix", "amount"]}}
RESULT_NOTE = ("status is draft, queued (released, on its way to Express), writing, written (docnum is its RE number in "
               "Express), failed (reason says why; balances are the invoices' balances now when they were already "
               "collected) or withdrawn.")

TOOLS = [
    {"name": "erp_collection_customers", "label": "Searched customers for a collection", "modes": ["chat"],
     "description": "Customers of the company this conversation is filed under that a collection can be recorded for "
                    "(from Express), by code or part of the name: code and name. Up to "
                    f"{CUSTOMERS_MAX} per call; pass next_offset as offset for the next page. When several match, ask "
                    "the user which one.",
     "input_schema": {"type": "object", "properties": {
         "search": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
         "limit": {"type": "integer", "minimum": 1, "maximum": CUSTOMERS_MAX}}},
     "handler": "pearnly_erp_portal.collection.cc_tools.customers"},
    {"name": "erp_collection_invoices", "label": "Listed open invoices", "modes": ["chat"],
     "description": "A customer's invoices that are not fully paid in Express, oldest first: docnum, date, net, received, "
                    "remaining and available (what can be collected now: remaining less what other collectors already "
                    "recorded). Only available can be collected; never work amounts out yourself.",
     "input_schema": {"type": "object", "properties": {"customer": {"type": "string", "description": "Customer code."}},
                      "required": ["customer"]},
     "handler": "pearnly_erp_portal.collection.cc_tools.invoices"},
    {"name": "erp_collection_methods", "label": "Listed collection methods", "modes": ["chat"],
     "description": "How a collection can be received in this company's account set: cash (true when cash can be "
                    "recorded), transfers (prefix, code, name: the account set's money-receiving transfer kinds) and "
                    "discount (the cash discount kind, or none). Only these can be used.",
     "input_schema": {"type": "object", "properties": {}},
     "handler": "pearnly_erp_portal.collection.cc_tools.methods"},
    {"name": "erp_collection_draft", "label": "Saved a collection draft", "modes": ["chat"],
     "description": "Save a collection the user is recording for one customer: the invoices with the amount collected on "
                    "each (or only docnums and amount, the total, collected oldest first), cash, and methods (transfers "
                    "and the discount by prefix, with amount; for a transfer the slip photo attached in this "
                    "conversation as file). To change a saved one give collection and version: the whole content is "
                    "replaced. The server checks every rule (no more than available, amounts add up, only listed "
                    "methods) and returns the confirm card: company, book (account set), customer, each invoice, "
                    "methods, discount, total, version, ready and problem. Nothing is written into Express by this.",
     "input_schema": {"type": "object", "properties": {
         "collection": COLLECTION_PROP, "version": VERSION_PROP,
         "customer": {"type": "string", "description": "Customer code (new collection)."},
         "invoices": INVOICES_PROP, "amount": {"type": "string"}, "cash": {"type": "string"},
         "methods": METHODS_PROP, "salesman": {"type": "string"}, "department": {"type": "string"},
         "date": {"type": "string", "description": "YYYY-MM-DD; leave out for today."}},
         "required": ["invoices"]},
     "handler": "pearnly_erp_portal.collection.cc_tools.draft"},
    {"name": "erp_collection_voucher", "label": "Attached a transfer slip", "modes": ["chat"],
     "description": "Attach the transfer slip photo the user sent in this conversation to one transfer (prefix) of a "
                    "saved collection; returns the confirm card with the new version.",
     "input_schema": {"type": "object", "properties": {
         "collection": COLLECTION_PROP, "version": VERSION_PROP, "prefix": {"type": "string"}, "file": FILE_PROP},
         "required": ["collection", "version", "prefix", "file"]},
     "handler": "pearnly_erp_portal.collection.cc_tools.voucher"},
    {"name": "erp_collection_card", "label": "Viewed a collection", "modes": ["chat"],
     "description": "The confirm card of one of the user's collections (the same as erp_collection_draft returns). "
                    + RESULT_NOTE,
     "input_schema": {"type": "object", "properties": {"collection": COLLECTION_PROP}, "required": ["collection"]},
     "handler": "pearnly_erp_portal.collection.cc_tools.card"},
    {"name": "erp_collection_push", "label": "Recorded a collection", "modes": ["chat"],
     "description": "Record a collection into Express as an RE receipt: only the document of the write plan the user "
                    "agreed to (pearnly_write_plan, said back, then pearnly_write_start after the user's reply); any "
                    "other comes back not_pushed with what to do, changed when it was changed after it was said back. "
                    f"It waits up to about {direct_push.PUSH_WAIT_SECONDS} seconds for Express: entered (docnum is "
                    "the RE number, tell it to the user), queued (not in Express yet; recorded once CC Desktop is back) or failed with a reason.",
     "input_schema": {"type": "object", "properties": {
         "document": {"type": "string", "description": "The document (R:…) from erp_collection_draft and the plan."}},
         "required": ["document"]},
     "handler": "pearnly_erp_portal.collection.cc_tools.push"},
    {"name": "erp_collection_status", "label": "Checked a collection", "modes": ["chat"],
     "description": "Where one of the user's collections is now. " + RESULT_NOTE,
     "input_schema": {"type": "object", "properties": {"collection": COLLECTION_PROP}, "required": ["collection"]},
     "handler": "pearnly_erp_portal.collection.cc_tools.status"},
    {"name": "erp_collection_list", "label": "Listed collections", "modes": ["chat"],
     "description": f"The user's latest collections, newest first, up to {LIST_MAX} per call; pass next_offset as offset "
                    "for the next page. " + RESULT_NOTE,
     "input_schema": {"type": "object", "properties": {
         "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": LIST_MAX}}},
     "handler": "pearnly_erp_portal.collection.cc_tools.recent"},
    {"name": "erp_collection_withdraw", "label": "Withdrew a collection", "modes": ["chat"],
     "description": "Withdraw one of the user's collections before CC Desktop picks it up (a draft, queued or failed "
                    "one), only when the user asks. One being written or already in Express cannot be withdrawn.",
     "input_schema": {"type": "object", "properties": {"collection": COLLECTION_PROP}, "required": ["collection"]},
     "handler": "pearnly_erp_portal.collection.cc_tools.withdraw"},
]

STEP_NAMES = {
    "erp_collection_customers": "Searching customers for a collection",
    "erp_collection_invoices": "Listing open invoices",
    "erp_collection_methods": "Listing collection methods",
    "erp_collection_draft": "Saving a collection draft",
    "erp_collection_voucher": "Attaching a transfer slip",
    "erp_collection_card": "Viewing a collection",
    "erp_collection_push": "Recording a collection",
    "erp_collection_status": "Checking a collection",
    "erp_collection_list": "Listing collections",
    "erp_collection_withdraw": "Withdrawing a collection",
}
