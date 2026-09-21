# -*- coding: utf-8 -*-
"""商品主数据。

停用（`is_active=0`）就是旧线上的"删除"：单据与库存记录继续指向它，所以这里
不做物理删除，也不允许从界面删掉已引用过的商品。

字段与旧线上 `services/sales/products.py` 的 products 列一一对应（不多不少）。
编码由系统发（`P-000001` 起，与旧线上 ERP 门户同一条规则），表单里只读。
"""

from frappe.model.document import Document


class ERPProduct(Document):
    def validate(self):
        self._validate_active_unit()

    def _validate_active_unit(self):
        """同一时间只能有一个默认销售单位，避免收银台取到两个价格。"""
        defaults = [row for row in self.get("units") or [] if row.is_default_sell]
        if len(defaults) > 1:
            for row in defaults[1:]:
                row.is_default_sell = 0
