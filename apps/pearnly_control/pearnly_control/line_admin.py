# -*- coding: utf-8 -*-
"""LINE 渠道的管理入口（控制面）。

渠道凭据是敏感字段（Password 类型，库里加密）。用函数来建/改，而不是让人手填
表单或拼 SQL：一是字段名不容易写错，二是可以在这里做校验（比如部署必须存在、
产品以部署为准）。

开通流程会读这条记录，把凭据推到该租户自己的站点上（见 provisioning.py 的
`_line_payload`）。
"""

import frappe
from frappe import _

CHANNEL = "Pearnly LINE Channel"


@frappe.whitelist(methods=["POST"])
def upsert_channel(deployment: str, channel_id: str = "", channel_secret: str = "",
                   channel_access_token: str = "", liff_id: str = "",
                   status: str = "active") -> dict:
    """建或更新某个部署的 LINE 渠道。幂等：同一部署只有一条。"""
    if not frappe.db.exists("Pearnly Deployment", deployment):
        frappe.throw(_("No such deployment: {0}").format(deployment))

    product = frappe.db.get_value("Pearnly Deployment", deployment, "product")
    existing = frappe.db.get_value(CHANNEL, {"deployment": deployment}, "name")
    payload = {
        "deployment": deployment,
        "product": product,
        "status": status,
        "channel_id": channel_id or "",
        "liff_id": liff_id or "",
    }
    # 凭据只在给了值时才覆盖，避免"改个备注把密钥清空了"
    if channel_secret:
        payload["channel_secret"] = channel_secret
    if channel_access_token:
        payload["channel_access_token"] = channel_access_token

    if existing:
        doc = frappe.get_doc(CHANNEL, existing)
        doc.update(payload)
        doc.save(ignore_permissions=True)
    else:
        if not channel_secret or not (channel_id or channel_access_token):
            frappe.throw(_("Creating a channel requires the channel secret and the Channel ID"))
        doc = frappe.get_doc({"doctype": CHANNEL, **payload})
        doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return {
        "name": doc.name,
        "deployment": deployment,
        "product": product,
        "webhook_url": doc.webhook_url,
        "status": doc.status,
    }


@frappe.whitelist()
def webhook_url_of(deployment: str) -> str:
    """该部署的 webhook 地址（填到 LINE 后台的那个）。"""
    name = frappe.db.get_value(CHANNEL, {"deployment": deployment}, "name")
    if not name:
        return ""
    return frappe.db.get_value(CHANNEL, name, "webhook_url") or ""
