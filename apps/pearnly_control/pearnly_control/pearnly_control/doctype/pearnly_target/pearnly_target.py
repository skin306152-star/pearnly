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
        self.line_login_channel_id = (self.line_login_channel_id or "").strip()
        if self.line_login_channel_id and not self.line_login_channel_id.isdigit():
            frappe.throw(_("Invalid LINE Login channel ID"))
        # LINE Login 频道要登记的回调地址：身份服务里这个频道的共用 LINE 入口（全平台只登记一次）。
        self.line_login_callback_url = (
            f"{self.base_url}/realms/{self.realm}/broker/line-{self.line_login_channel_id}/endpoint"
            if self.line_login_channel_id and self.realm else "")

    def on_update(self):
        """LINE 登录配置变了：把这个目标下可接 LINE 登录的渠道重排 `set_line`，已开通的租户随之开 / 关。"""
        if not (self.has_value_changed("line_login_channel_id")
                or self.has_value_changed("line_login_channel_secret")):
            return
        from pearnly_control.provisioning import requeue_line_login

        requeue_line_login(self.name)
