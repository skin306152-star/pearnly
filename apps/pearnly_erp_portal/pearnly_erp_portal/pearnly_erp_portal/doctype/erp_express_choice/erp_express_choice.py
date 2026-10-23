"""ERP 单据写进 Express 时要人拿主意的项，选过记住（ADR-025）：按「公司 + 哪一类 + 对象」一行，只经 `express.choices` 写。

名字是这三样的摘要（同一项再选就是改这一行）；原生读写只给系统管理员看，页面经 `express.api` 读写。
"""
from frappe.model.document import Document


class ERPExpressChoice(Document):
    pass
