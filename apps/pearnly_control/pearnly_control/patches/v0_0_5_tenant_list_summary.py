"""租户列表的「用户名」「产品」两列（2026-11-11）：给已有租户回填一次，之后由部署记录的变更维护。"""
import frappe

from pearnly_control.pearnly_control.doctype.pearnly_deployment.pearnly_deployment import refresh_tenant_summary


def execute():
    for tenant in frappe.get_all("Pearnly Tenant", pluck="name"):
        refresh_tenant_summary(tenant)
