# -*- coding: utf-8 -*-
"""这家公司做收银时**收哪几种钱**的一行（现金 / 刷卡 / 扫码 / 转账，各一行）。

对齐 ERPNext 的 POS Profile Payment（收银台自己声明支持哪些支付方式）：
建公司时默认四种全开，不需要的在公司页把那几行去掉。
"""

from frappe.model.document import Document


class ERPCompanyPaymentMethod(Document):
    pass
