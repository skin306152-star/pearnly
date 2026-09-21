      # -*- coding: utf-8 -*-
"""入库 / 出库单据。

单据只是"要动哪些货"的载体；一动就落成不可变流水（`ERP Stock Movement`），结存由
流水推动。提交前可改，提交后锁定，作废靠追回反向流水而不是删流水。
"""

import frappe
from frappe import _
from frappe.model.document import Document


class ERPStockEntry(Document):
    def validate(self):
        if not self.get("lines"):
            frappe.throw(_("A stock entry needs at least one line"))
        for line in self.lines:
            if (line.qty or 0) <= 0:
                frappe.throw(_("Row {0}: quantity must be greater than 0").format(line.idx))
