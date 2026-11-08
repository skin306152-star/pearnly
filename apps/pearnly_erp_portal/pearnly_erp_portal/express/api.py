"""ERP 页面的 Express 入口：单据上看写进 Express 的情况、要人拿主意的项在这里选一次（ADR-025）、管理员手动发起主档与库存同步
（后台落库）。

权限都在服务端：看单据要有这类单据的查看权与这家公司；选、再写一次要有开单权；同步只给管理员。选的值只认这份账套里有的
（主档、科目表、部门、存货科目组），选过记在这家公司名下（`choices`），之后同类自动套用。
"""
import json

import frappe
from frappe import _

from pearnly_core.cc_desktop import jobs
from pearnly_core.cc_desktop.wire import ack_rules
from pearnly_core.express_rules.push import choices as choice_rules

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.express import choices, contracts, facts, masters, pos_day, prepare, push
from pearnly_erp_portal.masters import company as companies

CAPABILITIES = {push.SALES: ("sales.read", "sales.write"), push.PURCHASE: ("purchase.read", "purchase.write"),
                push.STOCK: ("stock.read", "stock.write"), push.POS_DAY: ("cashier.read", "cashier.write"),
                push.COUNT: ("count.read", "count.write")}
FIXED = {"payment": choice_rules.PAYMENT_VALUES, "vat": choice_rules.VAT_VALUES}


@frappe.whitelist(methods=["POST"])
def sync_company(company):
    """公司页「同步 Express」：只发起读取并排后台任务，立刻回（落库在后台，做完推给本人，见 `masters.start`）。"""
    guard.require("team.manage")  # 只给管理员（ADMIN_CAPABILITIES）
    if not isinstance(company, str) or not frappe.db.exists(masters.COMPANY, company):
        frappe.throw(_("Company not found"), frappe.DoesNotExistError)
    return masters.start(company)


def _document(doctype, name, write=False):
    if doctype not in CAPABILITIES or not isinstance(name, str):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    guard.require(CAPABILITIES[doctype][1 if write else 0])
    fields = ["name", "status", "ledger_state", "ledger_docnum", "ledger_reason", "ledger_needed", "ledger_job",
              "ledger_book"]
    if doctype == push.STOCK:
        fields += ["ledger_void_state", "ledger_void_reason", "ledger_void_job"]  # 作废原单（B 段 ①，先只开出入库单）
    if doctype == push.POS_DAY:
        fields += ["vat_whole_document"]  # 按账套设置整张计税（主会话 10-26）：Express 弹窗标明
    row = frappe.db.get_value(doctype, name, fields, as_dict=True)
    if row:
        row.company = push.company_of(doctype, name)   # 出入库单没有公司栏，按仓库的公司
    if not row or row.company not in companies.allowed_companies():
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    return row


def _require_enabled(row):
    """选、再写一次、标手工处理都要这家公司还勾着「接 Express」：断开优先，断开后不再写（`push.detach`）。"""
    if not push.enabled(row.company):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


def _needed(row):
    try:
        needed = json.loads(row.ledger_needed or "[]")
    except ValueError:
        needed = []
    return [item for item in needed if isinstance(item, dict) and item.get("kind") and item.get("subject")]


def _options(item, data):
    """一项能选的值：写账规则给了就用给的，没给的（存货、费用科目、部门）从这份账套的主档里取。"""
    if item.get("options"):
        options = item["options"]
        if item["kind"] == "party":
            # 票面读出的分店（归类键第 3 段）：同税号的档里有这个分店的就只列这个分店的
            wanted = (str(item.get("subject") or "").split("|") + ["", "", ""])[2]
            options = [row for row in options if row.get("branch") == wanted] or options
        rows = [{"code": str(row.get("code") or ""), "name": str(row.get("name") or row.get("code") or ""),
                 **({"branch": row["branch"]} if row.get("branch") else {})} for row in options]
        if item["kind"] == "party" and not item.get("existing_only"):
            rows.append({"code": choice_rules.NEW, "name": ""})
        return rows
    reported = facts.reported(data)
    accounts = reported.get("reported_accounts") or []
    # 收银日汇总非现金付款方式的收款科目（kind receipt）、收银退货单的退货科目（kind refund）也是从这份账套的科目表里选
    source = {"item": [row for row in reported.get("reported_products") or [] if row.get("kind", "stock") == "stock"],
              "account": accounts, "receipt": accounts, "refund": accounts,
              "department": reported.get("reported_departments") or []}.get(item["kind"])
    if source is None:
        return [{"code": code, "name": ""} for code in FIXED.get(item["kind"], ())]
    if item["kind"] in ("account", "receipt", "refund"):
        source = facts.accounts_first(source, item["subject"])
    rows = [{"code": str(row.get("code") or ""), "name": str(row.get("name") or "")} for row in source]
    if item["kind"] == "item":
        rows.append({"code": choice_rules.NEW, "name": ""})
    return rows


def _party_blocked(doctype, row, job):
    """电脑拦下、要在 ERP 里选一次才能再写的（转人工）：对方档（疑似重复 / 同税号分店认不准）、新建库存品的存货科目组、
    赊购单服务品的进项税口径。
    回要人选的那一项；其余情形回 []（只能「重试」）。"""
    code = ack_rules.error_code(job["reason"]) if job and job["status"] == "manual" else ""
    if code in prepare.PARTY_CODES:
        return prepare.party_needed(push.document(doctype, row.name), push._company(row.company))
    if code in prepare.GROUP_CODES:
        if doctype in (push.STOCK, push.COUNT):
            # 库存单里新建的存货判不出存货科目（多仓闭环 P3x）：按这张单的日期用的那份账套问存货科目组
            return prepare.book_group_needed(push._company(row.company), push.document(doctype, row.name)["date"])
        return prepare.group_needed(push.document(doctype, row.name), push._company(row.company))
    if code in prepare.SERVICE_VAT_CODES:
        return prepare.service_vat_needed(push.document(doctype, row.name), push._company(row.company))
    return []


def _view(doctype, row):
    if not push.enabled(row.company):
        # 断开了：只看状态、Express 单号与原因（已断开的照实写），不再给选、再写一次
        return {"state": row.ledger_state or "", "docnum": row.ledger_docnum or "", "reason": row.ledger_reason or "",
                "void": row.get("ledger_void_state") or "", "needed": [], "can_answer": False, "can_retry": False,
                "can_mark_handled": False, "vat_whole": bool(row.get("vat_whole_document"))}
    data = facts.load(row.ledger_book) if row.ledger_book else None
    job = jobs.find_by_name(row.ledger_job) if row.ledger_job else None
    blocked = _party_blocked(doctype, row, job)
    needed = [{**item, "options": _options(item, data)} for item in (blocked or _needed(row))] \
        if row.ledger_state == contracts.HELD else []
    if row.get("ledger_void_state"):
        # 已写进 Express、又在 ERP 作废的：看作废那一件（转人工的能重试，电脑重核原单再作废）
        void_job = jobs.find_by_name(row.ledger_void_job) if row.ledger_void_job else None
        return {"state": row.ledger_state or "", "docnum": row.ledger_docnum or "", "reason": row.ledger_void_reason or "",
                "void": row.ledger_void_state, "needed": [], "can_answer": False,
                "can_retry": bool(void_job and void_job["status"] == "manual") and guard.has(CAPABILITIES[doctype][1])}
    stuck = _pos_stuck(doctype, row, job)
    # 结果不明、原电脑核对不了（ADR-027 §6）：人在 Express 核对后报结论，不给「再写一次」（会钉回一台核对不了的电脑）
    resolve = jobs.needs_person(job) and guard.has(CAPABILITIES[doctype][1])
    return {"state": row.ledger_state or "", "docnum": row.ledger_docnum or "", "reason": row.ledger_reason or "",
            "needed": needed, "can_answer": bool(needed) and guard.has(CAPABILITIES[doctype][1]),
            "can_retry": not resolve and bool((job and job["status"] == "manual" and not blocked)
                                              or (stuck and not needed and (not job or job["status"] in ("manual", "cancelled"))))
            and guard.has(CAPABILITIES[doctype][1]),
            "can_resolve": resolve,
            # 收银日汇总 / 退货单停住（定不了、跨年、原单是手工处理的…）的出口：人标记「已在 Express 手工处理」（主会话 10-26）
            "can_mark_handled": bool(stuck) and guard.has(CAPABILITIES[doctype][1]),
            "vat_whole": bool(row.get("vat_whole_document"))}


def _pos_stuck(doctype, row, job) -> bool:
    """收银日汇总 / 退货单是不是停住了（停下、失败，或电脑转了人工）：给「再写一次」「已在 Express 手工处理」两个出口。"""
    if doctype != push.POS_DAY:
        return False
    return row.ledger_state in (contracts.HELD, contracts.FAILED) or bool(job and job["status"] == "manual")


@frappe.whitelist(methods=["POST"])
def set_printed_total(doctype, name, value):
    """单据页改「票面合计」（已提交的单也能改，只这一栏）：开单权同「再写一次」；停在票面合计上的单当场重新写账。"""
    from pearnly_erp_portal.express import printed

    _document(doctype, name, write=True)
    printed.set_total(doctype, name, value)
    return _view(doctype, _document(doctype, name))


@frappe.whitelist(methods=["POST"])
def mark_handled(doctype, name, reason=None):
    """人在 Express 里手工处理好了这张收银日汇总 / 退货单：标「已在 Express 手工处理」，不再写；撤掉还挂着的写账件。库存对齐把它
    当作已写进 Express（`masters.WRITTEN_STATES`）。"""
    row = _document(doctype, name, write=True)
    _require_enabled(row)
    reason = " ".join(str(reason or "").split())[:500]
    if not reason:
        frappe.throw(_("Reason"), frappe.MandatoryError)
    job = jobs.find_by_name(row.ledger_job) if row.ledger_job else None
    if not _pos_stuck(doctype, row, job):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    if job and job["status"] not in ("done",):
        jobs.cancel(job["job"], "replaced")
    frappe.db.set_value(doctype, name, {"ledger_state": contracts.HANDLED, "ledger_reason": "", "ledger_needed": None,
                                        "ledger_job": None}, update_modified=False)
    # 审计：谁、什么时候标的、原因（单据时间线里原生的一条评论，带标记人和时间；标错了在 Express 里核对后照实处理，不另做撤销）
    frappe.get_doc(doctype, name).add_comment("Comment", f"{_('express.state.handled')}: {reason}")
    frappe.db.commit()
    return _view(doctype, _document(doctype, name))


@frappe.whitelist(methods=["POST"])
def document_express(doctype, name):
    """单据上的 Express 写账状态（单据页 `p.express`）。POST：转人工要人选对方档 / 存货科目组时，要先按单据此刻的内容准备一遍
    （`push.document`，日汇总会重算小票税额、存回合计），Frappe 不提交 GET 请求，这些写在 GET 里白做、被回滚。"""
    return _view(doctype, _document(doctype, name))


@frappe.whitelist(methods=["POST"])
def preflight(doctype, payload, answers=None, name=None):
    """提交前（用户 10-23：「点提交后，缺字段应该马上弹窗要求选择」）：表单此刻的内容（不存）按写账规则准备一遍，回要人定的
    项 ``{"needed": [...]}``，页面当场弹窗问；``answers`` 给了先照选的记住（同 :func:`answer` 的核对）再判一遍，直到没有要问的。
    要问的一次收齐（写账前置判定 `express.precheck.collect_form`：账套、选择项、存货科目组、库存单科目），``current`` 是预选。
    这家公司没接 Express、主档还没读到、没有账套这类不用人选的：回空，照常提交（提交后自动接着走）。``name``：改的是已有的草稿
    （采购「修改」给原单）—— 开始早于接上时刻的单不写进 Express，也不问（`push.started_linked`）。"""
    if doctype not in CAPABILITIES:
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    guard.require(CAPABILITIES[doctype][1])
    payload = frappe.parse_json(payload) if isinstance(payload, str) else payload
    if not isinstance(payload, dict) or not payload.get("lines"):
        return {"needed": []}
    company = companies.document_company(payload.get("company"))
    at = push.started(doctype, name) if isinstance(name, str) and name and frappe.db.exists(doctype, name) else None
    if not push.counts_from(company, at or frappe.utils.now_datetime()):
        return {"needed": []}
    from pearnly_erp_portal.express import operator, precheck as write_precheck

    answers = frappe.parse_json(answers) if isinstance(answers, str) else answers
    if answers:
        if not isinstance(answers, list):
            frappe.throw(_("Invalid request"))
        # 同一份判定（写账前置判定，一次收齐）：带着人选的值判，只认这一项能选的值，记在这家公司名下
        found = write_precheck.collect_form(doctype, payload, company, write_precheck.given_of(answers))
        write_precheck.save(company, found, answers)
        frappe.db.commit()
    items = operator.legacy_needed(write_precheck.collect_form(doctype, payload, company))
    return {"needed": items}


@frappe.whitelist(methods=["POST"])
def answer(doctype, name, answers):
    """在单据上选要人拿主意的项：``answers`` = ``[{kind, subject, value}]``。只认这一项能选的值；记在这家公司名下后再准备一次。"""
    row = _document(doctype, name, write=True)
    _require_enabled(row)
    job = jobs.find_by_name(row.ledger_job) if row.ledger_job else None
    blocked = _party_blocked(doctype, row, job)
    if row.ledger_state != contracts.HELD or (row.ledger_job and not blocked):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    answers = frappe.parse_json(answers) if isinstance(answers, str) else answers
    if not isinstance(answers, list) or not answers:
        frappe.throw(_("Invalid request"))
    data = facts.load(row.ledger_book) if row.ledger_book else None
    needed = {(item["kind"], item["subject"]): item for item in (blocked or _needed(row))}
    for given in answers:
        if not isinstance(given, dict):
            frappe.throw(_("Invalid request"))
        item = needed.get((given.get("kind"), given.get("subject")))
        value = str(given.get("value") or "").strip()
        options = {option["code"]: option["name"] for option in _options(item, data)} if item else {}
        if not item or value not in options:
            frappe.throw(_("Invalid request"))
        if item["kind"] == pos_day.VAT_REGISTERED:
            companies.set_vat_registered(row.company, value)  # 公司属性，记在公司上（收银小票按它拆税额），不进 Express 选择
            continue
        choices.remember(row.company, item["kind"], item["subject"], value, options[value] or item.get("label") or "")
    if blocked:
        jobs.cancel(job["job"], "replaced")  # 拦下的那件没写进去（整笔回滚）：撤掉，按选的对方档重新准备一件
        push._set(doctype, name, {"ledger_state": contracts.QUEUED, "ledger_job": None})
    push._set(doctype, name, {"ledger_reason": "", "ledger_needed": None})
    frappe.db.commit()
    push.send(doctype, name)
    return _view(doctype, _document(doctype, name))


@frappe.whitelist(methods=["POST"])
def retry(doctype, name):
    """转人工的写账再写一次（人看过、处理好了）。电脑写失败会整笔回滚，所以按单据此刻的内容重新准备一件（重新取编码、
    重新核记住的选择，不对的停下来在这里选），不拿原来那份载荷再试；只有结果不明的（可能已经写进去了）原样再排、钉在原来
    那台电脑上，由它按单据号查重。"""
    row = _document(doctype, name, write=True)
    _require_enabled(row)
    if row.get("ledger_void_state"):
        void_job = jobs.find_by_name(row.ledger_void_job) if row.ledger_void_job else None
        if not void_job or void_job["status"] != "manual":
            frappe.throw(_("Not Permitted"), frappe.PermissionError)
        jobs.retry(void_job["job"])  # 作废载荷不随单据变：原样再排，电脑先核原单再作废
        frappe.db.set_value(doctype, name, {"ledger_void_state": contracts.QUEUED, "ledger_void_reason": ""},
                            update_modified=False)
        frappe.db.commit()
        return _view(doctype, _document(doctype, name))
    job = jobs.find_by_name(row.ledger_job) if row.ledger_job else None
    if doctype == push.POS_DAY and row.ledger_state in (contracts.HELD, contracts.FAILED) and \
            (not job or job["status"] == "cancelled"):
        # 收银日汇总 / 退货单停下了（没交进队列）：按此刻的内容重新准备一次
        frappe.db.set_value(doctype, name, {"ledger_state": contracts.QUEUED, "ledger_reason": "", "ledger_needed": None},
                            update_modified=False)
        frappe.db.commit()
        push.send(doctype, name)
        return _view(doctype, _document(doctype, name))
    if not job or job["status"] != "manual":
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    if str(job.get("request_key") or "").startswith(push.OPERATOR_KEY):
        frappe.throw(_("express.reason.operatorOwned"))  # 代录的单由代录再录，这里不改成直写
    if job["reason"] == jobs.LOST:
        jobs.retry(job["job"])
        frappe.db.commit()
    else:
        jobs.cancel(job["job"], "replaced")
        push._set(doctype, name, {"ledger_state": contracts.QUEUED, "ledger_job": None, "ledger_reason": "",
                                  "ledger_needed": None})
        frappe.db.commit()
        push.send(doctype, name)
    return _view(doctype, _document(doctype, name))


@frappe.whitelist(methods=["POST"])
def resolve_unknown(doctype, name, entered, docnum=""):
    """结果不明、原电脑核对不了（移除、断开、开单的人不在它上面了，`jobs.needs_person`）时，人在 Express 里核对后报结论
    （ADR-027 §6，同 Cowork 复核页「核对」）：已入账 → 记成功（人报的单号），登记待补码（`push.job_changed` 不排主档同步，
    电脑上线后按单号读回补挂对方档码与存货码）；没有入账 → 解开钉住、从头排。原电脑还能核对的不收人报（交它核对：:func:`retry`）。"""
    row = _document(doctype, name, write=True)
    _require_enabled(row)
    job = jobs.find_by_name(row.ledger_job) if row.ledger_job else None
    if not jobs.needs_person(job):
        frappe.throw(_(jobs.COMPUTER_CHECKS))
    entered = entered if isinstance(entered, bool) else str(entered).lower() in ("1", "true")  # 网页传的是字符串
    jobs.settle_by_person(job["job"], entered, str(docnum or "")[:40])
    frappe.db.commit()
    return _view(doctype, _document(doctype, name))
