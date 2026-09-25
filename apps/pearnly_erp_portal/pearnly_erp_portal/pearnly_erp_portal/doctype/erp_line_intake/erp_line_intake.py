# -*- coding: utf-8 -*-
"""LINE 收进来的一张单据：选项、识别结果与预览卡状态（内部记录，网页端不显示）。

用户 2026-09-25 定稿：它不是草稿，网页端列表里看不到，只能从 LINE 卡片的「编辑」进来；
在网页端原生页提交后指向生成的采购 / 销售单，之后再点「编辑」打开的是那张单。
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
