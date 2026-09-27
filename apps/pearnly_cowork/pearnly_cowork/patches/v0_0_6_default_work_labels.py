"""已有看板补上默认的六个颜色标签（照 Trello；2026-09-27 实测：没有标签时「修改标签」是个空弹窗）。

只补一个标签都没有的看板；已经建过标签的不动。补过一次后用户自己删光也不会再补（新看板由
`work.service.seed_labels` 在建默认列表时一起建）。重复执行不重复建。
"""
from datetime import timedelta

import frappe
from frappe.utils import now_datetime

COLORS = ("green", "yellow", "orange", "red", "purple", "blue")


def execute():
    label = "Cowork Work Label"
    if not frappe.db.table_exists(label) or not frappe.db.table_exists("Cowork Work Column"):
        return
    boards = set(frappe.db.sql_list("SELECT DISTINCT workspace FROM `tabCowork Work Column`"))
    for workspace in boards - set(frappe.db.sql_list("SELECT DISTINCT workspace FROM `tabCowork Work Label`")):
        start = now_datetime()
        for index, color in enumerate(COLORS):
            doc = frappe.get_doc({"doctype": label, "workspace": workspace, "title": "", "color": color})
            # 迁移不经过看板服务（没有登录用户）；直接落原生行，字段与服务写入的一致（同 v0_0_4）。
            # 标签按建立时间排序：依次错开一微秒，顺序与 Trello 的默认色一致。
            doc.name = frappe.generate_hash(length=10)
            doc.creation = doc.modified = start + timedelta(microseconds=index)
            doc.owner = doc.modified_by = "Administrator"
            doc.db_insert()
