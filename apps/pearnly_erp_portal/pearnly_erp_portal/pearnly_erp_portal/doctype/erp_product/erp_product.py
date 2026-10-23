# -*- coding: utf-8 -*-
"""商品主数据。

停用（`is_active=0`）就是旧线上的"删除"：单据与库存记录继续指向它，所以这里
不做物理删除，也不允许从界面删掉已引用过的商品。

字段与旧线上 `services/sales/products.py` 的 products 列一一对应（不多不少）。
编码由系统发（`P-000001` 起，与旧线上 ERP 门户同一条规则），表单里只读。
"""

from frappe.model.document import Document

from pearnly_erp_portal.express import locks as express_locks


class ERPProduct(Document):
    def validate(self):
        express_locks.guard(self)
        self._validate_active_unit()
        # 编码在「公司 + 编码」上唯一（库里的唯一索引兜并发：两处同时发号拿到同一个 P- 号时后一件存不进）；同一编码在
        # 不同公司的 Express 档各有一件可以（2026-10-24 起编码不再全租户唯一，判重见 `products.service.taken`）
        self.code_key = f"{self.express_company or ''}|{self.code}" if self.code else None

    def _validate_active_unit(self):
        """同一时间只能有一个默认销售单位，避免收银台取到两个价格。"""
        defaults = [row for row in self.get("units") or [] if row.is_default_sell]
        if len(defaults) > 1:
            for row in defaults[1:]:
                row.is_default_sell = 0
