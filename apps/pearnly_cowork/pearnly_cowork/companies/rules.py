"""公司规则（数字会计员第 2 期「统一客户规则」，docs/work/2026-10-16-customer-rules-phase2.md）：一家公司全所共用的入账选择，
存在 `Cowork Company Choice`（知识库 `Cowork Company Note` 原样不动）。

- 一条 = 这家公司（或它的一个分店）这一类、这一归类键选定的值：类别与归类键同账套记忆（`push.express.choices`），键
  sha256(公司, 类别, 归类键, 分店) 唯一；分店空 = 全公司。每家最多 `RULES_MAX` 条。来源本期只有 `person`；
  证据（`evidence_json`）如实记：从哪份账套的记忆提升的、从 Express 历史提升的（张数与最近日期）、CC 按哪张单记的。
- 怎么叠在账套记忆之下见 `push.express.company_choices`；推送经钩子 ``cowork_push_company_choices``（`for_push`）取这家的规则，
  push 域不导入 companies。
- 权限、校验与「存规则时同一事务删掉各账套同键的记忆」在 `companies.choices`（主档页、CC 工具共用）；本模块只管形状与归属。
- 谁、何时存站点时区的裸时间，给页面前经 `service.when` 转成 UTC（同知识库）。
"""
from __future__ import annotations

import hashlib
import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from pearnly_cowork.companies import identity
from pearnly_cowork.companies.access import CHOICE, COMPANY, read, write
from pearnly_cowork.push.express import company_choices
from pearnly_cowork.push.express.choices import KINDS

RULES_MAX = 500
CONTEXT_MAX = 40
SOURCES = ("person",)
FIELDS = ["name", "kind", "subject", "subject_label", "value", "value_label", "branch", "source", "evidence_json",
          "set_by", "set_at", "creation"]
LABEL_MAX = 200
EVIDENCE_MAX = 2000


def rule_key(company, kind, subject, branch) -> str:
    return hashlib.sha256(json.dumps([company, kind, subject, branch or ""], ensure_ascii=False).encode()).hexdigest()


def _evidence(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        found = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def view(row) -> dict:
    """一条（库里的行或刚存的文档）给页面与 CC 的样子。"""
    get = row.get
    return {"id": get("name"), "kind": get("kind"), "subject": get("subject") or "",
            "subject_label": get("subject_label") or "", "value": get("value") or "",
            "value_label": get("value_label") or "", "branch": get("branch") or "", "source": get("source") or "person",
            "evidence": _evidence(get("evidence_json")), "set_by": get("set_by") or "",
            "set_at": str(get("set_at") or get("creation") or "")}


def listing(company, limit=RULES_MAX) -> list:
    """这家公司的规则，新 → 旧（一次查询）。"""
    rows = frappe.get_all(CHOICE, filters={"company": company}, fields=FIELDS, order_by="set_at desc, name asc",
                          limit_page_length=int(limit))
    return [view(row) for row in rows]


def one(company, name) -> dict:
    """这家公司的这一条；不是这家的、不存在的一律回「找不到」。"""
    if not isinstance(name, str) or not name or len(name) > 140:
        frappe.throw(_("cowork.company.choice_not_found"), frappe.DoesNotExistError)
    row = frappe.db.get_value(CHOICE, {"name": name, "company": company}, FIELDS, as_dict=True)
    if not row:
        frappe.throw(_("cowork.company.choice_not_found"), frappe.DoesNotExistError)
    return view(row)


def save(company, *, workspace, kind, subject, value, branch="", subject_label="", value_label="", evidence=None,
         session="") -> dict:
    """存一条（同键覆盖：同公司、同类别、同归类键、同分店）；回存好的那条与 ``created``。值、分店、权限由调用方核过。"""
    branch = company_choices.branch_of(branch)
    value = str(value or "").strip()
    if kind not in KINDS or not isinstance(subject, str) or not subject or len(subject) > 1000 or branch is None \
            or not value or len(value) > company_choices.VALUE_MAX:
        frappe.throw(_("cowork.company.choice_unsupported"))
    name = rule_key(company, kind, subject, branch)
    now, user = now_datetime(), frappe.session.user
    existing = frappe.db.exists(CHOICE, name)
    if existing:
        doc = read(CHOICE, name, for_update=True)
    else:
        frappe.db.get_value(COMPANY, company, "name", for_update=True)  # 同一家同时加的排队数上限
        if frappe.db.count(CHOICE, {"company": company}) >= RULES_MAX:
            frappe.throw(_("cowork.company.choices_full"))
        doc = frappe.new_doc(CHOICE)
        doc.update({"input_key": name, "company": company, "workspace": workspace, "kind": kind,
                    "subject": subject, "branch": branch})
    evidence = json.dumps(evidence or {}, ensure_ascii=False, default=str)
    doc.update({"value": value, "subject_label": str(subject_label or "")[:LABEL_MAX],
                "value_label": str(value_label or "")[:LABEL_MAX], "source": SOURCES[0],
                "evidence_json": evidence if len(evidence) <= EVIDENCE_MAX else "{}",
                "session": str(session or "")[:140], "set_by": user, "set_at": now})
    write(doc)
    return {**view(doc), "created": not existing}


def delete(company, name) -> dict:
    found = one(company, name)
    frappe.db.delete(CHOICE, {"name": found["id"]})
    return found


def forget_company(company) -> None:
    """删主档时连同它的规则（账套记忆不动，同知识库）。"""
    frappe.db.delete(CHOICE, {"company": company})


# 给 CC 的说明里每一类的英文名（`context.company_note`）
KIND_WORDS = {"party": "party record", "item": "stock item", "account": "expense account", "payment": "payment",
              "vat": "tax on receipts", "department": "department", "receipt": "receiving account"}


def context_rows(company) -> list:
    """给 CC 的 `<client_choices>`：这家公司的规则，一次查询、最多 `CONTEXT_MAX` 行（多一行用来判断「还有更多」）。"""
    rows = frappe.get_all(CHOICE, filters={"company": company},
                          fields=["kind", "subject_label", "subject", "value", "value_label", "branch"],
                          order_by="set_at desc, name asc", limit_page_length=CONTEXT_MAX + 1)
    return [{"kind": row.kind, "subject_label": row.subject_label or "", "value": row.value or "",
             "value_label": row.value_label or "", "branch": row.branch or ""} for row in rows]


def account_company(connection, account_row) -> tuple:
    """账套目录这一行属于哪份主档（同 `identity.belongs`：身份相同；没有税号的按建档连接与名称键）→ (主档, 这份账套的分店)。"""
    found = identity.company_identity("express", connection, account_row)
    if not found:
        return None, ""
    name = frappe.db.get_value(COMPANY, {"identity": found["key"]}, "name")
    if not name and not found["tax_id"] and found["name_key"]:
        name = frappe.db.get_value(COMPANY, {"adapter": "express", "seed_connection": str(connection or ""),
                                             "name_key": found["name_key"]}, "name")
    return name, found["branch"]


def for_push(connection, account_set, account_row) -> dict | None:
    """推送准备的钩子（``cowork_push_company_choices``）：这份账套所属公司的规则 ``{"company", "branch", "rules": [{kind,
    subject, value, branch, at}]}``（``at`` 是规则最后改的时间，站点时区；人推提议时据此分清人改了规则还是提议过时）；目录里没有这一行、没建主档回 None。直接按身份查公司（不读账套目录、不缓存）；出错由推送那边
    接住（这一张不用「历史已定」）。推送服务已核过录入权限与这条连接的使用授权；规则全所共用。"""
    del account_set  # 归属由目录这一行定
    name, branch = account_company(connection, account_row)
    if not name:
        return None
    rows = frappe.get_all(CHOICE, filters={"company": name}, fields=["kind", "subject", "value", "branch", "modified"],
                          limit_page_length=RULES_MAX)
    return {"company": name, "branch": branch,
            "rules": [{"kind": row.kind, "subject": row.subject, "value": row.value, "branch": row.branch or "",
                       "at": str(row.modified or "")} for row in rows]}


__all__ = ["CONTEXT_MAX", "KIND_WORDS", "RULES_MAX", "account_company", "context_rows", "delete", "for_push", "forget_company", "listing",
           "one", "rule_key", "save"]
