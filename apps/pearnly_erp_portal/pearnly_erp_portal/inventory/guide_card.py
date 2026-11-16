"""ERP 收发存报表的引导式（CC 对话里点「收发存报表」，总表 ERP-D-SC）：声明 `inventory.cc_guide_card.GUIDES` 点名的产品函数。

同收发存页的「导出」弹窗（`public/desk/stock-card.js` export_dialog）问的那几格：样式（合并全部仓 / 汇总 / 分仓）→ 格式（Excel / PDF，
默认 Excel、进「已定」可改）→ 起止日期（推荐本对话月份的首日、末日）→ 物料「从 / 到」（可空 = 不限）→ 仓「从 / 到」（只在分仓时问，
可空）→ 确定 → 文件放进对话。物料、仓的选项同弹窗的原生 Link（物料按 Frappe 原生 `search_widget` 服务端搜索、带权限；仓 = 本公司的仓）。
出文件只调 `card_export.build`（网页导出、CC 工具 ``erp_stock_card_export`` 同一个；权限、成本可见、范围、日期先后都由它核），
在本对话公司下执行（`masters.company.acting`）。只读：不写账本、不动库存。
"""
from __future__ import annotations

import calendar
import contextlib

import frappe
from frappe import _

REPORTS = (("all", "trade.export.all"), ("summary", "trade.export.summary"), ("by_warehouse", "trade.export.byWarehouse"))
FORMATS = (("xlsx", "Excel"), ("pdf", "PDF"))
PRODUCT = "ERP Product"
RANGE_FIELDS = ("product_from", "product_to", "warehouse_from", "warehouse_to")


@contextlib.contextmanager
def _acting(ctx):
    from pearnly_erp_portal.masters import company as companies

    with companies.acting(str(ctx.get("company") or "")):
        yield


@contextlib.contextmanager
def _conversation(ctx):
    before = getattr(frappe.local, "pearnly_assistant", None)
    frappe.local.pearnly_assistant = {**(before or {}), "session_id": str(ctx.get("session") or "")}
    try:
        yield
    finally:
        frappe.local.pearnly_assistant = before


def allowed(user) -> bool:
    """菜单上出不出「收发存报表」：有报表读取权的人（`card_export` 取数前 `guard.require("report.read")` 同一口径）。"""
    from pearnly_erp_portal.access import guard

    return bool(user) and user != "Guest" and guard.has("report.read", user)


def _only(rows, codes):
    wanted = {str(code) for code in codes}
    return {"options": [row for row in rows if row["code"] in wanted], "more": False}


def report_options(ctx, txt="", start=0, page_length=20, codes=None):
    rows = [{"code": code, "name": _(label)} for code, label in REPORTS]
    return _only(rows, codes) if codes is not None else {"options": rows, "more": False}


def format_options(ctx, txt="", start=0, page_length=20, codes=None):
    rows = [{"code": code, "name": label} for code, label in FORMATS]
    return _only(rows, codes) if codes is not None else {"options": rows, "more": False}


def format_default(ctx):
    """同弹窗默认 Excel（进「已定」，可改）。"""
    return {"value": "xlsx", "value_label": "Excel", "source": "default"}


def _month(ctx):
    period = str(ctx.get("period") or "")
    try:
        year, month = int(period[:4]), int(period[5:7])
    except ValueError:
        return None
    return year, month


def date_from_default(ctx):
    """推荐本对话月份的第一天（同弹窗带入页面当前区间）；对话没定月份就不推荐。"""
    found = _month(ctx)
    return {"value": f"{found[0]:04d}-{found[1]:02d}-01", "source": "conversation"} if found else None


def date_to_default(ctx):
    found = _month(ctx)
    if not found:
        return None
    return {"value": f"{found[0]:04d}-{found[1]:02d}-{calendar.monthrange(*found)[1]:02d}", "source": "conversation"}


def product_options(ctx, txt="", start=0, page_length=20, codes=None):
    """物料（同弹窗的原生 Link：`frappe.desk.search.search_widget`，按本人权限、服务端分页）；``codes`` 给了按本人能看的核
    （`frappe.get_list`）。"""
    from frappe.desk.search import search_widget

    with _acting(ctx):
        if codes is not None:
            wanted = [str(code) for code in codes]
            found = frappe.get_list(PRODUCT, filters={"name": ["in", wanted]}, fields=["name", "product_name"],
                                    limit_page_length=len(wanted) or 1)
            return {"options": [{"code": row.name, "name": row.product_name or row.name} for row in found], "more": False}
        size = max(1, min(int(page_length or 20), 50))
        rows = search_widget(PRODUCT, str(txt or "").strip(), start=max(0, int(start or 0)), page_length=size + 1, as_dict=True)
    out = [{"code": row["name"], "name": row.get("product_name") or row.get("title") or row["name"],
            **({"note": row["description"]} if row.get("description") else {})} for row in rows[:size]]
    return {"options": out, "more": len(rows) > size}


def warehouse_options(ctx, txt="", start=0, page_length=20, codes=None):
    """本公司的仓（同弹窗按当前公司过滤；一家公司的仓是有界小表）。"""
    from pearnly_erp_portal.inventory import count, warehouses

    with _acting(ctx):
        names = warehouses.names_of()
        labels = count._warehouse_labels(names)
    rows = [{"code": name, "name": labels.get(name) or name} for name in names]
    if codes is not None:
        return _only(rows, codes)
    text = str(txt or "").strip().casefold()
    rows = [row for row in rows if text in row["name"].casefold()] if text else rows
    start, size = max(0, int(start or 0)), max(1, int(page_length or 20))
    return {"options": rows[start:start + size], "more": len(rows) > start + size}


def to_check(ctx, value) -> str:
    """结束日期不早于开始日期（同 `card_export` 的口径；这里先挡住，免得确定了才失败）。"""
    begin = str((ctx.get("answers") or {}).get("date_from") or "")
    return "trade.export.error.dateOrder" if begin and str(value) < begin else ""


def draft(ctx, values) -> dict:
    return {"draft": "|".join(str(values.get(key) or "") for key in ("report", "format", "date_from", "date_to", *RANGE_FIELDS))}


def confirm(ctx, values, draft, consent) -> dict:
    """出文件（`card_export.build`）放进对话；业务拒绝（范围先后颠倒、没权限）退回日期那一步、带原因。"""
    from pearnly_core.assistant import files as chat_files
    from pearnly_erp_portal.inventory import card_export

    args = {key: values[key] for key in card_export.ARGS if values.get(key) not in (None, "")}
    with _acting(ctx), _conversation(ctx):
        try:
            built = card_export.build(**args)
            path = chat_files.add_to_conversation(built["filename"], built["content"], card_export.content_type(args.get("format")))
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
            frappe.clear_messages()
            return {"returned": {"date_from": {"reason": "stock_card_refused", "text": str(error)[:300]}}}
        return {"status": "entered", "files": [{"file": chat_files.resolve(path), "name": built["filename"]}]}
