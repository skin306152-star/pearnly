# -*- coding: utf-8 -*-
"""开户目标：一次发号要打到哪个环境。

控制面只在本机，但**开户动作要打到服务器上**。所以"打到哪"必须是一项配置，
不能写死在代码里：

- 本地样板：指向本机的身份服务，用来开发与验收。
- 生产：指向服务器上的身份服务与控制面接口，等服务器就绪后填。

凭据只在这里存一份，不写进代码、不写进日志。
"""

import frappe
from frappe import _
from frappe.model.document import Document


class PearnlyTarget(Document):
    def validate(self):
        if not self.target_name:
            frappe.throw(_("Target name is required"))
        if not self.base_url:
            frappe.throw(_("Service URL is required"))
        self.base_url = self.base_url.rstrip("/")
        if self.is_default:
            # 只能有一个默认目标
            others = frappe.get_all(
                "Pearnly Target",
                filters={"is_default": 1, "name": ("!=", self.name or "")},
                pluck="name",
            )
            for other in others:
                frappe.db.set_value("Pearnly Target", other, "is_default", 0)
