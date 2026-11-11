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
        refresh_tenant_summary(self.tenant)
        if self.has_value_changed("tenant"):
            refresh_tenant_summary((self.get_doc_before_save() or frappe._dict()).get("tenant"))

    def after_delete(self):
        refresh_tenant_summary(self.tenant)

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


def refresh_tenant_summary(tenant: str) -> None:
    """租户列表上的「用户名」「产品」两列（用户 2026-11-11）：用户名取开出这个租户的开户单，产品取它名下的部署。

    两列只读、只由这里写；本单据建出 / 变更 / 删除时调用，已有租户由补丁 ``v0_0_5_tenant_list_summary`` 回填。
    租户已删（拆除流程最后一步）就什么都不做。
    """
    if not tenant or not frappe.db.exists("Pearnly Tenant", tenant):
        return
    deployments = frappe.get_all(  # bounded: 一个租户的部署，每个产品至多一条（cowork / erp / dms）
        "Pearnly Deployment", filters={"tenant": tenant}, fields=["product", "issue"], order_by="creation asc")
    products = []
    for row in deployments:
        if row.product and row.product not in products:
            products.append(row.product)
    # 开户单回写 tenant 与建部署在同一事务里先后发生，这里两条路都认：按部署上记的开户单，或开户单上记的租户。
    issue = next((row.issue for row in deployments if row.issue), None)
    username = (frappe.db.get_value("Pearnly Issue", issue, "account_username") if issue else None) \
        or frappe.db.get_value("Pearnly Issue", {"tenant": tenant}, "account_username", order_by="creation asc") or ""
    frappe.db.set_value("Pearnly Tenant", tenant, {"account_username": username, "products": ", ".join(products)},
                        update_modified=False)
