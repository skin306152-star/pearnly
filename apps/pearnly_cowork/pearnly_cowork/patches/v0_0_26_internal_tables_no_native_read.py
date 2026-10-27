"""Cowork 内部表不再给 All / Guest 读（10-27 验收 17 岗位漏出清单，主会话审定）。

这些表原来靠 All / Guest 读把框架查询引到我们的拒绝钩子上：没有角色读权限时，Frappe 16 只按共享记录（DocShare）
出列表、不问钩子。现在 DocType 上不再给任何角色读，共享就是剩下的唯一原生读路径——新建共享早已被 `reject_share`
拦住，这里把存量共享清掉。按规则选表：本产品里没有任何角色可读的非子表（读写都只走各域服务、`ignore_permissions`）。
重跑无副作用。
"""
import frappe


def execute():
    readable = set(frappe.get_all("Custom DocPerm", filters={"read": 1}, pluck="parent"))
    doctypes = [name for name in frappe.get_all("DocType", filters={"module": "Pearnly Cowork", "istable": 0}, pluck="name")
                if name not in readable and not frappe.get_all("DocPerm", filters={"parent": name, "read": 1}, limit=1)]
    if doctypes:
        frappe.db.delete("DocShare", {"share_doctype": ["in", doctypes]})
