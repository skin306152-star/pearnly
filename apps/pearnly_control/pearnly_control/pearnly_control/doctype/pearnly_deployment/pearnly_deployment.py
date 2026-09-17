# -*- coding: utf-8 -*-
"""一个部署 = 公司 × 已开通产品 = 一个 Site。

标识用 `deployment_id`（本单据名），**域名只是绑定关系**：换域名改 `site_name`
不改单据名。这条是 P1.04 主体模型的硬要求，因为域名会变、也容易写错。
"""

from frappe.model.document import Document


class PearnlyDeployment(Document):
    pass
