"""发票历史表按工作区开头的组合索引（``cowork_ocr_history.INDEXES``），已有站点迁移时补建。

``on_doctype_update`` 只在 DocType 定义有变化或新装站点时被 Frappe 调用；这些索引是在 DocType JSON 不变的情况下加的
（2026-10-02 上线 387dae2b 时四个 Cowork 站点都没建出来，修复窗口手动补过）。这里在迁移时调一次，已有同名索引就跳过。
"""
from pearnly_cowork.pearnly_cowork.doctype.cowork_ocr_history.cowork_ocr_history import on_doctype_update


def execute():
    on_doctype_update()
