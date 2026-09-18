# -*- coding: utf-8 -*-
"""推送日志。

**这张表是推送状态的唯一来源**（计划 p0 的硬规则：不能拿 HTTP 200 当"推成功了"）。
业务表上的 `push_status` / `push_log_ref` 只是引用位，判断完成与否一律看这里。

控制器里不放业务逻辑：状态怎么流转、租约怎么抢，在
`pearnly_core/push_log.py` 里，一处实现。DocType 只保管字段与权限。
"""

from frappe.model.document import Document


class PearnlyPushLog(Document):
    pass
