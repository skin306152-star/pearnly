"""按台记录：一台一条（车 = 车架号 / 发动机号 / 颜色；其他商品 = S/N）。状态只由单据过账与作废改（`inventory.serials`）。"""

from frappe.model.document import Document


class ERPSerialNo(Document):
    pass
