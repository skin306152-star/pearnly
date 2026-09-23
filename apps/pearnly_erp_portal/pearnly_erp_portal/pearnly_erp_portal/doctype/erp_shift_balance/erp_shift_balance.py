# -*- coding: utf-8 -*-
"""班次里按支付方式的一行对账：期初 / 应有 / 实点 / 差额。

对齐 ERPNext 的 POS Opening/Closing Entry Detail（同一组列、同一个"实点减应有就是差额"）。
"""

from frappe.model.document import Document


class ERPShiftBalance(Document):
    pass
