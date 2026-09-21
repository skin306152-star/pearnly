# -*- coding: utf-8 -*-
"""一笔收款。找零只出自现金，因此非现金多付会在结算时被拒。"""

from frappe.model.document import Document


class ERPSalePayment(Document):
    pass
