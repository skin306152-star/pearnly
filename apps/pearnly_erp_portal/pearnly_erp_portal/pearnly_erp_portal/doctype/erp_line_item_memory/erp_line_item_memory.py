"""明细物料记忆：一家公司里，一个（方向 | 对方 | 票面品名）一行，记最近一次提交时这一行对哪件物料、动不动库存、放哪个仓位。

只有服务端在 `products/line_memory.py` 里读写它，员工不直接看、也改不了。
"""

from frappe.model.document import Document


class ERPLineItemMemory(Document):
    def autoname(self):
        from pearnly_erp_portal.products import line_memory

        self.name = line_memory.memory_name(self.company, self.subject)
