"""执行一次自定义工具调用（ADR-019）。

推送线程（`relay`）一看到智能体停下来等工具结果，就在服务端直接执行，不靠页面开着；页面的 `run_tool`
只作兜底。两边共用这里：按调用编号在 `Pearnly Assistant Tool Run` 登记（唯一），同一次调用只执行一遍。
工具一律以会话主人的身份执行（权限、租户、套账与数据范围沿用各产品自己的校验）；意外出错也要回一个错误结果，
不能让会话一直停在等待里。
"""
import frappe
from frappe import _

from pearnly_core.assistant import contracts, engine, tools

TOOL_RUN = "Pearnly Assistant Tool Run"


def fresh():
    """推送线程一轮里连着执行多次工具，用的是同一个 Frappe 上下文：读过的缓存（`frappe.local.cache`）和数据库快照会一直
    停在第一次读到的样子。每次执行工具、工具里每次重新查进度前都要清掉（2026-10-10 测试：对账 9 秒就做完，工具却一直读到
    「排队中」，智能体在沙箱里 sleep 干等）。"""
    frappe.db.rollback()
    cache = getattr(frappe.local, "cache", None)
    if isinstance(cache, dict):
        cache.clear()


def execute(conversation: str, remote: str, call: dict) -> str:
    """执行 `call`（一条 `agent.custom_tool_use` 事件）并把结果交回引擎。返回 done / error / running（别处已在执行）。"""
    use_id = contracts.event_id(call.get("id"))
    try:
        run = frappe.get_doc({"doctype": TOOL_RUN, "tool_use_id": use_id, "session_id": conversation,
                              "user": frappe.session.user, "tool": call.get("name") or "", "status": "running"})
        run.insert(ignore_permissions=True)
        frappe.db.commit()
    except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
        # 推送线程与页面兜底同时到：唯一字段冲突在 Frappe 里以 UniqueValidationError 抛出并顺手 msgprint，
        # 那条提示要清掉，否则会跟着页面的请求弹到对话页顶上（2026-10-10 测试：「Tool Use ID必须是唯一的」）。
        frappe.db.rollback()
        frappe.clear_messages()
        return "running"
    # 工具要用到的对话信息（例如取本对话的附件原件，`files.attachment`）；工具函数本身只收模型给的参数。
    frappe.local.pearnly_assistant = {"session_id": conversation}
    fresh()
    try:
        text, is_error = tools.run(call.get("name"), call.get("input"))
    except Exception:
        frappe.db.rollback()
        frappe.log_error(title="Assistant tool failed")
        text, is_error = _("The tool failed. Try again later."), True
    finally:
        frappe.local.pearnly_assistant = None
    engine.adapter().post(remote, [contracts.tool_result(use_id, text, is_error)])
    status = "error" if is_error else "done"
    frappe.db.set_value(TOOL_RUN, run.name, "status", status)
    frappe.db.commit()
    return status
