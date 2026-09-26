# -*- coding: utf-8 -*-
"""DMS 系统账号池里的一个账号（见 `pearnly_dms.adapter.admin_pool`）。

增删改走连接页的服务端接口（`connections.api`，先真登一次再存），这里不放业务规则。
"""

from frappe.model.document import Document


class DMSSystemAccount(Document):
    pass
