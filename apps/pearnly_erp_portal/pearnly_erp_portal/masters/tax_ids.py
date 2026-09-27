"""保存公司 / 供应商 / 客户时的税号检查（录制问题 #11）：规则在 `contracts.tax_id_problem`，这里只负责报错。

只在税号有变动时检查：库里已有的旧值不挡住这条记录其他字段的修改。
"""
import frappe
from frappe import _

from pearnly_erp_portal.masters import contracts

# 与采购识别提示同一句（识别查到校验位不对时也是这句）。
CHECKSUM = "A tax ID failed its check digit"


def require_valid(value, *, previous=None, thai_required=False, format_message="company.lookup.invalid_format"):
    if str(value or "").strip() == str(previous or "").strip():
        return
    problem = contracts.tax_id_problem(value, thai_required=thai_required)
    if problem == contracts.TAX_ID_CHECKSUM:
        frappe.throw(_(CHECKSUM))
    if problem == contracts.TAX_ID_FORMAT:
        frappe.throw(_(format_message))
