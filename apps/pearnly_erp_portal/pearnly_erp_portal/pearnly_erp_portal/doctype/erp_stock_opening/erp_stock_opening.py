      # -*- coding: utf-8 -*-
"""期初库存。

期初不是流水：它是收发存报表的起算点。期初日**当天及更早**的流水都算进期初，避免
"录了期初又录了同一天的入库"被重复计算。
"""

from frappe.model.document import Document


class ERPStockOpening(Document):
    pass
