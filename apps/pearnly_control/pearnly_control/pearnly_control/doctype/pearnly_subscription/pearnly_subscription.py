# -*- coding: utf-8 -*-
"""租户订阅（一租户一行）。

额度按**周期**算（订阅日起 30 天），与按量计费互不干扰：
有订阅 → 先吃套餐额度，超出的按套餐超额单价从余额扣；
没订阅 → 直接走阶梯价。这是老产品的口径。

字段值在订阅/续订时由 `pearnly_control.billing.subscribe()` 按目录写入 ——
**不要手改**：额度与单价是合同的产物，手改会让"页面上写的"和"实际扣的"不一致。
"""

from frappe.model.document import Document


class PearnlySubscription(Document):
    pass
