"""物料调整菜单（岳父 11-06，评估 `docs/work/2026-11-06-erp-stock-adjust-评估.md` 第 12 节 #2，主会话审定）。

历史盘点生成的 AD 单从入库 / 出库列表挪到「物料调整」列表，要 ``stock.adjust`` 才看得到。已经有「盘点」「入库」「出库」任一菜单的员工
补发「物料调整」菜单，不让他们突然看不到原来看得到的单。产品管理员（ERP Admin）本来全有，不经这里。

- 幂等：已有这个菜单的人跳过；只加不删。
- 岗位角色由 `access.setup.sync_roles`（迁移后）补权限，这里只保证角色存在、发给人。
"""
import frappe

from pearnly_erp_portal.access import menu

SOURCES = ("stock-count", "stock-in", "stock-out")
TARGET = "stock-adjust"


def execute():
    role = menu.role(TARGET)
    if not frappe.db.exists("Role", role):  # 同开户建岗位角色的写法（`access.setup.sync_roles` 迁移后再补权限）
        frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)
    holders = set(frappe.get_all("Has Role", filters={"parenttype": "User", "role": ["in", [menu.role(key) for key in SOURCES]]},
                                 pluck="parent", limit_page_length=0))  # bounded: 本站员工
    have = set(frappe.get_all("Has Role", filters={"parenttype": "User", "role": role}, pluck="parent",  # bounded: 本站员工
                              limit_page_length=0))
    added = 0
    for user in sorted(holders - have):
        if not frappe.db.exists("User", user):
            continue
        frappe.get_doc("User", user).add_roles(role)
        added += 1
    print(f"stock-adjust menu granted: {added}")
