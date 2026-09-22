"""主数据接口层。业务与权限在 services，这里只做参数解析。"""

import frappe

from pearnly_erp_portal.masters import company, customers


def _clean(kwargs):
    kwargs.pop("cmd", None)
    return kwargs


# ── 公司主档 / 公司资料 ─────────────────────────────────────────────────────
#
# 同一张表两个页面：列表页（公司主档）看登记过哪些公司；资料页（公司资料）看默认那条
# 的详细资料。不带 `name` 的读取与写入都落在默认公司上 —— 资料页不需要先知道公司名。


@frappe.whitelist()
def api_list_companies(**kwargs):
    return company.list_companies(**_clean(kwargs))


@frappe.whitelist(methods=["POST"])
def api_create_company(**kwargs):
    return company.create_company(**_clean(kwargs))


@frappe.whitelist()
def api_get_profile(**kwargs):
    kwargs = _clean(kwargs)
    return company.get_profile(kwargs.get("name"))


@frappe.whitelist(methods=["POST"])
def api_update_profile(**kwargs):
    return company.update_profile(**_clean(kwargs))


@frappe.whitelist(methods=["POST"])
def api_update_company(**kwargs):
    kwargs = _clean(kwargs)
    return company.update_company(kwargs.pop("name", None), **kwargs)


# ── 客户 ────────────────────────────────────────────────────────────────────


@frappe.whitelist()
def api_list_customers(**kwargs):
    return customers.list_customers(**_clean(kwargs))


@frappe.whitelist()
def api_get_customer(**kwargs):
    kwargs = _clean(kwargs)
    return customers.get_customer(kwargs.get("name"))


@frappe.whitelist(methods=["POST"])
def api_create_customer(**kwargs):
    return customers.create_customer(**_clean(kwargs))


@frappe.whitelist(methods=["POST"])
def api_update_customer(**kwargs):
    kwargs = _clean(kwargs)
    return customers.update_customer(kwargs.pop("name", None), **kwargs)


@frappe.whitelist(methods=["POST"])
def api_set_customer_active(**kwargs):
    kwargs = _clean(kwargs)
    return customers.set_active(kwargs.pop("name", None), kwargs.pop("is_active", 1))

