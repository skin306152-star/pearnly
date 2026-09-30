"""已开通的 ERP 站点补一家默认公司。

新站点安装时补丁清单里已有的 `v1_3_company_scope` 只被记为已跑、实际不执行，开通流程也没建过公司，
所以 mrerp 开通后一直没有公司，直到用户自己去公司资料页建（建出来的也不是默认公司）。
开通流程现在会建（`hooks.pearnly_tenant_ready`）；这里给已有站点补一次，幂等。
"""

import frappe

from pearnly_erp_portal.masters import company


def execute():
    if frappe.conf.get("pearnly_product") != "erp":
        return
    company.ensure_default()
