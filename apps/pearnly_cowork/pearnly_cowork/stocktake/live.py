"""多人同时盘点：有人记了一笔、改了、作废或结束盘点，通知正在看这份盘点的其他人刷新。

只推给能进盘点的人（角色 + 公司资料范围，与 `access.allowed` 同一判据），消息里只带盘点号；
也推给本人（同一账号手机和电脑同时开着时另一台也要刷新；刚保存的那一页自己会跳过）。页面收到后自己按本人权限重新取（原生 socket.io，连不上时照常手动刷新）。照看板 `work.notify.changed` 的做法。
"""
import frappe

from pearnly_cowork.access import scope
from pearnly_cowork.stocktake import access

EVENT = "pearnly_stocktake_changed"


def changed(task, workspace):
    # 同一请求里改了好几行（重盘会逐条作废）只推一次。
    if frappe.flags.pearnly_stocktake_live is None:
        frappe.flags.pearnly_stocktake_live = set()
    if task in frappe.flags.pearnly_stocktake_live:
        return
    frappe.flags.pearnly_stocktake_live.add(task)
    users = frappe.get_all("Has Role", filters={"parenttype": "User", "role": ["in", ["Cowork Stocktake", "Cowork Admin"]]},
                           pluck="parent", distinct=True)
    for user in sorted(set(users) - {"Guest"}):
        if access.allowed(user) and scope.contains(workspace, user=user):
            frappe.publish_realtime(EVENT, {"count": task}, user=user, after_commit=True)
