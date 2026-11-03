"""ERP 自己的 MR.ERP 连接（评估 2026-11-02 §14.6）：网址、CC 桌面版授权写入的登录（Password 字段加密存库）、账套目录快照、
最近一次读到的导入菜单与主档可用值。只经 `pearnly_erp_portal.mrerp.connection` / `catalog` / `choices` 写；原生读写只给系统管理员看。
"""
from frappe.model.document import Document


class ERPMRERPConnection(Document):
    pass
