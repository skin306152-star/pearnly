"""推送记录按「连接 + 状态 + 账套 + 建立时间」的组合索引（``cowork_push_record.INDEXES``），已有站点迁移时补建。

``on_doctype_update`` 只在 DocType 定义有变化或新装站点时被 Frappe 调用；这个索引是在 DocType JSON 不变的情况下加的
（同 `v0_0_11_history_workspace_indexes`）。这里在迁移时调一次，已有同名索引就跳过，重跑无变化。
"""
from pearnly_cowork.pearnly_cowork.doctype.cowork_push_record.cowork_push_record import on_doctype_update


def execute():
    on_doctype_update()
