# -*- coding: utf-8 -*-
"""DMS 操作员档案。

**校验放在服务层（`pearnly_dms.operators.roster`）**，这里只留 DocType 自己的约束 ——
两边各写一套规则迟早不一致，而"谁能改花名册"是权限问题，不属于文档校验。
"""

from frappe.model.document import Document


class DMSOperator(Document):
    def on_update(self):
        from pearnly_dms.operators.endpoints import sync_operator
        sync_operator(self)

    def on_trash(self):
        from pearnly_dms.operators.endpoints import disable_operator
        disable_operator(self)
