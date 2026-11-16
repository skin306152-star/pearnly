"""Cowork 增值税 ภ.พ.30 草稿的引导式（CC 对话里点「增值税」，总表 CW-D-VAT）：声明 :data:`GUIDES` 与它点名的产品函数。

只转调报税草稿的服务函数（`tax.service.vat_draft / workbook / previous_excess`，CC 工具 `cowork_vat_draft` 同一组），不另写规则：

- 月份（推荐对话月份）→ 这本账套这个月的回读（同对账：已读完不问，没有就出卡时请电脑读一次；本对话账套不是 Express 做不了）→
  第 3 栏免税销售额、第 10 栏上期结转的多缴税额（都可不填；第 10 栏推荐上月草稿第 12 栏、写明是哪个月的草稿，点了才算，
  主会话 11-16）→ 零税额的行算哪一栏（零税率 / 免税）→ 确定 → 草稿（第 1–12 栏、检查项数、工作簿进对话）。
- 只出草稿：不写账本、不写 Express、不代为申报。数值只来自卡上（CC 不转述）。
"""
from __future__ import annotations

import frappe
from frappe import _

from pearnly_cowork.assistant.guided import recon

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ZERO_VAT_AS = (("zero_rated", "cowork.tax.pp30.box.2"), ("exempt", "cowork.tax.pp30.box.3"))
PREVIOUS = "Box 12 of the {0} draft: {1}"


def allowed(user) -> bool:
    """菜单上出不出「增值税」：有报税草稿功能的人（`tax.access.allowed`，同 CC 工具）。"""
    from pearnly_cowork.tax import access

    return bool(user) and user != "Guest" and access.allowed(user)


def _place(ctx):
    from pearnly_cowork.assistant.guided import readback

    book = readback.book_default(ctx)
    return readback.place(ctx, book["value"]) if book else None


# ── 第 3、10 栏 ───────────────────────────────────────────────────────────────


def money_check(ctx, value) -> str:
    """金额不能是负数（形状由引擎核成数字；服务端出草稿时还会再核一次 `tax.option_invalid`）。"""
    from pearnly_core.assistant.guide import steps

    found = steps.amount(value)
    return "tax.option_invalid" if found is None or found < 0 else ""


def _previous(ctx) -> dict:
    from pearnly_cowork.tax import service

    place = _place(ctx)
    period = str((ctx.get("answers") or {}).get("period") or "")
    if not place or not period:
        return {}
    with recon._conversation(ctx):
        try:
            return service.previous_excess(place[0], place[1], period)
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError):
            frappe.clear_messages()
            return {}


def excess_default(ctx):
    """推荐上月草稿第 12 栏（点了才算）；上月没有草稿不推荐。"""
    found = _previous(ctx)
    return {"value": found["value"], "value_label": found["value"], "source": f"draft:{found['period']}"} if found else None


def excess_facts(ctx) -> list:
    """推荐值是哪个月草稿的第 12 栏（一行事实）。"""
    found = _previous(ctx)
    return [_(PREVIOUS).format(found["period"], found["value"])] if found else []


# ── 零税额的行 ───────────────────────────────────────────────────────────────


def zero_options(ctx, txt="", start=0, page_length=20, codes=None):
    rows = [{"code": code, "name": _(label)} for code, label in ZERO_VAT_AS]
    if codes is not None:
        rows = [row for row in rows if row["code"] in {str(code) for code in codes}]
    return {"options": rows, "more": False}


def zero_default(ctx):
    """推荐零税率（同服务的缺省 `tax.service._vat_options`）。"""
    return {"value": "zero_rated", "source": "default"}


# ── 确定 ─────────────────────────────────────────────────────────────────────


def confirm(ctx, values, draft, consent) -> dict:
    """点了「确定」：出这本账套这个月的草稿（`tax.service.vat_draft`），工作簿放进对话。没有回读退回回读那一步；
    服务拒绝（选项不对、没权限）退回第 3 栏那一步，带服务的原因。"""
    from pearnly_core.assistant import files as chat_files

    from pearnly_cowork.tax import service

    place = _place(ctx)
    if not place:
        return {"returned": {"readback": {"reason": "not_this_conversation_book"}}}
    options = {key: str(values[key]) for key in ("exempt_sales", "excess_brought_forward", "zero_vat_as")
               if values.get(key) not in (None, "")}
    with recon._conversation(ctx):
        try:
            summary = service.vat_draft(place[0], place[1], values["period"], options)
            if summary.get("status") == "readback_needed":
                return {"returned": {"readback": {"reason": "readback_needed"}}}
            if summary.get("status") != "drafted":
                return {"status": "failed", "reason_text": str(summary.get("reason") or summary.get("status") or "")}
            filename, content = service.workbook(summary, frappe.local.lang or "th")
            path = chat_files.add_to_conversation(filename, content, XLSX, origin="tool")
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
            frappe.db.rollback()
            frappe.clear_messages()
            return {"returned": {"exempt_sales": {"reason": "tax.option_invalid", "text": str(error)[:300]}}}
        files = [{"file": chat_files.resolve(path), "name": path.rsplit("/", 1)[-1]}]
    return {"status": "ready", "draft": summary.get("draft") or "", "period": summary.get("period") or "",
            "outcome": summary.get("outcome") or "", "boxes": dict(summary.get("boxes") or {}),
            "issues_total": len(summary.get("issues") or []), "files": files}


# ── 声明 ─────────────────────────────────────────────────────────────────────

GUIDES = [
    {"id": "vat", "label_key": "guide.business.vat", "icon": "percent", "order": 60,
     "modes": ["chat"], "ledgers": ["express_local", "express_cloud"],
     "allowed": "pearnly_cowork.assistant.guided.vat.allowed",
     "opening_key": "guide.open.vat", "source": "form", "writes": False,
     "steps": [
         {"key": "period", "input": "month", "rule": "always",
          "default": "pearnly_cowork.assistant.guided.recon.period_default",
          "check": "pearnly_cowork.assistant.guided.recon.period_check"},
         {"key": "readback", "input": "choice", "rule": "auto", "after": ["period"],
          "options": "pearnly_cowork.assistant.guided.recon.readback_options",
          "default": "pearnly_cowork.assistant.guided.recon.readback_default"},
         {"key": "exempt_sales", "input": "number", "rule": "always", "required": False, "after": ["readback"],
          "check": "pearnly_cowork.assistant.guided.vat.money_check"},
         {"key": "excess_brought_forward", "input": "number", "rule": "always", "required": False, "after": ["readback"],
          "default": "pearnly_cowork.assistant.guided.vat.excess_default",
          "facts": "pearnly_cowork.assistant.guided.vat.excess_facts",
          "check": "pearnly_cowork.assistant.guided.vat.money_check"},
         {"key": "zero_vat_as", "input": "choice", "rule": "always", "after": ["readback"],
          "options": "pearnly_cowork.assistant.guided.vat.zero_options",
          "default": "pearnly_cowork.assistant.guided.vat.zero_default"},
     ],
     "confirm": "pearnly_cowork.assistant.guided.vat.confirm",
     "tool": "cowork_vat_draft", "expires": ["ledger", "company"]},
]
