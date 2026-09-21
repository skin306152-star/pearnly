# -*- coding: utf-8 -*-
"""LINE 收进来的单据草稿。

为什么要有这张表，而不是只放在 LINE 会话里：会话是**按 LINE 用户、几十钟就过期**的
临时态（`pearnly_core.line_session`），而"员工下班前传来的单据，老板第二天早上来确认"
是常态。旧线上草稿过期后的提示就是"รายการหมดอายุ"（单据已过期）—— 用户得重传一遍。
这里把草稿落成可列表、可回溯的记录，过期不再是丢件。
"""

import frappe
from frappe import _
from frappe.model.document import Document


class ERPLINEIntake(Document):
    def validate(self):
        if self.source_file and not self.file_hash:
            frappe.throw(_("A stored source file must carry its hash"))
        if self.status == "confirmed":
            # 方向不同，指向的单据不同：进项指向采购记录，销项指向销售单据。
            linked = self.purchase if self.direction == "purchase" else self.sales_document
            if not linked:
                frappe.throw(_("A confirmed draft must point at the record it created"))
