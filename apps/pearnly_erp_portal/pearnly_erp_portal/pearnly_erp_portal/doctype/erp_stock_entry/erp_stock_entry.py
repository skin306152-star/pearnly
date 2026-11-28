      # -*- coding: utf-8 -*-
"""入库 / 出库 / 调拨 / 物料调整单据。

单据只是"要动哪些货"的载体；一动就落成不可变流水（`ERP Stock Movement`），结存由
流水推动。提交前可改，提交后锁定，作废靠追回反向流水而不是删流水。
"""

import frappe
from frappe import _
from frappe.model.document import Document

from pearnly_erp_portal.express import ledger_guard


class ERPStockEntry(Document):
    def validate(self):
        ledger_guard.keep_current(self)  # 账本栏只由写账链路写：旧副本保存不盖回（11-28 B ③）
        if not self.get("lines"):
            frappe.throw(_("A stock entry needs at least one line"))
        for line in self.lines:
            if self.direction == "adjust":
                # 物料调整（岳父 11-06）：一张单正负混录，减少直接输负数；只是不能为 0
                if not line.qty:
                    frappe.throw(_("Quantity cannot be 0"))
            elif (line.qty or 0) <= 0:
                frappe.throw(_("Row {0}: quantity must be greater than 0").format(line.idx))
