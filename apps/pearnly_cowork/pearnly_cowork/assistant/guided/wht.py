"""Cowork 预扣税 ภ.ง.ด.3 / 53 草稿的引导式（CC 对话里点「预扣税」，总表 CW-D-WHT）：声明 :data:`GUIDES` 与它点名的产品函数。

只转调报税草稿的服务函数（`tax.service.wht_draft / answer / workbook`，CC 工具 `cowork_wht_draft / cowork_wht_answer` 同一组）：

- 月份（推荐对话月份）→ 出草稿 → **复核卡逐行改**（用户 11-16「都按建议」：待确认行在 CC 引导里逐行复核，网页不另加页面）：
  还没定所得类型的收款方（改所得类型 = 按税号记住，整个工作空间，同 CC 工具）、待确认的行（算不算、日期、类型、税率、基数、税额、
  收款方税号、表）→ 确定 → 工作簿进对话、结果卡给两张表的收款方数与税额。
- **付款公司只认本对话公司**：税号由服务端从对话挂的公司主档取，卡上不选公司（主会话 11-16）。
- 改一格要引擎的 ``edit``（guided-erp 的 run.edit 对表单源分派）与前端行内可改的复核表，都进 main 后接上；只出草稿、不代为申报。
"""
from __future__ import annotations

import frappe
from frappe import _

from pearnly_cowork.assistant.guided import recon

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PAYEE = "payee."


def allowed(user) -> bool:
    """菜单上出不出「预扣税」：有报税草稿功能、且能读识别记录的人（同出草稿 `tax.service.wht_draft` 的两道）。"""
    from pearnly_cowork.access import scope
    from pearnly_cowork.tax import access

    if not user or user == "Guest" or not access.allowed(user):
        return False
    try:
        scope.require_read()
    except frappe.PermissionError:
        frappe.clear_messages()
        return False
    return True


def _company(ctx) -> dict:
    """本对话挂的公司：``{tax_id, name, branch}``；没挂或没有税号回空的税号。"""
    from pearnly_core.assistant import projects

    filed, _period, branch = projects.of_session(str(ctx.get("session") or ""))
    row = frappe.db.get_value("Cowork Company", filed, ["tax_id", "title"], as_dict=True) if filed else None
    return {"tax_id": str((row or {}).get("tax_id") or ""), "name": str((row or {}).get("title") or ""), "branch": branch or ""}


def _tax_id(value) -> str:
    from pearnly_cowork.tax import wht

    return wht.tax_id(value)


def period_facts(ctx) -> list:
    """对话公司没有 13 位税号：一行事实（服务的原因词条），出不了草稿。"""
    return [] if len(_tax_id(_company(ctx)["tax_id"])) == 13 else [_("tax.company_invalid")]


def period_check(ctx, value) -> str:
    found = recon.period_check(ctx, value)
    if found:
        return found
    return "" if len(_tax_id(_company(ctx)["tax_id"])) == 13 else "tax.company_invalid"


def _income_name(code) -> str:
    return _(f"cowork.tax.income.{code}")


DRAFT_SEP = "@"
CACHE_TTL = 2 * 3600
PENDING, PAYEES = "pending", "payees"
# 待确认行每格能怎么改（`tax.wht.clean_edit` 认的键；同 CC 工具 cowork_wht_draft 的 edits）
ROW_FIELDS = {"include": "choice", "payment_date": "date", "income_type": "choice", "rate": "number", "base": "number",
              "tax": "number", "payee_tax_id": "text", "form": "choice"}
COLUMNS = (("payee_name", "cowork.tax.col.payee_name"), ("payee_tax_id", "cowork.tax.col.payee_tax_id"),
           ("payment_date", "cowork.tax.col.paid_on"), ("income_type", "cowork.tax.col.income_type"),
           ("rate", "cowork.tax.col.rate"), ("base", "cowork.tax.col.paid"), ("tax", "cowork.tax.col.withheld"),
           ("form", "cowork.tax.col.form"), ("include", "cowork.tax.review.excluded"))


def _summary(ctx, period, edits=None) -> dict:
    """出（或按改动重出）这家这个月的草稿（付款公司 = 本对话公司），整份存进缓存给复核卡读（草稿表只存合计，行要现算）。"""
    from pearnly_cowork.tax import service

    company = _company(ctx)
    with recon._conversation(ctx):
        summary = service.wht_draft(company["tax_id"], period, list(edits or []), company["name"], company["branch"])
    frappe.cache.set_value(_cache_key(ctx), summary, expires_in_sec=CACHE_TTL)
    return summary


def _cache_key(ctx) -> str:
    return f"pearnly_assistant:guide-wht:{frappe.local.site}:{ctx.get('run') or ''}"


def _cached(ctx, period) -> dict:
    found = frappe.cache.get_value(_cache_key(ctx))
    return found if isinstance(found, dict) and found.get("period") == period else _summary(ctx, period)


def _period_of(draft) -> str:
    return str(draft or "").split(DRAFT_SEP)[1] if DRAFT_SEP in str(draft or "") else ""


def _next_draft(ctx, summary, period) -> str:
    """草稿号带一个改动次数（``WHT-1@2026-10@3``）：每改一格换一个号，复核卡与版本跟着刷新。"""
    parts = str(ctx.get("draft") or "").split(DRAFT_SEP)
    number = int(parts[2]) + 1 if len(parts) == 3 and parts[2].isdigit() else 1
    return f"{summary.get('draft') or ''}{DRAFT_SEP}{period}{DRAFT_SEP}{number}"


def draft(ctx, values) -> dict:
    """答完月份：出草稿（还不放工作簿），进复核卡。服务拒绝（税号不对、没权限）退回月份那一步。"""
    try:
        summary = _summary(ctx, values["period"])
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return {"returned": {"period": {"reason": "tax.request_invalid", "text": str(error)[:300]}}}
    return {"draft": _next_draft(ctx, summary, values["period"])}


def _choices(field) -> list:
    from pearnly_cowork.tax import wht

    if field == "income_type":
        return [{"code": code, "name": _income_name(code)} for code in wht.INCOME_CODES]
    if field == "form":
        return [{"code": form, "name": _(f"cowork.tax.form.{form}")} for form in wht.FORMS]
    if field == "include":
        return [{"code": "1", "name": _("Yes")}, {"code": "0", "name": _("cowork.tax.review.excluded")}]
    return []


def _editable() -> dict:
    return {field: {"input": kind, **({"options": _choices(field)} if kind == "choice" else {})}
            for field, kind in ROW_FIELDS.items()}


def _issues(row) -> list:
    return [_(f"cowork.tax.issue.{item.get('code')}") for item in row.get("issues") or () if item.get("code")]


def review(ctx, draft) -> dict:
    """复核卡：两张表、每行能就地改——待确认的行（算不算、日期、所得类型、税率、基数、税额、收款方税号、表；同 CC 工具的 edits）、
    还没定所得类型的收款方（改所得类型 = 按税号记住，整个工作空间）。合计行指回月份那一步。"""
    period = _period_of(draft)
    summary = _cached(ctx, period)
    pending = [{"code": str(row.get("row") or ""), "name": str(row.get("payee_name") or ""),
                "note": str(row.get("payee_tax_id") or ""), "amount": str(row.get("tax") or ""),
                "cells": {field: ("1" if row.get("include") else "0") if field == "include" else str(row.get(field) or "")
                          for field, _label in COLUMNS},
                "editable": _editable(), "issues": _issues(row)}
               for row in summary.get("review") or () if row.get("row")]
    payees = [{"code": PAYEE + _tax_id(item.get("payee_tax_id")), "name": str(item.get("payee_name") or ""),
               "note": _tax_id(item.get("payee_tax_id")), "amount": str(item.get("tax") or ""),
               "cells": {"income_type": str(item.get("suggested") or "")},
               "editable": {"income_type": {"input": "choice", "options": _choices("income_type")}}}
              for item in summary.get("questions") or () if _tax_id(item.get("payee_tax_id"))]
    forms = summary.get("forms") or {}
    return {"version": 1, "ready": True, "problem": "", "problem_text": "",
            "lines": [{"key": "period", "label_key": "guide.step.wht.period", "value": period, "step": "period"},
                      *[{"key": form, "label_key": f"cowork.tax.form.{form}", "value": str((forms.get(form) or {}).get("tax") or "0.00")}
                        for form in ("pnd53", "pnd3")]],
            "tables": [{"key": PAYEES, "label_key": "cowork.tax.review.questions", "rows": payees,
                        "columns": [{"key": "income_type", "label": _("cowork.tax.col.income_type")}]},
                       {"key": PENDING, "label_key": "cowork.tax.review.excluded", "rows": pending,
                        "columns": [{"key": key, "label": _(label)} for key, label in COLUMNS]}],
            "total": str((summary.get("totals") or {}).get("tax") or "0.00")}


def _refused(field, code="tax.edit_invalid", text="") -> dict:
    """被拒：码 + 已翻译的一句（前端显示在那一格下）。"""
    return {"refused": {field: code}, "refused_text": {field: text or _("tax.option_invalid")}}


def edit(ctx, draft, row, field, value) -> dict:
    """复核卡上改一格：收款方的所得类型按税号记住（`tax.service.answer`），待确认行的一格交给草稿的逐行改动（`edits`，同 CC 工具），
    再按同一个月重出草稿。服务不收回 ``refused``。（声明里加 ``edit`` 等 guided-erp 的 run.edit 对表单源分派进 main，约定见
    共享笔记「引导式-引擎通用能力N1-N9-约定-11-16.md」。）"""
    from pearnly_cowork.tax import service

    period, row, field = _period_of(draft), str(row or ""), str(field or "")
    if row.startswith(PAYEE):
        if field != "income_type":
            return _refused(field)
        with recon._conversation(ctx):
            try:
                service.answer([{"payee_tax_id": row[len(PAYEE):], "income_type": value}], draft.split(DRAFT_SEP)[0])
            except (frappe.ValidationError, frappe.PermissionError):
                frappe.db.rollback()
                frappe.clear_messages()
                return _refused(field)
        summary = _summary(ctx, period)
        return {"draft": _next_draft({**ctx, "draft": draft}, summary, period)}
    if field not in ROW_FIELDS:
        return _refused(field)
    given = (str(value) == "1") if field == "include" else value
    try:
        summary = _summary(ctx, period, [{"row": row, field: given}])
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return _refused(field, text=str(error)[:300])
    if summary.get("edits_rejected"):
        return _refused(field, str(summary["edits_rejected"][0].get("reason") or "tax.edit_invalid"))
    return {"draft": _next_draft({**ctx, "draft": draft}, summary, period)}


def confirm(ctx, values, draft, consent) -> dict:
    """点了「确定」：按复核后的草稿出工作簿放进对话，结果卡给两张表的收款方数与税额、还待确认几行。"""
    from pearnly_core.assistant import files as chat_files

    from pearnly_cowork.tax import service

    period = _period_of(draft) or values["period"]
    summary = _cached(ctx, period)
    with recon._conversation(ctx):
        filename, content = service.workbook(summary, frappe.local.lang or "th")
        path = chat_files.add_to_conversation(filename, content, XLSX, origin="tool")
        files = [{"file": chat_files.resolve(path), "name": path.rsplit("/", 1)[-1]}]
    forms = {form: {"payees": data.get("payees"), "tax": data.get("tax"), "base": data.get("base")}
             for form, data in (summary.get("forms") or {}).items()}
    return {"status": "ready", "draft": summary.get("draft") or "", "period": period, "forms": forms,
            "totals": summary.get("totals") or {}, "review_total": len(summary.get("review") or []),
            "questions_total": len(summary.get("questions") or []), "files": files}



def _draft_row(name):
    """确定时出的那份申报草稿此刻还在不在、合计多少（结果卡现算用，只读）。"""
    from pearnly_cowork.tax import service

    if not name:
        return None
    return frappe.db.get_value(service.DOCTYPE, name, ["name", "connection", "account_set", "period", "source_ref", "tax_total"],
                               as_dict=True)


def result(ctx, draft):
    """结果卡（现算）：草稿还在 → 已就绪、合计 = 草稿税额、工作簿；删了 → 未完成。确定回包在 ``ctx["done"]``；不是出了草稿的回 None。"""
    done = ctx.get("done") or {}
    if not isinstance(done, dict) or done.get("status") != "ready":
        return None
    row = _draft_row(str(done.get("draft") or ""))
    if not row:
        return {**done, "status": "failed", "files": []}
    return {**done, "status": "ready", "total": str(row.tax_total or ""), "files": list(done.get("files") or ())}


# ── 声明 ─────────────────────────────────────────────────────────────────────

GUIDES = [
    {"id": "wht", "label_key": "guide.business.wht", "icon": "receipt", "order": 80,
     "modes": ["chat"], "ledgers": ["express_local", "express_cloud", "mrerp"],
     "allowed": "pearnly_cowork.assistant.guided.wht.allowed",
     "opening_key": "guide.open.wht", "source": "form", "writes": False,
     "steps": [
         {"key": "period", "input": "month", "rule": "always",
          "default": "pearnly_cowork.assistant.guided.recon.period_default",
          "check": "pearnly_cowork.assistant.guided.wht.period_check",
          "facts": "pearnly_cowork.assistant.guided.wht.period_facts"},
     ],
     "draft": "pearnly_cowork.assistant.guided.wht.draft", "review": "pearnly_cowork.assistant.guided.wht.review",
     "confirm": "pearnly_cowork.assistant.guided.wht.confirm", "result": "pearnly_cowork.assistant.guided.wht.result",
     "tool": "cowork_wht_draft"},
]
