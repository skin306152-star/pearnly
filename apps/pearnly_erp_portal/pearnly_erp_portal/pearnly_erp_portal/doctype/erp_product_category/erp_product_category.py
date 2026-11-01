# -*- coding: utf-8 -*-
"""物料分类（见 `products/categories.py`）。原生写入也按产品口径判：新建要 `products.manage` 或盘点录入权 `count.write`，
改名 / 停用 / 删除只给 `products.manage`（盘点岗在就地新建弹窗里能建分类，不能改已有的）。"""

import frappe
from frappe import _
from frappe.model.document import Document

from pearnly_erp_portal.access import guard


class ERPProductCategory(Document):
    def validate(self):
        if self.is_new():
            from pearnly_erp_portal.products import categories

            categories.require_create()
        else:
            guard.require("products.manage")

    def on_trash(self):
        guard.require("products.manage")

    def before_rename(self, *args, **kwargs):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
