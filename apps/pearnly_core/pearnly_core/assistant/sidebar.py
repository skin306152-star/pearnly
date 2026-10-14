"""侧栏多设备同步（ADR-019，同 Claude）：对话新建、改名（含自动标题）、加星标、归档、删除时，按用户推一条实时事件。

- 走对话事件的同一个站点实时通道（`contracts.REALTIME_EVENT`，Frappe socket.io 里会话主人自己的房间，`user=`）：
  只推给本人，不推给文档订阅者、不广播；别人的房间收不到。
- 信封里 `session_id` 为空：这条事件不进哪段对话的页面，只给侧栏（对话页 lib/managed-agents/list-sync.ts）。
  旧版页面认不出这种事件，照旧忽略。
- 事件只说哪段对话、变了什么（`contracts.session_change`）；新出现的对话由侧栏重拉列表取全。
- 请求里改的在提交后才推（`after_commit`）：别的设备收到再读列表一定读得到；后台任务自己提交过就直接推。
"""
import frappe

from pearnly_core.assistant import contracts


def changed(user: str, change: str, session: str, after_commit: bool = True, **fields) -> None:
    frappe.publish_realtime(contracts.REALTIME_EVENT,
                            {"session_id": "", "event": contracts.session_change(change, session, **fields)},
                            user=user, after_commit=after_commit)
