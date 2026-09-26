"""卡片镜像：同一张卡也显示在另一个列表里（点开是原卡）；写入只走 `work.service`。"""
from pearnly_cowork.work.documents import WorkDocument


class CoworkWorkMirror(WorkDocument):
    pass
