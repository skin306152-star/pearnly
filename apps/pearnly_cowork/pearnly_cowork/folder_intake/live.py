"""定时任务页的实时刷新（2026-10-12）：任务、运行记录或文件进度变了，推一个只带任务名的事件。

照录入列表的做法（`history.push_state._changed`）：原生实时推送，事务提交后才发（回滚就不发，同一事务里同一任务只发一次），
推给任务负责人（文件进度另推给这份文件的 actor）。开着的定时任务页（Desk `intake-folders.js` 的 `p.live`、CC 的定时任务页）
收到就重拉；同事开着的页面靠兜底定时刷新。推不出去不影响业务本身。
"""
import frappe

from pearnly_cowork.folder_intake.access import RULE

EVENT = "cowork_folder_changed"


def changed(rule, *users):
    try:
        if not rule:
            return
        owner = frappe.db.get_value(RULE, rule, "rule_owner")
        for user in {owner, *users} - {None, "", "Guest"}:
            frappe.publish_realtime(EVENT, {"rule": rule}, user=user, after_commit=True)
    except Exception:
        pass
