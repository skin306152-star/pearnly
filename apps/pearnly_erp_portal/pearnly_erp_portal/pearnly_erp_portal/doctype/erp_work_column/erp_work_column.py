"""看板上的一个列表（标题、顺序、颜色、是否完成列表、归档）；写入只走 `work.service`。"""
from pearnly_erp_portal.work.documents import WorkDocument


class ERPWorkColumn(WorkDocument):
    pass
