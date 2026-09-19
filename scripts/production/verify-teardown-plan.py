"""拆户计划的回归检查：跑在本地控制面容器里，只碰自己造的临时记录。

盯住两件踩过的事：

1. **没有发号单的租户也要能删。** 这种租户（例如只剩一条租户记录、站点和单子早被
   删掉）在排拆除时会现造一张占位发号单；那张单子必须跳过"必须选产品"的校验，
   否则用户点删除只看到一句「请选择一个产品」，而界面上根本没有产品可选。
2. **排出来的步骤形状。** 没有部署时只有 `purge_control` 一步，不该凭空多出站点步骤。

本脚本自己收尾（直接调 purge_records），用完不留记录、也不依赖宿主机执行器。
"""
import json
import os

import frappe

os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site="control.localhost")
frappe.connect()
frappe.set_user("Administrator")

from pearnly_control import billing, teardown

tenant = frappe.get_doc({"doctype": "Pearnly Tenant", "tenant_name": "teardown-plan-check"}).insert(
    ignore_permissions=True
)
billing.wallet_of(tenant.name)
frappe.db.commit()
print("temporary tenant:", tenant.name)

plan = teardown.api_plan_teardown(tenant.name)
assert plan["counts"]["total"] == 1, plan
assert [s["action"] for s in plan["steps"]] == ["purge_control"], plan
assert plan["status"] == "pending", plan
print("PASS orphan tenant (no issue, no deployment) queues a purge-only teardown")

request = frappe.get_doc("Pearnly Provisioning Request", plan["request"])
placeholder = frappe.get_doc("Pearnly Issue", request.issue)
assert placeholder.tenant == tenant.name
assert not placeholder.apps, "the placeholder must not invent products"
assert frappe.db.get_value("Pearnly Tenant", tenant.name, "status") == "deleting"
print("PASS placeholder order carries no products and the tenant is marked deleting")

# 收尾：执行器最后一步做的同一件事（清控制面记录），不依赖宿主机。
teardown.purge_records(plan["request"])
assert not frappe.db.exists("Pearnly Tenant", tenant.name)
assert not frappe.db.exists("Pearnly Issue", placeholder.name)
assert not frappe.db.exists("Pearnly Provisioning Request", plan["request"])
print("PASS records cleaned up; nothing left behind")
frappe.destroy()
