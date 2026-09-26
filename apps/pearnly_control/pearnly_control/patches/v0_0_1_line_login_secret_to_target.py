"""LINE Login 频道密钥从"每个 LINE 渠道一份"改为"开户目标上全平台一份"（用户 2026-09-26）。

已经填在渠道上的密钥搬到该渠道所属开户目标（目标还没配时），并删掉渠道上的旧存档 ——
字段已从渠道表单去掉，不留孤儿密钥。只统计条数，不输出内容。
"""
import frappe
from frappe.utils.password import get_decrypted_password, set_encrypted_password

from pearnly_core.account.line_login_contract import login_channel_of


def execute():
    rows = frappe.db.sql("""select name from `__Auth`
        where doctype = 'Pearnly LINE Channel' and fieldname = 'login_channel_secret'""", pluck=True)
    moved = 0
    for name in rows:
        if not frappe.db.exists("Pearnly LINE Channel", name):
            continue
        secret = get_decrypted_password("Pearnly LINE Channel", name, "login_channel_secret", raise_exception=False)
        channel = login_channel_of(frappe.db.get_value("Pearnly LINE Channel", name, "liff_id"))
        deployment = frappe.db.get_value("Pearnly LINE Channel", name, "deployment")
        issue = frappe.db.get_value("Pearnly Deployment", deployment, "issue") if deployment else None
        target = frappe.db.get_value("Pearnly Issue", issue, "target") if issue else None
        if not (secret and channel and target and frappe.db.exists("Pearnly Target", target)):
            continue
        if frappe.db.get_value("Pearnly Target", target, "line_login_channel_id"):
            continue  # 目标已经配过：以目标上的为准
        set_encrypted_password("Pearnly Target", target, secret, "line_login_channel_secret")
        base, realm = frappe.db.get_value("Pearnly Target", target, ["base_url", "realm"])
        # 与表单保存时一致：列里只存掩码；回调地址按目标算。不在这里重排开通步骤 ——
        # 要等服务器换上新代码后再由运维触发（见 provisioning.requeue_line_login）。
        frappe.db.set_value("Pearnly Target", target, {
            "line_login_channel_id": channel,
            "line_login_channel_secret": "*" * len(secret),
            "line_login_callback_url": f"{(base or '').rstrip('/')}/realms/{realm}/broker/line-{channel}/endpoint",
        }, update_modified=False)
        moved += 1
    frappe.db.sql("""delete from `__Auth` where doctype = 'Pearnly LINE Channel'
        and fieldname = 'login_channel_secret'""")
    print(f"LINE Login secret moved to {moved} target(s); per-channel copies removed")
