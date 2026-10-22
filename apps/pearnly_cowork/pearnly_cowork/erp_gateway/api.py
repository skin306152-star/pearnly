"""网关的三个机器入口（只接 POST，身份见 `auth`）。Frappe 把 JSON 请求体放进参数，这里不用，按原始请求体读。"""
import frappe

from pearnly_cowork.erp_gateway import auth, service


@frappe.whitelist(allow_guest=True, methods=["POST"])
def submit(**_ignored):
    return auth.run(service.submit)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def status(**_ignored):
    return auth.run(service.status)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def masters(**_ignored):
    return auth.run(service.masters)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def company(**_ignored):
    return auth.run(service.company)
