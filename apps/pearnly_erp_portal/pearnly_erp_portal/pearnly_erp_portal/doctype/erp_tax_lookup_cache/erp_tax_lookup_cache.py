# -*- coding: utf-8 -*-
"""泰国税务局（RD）查询结果的本地缓存，7 天有效。

一个（税号 + 分支 + 服务）一行，"查不到"也缓存（免得每次开单都打一次外部接口）。
只有服务端在 `masters/rd.py` 里读写它，员工不直接看、也改不了。
"""

from frappe.model.document import Document


class ERPTaxLookupCache(Document):
    pass
