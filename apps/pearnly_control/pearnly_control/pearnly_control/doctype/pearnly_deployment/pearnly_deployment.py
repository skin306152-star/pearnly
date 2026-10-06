# -*- coding: utf-8 -*-
"""一个部署 = 公司 × 已开通产品 = 一个 Site。

标识用 `deployment_id`（本单据名），**域名只是绑定关系**：换域名改 `site_name`
不改单据名。这条是 P1.04 主体模型的硬要求，因为域名会变、也容易写错。
"""

import frappe
from frappe import _
from frappe.model.document import Document


# 拆除流程自己在删记录时会打这个 flag（见 `teardown.purge_records`）。
TEARDOWN_FLAG = "pearnly_teardown"


class PearnlyDeployment(Document):
    def on_update(self):
        """改了识别引擎（ADR-017）就下发到租户站点；新开的部署由开通计划带上。"""
        if self.product == "cowork" and not self.flags.in_insert and self.has_value_changed("ocr_engine"):
            from pearnly_control.provisioning import queue_ocr_engine

            queue_ocr_engine(self)

    def on_trash(self):
        """**一条部署记录背后是一台真实站点，不能单删。**

        为什么必须挡住（2026-09-19 实测踩到）：在 Desk 列表里直接删掉部署记录，
        只是把这条记录删了 —— 站点目录、数据库、证书、身份服务里的账号都还在，
        而控制面**再也找不到它**（拆除流程是靠部署记录才知道要删哪个站点）。
        结果就是"租户看着删掉了，域名其实还能打开、登录也还能用"，而且再也删不干净。

        要真的清空，走租户表单上的「删除租户」：它按部署记录排步骤，先是宿主机删
        站点与库，最后清身份账号与 OIDC 客户端。
        """
        if frappe.flags.get(TEARDOWN_FLAG):
            return
        frappe.throw(
            _("A deployment is a live site. Delete the tenant so its site and database go away too.")
        )
