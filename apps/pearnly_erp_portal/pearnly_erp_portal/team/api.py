"""团队管理的接口层。业务与权限在 `team.service`，这里只做参数解析。

一次取数就给全页面要的东西：员工名单 + **可勾选的岗位目录**（本租户买了的功能角色）。
页面因此不必自己维护"有哪些岗位"—— 目录与角色一旦不同步，就会发出空钥匙。
"""

import frappe
from frappe.recorder import do_not_record

from pearnly_erp_portal.team import service


def _clean(kwargs):
    kwargs.pop("cmd", None)
    return kwargs


def _json_args(kwargs, *names):
    for name in names:
        if isinstance(kwargs.get(name), str):
            kwargs[name] = frappe.parse_json(kwargs[name])
    return kwargs


@frappe.whitelist()
def api_list_members(**kwargs):
    return {"members": service.list_members(), "roles": service.role_catalog()}


@frappe.whitelist(methods=["POST"])
@do_not_record
def api_add_member(**kwargs):
    _json_args(kwargs, "roles", "companies")
    return service.add_member(**_clean(kwargs))


@frappe.whitelist(methods=["POST"])
def api_set_member_roles(**kwargs):
    _json_args(kwargs, "roles", "companies")
    kwargs = _clean(kwargs)
    return service.set_member_roles(kwargs.pop("email", None), **kwargs)


@frappe.whitelist(methods=["POST"])
def api_set_member_companies(**kwargs):
    """把账号指到指定的几家公司（空 = 不限制，官方 User Permission 那套）。"""
    _json_args(kwargs, "companies")
    kwargs = _clean(kwargs)
    return service.set_member_companies(kwargs.pop("email", None), **kwargs)


@frappe.whitelist(methods=["POST"])
@do_not_record
def api_set_member_password(**kwargs):
    kwargs = _clean(kwargs)
    return service.set_member_password(kwargs.pop("email", None), **kwargs)


@frappe.whitelist(methods=["POST"])
def api_set_member_active(**kwargs):
    kwargs = _clean(kwargs)
    return service.set_member_active(kwargs.pop("email", None), **kwargs)
