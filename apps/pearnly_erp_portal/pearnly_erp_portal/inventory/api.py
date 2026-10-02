"""库存域接口层。业务与权限在 services，这里只做参数解析。"""

import frappe
from frappe import _
from frappe.utils import flt

from pearnly_erp_portal.inventory import (
    count as counts,
    documents,
    openings,
    opening_batch,
    report,
    stock,
    workbook,
    warehouses,
)
from pearnly_erp_portal.numbering import service as numbering
from pearnly_erp_portal.access import guard

# 盘点表读不动时的提示（用户可见，走语言包）。解析层只报 token，文案在这一层翻。
STOCK_FILE_ERRORS = {
    "file_invalid": "That file could not be read; please use the template",
    "headers_invalid": "The sheet needs a product code or barcode column",
    "empty": "That file has no rows",
    "too_many_rows": "That file has too many rows",
}


def _clean(kwargs):
    kwargs.pop("cmd", None)
    kwargs.pop("internal", None)
    kwargs.pop("allow_inactive", None)
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
    guard.require("stock." + str(kwargs.get("direction")))
    kwargs.pop("numbering_direction", None)
    kwargs.pop("commit", None)
    _json_args(kwargs, "lines")
    return documents.create_entry(**_clean(kwargs))


@frappe.whitelist()
def api_list_stock_entries(**kwargs):
    return documents.list_entries(**_clean(kwargs))


@frappe.whitelist()
def api_next_stock_entry_number(**kwargs):
    """表单上显示"当前要发的单据号"（只读预览，不占号）—— MR.ERP 的表单也是这样把号
    填进只读输入框给用户看的。"""
    kwargs = _clean(kwargs)
    return {"doc_no": numbering.preview(direction=kwargs.get("direction"),
                                        on=kwargs.get("posting_date"))}


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
    """入库。不带仓库时落**这家公司卖货的那个仓**（`warehouses.receiving`）。"""
    guard.require("stock.in")
    _json_args(kwargs, "lines")
    kwargs = _clean(kwargs)
    warehouse = kwargs.pop("warehouse", None)
    return stock.apply_lines(warehouse=warehouse, movement_type="stock_in",
                             lines=kwargs.pop("lines"), **kwargs)


@frappe.whitelist(methods=["POST"])
def api_issue(**kwargs):
    """出库。"""
    guard.require("stock.out")
    _json_args(kwargs, "lines")
    kwargs = _clean(kwargs)
    warehouse = kwargs.pop("warehouse", None)
    return stock.apply_lines(warehouse=warehouse, movement_type="stock_out",
                             lines=kwargs.pop("lines"), **kwargs)


@frappe.whitelist(methods=["POST"])
def api_count(**kwargs):
    guard.require("stock.manage")
    _json_args(kwargs, "lines")
    kwargs = _clean(kwargs)
    warehouse = kwargs.pop("warehouse", None)
    return stock.count_lines(warehouse=warehouse, **kwargs)


@frappe.whitelist(methods=["POST"])
def api_adjust(**kwargs):
    guard.require("stock.manage")
    kwargs = _clean(kwargs)
    warehouse = kwargs.pop("warehouse", None)
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
    ("location", "stock.count.location"),
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


def _stock_export_rows(warehouse=None, q=None, include_zero=None, only_low=None):
    return stock.stock_overview(warehouse=warehouse, q=q, include_zero=bool(include_zero),
                                only_low=bool(only_low), limit=0)


def stock_export_size(**kwargs):
    """在库一览导出的行数（权限检查在 `stock_overview` 里，与生成同一道）。"""
    return len(_stock_export_rows(**kwargs))


def stock_export_file(**kwargs):
    return _csv(_stock_export_rows(**kwargs))


@frappe.whitelist()
def api_stock_export(**kwargs):
    """导出在库一览：与列表同一套筛选，导的是**全部**命中行，不只是当前那一页。
    小的当场回文件，大的排后台、做完推送下载（`pearnly_core.exports`）。"""
    from pearnly_core.exports import service as exports

    kwargs = _clean(kwargs)
    args = {key: kwargs.get(key) for key in ("warehouse", "q", "include_zero", "only_low") if kwargs.get(key)}
    return exports.deliver(count="pearnly_erp_portal.inventory.api.stock_export_size",
                           build="pearnly_erp_portal.inventory.api.stock_export_file", args=args,
                           filename="stock.csv", content_type="text/csv; charset=utf-8")


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
    ("location", "stock.count.location"),
    ("actual_positions", "stock.workbench.actualPositions"),
    ("book_qty", "stock.count.book"),
    ("counted_qty", "stock.count.counted"),
    ("difference", "stock.count.difference"),
    ("status", "stock.col.status"),
)
COUNT_DETAIL_COLUMNS = (
    ("code", "stock.col.code"),
    ("product_name", "stock.col.name"),
    ("book_warehouse_name", "stock.col.warehouse"),
    ("book_location", "stock.count.location"),
    ("warehouse_name", "stock.workbench.actualWarehouse"),
    ("location", "stock.workbench.actualLocation"),
    ("unit", "stock.col.unit"),
    ("qty", "stock.count.qty"),
    ("note", "stock.count.note"),
    ("counted_by", "stock.count.countedBy"),
    ("counted_at", "stock.count.countedAt"),
    ("state", "stock.col.status"),
    ("photo_total", "stock.count.photos"),
)


def _count_status_key(row) -> str:
    if not row.get("counted"):
        return "stock.count.rowUncounted"
    return "stock.count.rowDifference" if row.get("difference") or row.get("location_difference") else "stock.count.rowMatch"


def _count_datetime(value):
    """导出里的时间按界面同一条规矩：`2026-09-22 20:10`（库里的微秒不上表）。
    导出与页面看到同一个形状，省得又是一处一处报（用户 2026-09-22 口径）。"""
    if value in (None, ""):
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M")
    text = str(value).replace("T", " ").split(".")[0]
    return text[:16]


# 有差异的格子标红。色值与网页、差异看板同一个红（框架的 `--red-600`），
# 这样导出来的表和工作台上看到的是同一处红（用户 2026-09-22 口径）。
COUNT_DANGER_COLOR = "CC2929"


def _count_summary_marks(row, columns) -> set:
    """汇总行里"在讲差异"的那几格：差异数量 / 差异金额 / 状态，以及实际仓位对不上时那一格。"""
    fields = set()
    if row.get("counted") and flt(row.get("difference") or 0):
        fields |= {"difference", "status"}
        if row.get("difference_value") is not None:
            fields.add("difference_value")
    if row.get("location_difference"):
        fields.add("actual_positions")
    return {field for field in fields if field in columns}


def _count_detail_marks(entry, book, columns) -> set:
    """明细行里实盘仓库 / 仓位与账面不同的那几格；不计入合计的作废记录不标。"""
    if entry.get("voided") or not book:
        return set()
    fields = set()
    if entry.get("warehouse") != book.get("warehouse"):
        fields.add("warehouse_name")
    if (entry.get("location") or "") != (book.get("location") or ""):
        fields.add("location")
    return {field for field in fields if field in columns}


# 照片页里每张图的落脚尺寸（像素）。行高按它设，图不会溢到下一行。
COUNT_PHOTO_WIDTH = 200
COUNT_PHOTO_HEIGHT = 140


def _count_photo_files(entries) -> dict:
    """每条记录挂着的照片（私有 File），按记录名分组。读的权限跟记录本身同一档。"""
    names = [row["name"] for row in entries]
    if not names:
        return {}
    grouped = {}
    for row in frappe.get_list(
            "File", filters={"attached_to_doctype": counts.ENTRY, "attached_to_name": ("in", names)},
            fields=["name", "file_url", "file_name", "attached_to_name"],
            order_by="creation asc", limit_page_length=0):
        grouped.setdefault(row["attached_to_name"], []).append(row)
    return grouped


def _count_picture(photo_name: str):
    """把照片缩到表格放得下的尺寸，返回 (字节, 宽, 高)；读不动就返回空，别毁掉整份导出。"""
    import io

    try:
        content = frappe.get_doc("File", photo_name).get_content()
    except Exception:
        return None, 0, 0
    if not content:
        return None, 0, 0
    try:
        from PIL import Image as PILImage

        with PILImage.open(io.BytesIO(content)) as source:
            image = source.convert("RGB")
            scale = min(COUNT_PHOTO_WIDTH / image.width, COUNT_PHOTO_HEIGHT / image.height, 1)
            if scale < 1:
                image = image.resize((max(1, int(image.width * scale)),
                                      max(1, int(image.height * scale))))
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=85, optimize=True)
            return buffer.getvalue(), image.width, image.height
    except Exception:
        return content, 0, 0


def _count_photo_sheet(book, entries, summary_rows, detail_rows) -> None:
    """照片页（旧项目那份导出也有这一页）：一张图一行，带"返回汇总 / 返回记录"的跳转。"""
    grouped = _count_photo_files(entries)
    if not grouped:
        return

    import io

    from openpyxl.drawing.image import Image as SheetImage
    from openpyxl.styles import Font
    from openpyxl.worksheet.hyperlink import Hyperlink

    def link(cell, sheet_name, row, label):
        cell.value = label
        cell.hyperlink = Hyperlink(ref=cell.coordinate, location=f"'{sheet_name}'!A{row}",
                                   display=label)
        cell.font = Font(color="0563C1", underline="single")

    sheet = book.create_sheet(_label("stock.count.photos"))
    sheet.append([_label("stock.count.photos"), _label("stock.count.photoInfo"),
                  _label("stock.count.linkSummary"), _label("stock.count.linkEntries")])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for column, width in zip("ABCD", (26, 46, 16, 16)):
        sheet.column_dimensions[column].width = width
    row_number = 2
    for entry in entries:
        group = grouped.get(entry["name"]) or []
        if not group:
            continue
        caption = " / ".join(str(value) for value in (
            entry.get("product_name"), entry.get("warehouse_name"), entry.get("location"),
            entry.get("counted_by"), _count_datetime(entry.get("counted_at"))) if value not in (None, ""))
        if entry.get("voided"):
            caption = f"{caption} ({_label('stock.count.entryVoided')})"
        for photo in group:
            sheet.cell(row_number, 2, caption)
            link(sheet.cell(row_number, 3), "Summary",
                 summary_rows.get(entry.get("item"), 2), _label("stock.count.linkSummary"))
            link(sheet.cell(row_number, 4), "Entries",
                 detail_rows.get(entry["name"], 2), _label("stock.count.linkEntries"))
            content, width, height = _count_picture(photo["name"])
            if content:
                picture = SheetImage(io.BytesIO(content))
                if width and height:
                    picture.width, picture.height = width, height
                sheet.add_image(picture, f"A{row_number}")
            sheet.row_dimensions[row_number].height = 108
            row_number += 1


def _count_workbook(task, items, entries) -> bytes:
    """两张表：汇总（每行账面/实盘/差异）+ 明细（每条记录、谁、何时、几张照片）。"""
    import io

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, Side
    from openpyxl.utils import get_column_letter

    warehouses = {w["name"]: w["warehouse_name"] for w in frappe.get_list(
        "ERP Warehouse", fields=["name", "warehouse_name"], limit_page_length=0)}

    book = Workbook()
    columns = list(COUNT_COLUMNS)
    if any("difference_value" in row for row in items):
        columns.insert(-1, ("difference_value", "stock.count.differenceValue"))
    danger = Font(color=COUNT_DANGER_COLOR)
    summary_columns = {field: index for index, (field, _key) in enumerate(columns, start=1)}
    detail_columns = {field: index for index, (field, _key) in enumerate(COUNT_DETAIL_COLUMNS, start=1)}
    book_items = {row["name"]: row for row in items}
    summary = book.active
    summary.title = "Summary"
    summary.append([_label(key) for _field, key in columns])
    for cell in summary[1]:
        cell.font = Font(bold=True)
    for row in items:
        line = []
        for field, _key in columns:
            if field == "status":
                line.append(_label(_count_status_key(row)))
            elif field == "warehouse_name":
                line.append(warehouses.get(row.get("warehouse"), row.get("warehouse")))
            else:
                value = row.get(field)
                line.append("" if value is None else value)
        summary.append(line)
        for field in _count_summary_marks(row, summary_columns):
            summary.cell(summary.max_row, summary_columns[field]).font = danger
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
            elif field == "counted_at":
                line.append(_count_datetime(row.get(field)))
            else:
                value = row.get(field)
                line.append("" if value is None else value)
        detail.append(line)
        for field in _count_detail_marks(row, book_items.get(row.get("item")) or {}, detail_columns):
            detail.cell(detail.max_row, detail_columns[field]).font = danger
    for sheet in (summary, detail):
        for index, _key in enumerate(sheet is summary and columns or COUNT_DETAIL_COLUMNS,
                                     start=1):
            sheet.column_dimensions[get_column_letter(index)].width = 18
    # 照片页只在真有人传过图时才建（旧项目也是这么做的）。
    _count_photo_sheet(book, entries,
                       {row["name"]: index for index, row in enumerate(items, start=2)},
                       {row["name"]: index for index, row in enumerate(entries, start=2)})
    # 冻结首行 + 自动筛选 + 细边框：旧项目那份导出就是这样，读表的人不用自己再拉一遍。
    edge = Side(style="thin", color="A6ACB3")
    border = Border(left=edge, right=edge, top=edge, bottom=edge)
    for sheet in book:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        sheet.row_dimensions[1].height = 26
        for cells in sheet.iter_rows():
            for cell in cells:
                cell.alignment = Alignment(vertical="center")
                cell.border = border
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
    rows, problems = _stock_rows(file_url) if file_url else ([], [])
    task = counts.create_count(**{**kwargs, "snapshot": not file_url})
    if not file_url:
        return task
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

    # 取词就走共享那一份（`pearnly_core.browser_text.label`），别在页面层再写一遍 ——
    # 导出件、模板、界面必须是同一句话。
    return workbook.template_headers(browser_text.label)


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
def api_stock_count_entry_total(**kwargs):
    return counts.entries_total(**_clean(kwargs))


@frappe.whitelist()
def api_stock_count_photos(**kwargs):
    kwargs = _clean(kwargs)
    return counts.entry_photos(kwargs.get("entry"))


@frappe.whitelist()
def api_stock_count_item_photos(**kwargs):
    """该商品在本任务里的全部照片（记录弹窗表格下面那一片）。"""
    kwargs = _clean(kwargs)
    return counts.item_photos(count=kwargs.get("count"), item=kwargs.get("item"))


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
    return counts.close_count(kwargs.get("name"), review_token=kwargs.get("review_token"))


@frappe.whitelist(methods=["POST"])
def api_stock_count_post(**kwargs):
    kwargs = _clean(kwargs)
    return counts.post_count(kwargs.get("name"), review_token=kwargs.get("review_token"))


@frappe.whitelist()
def api_stock_count_review(name):
    return counts.review_count(name)


@frappe.whitelist()
def api_stock_count_lookup(**kwargs):
    return counts.lookup_count(**_clean(kwargs))


@frappe.whitelist(methods=["POST"])
def api_stock_count_recount(**kwargs):
    return counts.recount_product(**_clean(kwargs))


@frappe.whitelist(methods=["POST"])
def api_stock_count_delete(**kwargs):
    kwargs = _clean(kwargs)
    return counts.delete_count(kwargs.get("name"))


def stock_count_export_size(name=None):
    """盘点导出的行数：汇总行（任务上记着的品项数）+ 明细记录数；两处都带盘点的读权限检查。"""
    return int(counts.get_count(name).get("item_total") or 0) + int(counts.entries_total(count=name) or 0)


def stock_count_export_file(name=None):
    task = counts.get_count(name)
    items = counts.list_items(count=name, limit=0)
    entries = counts.list_entries(count=name, limit=0)
    return {"filename": f"{name}.xlsx", "content": _count_workbook(task, items, entries)}


@frappe.whitelist()
def api_stock_count_export(**kwargs):
    """导出盘点：汇总 + 明细两张表（明细里每条记录都带谁、何时、几张照片）。
    小的当场回文件，大的排后台、做完推送下载（`pearnly_core.exports`）。"""
    from pearnly_core.exports import service as exports

    name = _clean(kwargs).get("name")
    return exports.deliver(count="pearnly_erp_portal.inventory.api.stock_count_export_size",
                           build="pearnly_erp_portal.inventory.api.stock_count_export_file", args={"name": name},
                           filename=f"{name}.xlsx",
                           content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")



@frappe.whitelist()
def api_stock_summary(**kwargs):
    return report.summary(**_clean(kwargs))


@frappe.whitelist(methods=["POST"])
def api_preview_stock_opening(warehouse, as_of_date, rows, note=None):
    return opening_batch.preview(warehouse=warehouse, as_of_date=as_of_date, rows=rows, note=note)


def _opening_transaction(operation, **kwargs):
    """Retry only a dedicated local RPC with no caller-owned pending writes.

    MariaDB snapshot isolation can raise 1020 when a product lock is followed by
    a current read of a concurrently inserted opening. A fresh transaction must
    reauthorize and review every row. Domain callers use opening_batch directly;
    they own their surrounding transaction and are never silently restarted.
    """
    retry_safe = getattr(frappe.db, "transaction_writes", None) == 0
    for attempt in range(3):
        try:
            return operation(**kwargs)
        except frappe.QueryDeadlockError:
            if not retry_safe:
                raise
            frappe.db.rollback()
            if attempt == 2:
                raise


@frappe.whitelist(methods=["POST"])
def api_submit_stock_opening(warehouse, as_of_date, rows, review_token, note=None):
    return _opening_transaction(opening_batch.submit, warehouse=warehouse, as_of_date=as_of_date, rows=rows,
                                review_token=review_token, note=note)


@frappe.whitelist()
def api_stock_opening_template(warehouse):
    content = opening_batch.template(warehouse)
    frappe.local.response.filename = "stock-opening-template.xlsx"
    frappe.local.response.filecontent = content
    frappe.local.response.type = "binary"


@frappe.whitelist(methods=["POST"])
def api_read_stock_opening_file(file_url):
    return opening_batch.read_file(file_url)


@frappe.whitelist()
def api_list_openings(**kwargs):
    return openings.list_openings(**_clean(kwargs))


@frappe.whitelist(methods=["POST"])
def api_upsert_opening(**kwargs):
    return _opening_transaction(openings.upsert_opening, **_clean(kwargs))


@frappe.whitelist(methods=["POST"])
def api_rebuild_balance(**kwargs):
    """按流水重算结存。用于验证"结存确实能由流水重建"，也可用于修复。"""
    guard.require("warehouses.manage")
    kwargs = _clean(kwargs)
    return stock.rebuild_balance(kwargs.get("product"), kwargs.get("warehouse"), kwargs.get("location", ""))


@frappe.whitelist()
def api_stock_entry_total(**kwargs):
    kwargs = _clean(kwargs)
    kwargs.pop("start", None)
    kwargs.pop("limit", None)
    return len(documents.list_entries(**kwargs, limit=0))
