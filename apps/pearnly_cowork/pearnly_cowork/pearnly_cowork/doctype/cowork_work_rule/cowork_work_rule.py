"""看板的一条自动化规则（卡片进入某列表时自动做一件事）；写入只走 `work.service`。"""
from pearnly_cowork.work.documents import WorkDocument


class CoworkWorkRule(WorkDocument):
    pass
