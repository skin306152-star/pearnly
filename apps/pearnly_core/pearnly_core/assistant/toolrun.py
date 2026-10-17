"""执行一次自定义工具调用（ADR-019）。

推送线程（`relay`）一看到智能体停下来等工具结果，就在服务端直接执行，不靠页面开着；页面的 `run_tool`
只作兜底。两边共用这里：按调用编号在 `Pearnly Assistant Tool Run` 登记（唯一），同一次调用只执行一遍。
工具里要等别处结论的（例如推送后等写账结果）只在推送线程里等（`may_wait`），别的执行处立刻返回。
工具一律以会话主人的身份执行（权限、租户、套账与数据范围沿用各产品自己的校验）；意外出错也要回一个错误结果，
不能让会话一直停在等待里。
"""
import json
from datetime import datetime, timezone

import frappe
from frappe import _

from pearnly_core.assistant import contracts, engine, tools

TOOL_RUN = "Pearnly Assistant Tool Run"
LANG_TTL = 30 * 86400
WORK_RUNS = 200  # 接续时回看最近这么多次调用，凑工作清单


def _lang_key(conversation: str) -> str:
    return f"pearnly_assistant:lang:{conversation}"


def remember_lang(conversation: str) -> None:
    """用户发消息时记下对话的界面语言：推送线程、引擎回调的后台任务里执行工具没有这个请求，要按它翻译工具结果
    （2026-10-11 测试：后台执行时按站点默认泰文出结果，中文提问被答成泰文）。"""
    from pearnly_core.execution import state as pearnly_state

    if getattr(frappe.local, "lang", None):
        pearnly_state.store().set_value(_lang_key(conversation), frappe.local.lang, expires_in_sec=LANG_TTL)


def _use_lang(conversation: str) -> None:
    from pearnly_core.execution import state as pearnly_state

    lang = pearnly_state.store().get_value(_lang_key(conversation), use_local_cache=False) or \
        frappe.db.get_value("User", frappe.session.user, "language")
    if lang:
        frappe.local.lang = lang
        # Frappe 16 在后台任务里第一次翻译时会按任务用户重设语言（同 `recon_center.jobs`）：标成已定
        job = getattr(frappe.local, "job", None)
        if job is not None and hasattr(job, "lang_resolved"):
            job.lang_resolved = True


def fresh():
    """推送线程一轮里连着执行多次工具，用的是同一个 Frappe 上下文：读过的缓存（`frappe.local.cache`）和数据库快照会一直
    停在第一次读到的样子。每次执行工具、工具里每次重新查进度前都要清掉（2026-10-10 测试：对账 9 秒就做完，工具却一直读到
    「排队中」，智能体在沙箱里 sleep 干等）。"""
    frappe.db.rollback()
    cache = getattr(frappe.local, "cache", None)
    if isinstance(cache, dict):
        cache.clear()


def execute(conversation: str, remote: str, call: dict) -> str:
    """执行 `call`（一条 `agent.custom_tool_use` 事件）并把结果交回引擎。返回 done / error / running（别处已在执行）
    / awaiting_approval（要用户在对话里点「允许」，`decide`）。"""
    use_id = contracts.event_id(call.get("id"))
    gated = tools.needs_approval(call.get("name"), call.get("input"))
    refused = _refused_before_card(conversation, call) if gated else None
    gated = gated and not refused
    try:
        run = frappe.get_doc({"doctype": TOOL_RUN, "tool_use_id": use_id, "session_id": conversation,
                              "user": frappe.session.user, "tool": call.get("name") or "",
                              "status": "awaiting_approval" if gated else "running",
                              "tool_input": json.dumps(call.get("input") or {}, ensure_ascii=False)})
        run.insert(ignore_permissions=True)
        frappe.db.commit()
    except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
        # 推送线程与页面兜底同时到：唯一字段冲突在 Frappe 里以 UniqueValidationError 抛出并顺手 msgprint，
        # 那条提示要清掉，否则会跟着页面的请求弹到对话页顶上（2026-10-10 测试：「Tool Use ID必须是唯一的」）。
        frappe.db.rollback()
        frappe.clear_messages()
        # 等「允许」的要告诉页面（重开页面也要把按钮补上）；其余在执行或已执行
        waiting = frappe.db.get_value(TOOL_RUN, {"tool_use_id": use_id, "session_id": conversation}, "status")
        return "awaiting_approval" if waiting == "awaiting_approval" else "running"
    if gated:
        return "awaiting_approval"
    if refused:
        # 出卡片前就核出要拒绝：不执行工具，把拒绝结果交回模型（按出错），同样留记录。结果带上没问过用户的标记（`tools.not_asked`），
        # 对话页重开时才分得清它和点了「允许」后没做成的
        refused = tools.not_asked(refused)
        engine.adapter().post(remote, [contracts.tool_result(use_id, refused, True)])
        frappe.db.set_value(TOOL_RUN, run.name, {"status": "error", "result": refused[:60000]})
        frappe.db.commit()
        return "error"
    return _finish(conversation, remote, run.name, use_id, call.get("name"), call.get("input"))


def _refused_before_card(conversation: str, call: dict) -> str | None:
    """要「允许」的调用出卡片前的核对（`tools.refusal`），同卡片预览按这段对话现算（产品工具缺省取本对话挂的公司等）。"""
    before = getattr(frappe.local, "pearnly_assistant", None)
    frappe.local.pearnly_assistant = {**(before or {}), "session_id": conversation}
    try:
        return tools.refusal(call.get("name"), call.get("input"))
    finally:
        frappe.local.pearnly_assistant = before


def decide(conversation: str, remote: str, use_id: str, allow: bool) -> str:
    """用户在对话里对一次要授权的工具调用点了「允许 / 拒绝」：允许就以本人身份执行，拒绝就告诉智能体没得到同意。
    只认本对话、本人、还在等的那一次；授权只管这一次调用，不延续到下一次。"""
    name = frappe.db.get_value(TOOL_RUN, {"tool_use_id": use_id, "session_id": conversation,
                                          "user": frappe.session.user, "status": "awaiting_approval"}, "name")
    if not name:
        return "not_waiting"
    # 抢占：同一次点击重复到达、两个页面同时点，只有一个能把状态从等待改走
    frappe.db.sql(f"UPDATE `tab{TOOL_RUN}` SET status=%s WHERE name=%s AND status='awaiting_approval'",
                  ("running" if allow else "denied", name))
    claimed = frappe.db._cursor.rowcount == 1
    frappe.db.commit()
    if not claimed:
        return "not_waiting"
    _announce(conversation, use_id, allow)
    if not allow:
        # 带上 status: denied（不随语言变）：对话页重开时照它认出这一步被拒绝、没做，步骤不写成做完的说法（第 8 轮测试机）
        text = json.dumps({"status": "denied",
                           "message": _("The user did not allow this change. Do not try it again unless the user asks.")},
                          ensure_ascii=False)
        frappe.db.set_value(TOOL_RUN, name, "result", text)
        frappe.db.commit()
        _publish_run(conversation, use_id, "denied")
        _hand_back(conversation, remote, use_id, text, False)
        return "denied"
    run = frappe.db.get_value(TOOL_RUN, name, ["tool", "tool_input"], as_dict=True)
    return _finish(conversation, remote, name, use_id, run.tool, json.loads(run.tool_input or "{}"))


def cancel_waiting(conversation: str) -> int:
    """这一轮已经结束（点了停止、出错收尾、会话终止）：本对话还在等「允许」的调用都标成 cancelled。引擎不再等这些结果，
    别的设备上没刷新的旧卡片事后点「允许」也不会执行（`decide` 只认 awaiting_approval）。返回改了几条。
    逐条抢占（同 `decide`：只改还在等的那一条，同一刻被点了「允许 / 拒绝」的不算作废），改成了的推执行记录（`_publish_run`），
    别的设备上还挂着的卡片当场写「已取消」。"""
    runs = frappe.db.sql(f"SELECT name, tool_use_id, user FROM `tab{TOOL_RUN}` WHERE session_id=%s "
                         "AND status='awaiting_approval'", (conversation,), as_dict=True)
    changed = []
    for run in runs:
        frappe.db.sql(f"UPDATE `tab{TOOL_RUN}` SET status='cancelled' WHERE name=%s AND status='awaiting_approval'",
                      (run.name,))
        if frappe.db._cursor.rowcount == 1:
            changed.append(run)
    if runs:
        frappe.db.commit()
    for run in changed:
        _publish_run(conversation, run.tool_use_id, "cancelled", run.user)
    return len(changed)


def _announce(conversation: str, use_id: str, allow: bool) -> None:
    """回答落库后马上推给会话主人自己的房间（`contracts.answer_event`），不等工具执行完：别的设备上开着同一段对话的卡片
    1–2 秒内换成点完的样子（第 10 轮 C22）。推不出去不影响这次回答，那边照旧等结果或补拉。"""
    try:
        event = contracts.answer_event(use_id, allow, datetime.now(timezone.utc).isoformat())
        frappe.publish_realtime(contracts.REALTIME_EVENT, {"session_id": conversation, "event": event},
                                user=frappe.session.user)
    except Exception as err:
        print(f"assistant answer {conversation}: {type(err).__name__}", flush=True)


def _publish_run(conversation: str, use_id: str, status: str, user: str | None = None) -> None:
    """执行记录的状态变了（执行完 / 出错、拒绝、作废，`contracts.tool_run_event`）：推给会话主人自己的房间，开着这段对话的
    设备当场按本站记录定下那张卡（第 11 轮 A 段）。推不出去不影响主流程：那边重开、补拉时随事件并进来（`sessions.events`）。"""
    try:
        event = contracts.tool_run_event(use_id, status, datetime.now(timezone.utc).isoformat())
        frappe.publish_realtime(contracts.REALTIME_EVENT, {"session_id": conversation, "event": event},
                                user=user or frappe.session.user)
    except Exception as err:
        print(f"assistant tool run {conversation}: {type(err).__name__}", flush=True)


def _hand_back(conversation: str, remote: str, use_id: str, text: str, is_error: bool) -> None:
    """把这次调用的结果交回引擎。执行记录已经先落库：交不回去（多半是这一轮已被停止，引擎已代发中断结果、不再收这次的结果）
    不再抛给调用方——工具确实执行过，点「允许」的请求不报 500、卡片照本站记录写（第 11 轮 A2）。只记类型，内容不进日志。"""
    try:
        engine.adapter().post(remote, [contracts.tool_result(use_id, text, is_error)])
    except Exception as err:
        print(f"assistant tool result {conversation}: {type(err).__name__}", flush=True)


def _input(text) -> dict:
    try:
        value = json.loads(text or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def work_list(conversation: str) -> str:
    """这段对话（全部分段，记录按对话编号登记）调用过的工具与关键参数，新 → 旧：接续新上下文时带给智能体
    （`contracts.work_list`），它才知道在做什么、要最新结果时再调一次。"""
    runs = frappe.get_all(TOOL_RUN, filters={"session_id": conversation, "user": frappe.session.user},
                          fields=["tool", "tool_input", "status"], order_by="creation desc", limit=WORK_RUNS)
    return contracts.work_list({"tool": run.get("tool"), "input": _input(run.get("tool_input")),
                                "status": run.get("status")} for run in runs)


def may_wait() -> bool:
    """这次执行能不能在工具里有界地等一会儿别处的结论（例如推送后等写账结果，用户 2026-10-13 第 9 条）：只有推送线程
    （`relay`）能等 —— 那是本轮对话专有、以等网络为主的线程，等着不占别人的名额。网页请求（页面兜底 `run_tool`、点「允许」）
    占的是网页进程的请求线程，引擎回调排的后台任务（`webhook`）占的是 long 队列的进程，都是全站共用的，工具要立刻返回。"""
    return getattr(frappe.local, "request", None) is None and getattr(frappe.local, "job", None) is None


def _finish(conversation, remote, run_name, use_id, tool, tool_input) -> str:
    # 工具要用到的对话信息（例如取本对话的附件原件，`files.attachment`）与能不能等（`may_wait`）；工具函数本身只收模型给的参数。
    frappe.local.pearnly_assistant = {"session_id": conversation, "may_wait": may_wait()}
    fresh()
    _use_lang(conversation)
    try:
        text, is_error = tools.run(tool, tool_input)
    except Exception:
        frappe.db.rollback()
        frappe.log_error(title="Assistant tool failed")
        text, is_error = _("The tool failed. Try again later."), True
    finally:
        frappe.local.pearnly_assistant = None
    status = "error" if is_error else "done"
    # 留记录：谁（user）、何时（modified）、调了什么（tool / tool_input）、结果（result，含改后的状态）。先落库、推给对话页，
    # 再交回引擎：执行过就以本站记录为准，交回失败（这一轮已被停止）也不丢（第 11 轮 A2：工具已执行，记录却一直停在 running）
    frappe.db.set_value(TOOL_RUN, run_name, {"status": status, "result": text[:60000]})
    frappe.db.commit()
    _publish_run(conversation, use_id, status)
    _hand_back(conversation, remote, use_id, text, is_error)
    return status
