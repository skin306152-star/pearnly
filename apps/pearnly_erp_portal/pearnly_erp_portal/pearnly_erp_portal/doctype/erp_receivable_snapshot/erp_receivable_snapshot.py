"""Express 未收清发票的快照（按需请求 ``express_ar_open`` 读回），只经 `collection.snapshot` 写、只给收款流程读；Express 为准。"""
from frappe.model.document import Document


class ERPReceivableSnapshot(Document):
    pass
