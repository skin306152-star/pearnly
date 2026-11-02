"""ERP 版 CC 的公司接口（ADR-020，2026-11-02）：共用层 `projects.listing` 把这个前缀（`cc_projects.API`）交给 CC 对话页，页面的
公司列表、公司页都调这里。方法名与形状同 Cowork（`pearnly_cowork.companies.api`），只有 Cowork 前端已有的
那几个，字段是子集；ERP 不提供建、改名、删（公司在 ERP 公司页建、改、停用），另有 `desk`（「添加公司」去 ERP 新建公司页）。
读用 GET；逻辑与权限在 `cc_service`。
"""
import frappe

from pearnly_erp_portal.masters import cc_service as service


@frappe.whitelist(methods=["GET"])
def companies(search=None, start=0, limit=service.PAGE_MAX):
    return service.listing(search, start, limit)


@frappe.whitelist(methods=["GET"])
def detail(company, period=None):
    return service.detail(company, period)


@frappe.whitelist(methods=["GET"])
def desk():
    return service.desk()
