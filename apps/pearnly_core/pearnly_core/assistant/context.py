"""对话的上下文用量与最后活动时间（ADR-019），转发线程（`relay`）与会话读写（`sessions`）都要记，单独成模块，免得两者互相导入。
调用方负责提交。

- 上下文用量：最近一次模型请求的输入量，供计价分档的接续判断（`sessions.segment_for_turn`）。
- 最后活动时间：侧栏按它排（「上次更新」，`sessions.listing` 按会话表的 `modified` 从新到旧），也是列表里的 `updated_at`。
  只有对话里真的有动静才算：用户发出消息（`sessions.send`）、CC 回复（`relay` 收到 `agent.message`）、挂上提醒
  （`notices`）。改月份 / 分店、置顶、改名、记上下文用量、概括标题都不算（那些写法带 `update_modified=False`）。
  测试窗口第 10 轮 B 段：原来消息只写在引擎那边，会话表的 `modified` 只在新建后几秒（首条消息的临时标题）写一次，
  「上次更新」实际按建立时间排。历史数据不回填：旧对话下一次有消息时才更新。
"""
import frappe

from pearnly_core.assistant import contracts

SESSION = "Pearnly Assistant Session"


def note(conversation: str, events) -> bool:
    """事件里有模型请求的用量就记下；返回是否记了。"""
    size = contracts.context_size(events)
    if size is None:
        return False
    frappe.db.set_value(SESSION, {"session_id": conversation}, "context_tokens", size, update_modified=False)
    return True


def active(conversation: str) -> None:
    """这段对话此刻有动静（用户发消息、CC 回复）：最后活动时间记成现在。只写这一列（不改别的字段，也不经文档保存）。"""
    frappe.db.set_value(SESSION, {"session_id": conversation}, "modified", frappe.utils.now_datetime(), update_modified=False)
