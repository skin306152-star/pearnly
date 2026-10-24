"""看板的一个自定义字段（全板共用）；写入只走 `work.service`。"""
from pearnly_erp_portal.work.documents import WorkDocument


class ERPWorkField(WorkDocument):
    pass
