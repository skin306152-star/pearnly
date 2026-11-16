"""回读的四份工作簿放进 CC 对话（CC 工具 `cowork_express_readback` 与读回引导 `assistant.guided.readback` 共用；同一段对话、同一份回读只放一次）。"""
import frappe

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# 放进对话后记 7 天：同一段对话再查同一份回读不再放一遍
FILES_TTL = 7 * 86400


def into_conversation(state, conversation) -> dict:
    """``state`` 是 `readback.service.status` 的一份（ready）：{kind: 智能体读到的位置}；之后的工具像附件一样取用。"""
    from pearnly_core.assistant import files as chat_files

    from pearnly_cowork.readback import service as readback

    key = f"pearnly_assistant:readback:{frappe.local.site}:{conversation or ''}:{state['readback']}"
    added = frappe.cache.get_value(key)
    added = dict(added) if isinstance(added, dict) else {}
    for kind in state.get("files") or ():
        if kind not in added:
            filename, content = readback.file(state["readback"], kind)  # loop-query: 一份回读最多四本表（FILE_KINDS）
            added[kind] = chat_files.add_to_conversation(filename, content, XLSX, origin="tool")  # loop-query: 同上，最多四本
            frappe.cache.set_value(key, added, expires_in_sec=FILES_TTL)
    return added
