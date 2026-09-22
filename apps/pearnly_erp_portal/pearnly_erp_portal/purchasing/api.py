"""采购域接口层。业务与权限在 services，这里只做参数解析。"""

import frappe

from pearnly_erp_portal.purchasing import service, settings, suppliers


def _clean(kwargs):
    kwargs.pop("cmd", None)
    return kwargs


def _json_args(kwargs, *names):
    for name in names:
        if isinstance(kwargs.get(name), str):
            kwargs[name] = frappe.parse_json(kwargs[name])
    return kwargs


# ── 采购记录 ────────────────────────────────────────────────────────────────


@frappe.whitelist(methods=["POST"])
def api_create_purchase(**kwargs):
    _json_args(kwargs, "lines")
    return service.create_purchase(**_clean(kwargs))


@frappe.whitelist()
def api_list_purchases(**kwargs):
    return service.list_purchases(**_clean(kwargs))


@frappe.whitelist()
def api_purchase_total(**kwargs):
    return service.purchase_total(**_clean(kwargs))


@frappe.whitelist()
def api_get_purchase(**kwargs):
    kwargs = _clean(kwargs)
    return service.get_purchase(kwargs.get("name"))


@frappe.whitelist(methods=["POST"])
def api_update_purchase(**kwargs):
    _json_args(kwargs, "lines")
    kwargs = _clean(kwargs)
    name = kwargs.pop("name", None)
    return service.update_purchase(name, **kwargs)


@frappe.whitelist(methods=["POST"])
def api_submit_purchase(**kwargs):
    kwargs = _clean(kwargs)
    return service.set_status(kwargs.pop("name", None), "submitted",
                              receive_stock=kwargs.get("receive_stock"))


@frappe.whitelist(methods=["POST"])
def api_void_purchase(**kwargs):
    kwargs = _clean(kwargs)
    return service.set_status(kwargs.pop("name", None), "void", reason=kwargs.get("reason"))


@frappe.whitelist(methods=["POST"])
def api_pay_purchase(**kwargs):
    kwargs = _clean(kwargs)
    return service.pay(kwargs.pop("name", None), **kwargs)


@frappe.whitelist(methods=["POST"])
def api_set_payment_status(**kwargs):
    kwargs = _clean(kwargs)
    return service.set_payment_status(kwargs.pop("name", None), kwargs.get("status"))


@frappe.whitelist()
def api_summary(**kwargs):
    return service.summary(**_clean(kwargs))


# ── 供应商 ──────────────────────────────────────────────────────────────────


@frappe.whitelist()
def api_list_suppliers(**kwargs):
    return suppliers.list_suppliers(**_clean(kwargs))


@frappe.whitelist(methods=["POST"])
def api_create_supplier(**kwargs):
    return suppliers.create_supplier(**_clean(kwargs))


@frappe.whitelist(methods=["POST"])
def api_update_supplier(**kwargs):
    kwargs = _clean(kwargs)
    return suppliers.update_supplier(kwargs.pop("name", None), **kwargs)


# ── 采购设置 ────────────────────────────────────────────────────────────────


@frappe.whitelist()
def api_get_settings(**kwargs):
    return settings.get_settings()


@frappe.whitelist(methods=["POST"])
def api_update_settings(**kwargs):
    return settings.update_settings(**_clean(kwargs))
