# -*- coding: utf-8 -*-
"""一个部署（公司 × 产品）一条自己的 LINE 渠道。

用户 2026-09-18 定的模型：**每个租户一个域名一个 app 一条 LINE**。

与旧系统的区别（这是本单据存在的理由）：
旧系统按**产品**共享一套凭据（环境变量 `LINE_<产品>_CHANNEL_SECRET`），
所有租户的 webhook 打同一个端点、用同一个密钥验签 —— 那意味着 A 租户的渠道
能往 B 租户的端点投事件。新模型里凭据是**每部署一条**：开通时推到该租户自己的
站点，站点只认自己那条密钥，别的签名一律验不过。隔离靠的是凭据本身，
不是靠路由里判断。

存量情况：DMS 那条在旧系统已经跑通；ERP 与 Cowork 用同一套接口，只是还没配渠道。
"""

import frappe
from frappe import _
from frappe.model.document import Document


class PearnlyLINEChannel(Document):
    def validate(self):
        dep = frappe.db.get_value(
            "Pearnly Deployment", self.deployment,
            ["product", "site_name", "port"], as_dict=True,
        )
        if not dep:
            frappe.throw(_("No such deployment: {0}").format(self.deployment))
        # 产品以部署为准，避免单据之间对不上
        self.product = dep.product
        self.webhook_url = f"http://{dep.site_name}:{dep.port}/api/method/pearnly_core.line.webhook"
