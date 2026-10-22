"""运维：把本 ERP 站点接到事务所 Cowork 的 Express 网关（幂等）。令牌由事务所站点 `pearnly_cowork.erp_gateway.setup.link`
发，见那里的说明。

``bench --site <ERP 站点> execute pearnly_erp_portal.express.setup.link --kwargs
'{"api": "http://cowork-web:8000", "host": "<事务所 Cowork 站点>", "token": "<令牌>"}'``
接好后立刻同步一次各公司的主档（:func:`masters.sync_all` 排任务）。``unlink`` 断开（单据只留在 ERP，不再推）。
"""
import frappe
from frappe.installer import update_site_config

from pearnly_erp_portal.express import gateway, masters


def link(api, host, token, client=None):
    values = {"api": str(api or "").strip(), "host": str(host or "").strip(),
              "client": str(client or frappe.local.site).strip(), "token": str(token or "").strip()}
    if not (values["api"] and values["host"] and values["token"]):
        raise ValueError("api, host and token are required")  # 运维命令，不是界面文案
    update_site_config(gateway.CONFIG, values)
    frappe.local.conf[gateway.CONFIG] = values
    masters.sync_all()
    frappe.db.commit()
    return {"client": values["client"], "host": values["host"], "companies": [row.name for row in
                                                                            masters.linked_companies()]}


def unlink():
    update_site_config(gateway.CONFIG, {})
    return {"unlinked": True}
