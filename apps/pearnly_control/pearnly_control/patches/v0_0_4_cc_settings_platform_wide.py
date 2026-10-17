"""CC 设置改成全平台一份（2026-10-18，``onboarding.assistant``）：本机控制面站点配置还没配时，从原来在租户上填过的那一份
抄过来（当时只有 testcowork 填过；几家都填过就取最近改的那家）。

只写本机控制面的站点配置，不推到生产：推送是 ``onboarding.assistant.apply_to_all``，和开发线约好时间后手动跑。
表单上的三个字段已去掉，库里的旧列与旧密钥留着不删（老数据可查，不再读）。
"""
import frappe
from frappe.installer import update_site_config
from frappe.utils.password import get_decrypted_password

from pearnly_control.onboarding.assistant import CONF


def execute():
    if any((frappe.conf or {}).get(name) for name in CONF):
        return
    if not frappe.db.has_column("Pearnly Tenant", "assistant_engine"):
        return
    rows = frappe.db.sql("""select name, assistant_engine, assistant_model from `tabPearnly Tenant`
        where ifnull(assistant_engine, '') != '' order by modified desc""", as_dict=True)
    for row in rows:
        key = get_decrypted_password("Pearnly Tenant", row.name, "assistant_api_key", raise_exception=False)
        if key:
            for name, value in zip(CONF, (row.assistant_engine, row.assistant_model or "", key)):
                update_site_config(name, value)
            return
