"""ERP 公司的知识库（ADR-020，ERP 版 CC 按公司组织对话）：一条一句、团队共写，只经 `masters.company_notes` 写。

原生读写只给系统管理员看；页面与 CC 经 `masters.cc_companies` / `masters.cc_tools` 读写（权限在那里判）。
"""
from frappe.model.document import Document


class ERPCompanyNote(Document):
    pass
