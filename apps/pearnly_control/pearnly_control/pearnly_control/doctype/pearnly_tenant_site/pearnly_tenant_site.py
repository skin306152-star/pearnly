from frappe.model.document import Document


class PearnlyTenantSite(Document):
    """租户每个产品站点的登记（智能体入口按它找账号所在的账号池与站点，ADR-019）。

    只由开户计划经管理通道写入（`billing_control.dispatch` 的 `register`），不在界面上改。
    """
