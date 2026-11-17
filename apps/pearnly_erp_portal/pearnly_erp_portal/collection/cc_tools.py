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
from pearnly_erp_portal.collection import contracts, plan, service, snapshot, texts

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
                      "(ledger = its label), then go on; do not ask again after that.")
DAY_NEEDED_NOTE = ("date_needed: the collection date is outside the periods this conversation's account set can record "
                   "(periods: start–end), so nothing was saved. Ask the user once with ask_user for the collection date "
                   "(options: the last day of each period, the user may type another date inside a period), then call "
                   "again with date (YYYY-MM-DD) on erp_collection_invoices, erp_collection_methods and "
                   "erp_collection_draft. Do not change the account set here: the user switches it in the conversation's "
                   "account set selector.")
QUEUED_NOTE = ("queued: sent, but Express has not reported a result yet (CC Desktop on the computer that writes it may "
               "be offline: it is recorded once CC Desktop is back). Tell the user it is queued; check later with "
               "erp_collection_status when they ask. Do not wait. A queued collection is not in Express yet.")
REFUSED_NOTE = ("Express did not take this collection: reason is what CC Desktop reported. Tell the user that in one "
                "plain sentence, then ask with ask_user what to do: pick the customer (or the invoices) again from "
                "erp_collection_customers / erp_collection_invoices and change the draft, or stop. Do not tell the user "
                "to create or change anything in Express, and do not guess why.")


def _refusal_note(out, view) -> dict:
    """写前被拒退回草稿 / 写失败：给模型一句定式（说清事实、给选择），不让它自由发挥成「去 Express 建客户」（11-15 真机）。"""
    if view.get("returned") or view.get("status") == contracts.FAILED:
        out["note"] = REFUSED_NOTE
    return out


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
                "allowed_account_sets": [{"label": item["name"]}
                                         for item in (error.detail or {}).get("options") or ()]}
    if isinstance(error, contracts.CollectionError) and error.code == service.DAY_NEEDED:
        # 收款日期不在本对话账套的期间里：问日期（用户 11-15「人在 Express 改工作日期就能录」），换账套走对话的账套选择
        return {"status": "not_saved", "reason": error.code, "note": DAY_NEEDED_NOTE,
                "periods": [{"start": row["start"], "end": row["end"], "last": row["last"], "account_set": row["label"]}
                            for row in (error.detail or {}).get("periods") or ()]}
    if isinstance(error, contracts.CollectionError) and error.code == service.NO_CUSTOMER:
        # 编号对不上账本里唯一一位客户（多半是复述时空白变了，`service.customer_code`）：叫模型问人选，不说「不存在」
        from pearnly_core.assistant import codes

        detail = error.detail or {}
        return codes.unmatched("customer", detail.get("customer"), {"matches": detail.get("options") or ()})
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
    if card.get("returned"):
        out["refused_note"] = REFUSED_NOTE
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


# 读回未收清发票（按客户即时读一次，`snapshot.request`）的几种结果：模型只能按这一格说话（11-15 真机：刚开的发票还没读到，
# CC 说成「已收清，请在 Express 核对」）。CC 桌面版只报未收清的发票；建草稿时单号不在列表里，1.12.22 起同一次读按单号查它
# 在账套里的状态（`INVOICE_NOTES`），旧版仍只能说「此刻不是这位客户的未收发票」。
READ_WAIT_SECONDS = 8
READ_WAIT_STEP = 1
READ_NOTES = {
    "ready": ("read: ready — just read from Express. invoices are exactly this customer's open invoices in Express now. "
              "An invoice that is not listed is not open for this customer in Express now (it may be collected, voided, "
              "under another customer, or in another account set; Express does not say which): say only that and ask the "
              "user which listed invoice to collect, or to stop. Do not guess which, and do not ask the user to check Express."),
    "reading": ("read: reading — CC Desktop is reading this customer's invoices from Express right now and has not "
                "answered yet; invoices are from an earlier read and may miss new ones. Never say an invoice is collected, "
                "missing or not open. Tell the user it is still reading from Express, then call erp_collection_invoices "
                "again (or when the user asks)."),
    "offline": ("read: offline — CC Desktop on the computer that reads this account set is not connected, so nothing "
                "could be read now; invoices are from an earlier read and may miss new ones. Never say an invoice is "
                "collected, missing or not open. Tell the user CC Desktop is not connected and they can try again once "
                "it is on."),
    "outdated": ("read: outdated — CC Desktop on that computer needs an update before it can read invoices; invoices are "
                 "from an earlier read. Never say an invoice is collected, missing or not open. Tell the user CC Desktop "
                 "needs an update."),
}


# 按单号查到的状态（读回 ready 时才有）：模型据此说清是哪一种，不再只说「不是未收」
INVOICE_NOTES = {
    "settled": "invoice: settled — Express has this invoice for this customer and it is fully collected (balance 0). Say so.",
    "cancelled": "invoice: cancelled — this invoice is voided in Express. Say so.",
    "absent": "invoice: absent — there is no invoice with this number in this account set. Say so and ask for the right number.",
    "open": ("invoice: open under another customer — this number is an open invoice of another customer in this account "
             "set (customer code given). Say so and ask whether that customer is meant."),
}


def _fresh_invoices(company, customer, day=None, docnums=()) -> dict:
    """请电脑按这位客户即时读一次，能等的地方短等（照 LINE：读不到就如实说还在读）。回 `service.open_invoices` 的结果或
    `_call` 的拒绝。``day``：收款日期（不传按今天）；``docnums``：同时按单号查（够得到的电脑都还旧时改读不带单号的那种）。"""
    found = _call(service.open_invoices, company, customer, day, docnums=docnums)  # _call 提交：读取请求要先落库电脑才看得见
    if docnums and "result" in found and found["result"]["refresh"] == snapshot.OUTDATED:
        docnums = ()
        found = _call(service.open_invoices, company, customer, day)
    deadline = time.monotonic() + READ_WAIT_SECONDS
    while ("result" in found and found["result"]["refresh"] == snapshot.READING and direct_push.may_wait()
           and time.monotonic() < deadline):
        time.sleep(max(0.0, min(READ_WAIT_STEP, deadline - time.monotonic())))
        frappe.db.rollback()  # 回执在别的请求里提交：换一份新快照再读
        found = _call(service.open_invoices, company, customer, day, docnums=docnums)  # loop-query: 短等读回，最多 READ_WAIT_SECONDS 次
    return found


def _invoice_view(data, docnum) -> dict:
    """建草稿时不在列表里的那张：读回按单号查到了就带上状态与定式说明；没查（电脑旧、还在读）不带。"""
    seen = (data.get("looked_up") or {}).get(docnum)
    if not seen or seen.get("state") not in INVOICE_NOTES:
        return {}
    if seen["state"] == "open" and seen.get("customer") == data.get("customer"):
        return {}  # 这位客户的未收发票：列表里本该有（预留满了），照列表说
    view = {"invoice_state": seen["state"], "invoice_note": INVOICE_NOTES[seen["state"]]}
    if seen["state"] == "open":
        view["invoice_customer"] = seen.get("customer") or ""
    return view


def _read_view(data, customer) -> dict:
    state = data["refresh"] if data["refresh"] in READ_NOTES else snapshot.READING  # 说不清的不当「读到了」
    return {"customer": data.get("customer") or customer, "invoices": data["items"], "read": state,
            "note": READ_NOTES[state]}


def invoices(tool_input) -> str:
    customer = str(tool_input.get("customer") or "").strip()

    def run(company):
        found = _fresh_invoices(company, customer, tool_input.get("date") or None)
        return _read_view(found["result"], customer) if "result" in found else found

    return _in_company(run)


def methods(tool_input) -> str:
    def run(company):
        found = _call(service.methods, company, (tool_input or {}).get("date") or None)
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
    out = _refusal_note({**view, "result": RESULT.get(view["status"], view["status"])}, view)
    if view.get("mrerp_voided"):
        out["note"] = VOIDED_NOTE
    return _json(out)


def recent(tool_input) -> str:
    limit = max(1, min(int(tool_input.get("limit") or 10), LIST_MAX))
    offset = max(0, int(tool_input.get("offset") or 0))
    company = _company()
    if not company:
        return _json({"status": "not_saved", "reason": NO_COMPANY, "note": NO_COMPANY_NOTE})
    found = _call(service.listed, company, offset, limit, collector=str(tool_input.get("collector") or "") or None)
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
                                        salesman=tool_input.get("salesman"), department=tool_input.get("department"),
                                        doc_date=tool_input.get("date") or None)
        return service.create_draft(company, str(tool_input.get("customer") or ""), rows, cash=tool_input.get("cash") or 0,
                                    methods=_methods(tool_input.get("methods")), amount=tool_input.get("amount"),
                                    doc_date=tool_input.get("date") or None, source="cc",
                                    salesman=tool_input.get("salesman"), department=tool_input.get("department"))

    def run(company):
        found = _call(make, company)
        if "result" not in found and found.get("reason") == contracts.INVOICE_NOT_FOUND and not name:
            # 快照里没有这张：先按客户即时读一次再判（刚开的发票还没读到，11-15 真机），读到了就照原样再建一次
            customer = str(tool_input.get("customer") or "")
            missing = str((found.get("detail") or {}).get("docnum") or "").strip()
            read = _fresh_invoices(company, customer, tool_input.get("date") or None, docnums=[missing] if missing else ())
            if "result" not in read:
                return read
            view = _read_view(read["result"], customer)
            if view["read"] == snapshot.READY:
                again = _call(make, company)
                if "result" in again:
                    return _card(again["result"])
                if again.get("reason") != contracts.INVOICE_NOT_FOUND:
                    return again
                found = again
            docnum = (found.get("detail") or {}).get("docnum") or ""
            return {"status": "not_saved", "reason": contracts.INVOICE_NOT_FOUND, "docnum": docnum, **view,
                    **(_invoice_view(read["result"], docnum) if view["read"] == snapshot.READY else {})}
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
    if view.get("returned"):
        out["reason"] = view["reason"]
    return _json(_refusal_note(out, view))


def preview_withdraw(tool_input) -> list:
    """「撤回收款」卡片上列的（只读）：客户、各张发票、金额（用户 11-12：撤回类在对话里先点「允许」）。"""
    view = service.status(str(tool_input.get("collection") or ""))
    customer = " · ".join(str(part) for part in (view.get("customer"), view.get("customer_name")) if part)
    return [{"field": _("trade.customer"), "before": "", "after": customer},
            {"field": _("Invoices"), "before": "", "after": ", ".join(row["docnum"] for row in view.get("invoices") or [])},
            {"field": _("Amount"), "before": "", "after": str(view.get("total") or "")}]


def note_withdraw(tool_input) -> str:
    """卡片问句下面那一行事实：这笔收款的公司与账套（名字，同 Cowork 的卡）。直接读这一笔记的公司与账套名（只是名字，撤回本身照旧由
    `service.withdraw` 判权限）：11-16 真机这一行没出，经确认卡的读法一出错就整行不显示。账套名里已带公司名的不再重复。"""
    row = frappe.db.get_value("ERP Collection", str(tool_input.get("collection") or ""), ["company", "book_label"], as_dict=True)
    if not row:
        return ""
    company = str(frappe.db.get_value("ERP Company", row.company, "company_name") or row.company or "")
    book = str(row.book_label or "")
    if company and book.startswith(company):
        return book
    return " · ".join(part for part in (company, book) if part)


# 已写进 MR.ERP 后撤回（`collection.mrerp.withdraw`）：照给的两句说，下一步用收款引导卡（用户说要重录才开）
VOIDED_NOTE = ("mrerp_voided: this collection was already written into MR.ERP, so it is handed to a person. Say mrerp_voided.text "
               "as given (MR.ERP number and the menu to void it in, with the invoices it settled), then ask "
               "mrerp_voided.question. Only if the user says yes, start the Collection guide card for a new collection.")


def withdraw(tool_input) -> str:
    found = _call(service.withdraw, str(tool_input.get("collection") or ""))
    if "result" not in found:
        return _json(found)
    out = {"status": "withdrawn", "collection": found["result"]}
    if found["result"].get("mrerp_voided"):
        out["note"] = VOIDED_NOTE
    return _json(out)


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

DATE_PROP = {"type": "string", "description": "Collection date YYYY-MM-DD; leave out for today. Give the date the user chose "
                                            "after date_needed on every collection call."}
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
                    "recorded). Only available can be collected; never work amounts out yourself. It reads Express again "
                    "first; read says whether the list is fresh (ready) or not (reading / offline / outdated) — only a "
                    "ready list can tell that an invoice is not open; follow note.",
     "input_schema": {"type": "object", "properties": {"customer": {"type": "string", "description": "Customer code."},
                                                       "date": DATE_PROP},
                      "required": ["customer"]},
     "handler": "pearnly_erp_portal.collection.cc_tools.invoices"},
    {"name": "erp_collection_methods", "label": "Listed collection methods", "modes": ["chat"],
     "description": "How a collection can be received in this company's account set: cash (true when cash can be "
                    "recorded), transfers (prefix, code, name: the account set's money-receiving transfer kinds) and "
                    "discount (the cash discount kind, or none). Only these can be used.",
     "input_schema": {"type": "object", "properties": {"date": DATE_PROP}},
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
         "date": DATE_PROP},
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
     "description": f"This company's latest collections by anyone, newest first, up to {LIST_MAX} per call; pass "
                    "collector (a user id, as in each item's collector) to list only that person's, and next_offset as "
                    "offset for the next page. " + RESULT_NOTE,
     "input_schema": {"type": "object", "properties": {
         "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": LIST_MAX},
         "collector": {"type": "string"}}},
     "handler": "pearnly_erp_portal.collection.cc_tools.recent"},
    {"name": "erp_collection_withdraw", "label": "Withdrew a collection", "modes": ["chat"],
     "description": "Withdraw one of the user's collections before CC Desktop picks it up (a draft, queued or failed "
                    "one), only when the user asks; the user sees Allow / Deny first. One being written or already in Express cannot "
                    "be withdrawn.",
     "input_schema": {"type": "object", "properties": {"collection": COLLECTION_PROP}, "required": ["collection"]},
     "approval": True,
     "ask": "Withdraw this collection?",
     "note": "pearnly_erp_portal.collection.cc_tools.note_withdraw",
     "preview": "pearnly_erp_portal.collection.cc_tools.preview_withdraw",
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
