# -*- coding: utf-8 -*-
"""供应商名录。停用而不是删除，历史单据继续指向它。"""

from frappe.model.document import Document

from pearnly_erp_portal.express import locks as express_locks


class ERPSupplier(Document):
    def validate(self):
        express_locks.guard(self)
        if self.branch_type != "branch":
            self.branch_no = None
