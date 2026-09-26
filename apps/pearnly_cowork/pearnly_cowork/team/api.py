"""团队管理的接口层。业务与权限在 `team.service`，这里只做参数解析。"""

import frappe
from frappe.recorder import do_not_record

from pearnly_cowork.team import service


def _roles(value):
    return frappe.parse_json(value) if isinstance(value, str) else value


@frappe.whitelist()
def api_list_members():
    return {"members": service.list_members(), "roles": service.role_catalog()}


@frappe.whitelist(methods=["POST"])
@do_not_record
def api_add_member(username="", email="", roles=None, password=None):
    return service.add_member(username=username, email=email, roles=_roles(roles), password=password)


@frappe.whitelist(methods=["POST"])
def api_set_member_roles(email=None, roles=None):
    return service.set_member_roles(email, roles=_roles(roles))


@frappe.whitelist(methods=["POST"])
@do_not_record
def api_set_member_password(email=None, password=None):
    return service.set_member_password(email, password=password)


@frappe.whitelist(methods=["POST"])
def api_set_member_active(email=None, enabled=None):
    return service.set_member_active(email, enabled)
