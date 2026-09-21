"""库存域接口层。业务与权限在 services，这里只做参数解析。"""

import frappe

from pearnly_erp_portal.inventory import documents, openings, report, stock, warehouses


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
