"""管理员默认有收款（主会话 11-14）之后，已绑定 LINE 的人各重挂一次富菜单。

岗位变了菜单要重挂：平常由团队管理保存时 `line_menu.user_changed` 排；这次是规则变了、没人点保存，
已绑定的管理员要等他下次发消息（`line_menu.ensure`）才换上带收款格的那套。这里排一次 `reconcile`：
每个已绑定的人排一个去重的后台挂载（只在该挂的那套和现在不同时才写 LINE）。没发布过菜单的站点什么都不做。
"""

import frappe

from pearnly_erp_portal.integrations import line_menu


def execute():
    if str(frappe.conf.get("pearnly_product") or "").strip() != "erp" or not frappe.conf.get(line_menu.CONFIG):
        return
    line_menu.reconcile()
