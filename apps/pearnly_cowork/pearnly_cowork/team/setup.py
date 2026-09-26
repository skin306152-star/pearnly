"""建团队管理要用、但没有 DocType 挂着的角色（工作协调 / 个人信息 / LINE）。

录入、对账、盘点三个角色随各自 DocType 的权限行生成，不在这里建。
这几个角色不带任何单据权限：它们只决定菜单与 LINE 开关。
"""
import frappe

from pearnly_core.provisioning import _ensure_role
from pearnly_cowork.access import menu


def sync_roles():
    if frappe.conf.get("pearnly_product") not in (None, "", "cowork"):
        return
    for role in menu.OWN_ROLES:
        _ensure_role(role)
