      # -*- coding: utf-8 -*-
"""库存流水。**不可变**：只允许创建与读取；改结存靠追加流水。

作废单据不删流水，而是追加一笔反向流水，历史仍然看得见。金额与单价在"成本从未
确立"时为空，这是有意的，不是缺省值。
"""

from frappe.model.document import Document


class ERPStockMovement(Document):
    pass
