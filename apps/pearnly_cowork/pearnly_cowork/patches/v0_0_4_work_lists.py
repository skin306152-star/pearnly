"""工作协调看板从"固定三列"换成"用户自己管的列表"（用户 2026-09-27 第四批）。

第一、二批的卡片用 `status`（todo / doing / done）表示在哪一列、`Cowork Work Column` 只存列颜色。
这里为每个站点归属建出三个列表（名字用最早建卡那个人的界面语言写待办 / 进行中 / 已完成——列表名现在是
大家看得到、能改的数据；没有卡就用站点语言。「已完成」是完成列表），
沿用原来的列颜色，把卡片挂到对应列表上，已完成列里的卡记为已完成；旧清单项归到一个清单下。
重复执行不重复建（已挂好列表的卡、已有标题的列表都跳过）。
"""
import frappe
from frappe import _

LEGACY = (("todo", "cowork.work.status.todo", 0), ("doing", "cowork.work.status.doing", 0),
          ("done", "cowork.work.status.done", 1))
STEP = 1024.0


def execute():
    card, column = "Cowork Work Card", "Cowork Work Column"
    if not frappe.db.table_exists(card) or not frappe.db.has_column(card, "status"):
        return
    workspaces = set(frappe.db.sql_list("SELECT DISTINCT workspace FROM `tabCowork Work Card`"))
    workspaces |= set(frappe.db.sql_list("SELECT DISTINCT workspace FROM `tabCowork Work Column`"))
    site_lang = frappe.local.lang
    for workspace in workspaces:
        first = frappe.get_all(card, filters={"workspace": workspace}, pluck="owner", order_by="creation asc",
                               limit_page_length=1)
        frappe.local.lang = (first and frappe.db.get_value("User", first[0], "language")) or site_lang
        lists = {}
        for index, (status, key, done) in enumerate(LEGACY):
            row = frappe.db.get_value(column, {"workspace": workspace, "status": status}, ["name", "title"], as_dict=True)
            if row and row.title:
                lists[status] = row.name
                continue
            values = {"title": _(key), "position": STEP * (index + 1), "is_done_list": done, "archived": 0}
            if row:
                frappe.db.set_value(column, row.name, values, update_modified=False)
                lists[status] = row.name
            else:
                lists[status] = _insert(frappe.get_doc({"doctype": column, "workspace": workspace,
                                                        "status": status, **values}))
        for status, name in lists.items():
            frappe.db.sql("""UPDATE `tabCowork Work Card` SET board_list=%s, completed=%s
                WHERE workspace=%s AND status=%s AND COALESCE(board_list, '')=''""",
                          (name, 1 if status == "done" else 0, workspace, status))
    frappe.local.lang = site_lang
    frappe.db.sql("""UPDATE `tabCowork Work Checklist Item` SET checklist=%s, group_id='c1'
        WHERE COALESCE(group_id, '')=''""", (_("cowork.work.field.checklist"),))
    frappe.db.sql("""UPDATE `tabCowork Work Card` SET reminder='1d' WHERE COALESCE(reminder, '')=''""")
    frappe.db.sql("""UPDATE `tabCowork Work Card` SET recurrence='never' WHERE COALESCE(recurrence, '')=''""")


def _insert(doc):
    # 迁移不经过看板服务（没有登录用户）；直接落原生行，字段与服务写入的一致。
    doc.name = frappe.generate_hash(length=10)
    doc.db_insert()
    return doc.name
