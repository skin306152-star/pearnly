"""Cowork 收银日汇总（POS 导出）的引导式（CC 对话里点「收银日汇总」，总表 CW-D-POS）：声明 :data:`GUIDES` 与它点名的产品函数。

只转调 POS 导出的服务函数（`pos_import.service.preview / admit`，CC 工具 `cowork_pos_import / cowork_pos_layout` 同一组），不另写规则：

- 交导出（一份 Excel / CSV）→ 认列题（第一次见这种版式才问：哪列是日期 / 票号 / 金额…、日月顺序、金额含不含税、没见过的状态值；
  题目由 `preview` 的 ``questions`` 算出，引擎的子题输入；答了按公司记住，下次同版式不问）→ 入账方式（导出带商品明细才问：按金额记 /
  走库存）→ 复核（每天一行：日期、首末票号、税额、合计）→ 确定 → 建好每天一张日汇总票。
- **认列答案只来自卡上的点选**（P3 盘点 #31：以前由模型填 ``answers``），交给 `preview` 的形状由这里拼。
- 推送进 Express 不在这里另做：建好的票接到采购 / 销售那条单据引导（documents 出题源，主会话 11-16）；documents 源进 main 前
  这一格在总表算「服务端（未闭环）」。只接 Express（同 CC 工具）。
"""
from __future__ import annotations

import frappe
from frappe import _

from pearnly_cowork.assistant.guided import recon

FIELDS = {"date": "Date", "receipt": "Receipt No.", "amount": "Amount", "vat": "VAT", "payment": "Payment Method",
          "kind": "Receipt Type", "status": "Status", "item": "Item Name", "sku": "Product code", "qty": "Quantity"}
DATE_ORDERS = {"dmy": "Day/Month/Year", "mdy": "Month/Day/Year", "ymd": "Year/Month/Day"}
BASES = {"inclusive": "Includes VAT", "exclusive": "Excludes VAT"}
MEANINGS = {"sale": "Normal sale", "void": "Voided receipt", "refund": "Refund"}
POSTINGS = {"service": "By Amount", "stock": "With Stock"}
ITEM_FIELDS = ("item", "sku", "qty")


def allowed(user) -> bool:
    """菜单上出不出「收银日汇总」：有录入功能的人（同 CC 工具 `scope.require("intake")`）。"""
    from pearnly_cowork.access import scope

    if not user or user == "Guest":
        return False
    try:
        scope.require("intake")
    except frappe.PermissionError:
        frappe.clear_messages()
        return False
    return True


def _answers(ctx) -> dict:
    return ctx.get("answers") or {}


def _place(ctx):
    from pearnly_cowork.assistant.guided import readback

    book = readback.book_default(ctx)
    return readback.place(ctx, book["value"]) if book else None


def _source(ctx, file_ids) -> dict:
    """对话附件 → 录入工作台的原件（同 CC 工具：按本对话里的文件编号定请求号，同一个附件拿回同一份原件、同一批票）。"""
    from pearnly_cowork.assistant import tools

    return tools._pos_source(str((file_ids or [""])[0]))


def _preview(ctx, values=None) -> dict:
    """``preview``：``layout_needed``（问题）/ ``ready``（逐日汇总）/ ``failed``；认列答案按卡上的拼。"""
    from pearnly_cowork.access import scope
    from pearnly_cowork.pos_import import service

    values = values if values is not None else _answers(ctx)
    place = _place(ctx)
    if not place or not values.get("export"):
        return {"status": "none"}
    with recon._conversation(ctx):
        found = _source(ctx, values["export"])
        if not found.get("source"):
            return {"status": "failed", "reason_text": str(found.get("reason") or found.get("status") or "")}
        try:
            out = service.preview(found["source"], workspace=scope.default_workspace(), connection=place[0],
                                  account_set_key=place[1], answers=pos_answers(values.get("columns")),
                                  posting_kind=values.get("posting_kind") or None)
            frappe.db.commit()
        except (frappe.ValidationError, frappe.PermissionError) as error:
            frappe.db.rollback()
            frappe.clear_messages()
            return {"status": "failed", "reason_text": str(error)[:300]}
    if out.get("status") == "failed":
        out["reason_text"] = _(out.get("code") or "pos.file_unreadable")
    return out


# ── 交导出 ───────────────────────────────────────────────────────────────────


def export_check(ctx, value) -> str:
    """一次一份；本对话账套要是 Express（POS 只录 Express）；文件要读得出（同 CC 工具的类型判断）。"""
    if len(value or ()) != 1:
        return "pos.type_unsupported"
    if not _place(ctx):
        return "pos.not_express"
    found = _preview(ctx, {"export": value})
    return "" if found.get("status") in ("ready", "layout_needed") else str(found.get("code") or "pos.file_unreadable")


# ── 认列题（子题）───────────────────────────────────────────────────────────


def _subs(questions) -> list:
    """`pos_import.contracts` 的问题 → 引擎的子题（标题、选项都已翻译）。"""
    out = []
    for question in questions or ():
        kind = question.get("kind")
        if kind == "column":
            out.append({"key": f"column.{question['field']}", "input": "choice", "title": _(FIELDS.get(question["field"], question["field"])),
                        "options": [{"code": text, "name": text} for text in question.get("options") or ()]})
        elif kind == "date_order":
            out.append({"key": "date_order", "input": "choice", "title": _("Date Order"),
                        "options": [{"code": code, "name": _(DATE_ORDERS[code])} for code in question.get("options") or ()],
                        **({"note": ", ".join(str(item) for item in question.get("samples") or ())[:200]}
                           if question.get("samples") else {})})
        elif kind == "amount_basis":
            out.append({"key": "amount_basis", "input": "choice", "title": str(question.get("column") or _("Amount")),
                        "options": [{"code": code, "name": _(BASES[code])} for code in question.get("options") or ()]})
        elif kind == "status_values":
            for index, value in enumerate(question.get("values") or (), 1):
                out.append({"key": f"status.{index}", "input": "choice", "title": str(value),
                            "options": [{"code": code, "name": _(MEANINGS[code])} for code in question.get("options") or ()]})
    return out


def column_options(ctx, txt="", start=0, page_length=20, codes=None) -> dict:
    """这份导出此刻还要问的认列题（不带答案时 `preview` 回 ``layout_needed`` 的问题；认得出 / 记住过的版式没有题）。"""
    found = _preview(ctx, {**_answers(ctx), "columns": None})
    if found.get("status") != "layout_needed":
        return {"questions": []}
    return {"questions": _subs(found.get("questions")), "facts": [_("pos.layout_needed")]}


def pos_answers(columns) -> dict | None:
    """卡上认列子题的答案 → `pos_import.contracts.apply_answers` 要的形状（``columns`` / ``date_order`` / ``amount_basis`` /
    ``status_values``）。状态值那几题的题号先由 :func:`_with_status_values` 换成原值（``status.<原值>``）。"""
    if not columns:
        return None
    out: dict = {}
    for key, value in columns.items():
        if key.startswith("column."):
            out.setdefault("columns", {})[key.split(".", 1)[1]] = value
        elif key in ("date_order", "amount_basis"):
            out[key] = value
    statuses = {key.split(".", 1)[1]: value for key, value in columns.items() if key.startswith("status.")}
    if statuses:
        out["status_values"] = statuses   # 调用方已由 _with_status_values 把题号换成原值
    return out or None


def columns_check(ctx, value) -> str:
    """认列答完：拼好交给 `preview` 再核一次（列不在表头里、答了还有题都不收）。"""
    found = _preview(ctx, {**_answers(ctx), "columns": _with_status_values(ctx, value)})
    if found.get("status") == "layout_needed":
        return "pos.layout_invalid"
    return "" if found.get("status") == "ready" else str(found.get("code") or "pos.file_unreadable")


def _with_status_values(ctx, value) -> dict:
    """状态值子题的题号（``status.1``）换回原值：按出题时 `preview` 回的 ``values`` 顺序。"""
    if not value or not any(key.startswith("status.") for key in value):
        return value
    asked = _preview(ctx, {**_answers(ctx), "columns": None})
    values = next((q.get("values") or [] for q in asked.get("questions") or () if q.get("kind") == "status_values"), [])
    out = {key: item for key, item in value.items() if not key.startswith("status.")}
    for key, item in value.items():
        if key.startswith("status."):
            index = int(key.split(".", 1)[1]) - 1
            if 0 <= index < len(values):
                out[f"status.{values[index]}"] = item
    return out


# ── 入账方式（带商品明细才问）───────────────────────────────────────────────


def _has_items(ctx) -> bool:
    found = _preview(ctx, {**_answers(ctx), "columns": _with_status_values(ctx, _answers(ctx).get("columns"))})
    return found.get("status") == "ready" and any(name in ((found.get("layout") or {}).get("columns") or {})
                                                   for name in ITEM_FIELDS)


def posting_default(ctx):
    """导出没有商品明细：按金额记，不问。"""
    return None if _has_items(ctx) else {"value": "service", "source": "export"}


def posting_options(ctx, txt="", start=0, page_length=20, codes=None) -> dict:
    rows = [{"code": code, "name": _(label)} for code, label in POSTINGS.items()]
    if codes is not None:
        rows = [row for row in rows if row["code"] in {str(code) for code in codes}]
    return {"options": rows, "more": False}


# ── 草稿（逐日汇总）、复核、确定 ─────────────────────────────────────────────


def _values(ctx, values) -> dict:
    return {**values, "columns": _with_status_values(ctx, values.get("columns"))}


def draft(ctx, values) -> dict:
    """答完了：按卡上的认列与入账方式算逐日汇总（不建票），草稿号就是这份导出的文件编号；认列还不齐退回认列那一步。"""
    found = _preview(ctx, _values(ctx, values))
    if found.get("status") == "layout_needed":
        return {"returned": {"columns": {"reason": "pos.layout_invalid"}}}
    if found.get("status") != "ready":
        return {"returned": {"export": {"reason": str(found.get("code") or "pos.file_unreadable"),
                                        "text": str(found.get("reason_text") or "")}}}
    return {"draft": str(values["export"][0])}


def review(ctx, draft) -> dict:
    """复核卡：每天一行（日期、首末票号、税额、合计），点回交导出那一步改。"""
    found = _preview(ctx, _values(ctx, _answers(ctx)))
    days = found.get("days") or []
    total = sum((recon_decimal(day.get("gross")) for day in days), recon_decimal("0"))
    return {"version": 1, "ready": found.get("status") == "ready", "problem": "" if found.get("status") == "ready" else "pos",
            "problem_text": str(found.get("reason_text") or ""), "lines": [],
            "tables": [{"key": "days", "label_key": "guide.step.pos.days", "step": "export",
                        "rows": [{"code": day["date"], "name": day["date"], "note": day.get("range") or "",
                                  "amount": day.get("gross") or "0", "vat": day.get("vat") or "0"} for day in days]}],
            "total": f"{total:.2f}"}


def recon_decimal(value):
    from decimal import Decimal, InvalidOperation

    try:
        return Decimal(str(value or "0"))
    except InvalidOperation:
        return Decimal("0")


def confirm(ctx, values, draft, consent) -> dict:
    """点了「确定」：按天建票（`pos_import.service.admit`，按原件与请求号幂等）；版式此刻对不上退回认列那一步。"""
    from pearnly_cowork.access import scope
    from pearnly_cowork.pos_import import service

    place = _place(ctx)
    if not place:
        return {"returned": {"export": {"reason": "pos.not_express"}}}
    with recon._conversation(ctx):
        found = _source(ctx, values["export"])
        if not found.get("source"):
            return {"returned": {"export": {"reason": "pos.file_unreadable", "text": str(found.get("reason") or "")}}}
        try:
            made = service.admit(found["source"], found["key"], workspace=scope.default_workspace(), connection=place[0],
                                 account_set_key=place[1], posting_kind=values.get("posting_kind") or None)
        except (frappe.ValidationError, frappe.PermissionError) as error:
            frappe.db.rollback()
            frappe.clear_messages()
            return {"returned": {"export": {"reason": "pos.file_unreadable", "text": str(error)[:300]}}}
    if made.get("status") == "layout_needed":
        return {"returned": {"columns": {"reason": "pos.layout_invalid"}}}
    if made.get("status") != "ok":
        return {"returned": {"export": {"reason": str(made.get("code") or "pos.file_unreadable"),
                                        "text": _(made.get("code") or "pos.file_unreadable")}}}
    return {"status": "ready", "documents": list(made.get("histories") or ()),
            "days": [{"date": day["date"], "document": day["document"], "gross": day.get("gross"), "vat": day.get("vat")}
                     for day in made.get("days") or ()]}


# ── 声明 ─────────────────────────────────────────────────────────────────────

GUIDES = [
    {"id": "pos", "label_key": "guide.business.pos", "icon": "store", "order": 70,
     "modes": ["chat"], "ledgers": ["express_local", "express_cloud"],
     "allowed": "pearnly_cowork.assistant.guided.pos.allowed",
     "opening_key": "guide.open.pos", "source": "form", "writes": True,
     "steps": [
         {"key": "export", "input": "file", "rule": "always", "check": "pearnly_cowork.assistant.guided.pos.export_check"},
         {"key": "columns", "input": "questions", "rule": "always", "required": False, "after": ["export"],
          "options": "pearnly_cowork.assistant.guided.pos.column_options",
          "check": "pearnly_cowork.assistant.guided.pos.columns_check"},
         {"key": "posting_kind", "input": "choice", "rule": "auto", "after": ["export"],
          "options": "pearnly_cowork.assistant.guided.pos.posting_options",
          "default": "pearnly_cowork.assistant.guided.pos.posting_default"},
     ],
     "draft": "pearnly_cowork.assistant.guided.pos.draft", "review": "pearnly_cowork.assistant.guided.pos.review",
     "confirm": "pearnly_cowork.assistant.guided.pos.confirm",
     "tool": "cowork_pos_import", "expires": ["ledger", "company"]},
]
