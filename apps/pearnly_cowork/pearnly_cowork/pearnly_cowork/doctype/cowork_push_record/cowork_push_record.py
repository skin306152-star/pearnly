import frappe

from pearnly_cowork.push.native.documents import PushDocument


class CoworkPushRecord(PushDocument):
    pass


# 电脑写入队列的查询都按「连接 + 状态」开头（排头 `companion.queue_heads.Q_HEADS` 只看 pending、再按账套分组取最早的
# 建立时间；领取前收回到期租约 `companion.transport._recover` 只看 leased）。没有这个索引时每次都整表扫推送记录，而且是在
# 锁着连接行的时候（D9 压测 10-19：每个租户 2 万张旧记录时排头查询 400–600 毫秒，同一连接上的电脑排队等连接行锁，
# p95 2.6–3.7 秒）。排队中 / 领走的只是一小部分，按这两列开头就只读它们；账套键（500 字符）放不进索引长度上限，不放。
# 随 migrate 由 Frappe 原生的 on_doctype_update 建（新表建表时调，已有同名索引就跳过）；已有站点由补丁
# `v0_0_25_push_queue_index` 补建。
INDEXES = {"connection_status_created_index": ["connection", "status", "created_at"]}


def on_doctype_update():
    for index_name, fields in INDEXES.items():
        frappe.db.add_index("Cowork Push Record", fields, index_name)
