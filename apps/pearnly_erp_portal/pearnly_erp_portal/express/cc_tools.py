"""ERP 版 CC 的对话直写工具（评估 2026-11-02 §13 第 9 步）：对话里按公司列 ERP 单据、看一张单要人选的项、记下选择、交直写、
看推送结果、核对结果不明的单。照 Cowork 的同名工具（`pearnly_cowork.assistant.tools` 的 list_documents / push_choices /
save_push_choices / push_documents / push_results / resolve_push_result），两边真正相同的（时间、结果状态码与字段、按公司的范围与
翻页参数、结果不明的核对说法与结果、说话规矩的通用段）引用共用层 `pearnly_core.assistant.direct_push` 同一份。

- **只走 ERP 现有的写账路**：交直写 = 单据上「再写一次」（`api.retry`）或提交时排直写的同一个函数（`push.send`）；记选择 = 单据上
  「选一次」（`api.answer`，记在这家公司名下，选完当场再准备、交直写）；结果不明 = 单据上对这种情形的「再写一次」（钉回原来那台
  电脑，由它核对 Express 里有没有，有就报回单号、没有才写）。推送不查重、不拦。
- **按公司的账本目标自动走对应那条**：现在 ERP 公司的账本只有 Express（`ERP Company.express_enabled`）。MR.ERP 接进来后
  （评估第 6 步）只在 `_route` 加一个分支，工具形状与说法不变。
- **权限全在服务端**，与 ERP 单据页同一判据：公司在本人可见公司里（`masters.company.allowed_companies`）；看要这类单据的查看权、
  推 / 选 / 核对要开单权（`api.CAPABILITIES`，`access.guard`）；员工只看、只推本人录入的（`guard.scope_filters`）。缺省的公司取本对话
  挂的那家（`masters.cc_tools.filed`）；写到别家的单要显式给 ``company``（同样过权限）。
- **已交给代录、还没录成的单直写不接**（`push.operator_owned`，同单据页），结果如实写 ``with_operator``。
- 单据种类：采购、销售（与代录的 sources 一致），以及同样写 Express 的出入库 / 调拨、收银日汇总、盘点。五种都列、都看结果；
  交直写也五种都开——单据页的「Express」弹窗（选一次、再写一次）这五种都有，同一组函数。
- 列单在服务端分页（每类按页取、合并后切这一页），总数单独计；不一次取全。

单据编号给模型的是 ``<类>:<单号>``（``P`` 采购、``S`` 销售、``M`` 出入库、``D`` 收银日汇总、``C`` 盘点），同代录的 ``doc_id``。
"""
import json
import time
from collections import namedtuple

import frappe
from frappe import _

from pearnly_core.assistant import direct_push
from pearnly_core.cc_desktop import jobs
from pearnly_erp_portal.access import guard
from pearnly_erp_portal.express import api, contracts, pending, push
from pearnly_erp_portal import mrerp
from pearnly_erp_portal.mrerp import contracts as mrerp_contracts, push as mrerp_push

Kind = namedtuple("Kind", "code prefix doctype date number party amount by_warehouse search")
KINDS = {
    "purchase": Kind("purchase", "P", push.PURCHASE, "doc_date", "system_no", "supplier_name", "grand_total", False,
                     ("name", "system_no", "doc_no", "supplier_name", "supplier_tax_id", "ledger_docnum")),
    "sales": Kind("sales", "S", push.SALES, "doc_date", "system_no", "buyer_name", "grand_total", False,
                  ("name", "system_no", "buyer_name", "ledger_docnum")),
    "stock": Kind("stock", "M", push.STOCK, "posting_date", "doc_no", "", "", True, ("name", "doc_no", "ledger_docnum")),
    "pos_day": Kind("pos_day", "D", push.POS_DAY, "day", "", "", "grand_total", False, ("name", "ledger_docnum")),
    "count": Kind("count", "C", push.COUNT, "posted_at", "title", "", "", True, ("name", "title", "ledger_docnum")),
}
PREFIXES = {kind.prefix: kind for kind in KINDS.values()}
LIST_MAX = direct_push.LIST_MAX
PUSH_MAX = direct_push.PUSH_MAX
CHOICE_OPTIONS_SHOWN = 30  # 每项可选值最多给几个（同 Cowork；用 search 缩小）
WITH_OPERATOR = "with_operator"  # 交给代录、还没录成：直写不接
NOT_PERMITTED = "not_permitted"
OTHER_COMPANY = "other_company"
# 单据上的 Express 状态 → 推送状态（Cowork 的 last_push_status 同一组词）
LAST_STATUS = {contracts.QUEUED: "pending", contracts.WRITTEN: "success", contracts.MISMATCH: "success",
               contracts.HANDLED: "success", contracts.HELD: "manual", contracts.FAILED: "failed",
               contracts.DETACHED: "failed"}
# 列单 / 推送结果按推送状态筛：等着录入（没写进去、也不在路上）/ 在路上 / 已入账 / 没成
PUSH_STATES = {"waiting": (contracts.HELD, contracts.FAILED), "pending": (contracts.QUEUED,),
               "success": (contracts.WRITTEN, contracts.MISMATCH, contracts.HANDLED),
               "failed": (contracts.HELD, contracts.FAILED, contracts.DETACHED)}
# 电脑那头没人写：排着的单停在这些原因上时不等结论
OFFLINE = (contracts.OFFLINE, contracts.NOT_CONNECTED, contracts.NO_WRITER)


# ---------------- 公司与单据 ----------------


def _filed() -> str:
    from pearnly_erp_portal.masters import cc_tools

    return cc_tools.filed()


def _companies(tool_input) -> tuple:
    """这次看哪几家：(公司编号表, 范围说明, 认不出的原因)。给了 company 只看那家（本人可见的才认）；all_companies 看本人可见的
    全部；都没给看本对话挂的那家，没挂看全部。"""
    from pearnly_erp_portal.masters import cc_service, cc_tools

    visible = cc_service.visible()
    if tool_input.get("all_companies"):
        return visible, "all_companies", None
    wanted, reason = cc_tools.resolve(tool_input.get("company"), _filed())
    if reason:
        return [], "", reason
    if wanted:
        return ([wanted], wanted, None) if wanted in visible else ([], "", "unknown company: pass an id from erp_companies")
    return visible, "all_companies", None


def _parse(document):
    """``P:<单号>`` → (Kind, 单号)；认不出回 (None, None)。"""
    prefix, _sep, name = str(document or "").partition(":")
    kind = PREFIXES.get(prefix)
    return (kind, name) if kind and name else (None, None)


def _doc_id(kind, name) -> str:
    return f"{kind.prefix}:{name}"


def _row(kind, name, write=False):
    """单据页同一判据读一张（`api._document`：这类单据的查看权 / 开单权、公司在本人可见公司里）＋员工只看本人录入的。
    看不到回 None；看得到但没有开单权回 ``NOT_PERMITTED``。"""
    try:
        row = api._document(kind.doctype, name)
    except (frappe.PermissionError, frappe.DoesNotExistError):
        frappe.clear_messages()
        return None
    owner = guard.scope_filters().get("owner")
    if owner and frappe.db.get_value(kind.doctype, name, "owner") != owner:
        return None
    if write and not guard.has(api.CAPABILITIES[kind.doctype][1]):
        return NOT_PERMITTED
    return row


def _other_company(row, tool_input) -> bool:
    """写别家的单要显式点名：本对话挂了公司、单据是别家的、又没给 company = 那家时不动。"""
    filed = _filed()
    return bool(filed) and row.company != filed and str(tool_input.get("company") or "") != row.company


def _company_title(company) -> str:
    return frappe.db.get_value("ERP Company", company, "company_name") or company or ""


# ---------------- 推送状态 ----------------


def _job(row):
    return jobs.find_by_name(row.ledger_job) if row.get("ledger_job") else None


def _unknown(job) -> bool:
    return bool(job) and job.get("status") == "manual" and job.get("reason") == jobs.LOST


def _reason(state, reason) -> dict:
    """原因：给人看的一句（`pending.reason_label`，同待处理与单据页）与原因码（给后面的调用用）；没有回空。"""
    if not reason:
        return {}
    return {"reason": pending.reason_label(state, reason), "reason_code": str(reason)[:300]}


def _outcome(kind, name) -> dict:
    """一张单此刻的推送结论（同 Cowork 推送结果的词）：entered / queued / needs_choice / waiting_for_cc / unconfirmed /
    with_operator / failed。"""
    row = api._document(kind.doctype, name)
    state, reason = row.ledger_state or "", row.ledger_reason or ""
    if state in (contracts.WRITTEN, contracts.MISMATCH, contracts.HANDLED):
        out = {"status": direct_push.ENTERED, "docnum": row.ledger_docnum or ""}
        if state == contracts.MISMATCH:
            out["mismatch"] = True  # 写进去了，但电脑读回核对不上
        if state == contracts.HANDLED:
            out["handled"] = True  # 人在 Express 里手工处理的
        return out
    if state == contracts.QUEUED:
        out = {"status": direct_push.QUEUED}
        if reason in OFFLINE:
            out["cc_desktop_online"] = False
        return out
    if state == contracts.HELD and mrerp_push.handles(row.company):
        # 账本是 MR.ERP（功能开着才有）：结果不明不再推、要人选先选，其余如实写原因
        if reason.startswith(mrerp_contracts.UNKNOWN):
            return {"status": direct_push.UNCONFIRMED, **_reason(state, reason)}
        if reason == mrerp_contracts.CHOICE:
            return {"status": direct_push.NEEDS_CHOICE, **_reason(state, reason)}
        return {"status": direct_push.FAILED, **_reason(state, reason)}
    job = _job(row)
    if state == contracts.HELD:
        if push.operator_owned(kind.doctype, name):
            return {"status": WITH_OPERATOR, **_reason(state, contracts.OPERATOR_OWNED)}
        if _unknown(job):
            return {"status": direct_push.UNCONFIRMED, **_reason(state, reason)}
        if api._view(kind.doctype, row).get("needed"):
            return {"status": direct_push.NEEDS_CHOICE, **_reason(state, reason)}
        if reason in contracts.RETRY_REASONS:
            return {"status": direct_push.WAITING_FOR_CC, **_reason(state, reason)}
    if not state:
        return {"status": direct_push.FAILED, "reason_code": "not_for_express"}
    return {"status": direct_push.FAILED, **_reason(state, reason)}


def _route(kind, name, row) -> str:
    """按这家公司的账本目标交直写：MR.ERP（`mrerp.push`，功能开着、公司选了 MR.ERP 账套）走导入直写，其余 Express。
    回 ``sent`` 或交不了的结论码。"""
    if mrerp_push.handles(row.company):
        if row.ledger_state == contracts.HELD and row.ledger_reason == mrerp_contracts.CHOICE:
            return ""  # 要人选一次：先选（erp_save_push_choices 选完当场再交）
        return "sent" if mrerp_push.send(kind.doctype, name).get("status") == "sent" else ""
    view = api._view(kind.doctype, row)
    if view.get("needed"):
        return ""  # 要人选一次：先选（erp_save_push_choices 选完当场再交）
    if view.get("can_retry"):
        api.retry(kind.doctype, name)  # 单据上的「再写一次」
        return "sent"
    if row.ledger_state in contracts.OPEN_STATES and not row.ledger_job:
        push.send(kind.doctype, name)  # 提交时排直写的同一个函数（停在会自己好的原因上时每分钟也是它）
        return "sent"
    return ""


def _mrerp_view(kind, row) -> dict:
    """账本是 MR.ERP 的单要人选的项（停在单据上的 ``ledger_needed``，`mrerp.push`）与能不能选（开单权）。"""
    needed = []
    if row.ledger_state == contracts.HELD and row.ledger_reason == mrerp_contracts.CHOICE:
        needed = api._needed(row)
    return {"needed": needed, "can_answer": bool(needed) and guard.has(api.CAPABILITIES[kind.doctype][1])}


def _push_one(document, tool_input) -> dict:
    kind, name = _parse(document)
    row = _row(kind, name, write=True) if kind else None
    if row is None:
        return direct_push.not_found(document)
    if row == NOT_PERMITTED:
        return {"document": document, "status": direct_push.FAILED, "reason_code": NOT_PERMITTED}
    if _other_company(row, tool_input):
        return {"document": document, "status": direct_push.FAILED, "reason_code": OTHER_COMPANY,
                "company": row.company, "company_title": _company_title(row.company)}
    state = row.ledger_state or ""
    if state in (contracts.WRITTEN, contracts.MISMATCH, contracts.HANDLED):
        return {"document": document, "status": direct_push.ALREADY_SUCCESS, "docnum": row.ledger_docnum or ""}
    if state == contracts.QUEUED and row.ledger_job:
        return {"document": document, "status": direct_push.ALREADY_PENDING}
    if not push.enabled(row.company) and not mrerp_push.handles(row.company):
        return {"document": document, "status": direct_push.FAILED, **_reason(contracts.HELD, contracts.DISCONNECTED)}
    if push.operator_owned(kind.doctype, name):
        return {"document": document, "status": WITH_OPERATOR, **_reason(contracts.HELD, contracts.OPERATOR_OWNED)}
    if _unknown(_job(row)):
        return {"document": document, **_outcome(kind, name)}  # 结果不明：不再推，核对后记下（erp_resolve_push_result）
    try:
        sent = _route(kind, name, row)
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return {"document": document, "status": direct_push.FAILED, "reason": str(error)[:300]}
    if not sent:
        return {"document": document, **_outcome(kind, name)}
    return {"document": document, "status": "sent"}


def _settle(results) -> None:
    """发出去的几张（``sent``）等写账结论（同 Cowork `_settle`）：入账（带 Express 单号）、要人选、结果不明…，或到点仍在排队
    （``queued``）。电脑那头没人写的不等；所有张共用一个时限，只在推送线程里等（`direct_push.may_wait`）。"""
    waiting = {}
    for index, item in enumerate(results):
        if item.get("status") == "sent":
            item["status"] = direct_push.QUEUED
            waiting[index] = _parse(item["document"])
    deadline = time.monotonic() + direct_push.PUSH_WAIT_SECONDS
    first = True
    if waiting:
        frappe.db.commit()
    while waiting and (first or (direct_push.may_wait() and time.monotonic() < deadline)):
        if not first:
            time.sleep(max(0.0, min(direct_push.PUSH_WAIT_STEP, deadline - time.monotonic())))
        first = False
        frappe.db.rollback()  # 结论是电脑回报时在别的请求里提交的：换一份新快照再读
        for index, (kind, name) in list(waiting.items()):
            outcome = _outcome(kind, name)
            results[index] = {"document": results[index]["document"], **outcome}
            if outcome["status"] != direct_push.QUEUED or outcome.get("cc_desktop_online") is False:
                waiting.pop(index)


# ---------------- 工具 ----------------


def _list_row(kind, row) -> dict:
    state = row.ledger_state or ""
    date = row.get(kind.date)
    out = {"name": _doc_id(kind, row.name), "kind": kind.code, "company": row.get("company") or "",
           "invoice_no": (row.get(kind.number) if kind.number else "") or row.name,
           "invoice_date": str(date)[:10] if date else "",
           "total_amount": contracts.money(row.get(kind.amount)) if kind.amount and row.get(kind.amount) not in (None, "")
           else "",
           "last_push_status": LAST_STATUS.get(state, ""), "last_pushed_at": row.get("ledger_sent_at")}
    if kind.party:
        out["seller_name" if kind.code == "purchase" else "buyer_name"] = row.get(kind.party) or ""
    out.update(_reason(state, row.ledger_reason))
    if state == contracts.HELD and row.ledger_job and _unknown(jobs.find_by_name(row.ledger_job)):
        out["result_unknown"] = True
    if LAST_STATUS.get(state) == "success":
        out["written"] = direct_push.filled({"docnum": row.ledger_docnum or "", "mismatch": state == contracts.MISMATCH or None,
                                             "handled": state == contracts.HANDLED or None})
    return {**direct_push.filled(out), "last_push_status": out["last_push_status"]}


def _filters(kind, companies, tool_input) -> tuple:
    """一类单据这次的库内条件：(filters, or_filters)；这家公司没有这类单据（没有仓库）回 (None, None)。"""
    if kind.by_warehouse:
        from pearnly_erp_portal.inventory import warehouses

        places = [place for company in companies for place in warehouses.names_of(company)]
        if not places:
            return None, None
        filters = {"warehouse": ["in", places]}
    else:
        filters = {"company": ["in", list(companies)]}
    filters["status"] = push.ready(kind.doctype)
    state = tool_input.get("push_state")
    filters["ledger_state"] = ["in", list(PUSH_STATES[state])] if state in PUSH_STATES else ["is", "set"]
    filters.update(guard.scope_filters())
    since, until = tool_input.get("date_from"), tool_input.get("date_to")
    if since or until:
        filters[kind.date] = ["between", [since or "1900-01-01", (until or "2999-12-31")
                                          + (" 23:59:59" if kind.date == "posted_at" else "")]]
    search = str(tool_input.get("search") or "").strip()
    or_filters = [[field, "like", f"%{search}%"] for field in kind.search] if search else None
    return filters, or_filters


def list_documents(tool_input) -> str:
    companies, scope, reason = _companies(tool_input)
    if reason:
        return direct_push.dumps({"status": direct_push.FAILED, "reason": reason})
    limit = max(1, min(int(tool_input.get("limit") or 20), LIST_MAX))
    offset = max(0, int(tool_input.get("offset") or 0))
    wanted = [KINDS[tool_input["kind"]]] if tool_input.get("kind") in KINDS else list(KINDS.values())
    rows, total = [], 0
    for kind in wanted:
        if not companies or not guard.has(api.CAPABILITIES[kind.doctype][0]):
            continue
        filters, or_filters = _filters(kind, companies, tool_input)
        if filters is None:
            continue
        fields = ["name", kind.date, "ledger_state", "ledger_reason", "ledger_docnum", "ledger_job",
                  "ledger_sent_at", *(f for f in (kind.number, kind.party, kind.amount) if f),
                  *(["company"] if not kind.by_warehouse else [])]
        # 每类按页取到「这一页的最后一张」为止，合并后再切（各类都按日期新 → 旧）
        found = frappe.get_all(kind.doctype, filters=filters, or_filters=or_filters, fields=fields,
                               order_by=f"{kind.date} desc, name desc", limit_start=0,
                               limit_page_length=offset + limit)
        counted = frappe.get_all(kind.doctype, filters=filters, or_filters=or_filters,
                                 fields=[{"COUNT": "name", "as": "total"}], order_by="", limit_page_length=1)
        total += int((counted[0].get("total") if counted else 0) or 0)
        rows += [(kind, row) for row in found]
    rows.sort(key=lambda pair: (str(pair[1].get(pair[0].date) or ""), pair[1].name), reverse=True)
    page = rows[offset:offset + limit]
    places = {}
    documents = []
    for kind, row in page:
        if kind.by_warehouse:
            places.setdefault(row.name, push.company_of(kind.doctype, row.name))
            row["company"] = places[row.name]
        documents.append(_list_row(kind, row))
    total = max(total, offset + len(page))
    next_offset = offset + len(page)
    return direct_push.dumps({"documents": documents, "count": len(documents), "total": total, "offset": offset,
                              "next_offset": next_offset if next_offset < total else None, "scope": scope})


def push_results(tool_input) -> str:
    return list_documents(tool_input)


PENDING_MAX = 50
BY_DOCTYPE = {kind.doctype: kind for kind in KINDS.values()}


def pending_items(tool_input) -> str:
    """ERP 版 CC 的「待处理」（同侧栏待处理一个后端 `express.pending`）：要人处理的单与原因。识别来的采购 / 销售单另带 ``checks``：
    识别时的复核码、票面合计与单据合计（写账前核对，`contracts.printed_hold`），CC 据此去看单据、问人。"""
    companies, scope, reason = _companies(tool_input)
    if reason:
        return direct_push.dumps({"status": direct_push.FAILED, "reason": reason})
    found = {"items": [], "count": 0}
    for company in companies:
        part = pending.pending(company, PENDING_MAX, tool_input.get("search"))
        found["items"] += part["items"]
        found["count"] += part["count"]
    items = []
    for item in found["items"][:PENDING_MAX]:
        kind = BY_DOCTYPE.get(item["doctype"])
        row = {"document": _doc_id(kind, item["document"]) if kind else item["document"], "company": item["company"],
               "reason": item["reason_label"], "number": item["invoice_no"], "counterparty": item["counterparty"],
               "amount": item["amount"], "date": item["date"], "url": item["url"]}
        if item["doctype"] in (push.PURCHASE, push.SALES):
            saved = frappe.db.get_value(item["doctype"], item["document"],
                                        ["ocr_review", "printed_total", "grand_total", "ledger_reason"], as_dict=True) or {}
            row["reason_code"] = saved.get("ledger_reason") or ""
            if saved.get("ocr_review"):
                try:
                    review = json.loads(saved["ocr_review"])
                except ValueError:
                    review = {}
                row["checks"] = {"recognition": review.get("codes") or [], "printed_total": saved.get("printed_total"),
                                 "read_total": review.get("total"), "document_total": saved.get("grand_total")}
        items.append(row)
    return direct_push.dumps({"scope": scope, "count": found["count"], "items": items})


def push_documents(tool_input) -> str:
    names = [str(n) for n in (tool_input.get("documents") or [])][:PUSH_MAX]
    if not names:
        frappe.throw(_("Invalid request"))
    results = [_push_one(name, tool_input) for name in names]
    _settle(results)
    out = {"results": results}
    if any(item.get("status") == direct_push.QUEUED for item in results):
        out["note"] = QUEUED_NOTE
    return direct_push.dumps(out)


def _options(item, search) -> dict:
    rows = item.get("options") or []
    found = [row for row in rows if not search or search in f"{row.get('code')} {row.get('name')}".casefold()]
    return {"total": len(found), "shown": found[:CHOICE_OPTIONS_SHOWN]}


def push_choices(tool_input) -> str:
    document = str(tool_input.get("document") or "")
    kind, name = _parse(document)
    row = _row(kind, name) if kind else None
    if row is None:
        return direct_push.dumps(direct_push.not_found(document))
    view = _mrerp_view(kind, row) if mrerp_push.handles(row.company) else api._view(kind.doctype, row)
    search = str(tool_input.get("search") or "").strip().casefold()
    needed = [{**{key: value for key, value in item.items() if key != "options"}, "options": _options(item, search)}
              for item in view.get("needed") or []]
    out = {"document": document, "company": row.company, "company_title": _company_title(row.company),
           "needed": needed, "can_answer": bool(view.get("can_answer")), **_outcome(kind, name)}
    if row.ledger_state in (contracts.WRITTEN, contracts.MISMATCH, contracts.HANDLED):
        out["entered_note"] = ENTERED_RESULT
    return direct_push.dumps(out)


def save_push_choices(tool_input) -> str:
    document = str(tool_input.get("document") or "")
    kind, name = _parse(document)
    row = _row(kind, name, write=True) if kind else None
    if row is None:
        return direct_push.dumps(direct_push.not_found(document))
    if row == NOT_PERMITTED:
        return direct_push.dumps(direct_push.not_saved(document, NOT_PERMITTED))
    if _other_company(row, tool_input):
        return direct_push.dumps({**direct_push.not_saved(document, OTHER_COMPANY), "company": row.company,
                                  "company_title": _company_title(row.company)})
    answers = [{"kind": str(item.get("kind") or ""), "subject": str(item.get("subject") or ""),
                "value": str(item.get("value") or "")} for item in tool_input.get("items") or [] if isinstance(item, dict)]
    if mrerp_push.handles(row.company):
        if not mrerp_push.answer(kind.doctype, name, answers):  # MR.ERP 的菜单 / 默认值：记在公司上，再排导入
            frappe.db.rollback()
            return direct_push.dumps(direct_push.not_saved(document, _("Invalid request")))
        answers = None
    try:
        if answers is not None:
            api.answer(kind.doctype, name, answers)  # 单据上的「选一次」：记在这家公司名下，当场再准备、交直写
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return direct_push.dumps(direct_push.not_saved(document, str(error)[:300] or NOT_PERMITTED))
    results = [{"document": document, "status": "sent"}]
    _settle(results)
    outcome = {key: value for key, value in results[0].items() if key != "document"}
    return direct_push.dumps({"document": document, "status": direct_push.SAVED, "push": outcome})


def resolve_push_result(tool_input) -> str:
    """结果不明的单：照单据页对这种情形的「再写一次」交回原来那台电脑核对（有就报回单号，没有才写一次）。"""
    document = str(tool_input.get("document") or "")
    kind, name = _parse(document)
    row = _row(kind, name, write=True) if kind else None
    if row is None:
        return direct_push.dumps(direct_push.not_found(document))
    if row == NOT_PERMITTED:
        return direct_push.dumps(direct_push.not_saved(document, NOT_PERMITTED))
    if _other_company(row, tool_input):
        return direct_push.dumps({**direct_push.not_saved(document, OTHER_COMPANY), "company": row.company,
                                  "company_title": _company_title(row.company)})
    if row.ledger_state != contracts.HELD or not _unknown(_job(row)):
        return direct_push.dumps(direct_push.not_saved(document, direct_push.NO_PUSH_TO_CONFIRM))
    try:
        api.retry(kind.doctype, name)
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return direct_push.dumps(direct_push.not_saved(document, str(error)[:300] or NOT_PERMITTED))
    results = [{"document": document, "status": "sent"}]
    _settle(results)
    outcome = results[0]
    return direct_push.dumps(direct_push.resolved(document, {key: outcome.get(key) for key in ("status", "docnum", "reason")}))


# ---------------- 声明 ----------------

# 账本程序的叫法：MR.ERP 账本开着时 "Express or MR.ERP"（同 Cowork），关着时照旧
PROGRAM = "Express or MR.ERP" if mrerp.MRERP_LEDGER_ENABLED else "Express"
QUEUED_NOTE = ("queued: sent, but Express has not reported a result yet (CC Desktop on the computer that writes it may "
               "be offline: it is entered once CC Desktop is back). Tell the user it is queued; check later with "
               "erp_push_results when they ask. Do not wait or sleep. A queued document is not entered yet: never say "
               "it was entered or recorded into Express, not even at the start of the reply.")
ENTERED_RESULT = ("This document is already entered into Express and stays as entered there: CC cannot change it. Tell "
                  "the user it cannot be changed from here, with its docnum, amounts in baht and times in local time; "
                  "never say it needs to be or can be changed in Express or anywhere else, in any language.")
ROW_NOTE = ("name is the document id for the other tools (P purchase, S sales, M stock movement or transfer, D POS day "
            "summary, C stock count); invoice_no is its number in ERP. last_push_status is pending (on its way to "
            "Express), success (entered: written.docnum is its number in Express; mismatch means the computer's check "
            "after writing did not match; handled means a person handled it in Express), manual (stopped: reason says "
            "why; result_unknown means Express may or may not have it) or failed. A document of a company whose books "
            "are kept in Express is sent to Express by itself when it is submitted in ERP: one listed as pending needs "
            "nothing from the user.")
SCOPE_PROPS = direct_push.scope_props("erp_companies", "documents", "documents")
KIND_PROP = {"type": "string", "enum": list(KINDS)}
COMPANY_PROP = {"type": "string", "description": "Only when the document belongs to another company than the one this "
                                                 "conversation is filed under: that company's id, after checking with "
                                                 "the user."}

GUIDANCE = [
    direct_push.GUIDANCE_TIMES,
    direct_push.GUIDANCE_THIS_MONTH,
    direct_push.GUIDANCE_BAHT,
    direct_push.GUIDANCE_TABLE_ROWS,
    direct_push.cc_desktop("writes ERP documents into Express"),
    "Call the accounting program by its name, Express. A document already entered into Express is done: CC cannot "
    "change it (its account, cash or credit, amounts or anything else); say so plainly with its number in Express "
    "(docnum), and never suggest changing or checking it in Express or anywhere else, not even as a condition or a "
    "closing sentence, in any language.",
    direct_push.guidance_troubleshooting("erp_pending"),
]

TOOLS = [
    {"name": "erp_list_documents", "label": "Listed documents",
     "description": "List ERP documents that are written into Express (purchases, sales, stock movements and transfers, "
                    "POS day summaries, stock counts) for the company this conversation is filed under, newest first: "
                    "number, date, counterparty, total and the push status. Filter by push_state (waiting = not in "
                    "Express yet and not on its way: stopped or failed; pending; success; failed = stopped, failed or "
                    "disconnected), kind, document date or search (number, counterparty, tax ID or Express number). Up "
                    f"to {LIST_MAX} per call: total is how many match; pass next_offset as offset for the next page. "
                    + ROW_NOTE,
     "input_schema": {"type": "object", "properties": {
         "search": {"type": "string"}, "push_state": {"type": "string", "enum": list(PUSH_STATES)},
         "kind": KIND_PROP, "date_from": {"type": "string", "description": "YYYY-MM-DD"},
         "date_to": {"type": "string", "description": "YYYY-MM-DD"}, **direct_push.page_props(LIST_MAX),
         **SCOPE_PROPS}},
     "handler": "pearnly_erp_portal.express.cc_tools.list_documents"},
    {"name": "erp_push_documents", "label": "Pushed documents",
     "description": "Send ERP documents to Express again, the same as Retry in the document's Express window: only when "
                    f"the user asked. Up to {PUSH_MAX} at a time. After sending it waits up to about "
                    f"{direct_push.PUSH_WAIT_SECONDS} seconds for the result from Express, so do not wait or check "
                    "again yourself. Each result says entered (written into Express; docnum is its document number "
                    "there, tell it to the user), queued (no result yet, or CC Desktop is offline: it is entered "
                    "later; check with erp_push_results when the user asks), failed with a reason, "
                    + direct_push.unconfirmed_rule(PROGRAM, "erp_resolve_push_result")
                    + ", needs_choice (get the choices with erp_push_choices, ask the user and save them with "
                    "erp_save_push_choices, which sends it again), waiting_for_cc (CC Desktop has not reported this "
                    "company's account set yet, is still reading it, or no computer can write it now; ERP tries again "
                    "every minute by itself), with_operator (handed to operator entry and not entered yet: operator "
                    "entry enters it, a push from here does not), or already_success / already_pending (already "
                    "entered or on its way; it is not sent again and CC cannot change it). A document of another "
                    "company than the one this conversation is filed under comes back failed with reason_code "
                    "other_company: check with the user, then pass company.",
     "input_schema": {"type": "object", "properties": {
         "documents": direct_push.documents_prop(PUSH_MAX), "company": COMPANY_PROP}, "required": ["documents"]},
     "handler": "pearnly_erp_portal.express.cc_tools.push_documents"},
    {"name": "erp_push_choices", "label": "Checked push choices",
     "description": "What an ERP document still needs a person to choose once before it can be written into Express "
                    "(party, product, expense account, payment, VAT claim, department, account set, stock account "
                    "group and the like; needed, each with its allowed values: up to "
                    f"{CHOICE_OPTIONS_SHOWN}, pass search to narrow by code or name), and its push status now. Use it "
                    "when a push came back needs_choice. Ask the user, then save with erp_save_push_choices. A choice "
                    "is kept for the company and reused for its later documents of the same kind.",
     "input_schema": {"type": "object", "properties": {
         "document": {"type": "string"}, "search": {"type": "string"}}, "required": ["document"]},
     "handler": "pearnly_erp_portal.express.cc_tools.push_choices"},
    {"name": "erp_save_push_choices", "label": "Saved push choices",
     "description": "Record what the user chose for the items listed by erp_push_choices: each item is the needed "
                    "entry's kind and subject with the chosen value (a listed code; party and product also accept the "
                    "NEW value given there). The company reuses them for later documents of the same kind. Saving "
                    "sends the document to Express again right away (push in the result, with the same words as "
                    "erp_push_documents): do not push it again. Only values the user chose or confirmed in this chat.",
     "input_schema": {"type": "object", "properties": {
         "document": {"type": "string"}, "items": direct_push.CHOICE_ITEMS, "company": COMPANY_PROP},
         "required": ["document", "items"]},
     "handler": "pearnly_erp_portal.express.cc_tools.save_push_choices"},
    {"name": "erp_resolve_push_result", "label": "Confirmed a push result",
     "description": direct_push.resolve_description(
         PROGRAM, "and ERP sends it back to the computer that was writing it, which checks Express by the "
                  "document's number: it reports the number there if Express has it and writes it once if not (the "
                  "same as Retry on the document)."),
     "input_schema": {**direct_push.RESOLVE_SCHEMA, "properties": {**direct_push.RESOLVE_SCHEMA["properties"],
                                                                   "company": COMPANY_PROP}},
     "handler": "pearnly_erp_portal.express.cc_tools.resolve_push_result"},
    {"name": "erp_pending", "label": "Checked pending items",
     "description": "The pending items (the same list as Pending in the sidebar): ERP documents that need a person "
                    "before they can be written into Express, with the reason. For a recognized purchase or sales "
                    "document, checks has the recognition review codes, the printed total on the document (a field the "
                    "user can edit; read_total is what recognition read) and the document total; a document is held "
                    "when the printed total is missing or differs from the document total. A submitted document is "
                    "changed with Amend on its page (url), which makes a corrected copy that is written instead; CC "
                    "does not change it itself. In a conversation filed under a company it covers only that company "
                    "unless all_companies is true.",
     "input_schema": {"type": "object", "properties": {"search": {"type": "string"}, **SCOPE_PROPS}},
     "handler": "pearnly_erp_portal.express.cc_tools.pending_items"},
    {"name": "erp_push_results", "label": "Checked push results",
     "description": "Show recent push results of ERP documents: those that failed or stopped (with the reason), are "
                    "pending (on their way to Express), or succeeded (entered, with the Express number). In a "
                    "conversation filed under a company it covers only that company unless all_companies is true. "
                    + ROW_NOTE,
     "input_schema": {"type": "object", "properties": {
         "push_state": {"type": "string", "enum": list(direct_push.RESULT_STATES)}, "kind": KIND_PROP,
         **direct_push.page_props(LIST_MAX), **SCOPE_PROPS}, "required": ["push_state"]},
     "handler": "pearnly_erp_portal.express.cc_tools.push_results"},
]

# 进行中的说法（同 Cowork 的用词；译文在本 App 术语表）
STEP_NAMES = {
    "erp_list_documents": "Listing documents",
    "erp_pending": "Checking pending items",
    "erp_push_documents": "Pushing documents",
    "erp_push_choices": "Checking push choices",
    "erp_save_push_choices": "Saving push choices",
    "erp_resolve_push_result": "Confirming a push result",
    "erp_push_results": "Checking push results",
}
