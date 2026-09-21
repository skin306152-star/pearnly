      # -*- coding: utf-8 -*-
"""物化结存：每（商品, 仓库）一行。

命名由商品与仓库决定，所以同一个组合不可能出现两行 —— 并发下也不会。数量与加权平均
成本都从流水滚出来，因此可以直接重建。
"""

from frappe.model.document import Document


class ERPStockBalance(Document):
    pass
