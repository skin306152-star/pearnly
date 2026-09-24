"""收银域接口层。业务与权限在 services，这里只做参数解析。"""

import frappe

from pearnly_erp_portal.cashier import counter, sales, shifts, terminals


def _clean(kwargs):
    kwargs.pop("cmd", None)
    return kwargs


def _json_args(kwargs, *names):
    for name in names:
        if isinstance(kwargs.get(name), str):
            kwargs[name] = frappe.parse_json(kwargs[name])
    return kwargs


# ── 收银台 ──────────────────────────────────────────────────────────────────


@frappe.whitelist()
def api_list_terminals(**kwargs):
    return terminals.list_terminals(**_clean(kwargs))


@frappe.whitelist(methods=["POST"])
def api_create_terminal(**kwargs):
    # `internal` 只给**服务端自己**（公司页那条路）用，客户端传进来一律丢掉 —— 否则
    # 谁都能借这个开关跳过 `cashier.manage` 那道闸。
    kwargs = _clean(kwargs)
    kwargs.pop("internal", None)
    return terminals.create_terminal(**kwargs)


@frappe.whitelist(methods=["POST"])
def api_update_terminal(**kwargs):
    kwargs = _clean(kwargs)
    kwargs.pop("internal", None)
    return terminals.update_terminal(kwargs.pop("name", None), **kwargs)


# ── 班次 ────────────────────────────────────────────────────────────────────


# ── 收银台（商品区）─────────────────────────────────────────────────────────


@frappe.whitelist()
def api_counter_products(**kwargs):
    return counter.products_page(**_clean(kwargs))


@frappe.whitelist()
def api_counter_lookup(**kwargs):
    kwargs = _clean(kwargs)
    return counter.lookup(kwargs.pop("value", None), **kwargs)


# ── 班次 ────────────────────────────────────────────────────────────────────


@frappe.whitelist(methods=["POST"])
def api_open_shift(**kwargs):
    _json_args(kwargs, "balances")
    return shifts.open_shift(**_clean(kwargs))


@frappe.whitelist()
def api_current_shift(**kwargs):
    return shifts.current_shift(**_clean(kwargs))


@frappe.whitelist()
def api_shift_summary(**kwargs):
    kwargs = _clean(kwargs)
    return shifts.summary(kwargs.get("name"))


@frappe.whitelist(methods=["POST"])
def api_close_shift(**kwargs):
    _json_args(kwargs, "counted")
    kwargs = _clean(kwargs)
    return shifts.close_shift(kwargs.pop("name", None), **kwargs)


@frappe.whitelist()
def api_list_shifts(**kwargs):
    return shifts.list_shifts(**_clean(kwargs))


@frappe.whitelist()
def api_shifts_export(names=None):
    """导出班次（交班记录页勾选的那几个；不给 names 就是整张列表）。"""
    from pearnly_erp_portal.cashier import output

    output.workbook(frappe.parse_json(names) if names else None)


# ── 销售 ────────────────────────────────────────────────────────────────────


@frappe.whitelist(methods=["POST"])
def api_create_sale(**kwargs):
    _json_args(kwargs, "lines", "payments")
    return sales.create_sale(**_clean(kwargs))


@frappe.whitelist()
def api_list_sales(**kwargs):
    kwargs.pop("current_only", None)
    return sales.list_sales(**_clean(kwargs))


@frappe.whitelist()
def api_get_sale(**kwargs):
    kwargs = _clean(kwargs)
    return sales.get_sale(kwargs.get("name"))


@frappe.whitelist(methods=["POST"])
def api_refund_sale(**kwargs):
    _json_args(kwargs, "lines", "payments")
    kwargs = _clean(kwargs)
    return sales.refund(kwargs.pop("name", None), **kwargs)


@frappe.whitelist(methods=["POST"])
def api_void_sale(**kwargs):
    kwargs = _clean(kwargs)
    return sales.void_sale(kwargs.pop("name", None), **kwargs)


@frappe.whitelist()
def api_sale_total(**kwargs):
    kwargs.pop("current_only", None)
    kwargs = _clean(kwargs)
    kwargs.pop("start", None)
    kwargs.pop("limit", None)
    return len(sales.list_sales(**kwargs, limit=0))


@frappe.whitelist()
def api_current_sales(**kwargs):
    kwargs.pop("current_only", None)
    return sales.list_sales(**_clean(kwargs), current_only=True)
