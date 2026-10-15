"""多人同时盘点：有人记了一笔、改了、作废、重盘或结束盘点，通知能看盘点的其他人刷新。

只推给有「盘点·查看」权限的人（与 `guard.require("count.read")` 同一判据），消息里只带盘点号；
页面收到后按本人权限重新取（原生 socket.io，连不上时照常手动刷新）。同一请求里改了好几行
（重盘会逐条作废）只推一次。Cowork 盘点 `stocktake/live.py` 是同一个事件、同一种做法。
"""
import frappe

from pearnly_erp_portal.access import guard

EVENT = "pearnly_stocktake_changed"


def changed(count):
    if frappe.flags.pearnly_stocktake_live is None:
        frappe.flags.pearnly_stocktake_live = set()
    sent = frappe.flags.pearnly_stocktake_live
    if count in sent:
        return
    sent.add(count)
    actor = frappe.session.user
    users = frappe.get_all("User", filters={"enabled": 1, "user_type": "System User"}, pluck="name")
    for user in sorted(set(users) - {actor, "Administrator", "Guest"}):
        if guard.has("count.read", user):
            frappe.publish_realtime(EVENT, {"count": count}, user=user, after_commit=True)
