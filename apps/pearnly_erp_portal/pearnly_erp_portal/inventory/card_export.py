"""收发存报表导出（Excel / PDF）：照 MR.ERP「รายงานรับ-จ่ายสินค้า(สต็อคการ์ด)」三种——合并全部仓逐笔、汇总一物料一行、按仓分开逐笔。

评估与审定：docs/work/2026-11-08-stock-card-export-评估.md（§A 源码对照、§C 与 MR.ERP 的已知差异）。

- 网页（收发存页「导出」，`api.api_stock_card_export` → `pearnly_core.exports.deliver`）与 CC（ADR-027，`erp_stock_card_export`）
  调同一组函数：`size` 数行数（同时做权限检查），`build` 出文件。
- 取数：当前公司的仓库（`warehouses.scope_filter`）；行金额由成本引擎按「物料 × 仓 × 仓位」逐笔滚出（`report._project_position`，
  初始化并入上期结转，同收发存页面）；结余、小计、合计按 MR.ERP 报表算法（`card_rows`）。按物料分批读流水，内存不随全量涨。
- 版式：表头内容、栏位、分组、小计照 MR.ERP 模板；样式一套中性灰（主会话 11-08）。文字跟界面语言，泰文是 MR.ERP 原文；
  日期 dd/mm/yyyy，泰文界面佛历、其他公历。
"""

from __future__ import annotations

import html
import re
from decimal import ROUND_HALF_UP, Decimal
from io import BytesIO

import frappe
from frappe import _
from frappe.utils import getdate, now_datetime

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.inventory import card_rows, contracts, report, warehouses
from pearnly_erp_portal.masters import company as companies

REPORTS = ("all", "summary", "by_warehouse")
FORMATS = ("xlsx", "pdf")
ARGS = ("report", "format", "date_from", "date_to", "product_from", "product_to", "warehouse_from", "warehouse_to")
BATCH = 200
CHUNK = 500
INVALID = "Invalid value"
# 筛选校验照 MR.ERP 的原文（rpstkmm/component/javascript/rpstkmmform.js、rpstkmmstoreform.js）：说出是哪一栏。
# 物料、仓库「从 / 到」留空 = 不限（评估 §C-5），所以没有它们的「请选齐」。
DATE_REQUIRED = "trade.export.error.dateRequired"
DATE_ORDER = "trade.export.error.dateOrder"
PRODUCT_ORDER = "trade.export.error.productOrder"
WAREHOUSE_ORDER = "trade.export.error.warehouseOrder"
CENT = Decimal("0.01")


def _t(key, *args) -> str:
    """词条带 {0} {1} 占位：先按当前语言取译文再填（Frappe 的 `_()` 不接格式参数）。"""
    return _(key).format(*args)
CONTENT_TYPES = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "pdf": "application/pdf"}
TITLES = {"all": "trade.export.title.all", "summary": "trade.export.title.summary",
          "by_warehouse": "trade.export.title.byWarehouse"}
DETAIL_COLUMNS = 11
SUMMARY_COLUMNS = 14


# ── 参数与范围 ─────────────────────────────────────────────────────────────────────

def _params(report=None, format=None, date_from=None, date_to=None, product_from=None, product_to=None,
            warehouse_from=None, warehouse_to=None, **_ignored) -> dict:
    kind = str(report or "").strip()
    output = str(format or "").strip()
    if kind not in REPORTS or output not in FORMATS:
        frappe.throw(_(INVALID))
    if not date_from or not date_to:
        frappe.throw(_(DATE_REQUIRED))
    try:
        start, end = getdate(date_from), getdate(date_to)
    except ValueError:
        frappe.throw(_(DATE_REQUIRED))
    if start > end:
        frappe.throw(_(DATE_ORDER))
    clean = lambda value: str(value or "").strip()  # noqa: E731
    return {"report": kind, "format": output, "date_from": str(start), "date_to": str(end),
            "product_from": clean(product_from), "product_to": clean(product_to),
            "warehouse_from": clean(warehouse_from) if kind == "by_warehouse" else "",
            "warehouse_to": clean(warehouse_to) if kind == "by_warehouse" else ""}


def _product_code(row) -> str:
    """物料代码：打印、页面同一个（Express 码优先，否则本系统编码；`printing.context._code`）。"""
    return (row.get("express_code") or row.get("code") or row.get("name") or "").strip()


def _warehouse_code(row) -> str:
    """仓的编号：与仓库页「รหัสคลัง」同一个（`warehouses.display_code`），不拿仓名顶替。"""
    return warehouses.display_code(row)


def _product_master_range() -> tuple:
    """物料主档的第一个 / 最后一个代码（MR.ERP 筛选页的默认「从 ~ 到」）：当前公司看得到的物料 = 本地档 + 本公司 Express 档
    （`products.codes._scope`），代码口径同 `_product_code`，按库的排序规则（MR.ERP 也是库里 ORDER BY）。"""
    rows = frappe.db.sql(
        """select min(c), max(c) from (select coalesce(nullif(express_code, ''), nullif(code, ''), name) as c
           from `tabERP Product` where ifnull(express_company, '') in ('', %s)) as codes""",
        [companies.active_company()])
    first, last = (rows[0] if rows else (None, None))
    return (first or "").strip(), (last or "").strip()


def _warehouse_master_range() -> tuple:
    """当前公司仓库主档的第一个 / 最后一个编号（同仓库页的编号）。"""
    rows = _rows_by_name("ERP Warehouse", warehouses.names_of(), ["warehouse_name", "warehouse_code", "express_code"])
    codes = sorted((_warehouse_code(row) for row in rows.values()), key=card_rows.code_key)
    return (codes[0], codes[-1]) if codes else ("", "")


def _rows_by_name(doctype, names, fields) -> dict:
    out = {}
    names = sorted(set(names))
    for start in range(0, len(names), CHUNK):
        chunk = names[start:start + CHUNK]
        for row in frappe.get_all(doctype, filters={"name": ["in", chunk]}, fields=["name", *fields],
                                  limit_page_length=len(chunk)):
            out[row["name"]] = row
    return out


def _bound(value, rows, code_of) -> str:
    """范围的一头：页面传的是选中的记录名（Link），CC 传的可能是代码；认得出记录名就换成它的代码。"""
    if not value:
        return ""
    row = rows.get(value)
    return code_of(row) if row else value


def _selection(params) -> dict:
    """这次导出的范围：当前公司有流水的「物料 × 仓 × 仓位」，按物料代码区间（分仓版再按仓代码区间）取，按代码排。"""
    guard.require("report.read")
    sums = report._position_sums({"date_to": params["date_to"]}, params["date_from"])
    keys = sorted(sums)
    products = _rows_by_name("ERP Product", {key[0] for key in keys} | {params["product_from"], params["product_to"]} - {""},
                             ["product_name", "code", "express_code", "unit", "base_unit"])
    stores = _rows_by_name("ERP Warehouse", {key[1] for key in keys} | {params["warehouse_from"], params["warehouse_to"]} - {""},
                           ["warehouse_name", "warehouse_code", "express_code"])
    product_from = _bound(params["product_from"], products, _product_code)
    product_to = _bound(params["product_to"], products, _product_code)
    store_from = _bound(params["warehouse_from"], stores, _warehouse_code)
    store_to = _bound(params["warehouse_to"], stores, _warehouse_code)
    if product_from and product_to and card_rows.code_key(product_from) > card_rows.code_key(product_to):
        frappe.throw(_(PRODUCT_ORDER))
    if store_from and store_to and card_rows.code_key(store_from) > card_rows.code_key(store_to):
        frappe.throw(_(WAREHOUSE_ORDER))
    chosen = [key for key in keys
              if key[0] in products and card_rows.in_range(_product_code(products[key[0]]), product_from, product_to)
              and (params["report"] != "by_warehouse"
                   or (key[1] in stores and card_rows.in_range(_warehouse_code(stores[key[1]]), store_from, store_to)))]
    product_ids = sorted({key[0] for key in chosen},
                         key=lambda name: (card_rows.code_key(_product_code(products[name])), name))
    store_ids = sorted({key[1] for key in chosen},
                       key=lambda name: (card_rows.code_key(_warehouse_code(stores[name])), name))
    # 表头的「从 ~ 到」：没选的那头写主档的第一个 / 最后一个代码（MR.ERP 筛选页默认就是主档首末，主会话 11-09）
    first_product, last_product = _product_master_range() if not (product_from and product_to) else ("", "")
    store_range = ("", "")
    if params["report"] == "by_warehouse" and not (store_from and store_to):
        store_range = _warehouse_master_range()
    return {"positions": chosen, "products": product_ids, "warehouses": store_ids, "product_rows": products,
            "warehouse_rows": stores,
            "product_range": (product_from or first_product, product_to or last_product),
            "warehouse_range": (store_from or store_range[0], store_to or store_range[1])}


def size(**kwargs) -> int:
    """要出的行数（`pearnly_core.exports` 据此决定当场给还是排后台），同时做与 `build` 一样的权限检查。"""
    params = _params(**kwargs)
    selection = _selection(params)
    if params["report"] == "summary" or not selection["products"]:
        return len(selection["products"])
    filters = [["posting_date", ">=", params["date_from"]], ["posting_date", "<=", params["date_to"]],
               ["warehouse", "in", selection["warehouses"]]]
    if params["product_from"] or params["product_to"]:
        filters.append(["product", "in", selection["products"]])
    return frappe.db.count(report.MOVEMENT, filters) + len(selection["products"])


# ── 取数：逐批物料 → 块 ─────────────────────────────────────────────────────────────

def _blocks(params, selection, costs):
    """按物料代码顺序逐批产出 `(物料, 仓库或 None, Block)`：合并 / 汇总一个物料一块，分仓一个「仓 × 物料」一块。"""
    wanted = set(selection["positions"])
    by_warehouse = params["report"] == "by_warehouse"
    for start in range(0, len(selection["products"]), BATCH):
        batch = selection["products"][start:start + BATCH]
        movements = report._movements(products=set(batch), date_to=params["date_to"])
        grouped: dict = {}
        for index, row in enumerate(movements):
            key = (row["product"], row["warehouse"], row.get("location") or "")
            if key in wanted:
                grouped.setdefault(key, []).append(dict(row, posting_date=report._date(row["posting_date"]), seq=index))
        parts: dict = {}
        for (product, store, location), rows in grouped.items():
            opening, _final, period = report._project_position(rows, params["date_from"])
            position = (store, location)
            target = parts.setdefault((product, store if by_warehouse else None), ([], []))
            target[0].append(card_rows.Opening(position, contracts.d(opening.qty), opening.value))
            target[1].extend(card_rows.Movement(
                date=row["posting_date"], doc=str(row.get("doc_no") or row.get("ref_id") or row.get("reason")
                                                  or row.get("movement_type") or ""),
                qty=contracts.d(row["qty_base"]), unit_price=row.get("unit_price"), amount=row.get("amount"),
                seq=row["seq"], position=position) for row in period)
        for product in batch:
            stores = selection["warehouses"] if by_warehouse else [None]
            for store in stores:
                if (product, store) in parts:
                    openings, rows = parts[(product, store)]
                    yield product, store, card_rows.block(openings, rows, costs=costs)


# ── 版面模型（Excel 与 PDF 共用一份）────────────────────────────────────────────────

def _cell(value=None, span=1, align="left", number=False, rowspan=1, pdf_align=None):
    return {"value": value, "span": span, "align": align, "number": number, "rowspan": rowspan,
            "pdf_align": pdf_align or align}


def _round(value):
    """两位小数、四舍五入（MR.ERP 库存卡是 decimal(12,2)、印出来用 PHP number_format，都是四舍五入）。
    Excel 与 PDF 都取这里的值，同一份数据两种格式一模一样（不让 Python 的取偶或 Excel 的显示各自舍入）。"""
    return None if value is None else Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def _num(value):
    return _cell(_round(value), align="right", number=True)


def _qty(value):
    """数量格：Excel 靠右（同 57.php），PDF 居中（58.php / 60.php / 199.php 的 Cell 对齐 "C"）。"""
    return _cell(_round(value), align="right", number=True, pdf_align="center")


def _blank(span=1):
    return _cell(None, span)


def _three(qty, unit_price, amount):
    return [_qty(qty), _num(unit_price), _num(amount)]


def _count(value) -> str:
    return f"{int(value):,}"


def _date_text(value) -> str:
    """dd/mm/yyyy；泰文界面佛历（MR.ERP 与收发存页面同一个写法），其他语言公历（主会话 11-08）。"""
    day = getdate(value)
    year = day.year + 543 if (frappe.local.lang or "").startswith("th") else day.year
    return f"{day.day:02d}/{day.month:02d}/{year}"


def _detail_head():
    return [
        {"kind": "head1", "cells": [_cell(_("trade.export.voucher"), 2, "center"),
                                    _cell(_("trade.export.receipts"), 3, "center"),
                                    _cell(_("trade.export.issues"), 3, "center"),
                                    _cell(_("trade.export.balance"), 3, "center")]},
        {"kind": "head2", "cells": [_cell(_(key), 1, "center") for key in (
            "trade.export.date", "trade.export.number", "trade.export.qty", "trade.export.unitPrice",
            "trade.export.receiptValue", "trade.export.qty", "trade.export.unitPrice", "trade.export.issueValue",
            "trade.export.qty", "trade.export.unitPrice", "trade.export.balanceValue")]},
    ]


def _summary_head():
    return [
        {"kind": "head1", "cells": [_cell(_("trade.export.code"), 1, "center", rowspan=2),
                                    _cell(_("trade.export.name"), 1, "center", rowspan=2),
                                    *[_cell(_(key), 3, "center") for key in (
                                        "trade.export.carried", "trade.export.receiptValue",
                                        "trade.export.issueValue", "trade.export.balanceValue")]]},
        {"kind": "head2", "cells": [_cell(None), _cell(None), *[_cell(_(key), 1, "center") for _block in range(4) for key in (
            "trade.export.qty", "trade.export.unitPrice", "trade.export.amount")]]},
    ]


def _product_rows(meta, item):
    """一个物料块（MR.ERP 57.php / 198.php）：物料标题、ยอดยกมา、逐笔。"""
    unit = meta.get("base_unit") or meta.get("unit") or ""
    rows = [{"kind": "product", "cells": [_cell(f"{_product_code(meta)}  {meta.get('product_name') or ''}", 8),
                                          _cell(_t("trade.export.unit", unit), 3, "right")]},
            {"kind": "carried", "cells": [_cell(_("trade.export.carried"), 2, "right"), _blank(6),
                                          *_three(item.opening_qty, item.opening_price, item.opening_amount)]}]
    for line in item.lines:
        own = _three(line.qty, line.unit_price, line.amount)
        empty = [_blank(), _blank(), _blank()]
        rows.append({"kind": "line", "cells": [_cell(_date_text(line.date), 1, "center"), _cell(line.doc, 1, "center"),
                                               *(own + empty if line.side == "in" else empty + own),
                                               *_three(line.bal_qty, line.bal_price, line.bal_amount)]})
    return rows


def _flows(label, total, closing):
    """小计 / 合计行：收数量、收金额、发数量、发金额（单价空）；`closing` 时再加结余数量、结余金额。"""
    tail = [_qty(total.closing_qty), _blank(), _num(total.closing_amount)] if closing else [_blank(), _blank(), _blank()]
    return [_cell(label, 2), _qty(total.in_qty), _blank(), _num(total.in_amount),
            _qty(total.out_qty), _blank(), _num(total.out_amount), *tail]


def _layout(params, selection, costs) -> dict:
    """整份报表：表头几行（左 / 右）、列数、表格行。"""
    kind = params["report"]
    company = frappe.db.get_value("ERP Company", companies.active_company(),
                                  ["company_name", "tax_id"], as_dict=True) or frappe._dict()
    dates = _t("trade.export.dateRange", _date_text(params["date_from"]), _date_text(params["date_to"]))
    products = _t("trade.export.productRange", *selection["product_range"])
    lines = [(company.company_name or "", ""), (_(TITLES[kind]), _t("trade.export.taxId", company.tax_id or ""))]
    if kind == "by_warehouse":
        lines += [(_t("trade.export.warehouseRange", *selection["warehouse_range"]), ""), (products, ""), (dates, "")]
    else:
        lines += [(dates, ""), (products, "")]
    meta = selection["product_rows"]
    body = []
    if kind == "summary":
        grand = card_rows.totals(costs)
        for product, _store, item in _blocks(params, selection, costs):
            if not item.shown_in_summary():
                continue
            grand.take(item)
            row = meta[product]
            body.append({"kind": "line", "cells": [_cell(_product_code(row)), _cell(row.get("product_name") or ""),
                                                   *_three(item.opening_qty, item.opening_price, item.opening_amount),
                                                   *_three(item.in_qty, item.in_price, item.in_amount),
                                                   *_three(item.out_qty, item.out_price, item.out_amount),
                                                   *_three(item.closing_qty, item.closing_price, item.closing_amount)]})
        if grand.any():
            body.append({"kind": "grand", "cells": [_cell(_t("trade.export.grand", _count(grand.blocks)), 2)] + [
                cell for qty, amount in ((grand.opening_qty, grand.opening_amount), (grand.in_qty, grand.in_amount),
                                         (grand.out_qty, grand.out_amount), (grand.closing_qty, grand.closing_amount))
                for cell in (_qty(qty), _blank(), _num(amount))]})
        return {"lines": lines, "columns": SUMMARY_COLUMNS, "head": _summary_head(), "body": body}
    if kind == "all":
        grand = card_rows.totals(costs)
        for product, _store, item in _blocks(params, selection, costs):
            if not item.shown_in_detail():
                continue
            grand.take(item)
            body += _product_rows(meta[product], item)
            body.append({"kind": "subtotal", "cells": _flows(_t("trade.export.rows", _count(len(item.lines))),
                                                            item, closing=False)})
            body.append({"kind": "gap", "cells": [_blank(DETAIL_COLUMNS)]})
        if grand.any():
            body.append({"kind": "grand", "cells": _flows(_t("trade.export.grand", _count(grand.blocks)),
                                                         grand, closing=True)})
        return {"lines": lines, "columns": DETAIL_COLUMNS, "head": _detail_head(), "body": body}
    # 分仓（198.php）：仓 → 物料；先按物料批次算完，再按仓的代码顺序排出
    blocks: dict = {}
    for product, store, item in _blocks(params, selection, costs):
        if item.shown_in_detail():
            blocks.setdefault(store, []).append((product, item))
    grand, stores = card_rows.totals(costs), 0
    store_rows = selection["warehouse_rows"]
    for store in selection["warehouses"]:
        items = blocks.get(store) or []
        if not items:
            continue
        row = store_rows.get(store) or {}
        body.append({"kind": "store", "cells": [_cell(f"{_warehouse_code(row)}  {row.get('warehouse_name') or ''}",
                                                      DETAIL_COLUMNS)]})
        subtotal = card_rows.totals(costs)
        for product, item in items:
            subtotal.take(item)
            body += _product_rows(meta[product], item)
            body.append({"kind": "subtotal", "cells": _flows(_("trade.export.productTotal"), item, closing=False)})
            body.append({"kind": "note", "cells": [_cell(_t("trade.export.productDetail", _count(len(item.lines))),
                                                         DETAIL_COLUMNS)]})
        body.append({"kind": "store-total", "cells": _flows(_("trade.export.warehouseTotal"), subtotal, closing=True)})
        body.append({"kind": "note", "cells": [_cell(_t("trade.export.warehouseDetail",
                                                        _count(subtotal.lines), _count(subtotal.blocks)), DETAIL_COLUMNS)]})
        body.append({"kind": "gap", "cells": [_blank(DETAIL_COLUMNS)]})
        grand.merge(subtotal)
        stores += 1
    if grand.any() or stores:
        body.append({"kind": "grand", "cells": _flows(_("trade.export.allTotal"), grand, closing=True)})
        body.append({"kind": "note", "cells": [_cell(_t("trade.export.allDetail",
                                                        _count(grand.lines), _count(grand.blocks), _count(stores)),
                                                     DETAIL_COLUMNS)]})
    return {"lines": lines, "columns": DETAIL_COLUMNS, "head": _detail_head(), "body": body}


# ── Excel ──────────────────────────────────────────────────────────────────────

NUMBER_FORMAT = "#,##0.00"
HEAD_FILL = "E7E7E7"
TOTAL_FILL = "F2F2F2"


def _safe_text(value):
    # 用户填的文字（单号、品名）不能变成 Excel 公式
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _xlsx(layout) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    book = Workbook()
    sheet = book.active
    sheet.title = "Report"
    columns = layout["columns"]
    last = get_column_letter(columns)
    font = Font(name="Tahoma", size=11)
    thin, medium, double = Side(style="thin"), Side(style="medium"), Side(style="double")
    fills = {kind: PatternFill("solid", fgColor=color) for kind, color in (
        ("head1", HEAD_FILL), ("head2", HEAD_FILL), ("store", TOTAL_FILL), ("store-total", TOTAL_FILL),
        ("grand", HEAD_FILL))}
    borders = {"subtotal": Border(top=thin, bottom=medium), "store-total": Border(top=thin, bottom=medium),
               "grand": Border(top=thin, bottom=double)}
    widths = [8] * (columns + 1)
    current = 0
    for left, right in layout["lines"]:
        current += 1
        if right:
            split = columns - 4
            sheet.merge_cells(start_row=current, start_column=1, end_row=current, end_column=split)
            sheet.merge_cells(start_row=current, start_column=split + 1, end_row=current, end_column=columns)
            sheet.cell(current, 1, _safe_text(left)).font = font
            cell = sheet.cell(current, split + 1, _safe_text(right))
            cell.font, cell.alignment = font, Alignment(horizontal="right", vertical="center")
        else:
            sheet.merge_cells(f"A{current}:{last}{current}")
            sheet.cell(current, 1, _safe_text(left)).font = font
    for row in [*layout["head"], *layout["body"]]:
        current += 1
        column = 1
        for spec in row["cells"]:
            span, rowspan = spec["span"], spec["rowspan"]
            if span > 1 or rowspan > 1:
                sheet.merge_cells(start_row=current, start_column=column, end_row=current + rowspan - 1,
                                  end_column=column + span - 1)
            cell = sheet.cell(current, column)
            value = spec["value"]
            if value is not None:
                cell.value = value if spec["number"] else _safe_text(value)
            if spec["number"]:
                cell.number_format = NUMBER_FORMAT
            cell.font = font
            cell.alignment = Alignment(horizontal=spec["align"], vertical="center", wrap_text=False)
            for offset in range(span):
                target = sheet.cell(current, column + offset)
                if row["kind"] in fills:
                    target.fill = fills[row["kind"]]
                if row["kind"] in borders:
                    target.border = borders[row["kind"]]
            if span == 1 and value is not None:
                text = f"{value:,.2f}" if spec["number"] else str(value)
                widths[column] = max(widths[column], min(60, len(text) + 2))
            column += span
    for index in range(1, columns + 1):
        sheet.column_dimensions[get_column_letter(index)].width = widths[index]
    for index in range(1, current + 1):
        sheet.row_dimensions[index].height = 23
    # 页面设置照 MR.ERP 模板：A4 竖、边距 0.5"、页眉右上「页码/总页数」
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.orientation = "portrait"
    sheet.page_margins.left = sheet.page_margins.right = sheet.page_margins.top = sheet.page_margins.bottom = 0.5
    sheet.page_margins.header = sheet.page_margins.footer = 0.3
    sheet.oddHeader.right.text = "&P/&N"
    sheet.oddHeader.right.size = 10
    sheet.oddHeader.right.font = "Tahoma"
    output = BytesIO()
    book.save(output)
    return output.getvalue()


# ── PDF ────────────────────────────────────────────────────────────────────────

DETAIL_WIDTHS = (21, 40, *([24] * 9))          # mm，照 58.php / 199.php（日期栏放宽 2 mm，佛历日期一行放得下）
SUMMARY_WIDTHS = (20, 29, *([19] * 12))        # mm，照 60.php


def _html_value(spec) -> str:
    value = spec["value"]
    if value is None:
        return ""
    if spec["number"]:
        return f"{Decimal(value):,.2f}"
    return html.escape(str(value))


def _html(layout) -> str:
    from pearnly_erp_portal.printing import context as printing

    columns = layout["columns"]
    widths = DETAIL_WIDTHS if columns == DETAIL_COLUMNS else SUMMARY_WIDTHS
    small = columns == SUMMARY_COLUMNS
    parts = [f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
{printing.fonts_css()}
body {{ margin: 0; font-family: "{printing.FONT_FAMILY}", "Loma", "FreeSerif", sans-serif; color: #222; }}
table.r {{ border-collapse: collapse; width: 277mm; table-layout: fixed; font-size: {"8.5pt" if small else "9pt"}; }}
table.r td, table.r th {{ padding: 1.2mm 1mm; overflow: hidden; word-wrap: break-word; }}
table.r thead {{ display: table-header-group; }}
table.r tr {{ page-break-inside: avoid; }}
table.r .t1 td {{ font-size: 15pt; font-weight: bold; border: 0; padding: 0 0 1mm; }}
table.r .tl td {{ font-size: 11.5pt; border: 0; padding: 0 0 1mm; }}
table.r .head th {{ background: #e7e7e7; border: 1px solid #8c8c8c; font-weight: bold; text-align: center; }}
table.r tbody td {{ border: 1px solid #bdbdbd; }}
table.r tbody td.right, table.r tbody td.center {{ white-space: nowrap; }}
table.r .product td, table.r .store td {{ font-weight: bold; }}
table.r .store td {{ background: #f2f2f2; }}
table.r .subtotal td, table.r .store-total td {{ background: #f2f2f2; font-weight: bold; }}
table.r .grand td {{ background: #e7e7e7; font-weight: bold; border-bottom: 3px double #555; }}
table.r .note td {{ border: 0; }}
table.r .gap td {{ border: 0; height: 3mm; padding: 0; }}
.right {{ text-align: right; }} .center {{ text-align: center; }} .left {{ text-align: left; }}
</style></head><body><table class="r"><colgroup>"""]
    parts += [f'<col style="width:{width}mm">' for width in widths]
    parts.append("</colgroup><thead>")
    for index, (left, right) in enumerate(layout["lines"]):
        css = "t1" if index == 0 else "tl"
        if right:
            split = columns - 5
            parts.append(f'<tr class="{css}"><td colspan="{split}">{html.escape(left)}</td>'
                         f'<td colspan="{columns - split}" class="right">{html.escape(right)}</td></tr>')
        else:
            parts.append(f'<tr class="{css}"><td colspan="{columns}">{html.escape(left)}</td></tr>')
    for row in layout["head"]:
        cells = "".join(f'<th colspan="{spec["span"]}" rowspan="{spec["rowspan"]}">{_html_value(spec)}</th>'
                        for spec in row["cells"]
                        if not (row["kind"] == "head2" and spec["value"] is None))
        parts.append(f'<tr class="head">{cells}</tr>')
    parts.append("</thead><tbody>")
    for row in layout["body"]:
        cells = "".join(f'<td colspan="{spec["span"]}" class="{spec["pdf_align"]}">{_html_value(spec)}</td>'
                        for spec in row["cells"])
        parts.append(f'<tr class="{row["kind"]}">{cells}</tr>')
    parts.append("</tbody></table></body></html>")
    return "".join(parts)


def _pdf(layout) -> bytes:
    from frappe.utils.pdf import get_pdf

    # 照 MR.ERP PDF：A4 横、每页右上「页码/总页数」（wkhtmltopdf 自带的页眉替换，不靠页面脚本）、每页重复表头（thead）
    return get_pdf(_html(layout), options={
        "orientation": "Landscape", "page-size": "A4", "margin-top": "12mm", "margin-bottom": "10mm",
        "margin-left": "10mm", "margin-right": "10mm", "header-right": "[page]/[topage]", "header-font-size": "9",
        "header-spacing": "3"})


# ── 出文件 ─────────────────────────────────────────────────────────────────────

def _filename(kind, output) -> str:
    """MR.ERP 的命名：「报表名 [日月年_时分秒]」；年份泰文界面佛历。去掉文件名里不能用的字符。"""
    now = now_datetime()
    year = now.year + 543 if (frappe.local.lang or "").startswith("th") else now.year
    title = re.sub(r'\s*[\\/:*?"<>|]+\s*', " - ", _(TITLES[kind])).strip()
    return f"{title} [{now.day:02d}{now.month:02d}{year}_{now:%H%M%S}].{output}"


def build(**kwargs) -> dict:
    """出文件：`{"filename", "content"}`。网页导出（排后台时以发起人身份、发起时语言跑）与 CC 工具共用。"""
    params = _params(**kwargs)
    selection = _selection(params)
    layout = _layout(params, selection, costs=guard.cost_visible())
    content = _xlsx(layout) if params["format"] == "xlsx" else _pdf(layout)
    return {"filename": _filename(params["report"], params["format"]), "content": content}


def content_type(output) -> str:
    return CONTENT_TYPES.get(output) or "application/octet-stream"
