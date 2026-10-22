# -*- coding: utf-8 -*-
"""仓库。**没有"默认仓"**（用户 2026-09-24："不要做默认仓，依赖全部删掉"）：
哪个单据、哪台收银台用哪个仓，都写在那个地方自己身上（见 `inventory/warehouses.py` 抬头）。

所以这个控制器**不做任何校验/联动**，只留着这个类（Frappe 按约定要这个模块）。
"""

from frappe.model.document import Document

from pearnly_erp_portal.express import locks as express_locks


class ERPWarehouse(Document):
    def validate(self):
        express_locks.guard(self)
