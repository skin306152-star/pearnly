"""运维：把一个 Pearnly ERP 站点接到本事务所的 Express 账本（幂等，可重复跑）。

``bench --site <事务所 Cowork 站点> execute pearnly_cowork.erp_gateway.setup.link --kwargs '{"client": "<ERP 站点>"}'``
回一个新令牌（只回这一次，站点配置里只存它的 sha256），再到 ERP 站点执行
``pearnly_erp_portal.express.setup.link``（地址、Host、本站点、这个令牌）。同一个 ERP 站点再跑一次就换一个令牌。

做三件事：建 / 启用网关服务账号（录入与查看识别记录两个角色，不能登录网页）；Express 主连接没勾「全所可用」时把它放进
使用名单；把令牌摘要写进站点配置 ``pearnly_erp_gateway``。``unlink`` 撤掉一个 ERP 站点（令牌立即失效）。
"""
import secrets

import frappe
from frappe.installer import update_site_config

from pearnly_cowork.connections import model as connection_model
from pearnly_cowork.connections import service as connections
from pearnly_cowork.erp_gateway import auth

USER = "erp-gateway@pearnly.internal"
ROLES = ("Cowork Intake", "Cowork OCR History")


def _user():
    if not frappe.db.exists("User", USER):
        frappe.get_doc({"doctype": "User", "email": USER, "first_name": "ERP Gateway", "user_type": "System User",
                        "send_welcome_email": 0, "enabled": 1}).insert(ignore_permissions=True)
    user = frappe.get_doc("User", USER)
    user.enabled = 1
    have = {row.role for row in user.roles}
    for role in ROLES:
        if role not in have:
            user.append("roles", {"role": role})
    user.save(ignore_permissions=True)
    # 不能登录网页：没有密码；只经网关以这个身份做事
    return USER


def _member(user):
    for workspace in frappe.get_all("Cowork Workspace", filters={"enabled": 1}, pluck="name"):
        name = connections.main_connections(workspace).get("express")
        if not name:
            continue
        doc = frappe.get_doc("Cowork ERP Connection", name)
        if int(doc.get("all_members") or 0):
            continue
        users = connection_model.parse(doc.get("allowed_users"), [])
        if user not in users:
            frappe.db.set_value("Cowork ERP Connection", name, "allowed_users",
                                frappe.as_json(sorted({*users, user})), update_modified=False)


def link(client):
    client = str(client or "").strip()
    if not client or len(client) > 140:
        raise ValueError("client (the ERP site) is required")  # 运维命令，不是界面文案
    user = _user()
    _member(user)
    token = secrets.token_urlsafe(32)
    config = frappe.conf.get(auth.CONFIG) if isinstance(frappe.conf.get(auth.CONFIG), dict) else {}
    clients = dict(config.get("clients") or {})
    clients[client] = auth.digest(token)
    update_site_config(auth.CONFIG, {"user": user, "clients": clients})
    frappe.db.commit()
    return {"client": client, "user": user, "token": token}


def unlink(client):
    config = frappe.conf.get(auth.CONFIG) if isinstance(frappe.conf.get(auth.CONFIG), dict) else {}
    clients = dict(config.get("clients") or {})
    removed = clients.pop(str(client or "").strip(), None) is not None
    if config:
        update_site_config(auth.CONFIG, {**config, "clients": clients})
    return {"removed": removed}
