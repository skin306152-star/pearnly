"""补登记（ADR-019）：入口登记上线前开通的云端部署，按开户记录重新写一次计费服务上的「租户站点」登记。

与开户计划的 register 同一个调用（`billing_remote.provision_scope`，计费服务按站点更新、可重复执行），
只是不重跑其余开户步骤。在本机管理后台运行：
`bench --site <管理站点> execute pearnly_control.assistant_entry.backfill.run`
"""
import frappe

from pearnly_control import billing_remote, infra, provisioning
from pearnly_control.account import identity_pools


def run() -> list[str]:
    done = []
    for dep in frappe.get_all("Pearnly Deployment", filters={"status": "active"},
                              fields=["name", "issue", "product", "site_name"], order_by="creation asc"):
        if not dep.issue or not dep.site_name:
            continue
        issue = frappe.get_doc("Pearnly Issue", dep.issue)
        if not infra.is_cloud(issue.target):
            continue
        billing_remote.provision_scope(issue.tenant, dep.product, issue.tenant_name,
                                       owner_subject=identity_pools.subject_of(issue, dep.product),
                                       site=dep.site_name, issuer=provisioning._issuer_url(issue.target, dep.product),
                                       owner_username=issue.account_username, owner_email=issue.account_email)
        done.append(dep.site_name)
    print("registered", done)
    return done
