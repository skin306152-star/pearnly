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
