"""代录 Express 的 ERP 这一侧（共用层 ``pearnly_core.express_operator``，``hooks.pearnly_express_operator`` 声明）。

ERP 的单在提交时就排进直写（机器产生的单固定走直写，主会话 10-26 定）；这里只列这家公司这个月**还没交进写账队列**的
采购 / 销售单（排着还没准备、停下等人的），会计可以改用代录录。

- 谁能发起：这家公司在本人可见公司里，且有这类单据的开单权（与 ERP 推送、单据上「再写一次」同一判据）；
- 交单：逐张走 ERP 推送同一条路（``push.send``：要人选的照旧停在单据上），载荷加代录标记；回执照直写同一条回到单据上
  （ledger_state / ledger_docnum / ledger_reason）。写账电脑照 ERP 原来的口径（开单的人登录过 CC 的电脑）。
"""
import calendar

import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_core.cc_desktop import jobs
from pearnly_erp_portal.express import contracts, push
from pearnly_erp_portal.masters import company as companies

KINDS = {"P": (push.PURCHASE, "purchase.write"), "S": (push.SALES, "sales.write")}


def _company_ok(user, company) -> bool:
    return bool(company) and any(row.get("name") == company if isinstance(row, dict) else row == company
                                 for row in companies.allowed_companies(user))


def allowed(user, company) -> bool:
    return _company_ok(user, company) and any(guard.has(capability, user) for _doctype, capability in KINDS.values())


def _month(period: str):
    year, month = (int(part) for part in str(period).split("-")[:2])
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"


def sources(company, period, offset, limit) -> dict:
    """任务选的这家公司这个月的单。按任务给的公司取，不走单据列表的权限条件：那条只认本人「当前公司」，任务选的公司不是当前公司时
    一张都取不到（10-29 测试机）。同一套判据在这里自己守：公司在本人可见公司里、有开单权，员工只看本人录入的（``guard.scope_filters``）。"""
    if not _company_ok(frappe.session.user, company):
        return {"documents": [], "next_offset": None}
    first, last = _month(period)
    scope = guard.scope_filters()
    rows = []
    for prefix, (doctype, capability) in KINDS.items():
        if not guard.has(capability):
            continue
        party = "customer" if doctype == push.SALES else "supplier_name"
        # 还没交进写账队列的；加上代录交过、停在要人处理的（上一件是代录的、转人工了：代录可以再录，`push.release_operator`）
        found = frappe.get_all(doctype, filters={"company": company, "doc_date": ["between", [first, last]],
                                                 "status": push.ready(doctype),
                                                 "ledger_state": ["in", [*contracts.OPEN_STATES, contracts.FAILED]], **scope},
                               fields=["name", "doc_date", "grand_total", "system_no", party, "ledger_reason",
                                       "ledger_job"],
                               order_by="doc_date asc, name asc", limit_start=0, limit_page_length=offset + limit)
        # 带按台号码的单不交代录（代录还不会在 Express 里点 Note 录行说明，主会话 11-02）：照直写，号码写进附加说明
        units = set(frappe.db.sql_list("""SELECT DISTINCT parent FROM `tabERP Line Serial`
            WHERE parenttype = %s AND parent IN %s""", (doctype, tuple(row.name for row in found)))) if found else set()
        found = [row for row in found if row.name not in units]
        rows += [{"doc_id": f"{prefix}:{row.name}", "kind": "sales" if prefix == "S" else "purchase",
                  "group": "sales" if prefix == "S" else "purchase",
                  "date": str(row.doc_date or ""), "party": row.get(party) or "", "ref": row.system_no or row.name,
                  "total": str(row.grand_total or ""), "stopped": row.ledger_reason or ""} for row in found
                 if not row.ledger_job or _operator_held(row.ledger_job)]
    rows.sort(key=lambda r: (r["date"], r["doc_id"]))
    page = rows[offset:offset + limit]
    return {"documents": page, "next_offset": offset + limit if len(rows) > offset + limit else None}


def pending_documents() -> list:
    """本人此刻在 ERP 版 CC「待处理」里列着的采购 / 销售单（``P:<名>`` / ``S:<名>``，与 ``express.pending`` 同一口径）：共用层
    「待处理」的代录一组按单据去重用。"""
    from pearnly_erp_portal.express import pending

    prefix = {doctype: key for key, (doctype, _capability) in KINDS.items()}
    return [f"{prefix[item['doctype']]}:{item['document']}" for item in pending._items(pending.visible())
            if item["doctype"] in prefix]


def _target(company, doc_id):
    """``P:<名>`` / ``S:<名>`` → ``(doctype, 名)``；不是这家公司的、本人没有这类单据开单权的回 None。"""
    prefix, _sep, name = str(doc_id).partition(":")
    if prefix not in KINDS or not name:
        return None
    doctype, capability = KINDS[prefix]
    if not guard.has(capability) or push.company_of(doctype, name) != company:
        return None
    owner = guard.scope_filters().get("owner")
    if owner and frappe.db.get_value(doctype, name, "owner") != owner:
        return None  # 员工只动本人录入的单（同单据页的记录范围）
    return doctype, name


def precheck(company, doc_ids) -> dict:
    """代录开录前逐张按写账规则准备一遍（`push.prepare_only`，与直写同一条路，不写单据、不排写账件）：要人选的项（新商品、
    费用科目、对方档……连同可选值，商品与对方可选「新建」）当场列出一次问完；准备不了的写明原因。"""
    from pearnly_erp_portal.express import api, facts

    documents = []
    for doc_id in doc_ids:
        target = _target(company, doc_id)
        if target is None:
            documents.append({"doc_id": doc_id, "state": "blocked", "reason": "not_permitted", "message": _("Not Permitted")})
            continue
        doctype, name = target
        current = frappe.db.get_value(doctype, name, "ledger_job")
        if current and not _operator_held(current):
            documents.append({"doc_id": doc_id, "state": "blocked", "reason": "already_queued"})
            continue
        result = push.prepare_only(doctype, name)
        hold = str(result.get("hold") or "")
        target = book_target(result.get("book"))
        if "failed" in result:
            row = {"doc_id": doc_id, "state": "blocked", "reason": result["failed"], "message": _(result["failed"])}
        elif hold.startswith(contracts.CHOICE):
            book = result.get("book") or {}
            data = facts.load(book["key"]) if book.get("key") else None
            row = {"doc_id": doc_id, "state": "choose", "reason": hold,
                   "needed": [{**item, "options": api._options(item, data)} for item in result.get("needed") or []]}
        elif hold == contracts.READING:
            row = {"doc_id": doc_id, "state": "waiting", "reason": hold, "message": _(hold)}
        elif hold:
            row = {"doc_id": doc_id, "state": "blocked", "reason": hold, "message": _(hold)}
        else:
            row = {"doc_id": doc_id, "state": "ready"}
        documents.append({**row, "target": target} if target else row)
    return {"documents": documents}


def book_target(book) -> dict | None:
    """这张单交了会写进哪份账套（Code 版 CC B4）：准备那一步按税号、分店、单据日期选出的那份（`express.target.pick`，与交单
    同一条准备）；``writers`` 是报过能写它的电脑。没选出（没有账套、同期几份没选）回 None。纯函数。"""
    if not isinstance(book, dict) or not book.get("key"):
        return None
    return {"account_set": str(book["key"]), "account_set_title": str(book.get("label") or book["key"]),
            "account_set_path": str(book.get("path") or ""), "connection": "", "connection_title": "",
            "writers": list(book.get("writers") or [])}


def _operator_held(job_name) -> bool:
    job = jobs.find_by_name(job_name)
    return bool(job) and job.get("status") == "manual" and str(job.get("request_key") or "").startswith(push.OPERATOR_KEY)


def _remember(company, doctype, name, given) -> None:
    """预检里人定的项按单据页「选一次」同一套核对记下（只认这一项能选的值，记在这家公司名下，`api.answer`）。"""
    from pearnly_erp_portal.express import api, choices, facts

    result = push.prepare_only(doctype, name)
    book = result.get("book") or {}
    data = facts.load(book["key"]) if book.get("key") else None
    asked = {(item["kind"], item["subject"]): item for item in result.get("needed") or []}
    for answer in given if isinstance(given, list) else []:
        if not isinstance(answer, dict):
            frappe.throw(_("Invalid request"))
        item = asked.get((answer.get("kind"), answer.get("subject")))
        if not item:
            continue  # 同一问题前一张已记下（例如这家公司这一年用哪套账，10-30 实测三张同问），这张不再问：不重复记
        value = str(answer.get("value") or "").strip()
        options = {option["code"]: option["name"] for option in api._options(item, data)}
        if value not in options:
            frappe.throw(_("Invalid request"))
        choices.remember(company, item["kind"], item["subject"], value, options[value] or item.get("label") or "")


def submit(company, doc_ids, batch, computer, answers=None) -> list:
    results = []
    for index, doc_id in enumerate(doc_ids, start=1):
        target = _target(company, doc_id)
        if target is None:
            results.append({"doc_id": doc_id, "status": "not_permitted", "reason": ""})
            continue
        doctype, name = target
        if (answers or {}).get(doc_id):
            _remember(company, doctype, name, answers[doc_id])
            frappe.db.commit()
        push.release_operator(doctype, name)
        result = push.send(doctype, name, operator={**batch, "index": index, "doc": doc_id, "company": company}) or {}
        results.append({"doc_id": doc_id, "status": result.get("status") or "", "reason": result.get("reason") or ""})
    return results


def status(company, doc_ids) -> list:
    """CC 重启后恢复出来的任务，以站点为准收尾（共用层 ``status``）：按单据的 Express 写账状态——已写入 → 已入账（带单号）、
    排着写账件 → 仍在队列、停住 / 失败 / 对不上 → 未完成（带原因）、没排或已撤回、人标了手工处理 → 已取消；不是这家公司、
    本人没权限的 → unknown。"""
    out = []
    for doc_id in doc_ids:
        target = _target(company, doc_id)
        row = frappe.db.get_value(target[0], target[1], ["ledger_state", "ledger_docnum", "ledger_reason", "ledger_job"],
                                  as_dict=True) if target else None
        if not row:
            out.append({"doc_id": doc_id, "state": "unknown", "docnum": "", "reason": ""})
            continue
        if row.ledger_state == contracts.WRITTEN:
            state = "saved"
        elif row.ledger_state == contracts.QUEUED and row.ledger_job:
            state = "queued"
        elif row.ledger_state in (contracts.HELD, contracts.FAILED, contracts.MISMATCH):
            state = "not_done"
        else:
            state = "cancelled"
        out.append({"doc_id": doc_id, "state": state, "docnum": (row.ledger_docnum or "") if state == "saved" else "",
                    "reason": (row.ledger_reason or "") if state == "not_done" else ""})
    return out
