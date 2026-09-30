"""采购明细分类记忆：一个（供应商 + 品名）一行，记最近一次入账选的分类 / 子分类。

只有服务端在 `purchasing/category_memory.py` 里读写它，员工不直接看、也改不了。
"""

from frappe.model.document import Document


class ERPPurchaseCategoryMemory(Document):
    def autoname(self):
        from pearnly_erp_portal.purchasing import category_memory

        self.name = category_memory.memory_name(self.supplier, self.item_key)
