"""发票历史表补一个只含工作区与客户的窄索引（``cowork_ocr_history.INDEXES`` 的 workspace_client_index），录入工作台计数只扫索引。

``v0_0_11`` 已在各站点执行过、不会再跑，这里再调一次 ``on_doctype_update``（已有的索引跳过，只建新加的）。
"""
from pearnly_cowork.pearnly_cowork.doctype.cowork_ocr_history.cowork_ocr_history import on_doctype_update


def execute():
    on_doctype_update()
