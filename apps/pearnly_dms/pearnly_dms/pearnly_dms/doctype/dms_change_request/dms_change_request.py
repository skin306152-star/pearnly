# -*- coding: utf-8 -*-
"""DMS 改写审批申请（销售改不动 → 提管理员批 → 以批准人凭据执行）。

**状态迁移不在这张表里做**，在 `pearnly_dms.dms_approval_store`：
那个模块用"Redis 锁 + 锁内读改写"实现"谁先点谁认领"（老产品是一条 Postgres CAS）。
这里只放 DocType 自己的字段说明与约束 —— 两边各写一套迁移规则迟早不一致。
"""

from frappe.model.document import Document


class DMSChangeRequest(Document):
    pass
