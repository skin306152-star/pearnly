"""库存域接口层。业务与权限在 services，这里只做参数解析。"""

import frappe
from frappe.utils import flt

from pearnly_erp_portal.inventory import (
    count as counts,
    documents,
    openings,
    report,
    stock,
    workbook,
    warehouses,
)

# 盘点表读不动时的提示（用户可见，走语言包）。解析层只报 token，文案在这一层翻。
STOCK_FILE_ERRORS = {
    "file_invalid": "That file could not be read; please use the template",
    "headers_invalid": "The sheet needs a product code or barcode column",
    "empty": "That file has no rows",
    "too_many_rows": "That file has too many rows",
}


def _clean(kwargs):
    kwargs.pop("cmd", None)
    return kwargs


def _json_args(kwargs, *names):
    for name in names:
        if isinstance(kwargs.get(name), str):
            kwargs[name] = frappe.parse_json(kwargs[name])
    return kwargs


# ── 仓库 ────────────────────────────────────────────────────────────────────


@frappe.whitelist()
def api_list_warehouses(**kwargs):
    return warehouses.list_warehouses(**_clean(kwargs))


@frappe.whitelist(methods=["POST"])
def api_create_warehouse(**kwargs):
    return warehouses.create_warehouse(**_clean(kwargs))


@frappe.whitelist(methods=["POST"])
def api_update_warehouse(**kwargs):
    kwargs = _clean(kwargs)
    return warehouses.update_warehouse(kwargs.pop("name", None), **kwargs)


# ── 出入库单据 ──────────────────────────────────────────────────────────────


@frappe.whitelist(methods=["POST"])
def api_create_stock_entry(**kwargs):
    _json_args(kwargs, "lines")
    return documents.create_entry(**_clean(kwargs))


@frappe.whitelist()
def api_list_stock_entries(**kwargs):
    return documents.list_entries(**_clean(kwargs))


@frappe.whitelist()
def api_get_stock_entry(**kwargs):
    kwargs = _clean(kwargs)
    return documents.get_entry(kwargs.get("name"))


@frappe.whitelist(methods=["POST"])
def api_update_stock_entry(**kwargs):
    _json_args(kwargs, "lines")
    kwargs = _clean(kwargs)
    name = kwargs.pop("name", None)
    return documents.update_entry(name, **kwargs)


@frappe.whitelist(methods=["POST"])
def api_submit_stock_entry(**kwargs):
    kwargs = _clean(kwargs)
    return documents.submit_entry(kwargs.get("name"))


@frappe.whitelist(methods=["POST"])
def api_void_stock_entry(**kwargs):
    kwargs = _clean(kwargs)
    return documents.void_entry(kwargs.get("name"))


# ── 直接动库存（盘点 / 调整 / 快速出入）─────────────────────────────────────


@frappe.whitelist(methods=["POST"])
def api_receive(**kwargs):
    """入库。不带仓库时落默认仓库。"""
    _json_args(kwargs, "lines")
    kwargs = _clean(kwargs)
    warehouse = kwargs.pop("warehouse", None) or warehouses.ensure_default()
    return stock.apply_lines(warehouse=warehouse, movement_type="stock_in",
                             lines=kwargs.pop("lines"), **kwargs)


@frappe.whitelist(methods=["POST"])
def api_issue(**kwargs):
    """出库。"""
    _json_args(kwargs, "lines")
    kwargs = _clean(kwargs)
    warehouse = kwargs.pop("warehouse", None) or warehouses.ensure_default()
    return stock.apply_lines(warehouse=warehouse, movement_type="stock_out",
                             lines=kwargs.pop("lines"), **kwargs)


@frappe.whitelist(methods=["POST"])
def api_count(**kwargs):
    _json_args(kwargs, "lines")
    kwargs = _clean(kwargs)
    warehouse = kwargs.pop("warehouse", None) or warehouses.ensure_default()
    return stock.count_lines(warehouse=warehouse, **kwargs)


@frappe.whitelist(methods=["POST"])
def api_adjust(**kwargs):
    kwargs = _clean(kwargs)
    warehouse = kwargs.pop("warehouse", None) or warehouses.ensure_default()
    return stock.apply_movement(warehouse=warehouse, movement_type="adjust", **kwargs)


# ── 在库、报表、期初 ────────────────────────────────────────────────────────


@frappe.whitelist()
def api_stock(**kwargs):
    return stock.stock_overview(**_clean(kwargs))


@frappe.whitelist()
def api_stock_totals(**kwargs):
    return stock.stock_totals(**_clean(kwargs))


@frappe.whitelist()
def api_stock_count(**kwargs):
    return stock.stock_count(**_clean(kwargs))


# 导出 CSV 的列：字段 → 语言包里的键（与页面用的是同一批键，措辞就不会两样）。
EXPORT_COLUMNS = (
    ("product_name", "stock.col.name"),
    ("code", "stock.col.code"),
    ("barcode", "stock.col.barcode"),
    ("warehouse_name", "stock.col.warehouse"),
    ("unit", "stock.col.unit"),
    ("qty_on_hand", "stock.col.onHand"),
    ("unit_cost", "stock.col.cost"),
    ("value", "stock.col.value"),
    ("status", "stock.col.status"),
)
STATUS_LABELS = {"ok": "stock.status.ok", "low": "stock.status.low", "out": "stock.status.out"}

# 导出里的数字列 → 最多几位（按实际值写，不留固定的尾随 0）。
# 与页面同一条规矩：用户 2026-09-22 定"全站涉及到数量、金额的地方都按实际显示"。
EXPORT_NUMBERS = {"qty_on_hand": 3, "unit_cost": 2, "value": 2}


def _num(value, decimals=3) -> str:
    """数字按实际值写成文本：8.000 → 8，3.500 → 3.5。空值仍然是空（不是 0）。"""
    if value is None or value == "":
        return ""
    try:
        number = flt(value, decimals)
    except (TypeError, ValueError):
        return str(value)
    text = f"{number:.{decimals}f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _label(key) -> str:
    """取本站语言的那一句。**不走 `_()`**：短词（Code/Value/Status）会被框架自带的译文
    抢走（"Code" 会变成"代码"），导出与页面对不上；页面本来就是按**键**取包的。
    """
    from pearnly_core import browser_text

    lang = (frappe.local.lang or browser_text.FALLBACK_LANG)[:2]
    texts = browser_text.all_languages(key)
    return texts.get(lang) or texts.get("en") or key


def _csv(rows) -> bytes:
    """在库一览的 CSV。表头与状态都走语言包；成本没权限时那两列留空（不是 0）。"""
    import csv as csvlib
    import io

    buffer = io.StringIO()
    writer = csvlib.writer(buffer)
    writer.writerow([_label(key) for _field, key in EXPORT_COLUMNS])
    for row in rows:
        line = []
        for field, _key in EXPORT_COLUMNS:
            if field == "status":
                key = STATUS_LABELS.get(row.get(field))
                line.append(_label(key) if key else (row.get(field) or ""))
            elif field in EXPORT_NUMBERS:
                line.append(_num(row.get(field), EXPORT_NUMBERS[field]))
            else:
                value = row.get(field)
                line.append("" if value is None else value)
        writer.writerow(line)
    # BOM：Excel 打开泰文/中文不乱码。
    return buffer.getvalue().encode("utf-8-sig")


@frappe.whitelist()
def api_stock_export(**kwargs):
    """导出在库一览：与列表同一套筛选，导的是**全部**命中行，不只是当前那一页。"""
    kwargs = _clean(kwargs)
    rows = stock.stock_overview(warehouse=kwargs.get("warehouse"), q=kwargs.get("q"),
                                include_zero=bool(kwargs.get("include_zero")),
                                only_low=bool(kwargs.get("only_low")), limit=0)
    frappe.local.response.filename = "stock.csv"
    frappe.local.response.filecontent = _csv(rows)
    frappe.local.response.type = "binary"


@frappe.whitelist()
def api_stock_card(**kwargs):
    return report.card(**_clean(kwargs))


# ── 盘点（任务 / 记录 / 结束 / 过账 / 导出）──────────────────────────────────

# 导出用的列：字段 → 语言包里的键（与页面同一批键）。
COUNT_COLUMNS = (
    ("code", "stock.col.code"),
    ("product_name", "stock.col.name"),
    ("barcode", "stock.col.barcode"),
    ("unit", "stock.col.unit"),
    ("warehouse_name", "stock.col.warehouse"),
    ("book_qty", "stock.count.book"),
    ("counted_qty", "stock.count.counted"),
    ("difference", "stock.count.difference"),
    ("status", "stock.col.status"),
)
COUNT_DETAIL_COLUMNS = (
    ("code", "stock.col.code"),
    ("product_name", "stock.col.name"),
    ("warehouse_name", "stock.col.warehouse"),
    ("location", "stock.count.location"),
    ("unit", "stock.col.unit"),
    ("qty", "stock.count.qty"),
    ("counted_by", "stock.count.countedBy"),
    ("counted_at", "stock.count.countedAt"),
    ("state", "stock.count.entryState"),
    ("photo_total", "stock.count.photos"),
)


def _count_status_key(row) -> str:
    if not row.get("counted"):
        return "stock.count.rowUncounted"
    return "stock.count.rowDifference" if row.get("difference") else "stock.count.rowMatch"


def _count_workbook(task, items, entries) -> bytes:
    """两张表：汇总（每行账面/实盘/差异）+ 明细（每条记录、谁、何时、几张照片）。"""
    import io

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    warehouses = {w["name"]: w["warehouse_name"] for w in frappe.get_list(
        "ERP Warehouse", fields=["name", "warehouse_name"], limit_page_length=0)}

    book = Workbook()
    summary = book.active
    summary.title = "Summary"
    summary.append([_label(key) for _field, key in COUNT_COLUMNS])
    for cell in summary[1]:
        cell.font = Font(bold=True)
    placeholders = {w["name"]: w["warehouse_name"] for w in frappe.get_list(
        "ERP Warehouse", fields=["name", "warehouse_name"], limit_page_length=0)}
    for row in items:
        line = []
        for field, _key in COUNT_COLUMNS:
            if field == "status":
                line.append(_label(_count_status_key(row)))
            elif field == "warehouse_name":
                line.append(warehouses.get(row.get("warehouse"), row.get("warehouse")))
            else:
                value = row.get(field)
                line.append("" if value is None else value)
        summary.append(line)
    detail = book.create_sheet("Entries")
    detail.append([_label(key) for _field, key in COUNT_DETAIL_COLUMNS])
    for cell in detail[1]:
        cell.font = Font(bold=True)
    for row in entries:
        line = []
        for field, _key in COUNT_DETAIL_COLUMNS:
            if field == "state":
                line.append(_label("stock.count.entryVoided" if row.get("voided")
                                   else "stock.count.entryLive"))
            elif field == "warehouse_name":
                line.append(warehouses.get(row.get("warehouse"), row.get("warehouse")))
            else:
                value = row.get(field)
                line.append("" if value is None else value)
        detail.append(line)
    for sheet in (summary, detail):
        for index, _key in enumerate(sheet is summary and COUNT_COLUMNS or COUNT_DETAIL_COLUMNS,
                                     start=1):
            sheet.column_dimensions[get_column_letter(index)].width = 18
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="center")
    buffer = io.BytesIO()
    book.save(buffer)
    book.close()
    return buffer.getvalue()


@frappe.whitelist()
def api_list_stock_counts(**kwargs):
    return counts.list_counts(**_clean(kwargs))


@frappe.whitelist()
def api_stock_count_total(**kwargs):
    return counts.count_total(**_clean(kwargs))


@frappe.whitelist()
def api_get_stock_count(**kwargs):
    kwargs = _clean(kwargs)
    return counts.get_count(kwargs.get("name"))


@frappe.whitelist(methods=["POST"])
def api_create_stock_count(**kwargs):
    """开一份盘点。带 `file_url` 就是"上传导入"：先建空任务，再把表里的行并进去。"""
    kwargs = _clean(kwargs)
    file_url = str(kwargs.pop("file_url", "") or "").strip()
    task = counts.create_count(**{**kwargs, "snapshot": not file_url})
    if not file_url:
        return task
    rows, problems = _stock_rows(file_url)
    imported = counts.apply_import(count=task["name"], rows=rows)
    return {**task, "rows": imported["rows"], "counted": imported["counted"],
            "problems": imported["problems"] + problems}


@frappe.whitelist()
def api_stock_count_template(**kwargs):
    """下载盘点表。

    - 带 `name`：按那份盘点的快照给（把这次数到的填进"实盘数量"再传回来）；
    - 不带：按当前结存给（先导表、离线填、再开"上传导入"）。

    走 `binary` 直接回文件内容，不在租户里留 `File` 记录。
    """
    kwargs = _clean(kwargs)
    name = kwargs.get("name")
    rows = counts.template_rows(name=name, warehouse=kwargs.get("warehouse"))
    frappe.local.response.filename = f"{name or 'stock-count'}-template.xlsx"
    frappe.local.response.filecontent = workbook.build_bytes(_stock_headers(), rows)
    frappe.local.response.type = "binary"


@frappe.whitelist(methods=["POST"])
def api_stock_count_import(**kwargs):
    """往一份已开的盘点里传表（同一份表重复上传是**覆盖**那一行，不是又记一笔）。"""
    kwargs = _clean(kwargs)
    name = kwargs.get("name")
    rows, problems = _stock_rows(kwargs.get("file_url"))
    imported = counts.apply_import(count=name, rows=rows)
    return {**imported, "problems": imported["problems"] + problems}


@frappe.whitelist()
def api_stock_count_chart(**kwargs):
    return counts.count_chart(name=_clean(kwargs).get("name"))


def _stock_rows(file_url) -> tuple:
    """上传的原生文件（`File` 记录）按 URL 读回内容，再交给纯解析层。"""
    url = str(file_url or "").strip()
    name = frappe.db.get_value("File", {"file_url": url}, "name", order_by="creation desc") \
        if url else None
    if not name:
        frappe.throw(_("Choose a file to import"))
    try:
        return workbook.parse_workbook(frappe.get_doc("File", name).get_content(),
                                       workbook.header_aliases(_stock_label_texts()))
    except ValueError as error:
        frappe.throw(_(STOCK_FILE_ERRORS.get(str(error), "That file could not be read; "
                                                       "please use the template")))


def _stock_label_texts() -> dict:
    """界面标签 → 字段：四种语言都给（表可能是在另一种语言的站点上下载的）。"""
    from pearnly_core import browser_text

    labels: dict = {}
    for field, key in workbook.FIELD_LABELS:
        labels[key] = field
        for text in browser_text.all_languages(key).values():
            if text:
                labels[text] = field
    return labels


def _stock_headers() -> list:
    from pearnly_core import browser_text

    lang = (frappe.local.lang or browser_text.FALLBACK_LANG)[:2]

    def label_of(key):
        texts = browser_text.all_languages(key)
        return texts.get(lang) or texts.get("en") or key

    return workbook.template_headers(label_of)


@frappe.whitelist()
def api_stock_count_items(**kwargs):
    return counts.list_items(**_clean(kwargs))


@frappe.whitelist()
def api_stock_count_item_total(**kwargs):
    return counts.items_total(**_clean(kwargs))


@frappe.whitelist()
def api_stock_count_entries(**kwargs):
    return counts.list_entries(**_clean(kwargs))


@frappe.whitelist()
def api_stock_count_photos(**kwargs):
    kwargs = _clean(kwargs)
    return counts.entry_photos(kwargs.get("entry"))


@frappe.whitelist(methods=["POST"])
def api_stock_count_add_entry(**kwargs):
    _json_args(kwargs, "photos")
    return counts.add_entry(**_clean(kwargs))


@frappe.whitelist(methods=["POST"])
def api_stock_count_edit_entry(**kwargs):
    _json_args(kwargs, "photos")
    return counts.edit_entry(**_clean(kwargs))


@frappe.whitelist(methods=["POST"])
def api_stock_count_close(**kwargs):
    kwargs = _clean(kwargs)
    return counts.close_count(kwargs.get("name"))


@frappe.whitelist(methods=["POST"])
def api_stock_count_post(**kwargs):
    kwargs = _clean(kwargs)
    return counts.post_count(kwargs.get("name"))


@frappe.whitelist(methods=["POST"])
def api_stock_count_delete(**kwargs):
    kwargs = _clean(kwargs)
    return counts.delete_count(kwargs.get("name"))


@frappe.whitelist()
def api_stock_count_export(**kwargs):
    """导出盘点：汇总 + 明细两张表（明细里每条记录都带谁、何时、几张照片）。"""
    kwargs = _clean(kwargs)
    name = kwargs.get("name")
    task = counts.get_count(name)
    items = counts.list_items(count=name, limit=0)
    entries = counts.list_entries(count=name, limit=0)
    frappe.local.response.filename = f"{name}.xlsx"
    frappe.local.response.filecontent = _count_workbook(task, items, entries)
    frappe.local.response.type = "binary"



@frappe.whitelist()
def api_stock_summary(**kwargs):
    return report.summary(**_clean(kwargs))


@frappe.whitelist()
def api_list_openings(**kwargs):
    return openings.list_openings(**_clean(kwargs))


@frappe.whitelist(methods=["POST"])
def api_upsert_opening(**kwargs):
    return openings.upsert_opening(**_clean(kwargs))


@frappe.whitelist(methods=["POST"])
def api_rebuild_balance(**kwargs):
    """按流水重算结存。用于验证"结存确实能由流水重建"，也可用于修复。"""
    kwargs = _clean(kwargs)
    return stock.rebuild_balance(kwargs.get("product"), kwargs.get("warehouse"))
