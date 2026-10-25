"""按台流水：每台每次进、出、退回、期初、盘点挪位一行；作废单据时按单据反向回退（`inventory.serials.reverse`）。"""

from frappe.model.document import Document


class ERPSerialMovement(Document):
    pass
