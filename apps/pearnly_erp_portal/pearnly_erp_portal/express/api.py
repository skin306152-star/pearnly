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
from pearnly_erp_portal.express import choices, contracts, facts, masters, pos_day, prepare, push, stock_entry
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
    fields = ["name", "status", "express_state", "express_docnum", "express_reason", "express_needed", "express_job",
              "express_book"]
    if doctype == push.STOCK:
        fields += ["express_void_state", "express_void_reason", "express_void_job"]  # 作废原单（B 段 ①，先只开出入库单）
    row = frappe.db.get_value(doctype, name, fields, as_dict=True)
    if row:
        row.company = push.company_of(doctype, name)   # 出入库单没有公司栏，按仓库的公司
    if not row or row.company not in companies.allowed_companies():
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    return row


def _needed(row):
    try:
        needed = json.loads(row.express_needed or "[]")
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
    # 收银日汇总非现金付款方式的收款科目（kind receipt）也是从这份账套的科目表里选
    source = {"item": [row for row in reported.get("reported_products") or [] if row.get("kind", "stock") == "stock"],
              "account": accounts, "receipt": accounts,
              "department": reported.get("reported_departments") or []}.get(item["kind"])
    if source is None:
        return [{"code": code, "name": ""} for code in FIXED.get(item["kind"], ())]
    if item["kind"] in ("account", "receipt"):
        source = facts.accounts_first(source, item["subject"])
    rows = [{"code": str(row.get("code") or ""), "name": str(row.get("name") or "")} for row in source]
    if item["kind"] == "item":
        rows.append({"code": choice_rules.NEW, "name": ""})
    return rows


def _party_blocked(doctype, row, job):
    """电脑拦下、要在 ERP 里选一次才能再写的（转人工）：对方档（疑似重复 / 同税号分店认不准）、新建库存品的存货科目组。
    回要人选的那一项；其余情形回 []（只能「重试」）。"""
    code = ack_rules.error_code(job["reason"]) if job and job["status"] == "manual" else ""
    if code in prepare.PARTY_CODES:
        return prepare.party_needed(push.document(doctype, row.name), push._company(row.company))
    if code in prepare.GROUP_CODES:
        return prepare.group_needed(push.document(doctype, row.name), push._company(row.company))
    return []


def _view(doctype, row):
    data = facts.load(row.express_book) if row.express_book else None
    job = jobs.find_by_name(row.express_job) if row.express_job else None
    blocked = _party_blocked(doctype, row, job)
    needed = [{**item, "options": _options(item, data)} for item in (blocked or _needed(row))] \
        if row.express_state == contracts.HELD else []
    if row.get("express_void_state"):
        # 已写进 Express、又在 ERP 作废的：看作废那一件（转人工的能重试，电脑重核原单再作废）
        void_job = jobs.find_by_name(row.express_void_job) if row.express_void_job else None
        return {"state": row.express_state or "", "docnum": row.express_docnum or "", "reason": row.express_void_reason or "",
                "void": row.express_void_state, "needed": [], "can_answer": False,
                "can_retry": bool(void_job and void_job["status"] == "manual") and guard.has(CAPABILITIES[doctype][1])}
    return {"state": row.express_state or "", "docnum": row.express_docnum or "", "reason": row.express_reason or "",
            "needed": needed, "can_answer": bool(needed) and guard.has(CAPABILITIES[doctype][1]),
            "can_retry": bool(job and job["status"] == "manual" and not blocked) and guard.has(CAPABILITIES[doctype][1])}


@frappe.whitelist()
def document_express(doctype, name):
    return _view(doctype, _document(doctype, name))


@frappe.whitelist(methods=["POST"])
def preflight(doctype, payload, answers=None):
    """提交前（用户 10-23：「点提交后，缺字段应该马上弹窗要求选择」）：表单此刻的内容（不存）按写账规则准备一遍，回要人定的
    项 ``{"needed": [...]}``，页面当场弹窗问；``answers`` 给了先照选的记住（同 :func:`answer` 的核对）再判一遍，直到没有要问的。
    这家公司没接 Express、主档还没读到、没有账套这类不用人选的：回空，照常提交（提交后自动接着走）。"""
    if doctype not in CAPABILITIES:
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    guard.require(CAPABILITIES[doctype][1])
    payload = frappe.parse_json(payload) if isinstance(payload, str) else payload
    if not isinstance(payload, dict) or not payload.get("lines"):
        return {"needed": []}
    company = companies.document_company(payload.get("company"))
    if not push.linked(company):
        return {"needed": []}
    company_row = push._company(company)

    def needed():
        if doctype == push.STOCK:  # 出入库单：只问库存单的科目（`stock_entry.prepare`）
            result = stock_entry.prepare(stock_entry.form_body(payload), company_row, masters.ensure)
        else:
            result = prepare.prepare(push.form_document(doctype, payload, company), company_row, masters.ensure)
        if not str(result.get("hold") or "").startswith(contracts.CHOICE):
            return [], None
        book = result.get("book") or {}
        data = facts.load(book["key"]) if book.get("key") else None
        return [{**item, "options": _options(item, data)} for item in result.get("needed") or []], data

    items, _data = needed()
    answers = frappe.parse_json(answers) if isinstance(answers, str) else answers
    if answers:
        if not isinstance(answers, list):
            frappe.throw(_("Invalid request"))
        asked = {(item["kind"], item["subject"]): item for item in items}
        for given in answers:
            item = asked.get((given.get("kind"), given.get("subject"))) if isinstance(given, dict) else None
            value = str((given or {}).get("value") or "").strip()
            options = {option["code"]: option["name"] for option in item["options"]} if item else {}
            if not item or value not in options:
                frappe.throw(_("Invalid request"))
            choices.remember(company, item["kind"], item["subject"], value, options[value] or item.get("label") or "")
        frappe.db.commit()
        items, _data = needed()
    return {"needed": items}


@frappe.whitelist(methods=["POST"])
def answer(doctype, name, answers):
    """在单据上选要人拿主意的项：``answers`` = ``[{kind, subject, value}]``。只认这一项能选的值；记在这家公司名下后再准备一次。"""
    row = _document(doctype, name, write=True)
    job = jobs.find_by_name(row.express_job) if row.express_job else None
    blocked = _party_blocked(doctype, row, job)
    if row.express_state != contracts.HELD or (row.express_job and not blocked):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    answers = frappe.parse_json(answers) if isinstance(answers, str) else answers
    if not isinstance(answers, list) or not answers:
        frappe.throw(_("Invalid request"))
    data = facts.load(row.express_book) if row.express_book else None
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
        frappe.db.set_value(doctype, name, {"express_state": contracts.QUEUED, "express_job": None},
                            update_modified=False)
    frappe.db.set_value(doctype, name, {"express_reason": "", "express_needed": None}, update_modified=False)
    frappe.db.commit()
    push.send(doctype, name)
    return _view(doctype, _document(doctype, name))


@frappe.whitelist(methods=["POST"])
def retry(doctype, name):
    """转人工的写账再写一次（人看过、处理好了）。电脑写失败会整笔回滚，所以按单据此刻的内容重新准备一件（重新取编码、
    重新核记住的选择，不对的停下来在这里选），不拿原来那份载荷再试；只有结果不明的（可能已经写进去了）原样再排、钉在原来
    那台电脑上，由它按单据号查重。"""
    row = _document(doctype, name, write=True)
    if row.get("express_void_state"):
        void_job = jobs.find_by_name(row.express_void_job) if row.express_void_job else None
        if not void_job or void_job["status"] != "manual":
            frappe.throw(_("Not Permitted"), frappe.PermissionError)
        jobs.retry(void_job["job"])  # 作废载荷不随单据变：原样再排，电脑先核原单再作废
        frappe.db.set_value(doctype, name, {"express_void_state": contracts.QUEUED, "express_void_reason": ""},
                            update_modified=False)
        frappe.db.commit()
        return _view(doctype, _document(doctype, name))
    job = jobs.find_by_name(row.express_job) if row.express_job else None
    if not job or job["status"] != "manual":
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    if job["reason"] == jobs.LOST:
        jobs.retry(job["job"])
        frappe.db.commit()
    else:
        jobs.cancel(job["job"], "replaced")
        frappe.db.set_value(doctype, name, {"express_state": contracts.QUEUED, "express_job": None, "express_reason": "",
                                            "express_needed": None}, update_modified=False)
        frappe.db.commit()
        push.send(doctype, name)
    return _view(doctype, _document(doctype, name))
