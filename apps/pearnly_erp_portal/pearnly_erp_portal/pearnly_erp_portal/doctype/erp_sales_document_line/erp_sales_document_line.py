# -*- coding: utf-8 -*-
"""销售单据明细行。金额由父单在保存时按纯规则层统一重算并回写。"""

from frappe.model.document import Document


class ERPSalesDocumentLine(Document):
    pass
