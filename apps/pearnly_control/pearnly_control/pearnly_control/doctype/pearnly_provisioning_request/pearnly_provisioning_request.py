# -*- coding: utf-8 -*-
"""一次开通请求：把"发号单"变成"真的建出站点"这件事的可追踪载体。

为什么要有它（P1.05 / P4.02 的硬要求）：
- 幂等键：同一个键重复提交只建一套资源；
- 分步骤、逐步落库：页面刷新能看到进度，中断后能从断点继续；
- 不虚报：任一步失败就是失败，不把"部分成功"写成"成功"。
"""

from frappe.model.document import Document


class PearnlyProvisioningRequest(Document):
    pass
