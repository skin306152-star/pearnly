"""看板的一个自定义字段（全板共用）；写入只走 `work.service`。"""
from pearnly_cowork.work.documents import WorkDocument


class CoworkWorkField(WorkDocument):
    pass
