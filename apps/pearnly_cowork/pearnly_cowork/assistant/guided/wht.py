"""Cowork 预扣税 ภ.ง.ด.3 / 53 草稿的引导式（CC 对话里点「预扣税」，总表 CW-D-WHT）：声明 :data:`GUIDES` 与它点名的产品函数。

只转调报税草稿的服务函数（`tax.service.wht_draft / answer / workbook`，CC 工具 `cowork_wht_draft / cowork_wht_answer` 同一组）：

- 月份（推荐对话月份）→ 确定 → 草稿（ภ.ง.ด.53 法人、ภ.ง.ด.3 个人：收款方数、税额、待确认几行、工作簿进对话）→ 结果里没定所得类型的
  收款方每位一题（13 种所得类型，只一种税率时推荐对应类型、点了才算）→ 答了按收款方税号记住（整个工作空间，同 CC 工具）、重出草稿。
- **付款公司只认本对话公司**：税号由服务端从对话挂的公司主档取，卡上不选公司（主会话 11-16）。
- 「待确认的行」逐行改不在这里（另做「预扣税复核」）；网页没有报税草稿页面。只出草稿、不代为申报。
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


def _questions(summary) -> list:
    """草稿里没定所得类型的收款方 → 结果里的追问（每位一题，题目是收款方名称与税号原值）。"""
    from pearnly_cowork.tax import wht

    out = []
    for item in summary.get("questions") or ():
        payee = _tax_id(item.get("payee_tax_id"))
        if not payee:
            continue
        sub = {"key": PAYEE + payee, "input": "choice",
               "title": " ".join(part for part in (str(item.get("payee_name") or ""), payee) if part),
               "options": [{"code": code, "name": _income_name(code)} for code in wht.INCOME_CODES],
               "note": f"{item.get('base') or ''} / {item.get('tax') or ''}"}
        if item.get("suggested") in wht.INCOME_CODES:
            sub["preselected"] = item["suggested"]
        out.append(sub)
    return out


def _draft(ctx, period) -> dict:
    """出（或重出）这家这个月的草稿，工作簿放进对话；回结果卡。"""
    from pearnly_core.assistant import files as chat_files

    from pearnly_cowork.tax import service

    company = _company(ctx)
    with recon._conversation(ctx):
        summary = service.wht_draft(company["tax_id"], period, [], company["name"], company["branch"])
        filename, content = service.workbook(summary, frappe.local.lang or "th")
        path = chat_files.add_to_conversation(filename, content, XLSX, origin="tool")
        files = [{"file": chat_files.resolve(path), "name": path.rsplit("/", 1)[-1]}]
    forms = {form: {"payees": data.get("payees"), "tax": data.get("tax"), "base": data.get("base")}
             for form, data in (summary.get("forms") or {}).items()}
    out = {"status": "ready", "draft": summary.get("draft") or "", "period": summary.get("period") or period,
           "forms": forms, "totals": summary.get("totals") or {}, "review_total": len(summary.get("review") or []),
           "files": files}
    questions = _questions(summary)
    if questions:
        out["questions"] = questions
    return out


def confirm(ctx, values, draft, consent) -> dict:
    """点了「确定」：出草稿（付款公司 = 本对话公司）。服务拒绝（税号不对、没权限）退回月份那一步，带服务的原因。"""
    try:
        return _draft(ctx, values["period"])
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return {"returned": {"period": {"reason": "tax.request_invalid", "text": str(error)[:300]}}}


def follow(ctx, draft, answers) -> dict:
    """结果里的所得类型作答：按收款方税号记住（`tax.service.answer`，同 CC 工具），再按同一个月重出草稿。"""
    from pearnly_cowork.tax import service

    done = ctx.get("done") or {}
    items = [{"payee_tax_id": key[len(PAYEE):], "income_type": value} for key, value in answers.items()]
    with recon._conversation(ctx):
        service.answer(items, str(done.get("draft") or ""))
    return _draft(ctx, str(done.get("period") or (ctx.get("answers") or {}).get("period") or ""))


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
     "confirm": "pearnly_cowork.assistant.guided.wht.confirm",
     "result": "pearnly_cowork.assistant.guided.wht.result",
     "follow": "pearnly_cowork.assistant.guided.wht.follow",
     "tool": "cowork_wht_draft"},
]


def result(ctx, draft):
    """结果卡就是确定 / 作答时存下的那一份（引擎照存下的显示）。"""
    return None
