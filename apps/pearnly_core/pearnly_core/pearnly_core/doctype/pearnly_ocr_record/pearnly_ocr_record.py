# -*- coding: utf-8 -*-
"""识别记录（三套产品共用一张表，写在**各自的产品站点**上）。

对应老产品的 `ocr_history`：老产品所有租户一个库，所以一张表就够；
新平台每个租户是独立站点，识别发生在哪个站点就记在哪个站点 ——
首页的「识别记录」读的也是本站自己的（跨租户看不到别人的）。

写入口只有 `pearnly_core.ocr_record.record()`：每次识别**无论成败都落一条**
（失败也要落，否则用户看到"什么都没发生"，而我们连排查的依据都没有）。

计费金额记在这里只是**展示用**，权威是控制面的台账 —— 两边不一致时以控制面为准。
"""

from frappe.model.document import Document


class PearnlyOCRRecord(Document):
    pass
