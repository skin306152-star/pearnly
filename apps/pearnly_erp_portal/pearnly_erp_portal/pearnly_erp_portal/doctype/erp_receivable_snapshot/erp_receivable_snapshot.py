"""Express 未收清发票的快照（按需请求 ``express_ar_open`` 读回），只经 `collection.snapshot` 写、只给收款流程读；Express 为准。"""
import frappe
from frappe.model.document import Document


class ERPReceivableSnapshot(Document):
    pass


# 一本账套里一张发票只有一行（11-28 并发冲突 ④ C1）：两路同时落同一份读回会各插一行，放行判余额按哪一行不确定（管钱）。
# 新装站点随 Frappe 原生的 on_doctype_update 建；已有站点由补丁 `v1_35_receivable_snapshot_unique` 先合并旧的重复再建。
# 已有同名唯一约束就跳过。
UNIQUE = ["book_key", "docnum"]


def on_doctype_update():
    frappe.db.add_unique("ERP Receivable Snapshot", UNIQUE)
