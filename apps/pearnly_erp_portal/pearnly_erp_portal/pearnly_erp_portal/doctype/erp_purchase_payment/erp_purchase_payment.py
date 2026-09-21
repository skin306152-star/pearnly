# -*- coding: utf-8 -*-
"""采购单据的一笔付款。多笔累加成 `paid_amount`，不再另建一套付款台账。"""

from frappe.model.document import Document


class ERPPurchasePayment(Document):
    pass
