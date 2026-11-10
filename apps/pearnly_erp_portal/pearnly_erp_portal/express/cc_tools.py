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
WITH_OPERATOR = "with_operator"  # 交给代录、还没录成：直写不接
NOT_PERMITTED = "not_permitted"
OTHER_COMPANY = "other_company"
NO_COMPANY = "no_company"
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


def _other_company(row, tool_input) -> str:
    """CC 只写本对话公司的单（用户 11-10：「CC 或模型录进别的账套就是产品缺陷」，同 Cowork `assistant.write_scope`）：
    单据是别家的回 ``other_company``——点名别家也不写（旧口径「显式给 company 就放行」作废）；对话没挂公司回 ``no_company``；
    是本对话公司的回空串。ERP 写哪份账套由单据的公司在服务端算（`express.target.pick`），公司对了账套就对。"""
    filed = _filed()
    if not filed:
        return NO_COMPANY
    return OTHER_COMPANY if row.company != filed else ""


def _refused(document, row, reason) -> dict:
    out = {"document": document, "status": direct_push.FAILED, "reason_code": reason}
    if reason == OTHER_COMPANY:
        out.update(company=row.company, company_title=_company_title(row.company))
    return out


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
            return ""  # 要人选一次：先选（pearnly_precheck_answer 带 resume 选完当场再交）
        return "sent" if mrerp_push.send(kind.doctype, name).get("status") == "sent" else ""
    view = api._view(kind.doctype, row)
    if view.get("needed"):
        return ""  # 要人选一次：先选（pearnly_precheck_answer 带 resume 选完当场再交）
    if view.get("can_retry"):
        api.retry(kind.doctype, name)  # 单据上的「再写一次」
        return "sent"
    if row.ledger_state in contracts.OPEN_STATES and not row.ledger_job:
        push.send(kind.doctype, name)  # 提交时排直写的同一个函数（停在会自己好的原因上时每分钟也是它）
        return "sent"
    return ""


def _push_one(document, tool_input) -> dict:
    kind, name = _parse(document)
    row = _row(kind, name, write=True) if kind else None
    if row is None:
        return direct_push.not_found(document)
    if row == NOT_PERMITTED:
        return {"document": document, "status": direct_push.FAILED, "reason_code": NOT_PERMITTED}
    refused = _other_company(row, tool_input)
    if refused:
        return _refused(document, row, refused)
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
    sent = dict(waiting)
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
    _follow_up(results, sent)


def _follow_up(results, sent) -> None:
    """到点仍在排队、或结果不明的几张：记下是在这段对话里推的，电脑回报了在对话里补一句（同 Cowork，11-08 生产第 9 条，
    ``push_followup``；回报那头见 `push.job_changed`）。"""
    from pearnly_core.assistant import push_followup

    for index, (kind, name) in sent.items():
        if results[index].get("status") in (direct_push.QUEUED, direct_push.UNCONFIRMED):
            try:  # 只是补一句：记不上只记日志，不影响推送结果
                label = frappe.db.get_value(kind.doctype, name, kind.number) or name
                push_followup.remember("erp", f"{kind.doctype}:{name}", label)
            except Exception:
                frappe.log_error(title="Push follow-up remember failed")


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
LINES_SHOWN = 40
ORIGINAL_PAGES_MAX = 3
FILES_NOTE = ("Added to this conversation: the user opens them in the chat. They were not saved to any folder on the "
              "user's computer.")


def _review(raw) -> dict:
    try:
        found = json.loads(raw) if raw else {}
    except ValueError:
        found = {}
    return found if isinstance(found, dict) else {}


def _readable(tool_input, write=False, load=True):
    """CC 查 / 改的这张采购或销售单：(Kind, 单号, 文档；``load`` 为假时不取文档) 或 (None, None, 失败结果)。"""
    kind, name = _parse(tool_input.get("document"))
    if not kind or kind.doctype not in (push.PURCHASE, push.SALES):
        return None, None, {"status": direct_push.FAILED, "reason": "not_found: pass a P: or S: document from erp_pending "
                                                                    "or erp_list_documents"}
    row = _row(kind, name, write=write)
    if row is None or row == NOT_PERMITTED:
        return None, None, {"document": tool_input.get("document"), "status": direct_push.FAILED,
                            "reason": NOT_PERMITTED if row == NOT_PERMITTED else "not_found"}
    return kind, name, frappe.get_doc(kind.doctype, name) if load else None


def document_detail(tool_input) -> str:
    """ERP 单据详情（2026-11-03「CC 通用疑难处理」）：表头、金额、票面合计、明细、写账状态与原因；``checks`` 给识别复核码、
    识别读到的合计、写账前核对的结论（`contracts.printed_hold`）；``edits`` 改动依据。CC 据此去看原件、问人。"""
    kind, name, doc = _readable(tool_input)
    if not kind:
        return direct_push.dumps(doc)
    review = _review(doc.get("ocr_review"))
    party = doc.get("supplier_name") if kind.doctype == push.PURCHASE else doc.get("buyer_name")
    out = {"document": _doc_id(kind, name), "status": doc.status, "kind": doc.get("doc_kind"),
           "number": doc.get(kind.number) or name, "document_number": doc.get("doc_no") or "",
           "date": str(doc.get("doc_date") or ""), "counterparty": party or "",
           "subtotal": doc.get("subtotal"), "discount": doc.get("discount_total"), "vat": doc.get("vat_amount"),
           "total": doc.get("grand_total"), "printed_total": doc.get("printed_total") if doc.get("ocr_review") else None,
           "lines": [{"description": line.description, "qty": line.qty, "unit_price": line.unit_price,
                      "amount": line.get("line_total") if line.get("line_total") is not None else line.get("amount")}
                     for line in (doc.get("lines") or [])[:LINES_SHOWN]],
           "lines_total": len(doc.get("lines") or []), "pages": len(_originals(doc)),
           "push_state": doc.get("ledger_state") or "", "push_reason": doc.get("ledger_reason") or "",
           "docnum": doc.get("ledger_docnum") or "", "url": pending._url({"doctype": kind.doctype, "name": name,
                                                                          "direction": ""})}
    if doc.get("ocr_review"):
        hold = contracts.printed_hold(True, doc.get("printed_total"), doc.get("grand_total"), doc.get("net_payable"))
        out["checks"] = {key: value for key, value in {
            "recognition": review.get("codes") or [], "read_total": review.get("total"), "printed_hold": hold or "",
            "push": doc.get("ledger_reason") or ""}.items() if value not in (None, "", [])}
    from pearnly_erp_portal.express import printed

    edits = printed.edits(kind.doctype, name)
    if edits:
        out["edits"] = edits
    return direct_push.dumps(out)


def _originals(doc) -> list:
    """单据附的原件（票据在前，付款凭证在后；系统生成的不算）：File 名单。"""
    rows = [row for row in doc.get("attachments") or [] if row.get("file") and not row.get("generated")]
    return [row.file for row in sorted(rows, key=lambda row: 0 if (row.get("kind") or "bill") == "bill" else 1)]


def document_original(tool_input) -> str:
    """原件页图放进对话（同 Cowork `cowork_document_original`）：几份附件按顺序连起来编页码，默认前 3 页。"""
    from pearnly_core.assistant import files as chat_files
    from pearnly_core.originals import checks as originals, registered

    kind, name, doc = _readable(tool_input)
    if not kind:
        return direct_push.dumps(doc)
    pages = []  # (File, 类型, 这份里的第几页)
    for file in _originals(doc):
        content = registered.content(file)
        filename = frappe.db.get_value("File", file, "file_name") or file
        try:
            facts = originals.inspect(filename, content)
        except originals.Rejected:
            continue
        pages += [(file, facts["kind"], number, content) for number in range(1, (facts["page_count"] or 1) + 1)]
    wanted = [int(tool_input["page"])] if tool_input.get("page") else list(range(1, min(len(pages), ORIGINAL_PAGES_MAX) + 1))
    shown = []
    for number in wanted:
        if not 1 <= number <= len(pages):
            continue
        _file, file_kind, index, content = pages[number - 1]
        picture = originals.render_page(file_kind, content, index)
        mime = originals.preview_type(picture)
        shown.append(chat_files.add_to_conversation(f"{name}-p{number}.{'jpg' if mime == 'image/jpeg' else 'png'}",
                                                    picture, mime))
    frappe.db.commit()
    out = {"document": _doc_id(kind, name), "pages_total": len(pages), "files": shown}
    return direct_push.dumps({**out, "files_added_to_chat": True, "files_note": FILES_NOTE} if shown else out)


def set_printed_total(tool_input) -> str:
    """补 / 改票面合计（已提交的单也能改，只这一栏）：必须带依据；停在票面合计上的单当场重新写账（`printed.set_total`）。"""
    from pearnly_erp_portal.express import printed

    basis = str(tool_input.get("basis") or "").strip()
    if not basis:
        return direct_push.dumps({"status": "not_saved", "reason": "Pass basis: what the printed total is based on (the "
                                                                   "original's page, the user's words)."})
    kind, name, failed = _readable(tool_input, write=True, load=False)
    if not kind:
        return direct_push.dumps(failed)
    try:
        result = printed.set_total(kind.doctype, name, tool_input.get("value"), basis=basis, by="cc")
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return direct_push.dumps({"document": _doc_id(kind, name), "status": "not_saved", "reason": str(error)[:300]})
    return direct_push.dumps({"document": _doc_id(kind, name), "status": "saved", **result})


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


def resolve_push_result(tool_input) -> str:
    """结果不明的单：照单据页对这种情形的「再写一次」交回原来那台电脑核对（有就报回单号，没有才写一次）。"""
    document = str(tool_input.get("document") or "")
    kind, name = _parse(document)
    row = _row(kind, name, write=True) if kind else None
    if row is None:
        return direct_push.dumps(direct_push.not_found(document))
    if row == NOT_PERMITTED:
        return direct_push.dumps(direct_push.not_saved(document, NOT_PERMITTED))
    refused = _other_company(row, tool_input)
    if refused:
        out = direct_push.not_saved(document, refused)
        if refused == OTHER_COMPANY:
            out.update(company=row.company, company_title=_company_title(row.company))
        return direct_push.dumps(out)
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
ROW_NOTE = ("name is the document id for the other tools (P purchase, S sales, M stock movement, transfer or adjustment, D POS day "
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
    # 用户按单号、对方、金额、日期说是哪几张（与 Cowork 同一句，盘点 2026-11-08 P1-3）：ERP 列单的搜索认单号、对方、税号、Express 单号
    direct_push.guidance_find_documents("erp_list_documents"),
    direct_push.cc_desktop("writes ERP documents into Express"),
    "Call the accounting program by its name, Express. A document already entered into Express is done: CC cannot "
    "change it (its account, cash or credit, amounts or anything else); say so plainly with its number in Express "
    "(docnum), and never suggest changing or checking it in Express or anywhere else, not even as a condition or a "
    "closing sentence, in any language.",
    direct_push.guidance_troubleshooting("erp_pending"),
]

TOOLS = [
    {"name": "erp_list_documents", "label": "Listed documents",
     "description": "List ERP documents that are written into Express (purchases, sales, stock movements, transfers and adjustments, "
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
    {"name": "erp_push_documents", "label": "Pushed documents", "modes": ["chat"],
     "description": "Send ERP documents to Express again, the same as Retry in the document's Express window: only when "
                    f"the user asked. Up to {PUSH_MAX} at a time. After sending it waits up to about "
                    f"{direct_push.PUSH_WAIT_SECONDS} seconds for the result from Express, so do not wait or check "
                    "again yourself. Each result says entered (written into Express; docnum is its document number "
                    "there, tell it to the user), queued (no result yet, or CC Desktop is offline: it is entered "
                    "later; check with erp_push_results when the user asks), failed with a reason, "
                    + direct_push.unconfirmed_rule(PROGRAM, "erp_resolve_push_result")
                    + ", needs_choice (call pearnly_precheck for those documents, ask the user with ask_user and "
                    "save the answers with pearnly_precheck_answer with resume true, which sends them again: do not "
                    "push them again; for an MR.ERP company the items are its import menus and default values, chosen "
                    "once for the company), waiting_for_cc (CC Desktop has not reported this "
                    "company's account set yet, is still reading it, or no computer can write it now; ERP tries again "
                    "every minute by itself), with_operator (handed to operator entry and not entered yet: operator "
                    "entry enters it, a push from here does not), or already_success / already_pending (already "
                    "entered or on its way; it is not sent again and CC cannot change it). CC writes only the "
                    "documents of the company this conversation is filed under: a document of another company comes "
                    "back failed with reason_code other_company and is not written even when company names it (say "
                    "so in one sentence; the user writes it from that company's own conversation); in a conversation "
                    "not filed under a company it comes back failed with reason_code no_company (ask the user with "
                    "ask_user which company, options from erp_companies labelled with the company name, file the "
                    "conversation under the one they chose with pearnly_file_conversation, then push again).",
     "input_schema": {"type": "object", "properties": {
         "documents": direct_push.documents_prop(PUSH_MAX), "company": COMPANY_PROP}, "required": ["documents"]},
     "handler": "pearnly_erp_portal.express.cc_tools.push_documents"},
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
                    "when the printed total is missing or differs from the document total: look at it with "
                    "erp_document_detail and erp_document_original, and set the printed total with "
                    "erp_set_printed_total when the original shows it. Items are corrected with Amend on the "
                    "document's page (url), which makes a corrected copy. In a conversation filed under a company it covers only that company "
                    "unless all_companies is true.",
     "input_schema": {"type": "object", "properties": {"search": {"type": "string"}, **SCOPE_PROPS}},
     "handler": "pearnly_erp_portal.express.cc_tools.pending_items"},
    {"name": "erp_document_detail", "label": "Viewed a document",
     "description": "One purchase or sales document (P: or S: from erp_pending or erp_list_documents): header, amounts, "
                    "items, how many original pages it has, its push state and reason, and url (its page). For a "
                    "recognized document, printed_total is the total printed on the original (a field the user and you "
                    "can set) and checks has the recognition review codes, read_total (what recognition read) and "
                    "printed_hold (why it is held before Express: the printed total is missing or differs from the "
                    "document total). edits lists the changes made with their basis. Look at the original with "
                    "erp_document_original to find the cause.",
     "input_schema": {"type": "object", "properties": {"document": {"type": "string"}}, "required": ["document"]},
     "handler": "pearnly_erp_portal.express.cc_tools.document_detail"},
    {"name": "erp_document_original", "label": "Opened the original",
     "description": "Put the original page image(s) of a purchase or sales document into this conversation: its "
                    "attached originals in order, pages numbered across them. The first 3 pages unless page is given. "
                    "Look at them to find why a document is held (a missing page, a misread number).",
     "input_schema": {"type": "object", "properties": {"document": {"type": "string"},
                                                        "page": {"type": "integer", "minimum": 1}},
                      "required": ["document"]},
     "handler": "pearnly_erp_portal.express.cc_tools.document_original"},
    {"name": "erp_set_printed_total", "label": "Set the printed total",
     "description": "Set the printed total of a recognized purchase or sales document, also when it is submitted (only "
                    "this field; it is kept in the document's history). Only with a basis: the total as printed on the "
                    "original page you looked at, or the user's words. A document held for its printed total is written "
                    "into Express right after (the result has its push state). It does not change items or amounts: "
                    "when the items are wrong or missing, the user corrects them with Amend on the document's page (url), "
                    "which makes a corrected copy; give them the link.",
     "input_schema": {"type": "object", "properties": {
         "document": {"type": "string"}, "value": {"type": ["string", "number"]},
         "basis": {"type": "string", "description": "What the value is based on, in a few words."}},
         "required": ["document", "value", "basis"]},
     "handler": "pearnly_erp_portal.express.cc_tools.set_printed_total"},
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
    "erp_document_detail": "Viewing a document",
    "erp_document_original": "Opening the original",
    "erp_set_printed_total": "Setting the printed total",
    "erp_push_documents": "Pushing documents",
    "erp_resolve_push_result": "Confirming a push result",
    "erp_push_results": "Checking push results",
}
