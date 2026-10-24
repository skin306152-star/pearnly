"""看板的一条自动化规则（卡片进入某列表时自动做一件事）；写入只走 `work.service`。"""
from pearnly_erp_portal.work.documents import WorkDocument


class ERPWorkRule(WorkDocument):
    pass
