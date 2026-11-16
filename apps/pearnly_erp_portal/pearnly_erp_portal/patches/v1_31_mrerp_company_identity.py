"""接着 MR.ERP 的公司记上「这家 MR.ERP 公司」的标识（公司接入第 3 批，11-14：各年度账套归成一家，按单据日期选那一本）。

此前只记了人选的那一本账套（``mrerp_account_set``）；按那一本在目录里的公司算出标识（`mrerp.catalog.identity`）补上。
线上 MR.ERP 开关一直关着、没有这样的公司；这一步只为实验栈与测试库。目录里已没有那一本的公司不动（`books_of` 照旧按那一本找）。
"""

import frappe


def execute():
    from pearnly_erp_portal.mrerp import catalog

    rows = frappe.get_all("ERP Company", filters={"mrerp_account_set": ["is", "set"], "mrerp_connection": ["is", "set"]},
                          fields=["name", "mrerp_connection", "mrerp_account_set", "mrerp_company"],
                          limit_page_length=0)  # bounded: 一个租户的公司数
    for row in rows:
        if row.mrerp_company:
            continue
        found = next((item for item in catalog._sets(row.mrerp_connection) if item["source_id"] == row.mrerp_account_set),
                     None)
        if found:
            frappe.db.set_value("ERP Company", row.name, "mrerp_company",
                                catalog.identity(row.mrerp_connection, found.get("attributes") or {}), update_modified=False)
