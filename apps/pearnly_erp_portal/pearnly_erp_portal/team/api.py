"""团队管理的接口层。业务与权限在 `team.service`，这里只做参数解析。"""

import frappe

from pearnly_erp_portal.team import service


@frappe.whitelist()
def api_list_members(**kwargs):
    kwargs.pop("cmd", None)
    return service.list_members()


@frappe.whitelist(methods=["POST"])
def api_add_member(**kwargs):
    kwargs.pop("cmd", None)
    return service.add_member(**kwargs)


@frappe.whitelist(methods=["POST"])
def api_set_member_roles(**kwargs):
    kwargs.pop("cmd", None)
    return service.set_member_roles(**kwargs)


@frappe.whitelist(methods=["POST"])
def api_set_member_active(**kwargs):
    kwargs.pop("cmd", None)
    return service.set_member_active(**kwargs)
