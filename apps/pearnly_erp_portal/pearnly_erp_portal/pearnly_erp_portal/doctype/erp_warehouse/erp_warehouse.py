# -*- coding: utf-8 -*-
"""仓库。**没有"默认仓"**（用户 2026-09-24："不要做默认仓，依赖全部删掉"）：
哪个单据、哪台收银台用哪个仓，都写在那个地方自己身上（见 `inventory/warehouses.py` 抬头）。

所以这个控制器不做仓的联动；只核 Express 写者锁与仓位编码不重（11-07）。
"""

import frappe
from frappe import _
from frappe.model.document import Document

from pearnly_erp_portal.express import locks as express_locks


class ERPWarehouse(Document):
    def validate(self):
        express_locks.guard(self)
        # 仓位编码同一仓库里不能重（11-07）：仓位清单页之外的保存路径（导入、后台、桌面表单）也拦
        from pearnly_erp_portal.inventory import locations

        duplicate = locations.duplicate_code(self.get("locations"))
        if duplicate:
            frappe.throw(_(locations.DUPLICATE_CODE) + f": {duplicate}")
