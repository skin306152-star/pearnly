"""ERP 页面的 Express 入口：单据上看写进 Express 的情况、要人拿主意的项在这里选一次（ADR-025）、管理员手动同步主档与库存。

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
from pearnly_erp_portal.express import choices, contracts, facts, masters, prepare, push
from pearnly_erp_portal.masters import company as companies

WAIT = 30  # 电脑读一本账套一般十几秒：按钮在请求里等它读完，超了就回「读取中」，后台接着等
CAPABILITIES = {push.SALES: ("sales.read", "sales.write"), push.PURCHASE: ("purchase.read", "purchase.write")}
FIXED = {"payment": choice_rules.PAYMENT_VALUES, "vat": choice_rules.VAT_VALUES}


@frappe.whitelist(methods=["POST"])
def sync_company(company):
    guard.require("team.manage")  # 只给管理员（ADMIN_CAPABILITIES）
    if not isinstance(company, str) or not frappe.db.exists(masters.COMPANY, company):
        frappe.throw(_("Company not found"), frappe.DoesNotExistError)
    result = masters.sync(company, wait=WAIT)
    if result.get("state") == "reading":
        masters.enqueue(company)
    return result


def _document(doctype, name, write=False):
    if doctype not in CAPABILITIES or not isinstance(name, str):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    guard.require(CAPABILITIES[doctype][1 if write else 0])
    row = frappe.db.get_value(doctype, name, ["name", "company", "status", "express_state", "express_docnum",
                                              "express_reason", "express_needed", "express_job", "express_book"],
                              as_dict=True)
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
    source = {"item": [row for row in reported.get("reported_products") or [] if row.get("kind", "stock") == "stock"],
              "account": reported.get("reported_accounts") or [],
              "department": reported.get("reported_departments") or []}.get(item["kind"])
    if source is None:
        return [{"code": code, "name": ""} for code in FIXED.get(item["kind"], ())]
    if item["kind"] == "account":
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
    return {"state": row.express_state or "", "docnum": row.express_docnum or "", "reason": row.express_reason or "",
            "needed": needed, "can_answer": bool(needed) and guard.has(CAPABILITIES[doctype][1]),
            "can_retry": bool(job and job["status"] == "manual" and not blocked) and guard.has(CAPABILITIES[doctype][1])}


@frappe.whitelist()
def document_express(doctype, name):
    return _view(doctype, _document(doctype, name))


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
