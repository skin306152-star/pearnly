"""对话的上下文用量（ADR-019）：记下最近一次模型请求的输入量，供计价分档的接续判断（`sessions.segment_for_turn`）。

转发线程（`relay`）与会话读写（`sessions`）都要记，单独成模块，免得两者互相导入。调用方负责提交。
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
