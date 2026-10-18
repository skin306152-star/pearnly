"""对话事件实时推送（ADR-019）。

为什么：引擎的「列出事件」接口偶尔单次要 30 多秒（2026-10-10 测试机与本机同样实测），靠轮询取新回复就会整段卡住；
事件流的连接稳定在 0.4 秒左右。所以用户一侧每次发出（消息 / 批准 / 工具结果）前，先确保本会话有一路转发：
在本进程起一个后台线程接引擎的事件流，每收到一条就经站点实时通道（Frappe socket.io，用户自己的房间）推给会话主人；
回合结束（空闲且不是在等工具或批准）、会话终止或到时限就收线。

- 不占网页请求线程，也不占后台队列进程：线程只在一轮对话期间存在，以等网络为主。
- 同一会话同一时刻只有一路（Redis 锁，带过期，收到事件时续期）。进程重启丢了这一路也不丢数据：
  对话页仍会低频补拉事件记录，事件按编号去重。
- 只推给会话主人自己的房间，内容与「列出事件」相同，另加回复的逐字预览。推送带的是浏览器认的对话编号
  （分段接续后远端会话换了，对话编号不变，见 `sessions`）。
- 顺手记下每次模型请求的输入量（`context.note`），供计价分档的接续判断；CC 每回一段话（`agent.message`）把对话的最后活动
  时间记成现在（`context.active`，侧栏「上次更新」按它排）。
- 智能体停下来等 Pearnly 工具结果时，直接在服务端以会话主人的身份执行（`toolrun`），不靠页面开着。
- 一轮结束时告诉 `titles`：还挂着临时标题的对话这时排概括（同 Claude 在第一轮之后起标题）。
- 记输入量、最后活动时间、执行工具都是附带的，出错只记类型、不打断转发；每次写库前先结束这条线程的旧读快照
  （`_fresh`）。第 11 轮重测 A 段：线程一开头读会话行就有了快照，用户发消息的请求随后改了同一行（最后活动时间）并提交；
  智能体一上来就调工具（前面没有回复正文）时，第一次写库是记输入量，按旧快照改这一行撞 1020（MariaDB 11.8 快照隔离，
  Frappe 抛 QueryDeadlockError），异常打断事件流、引擎此刻已停下等工具就收线——「停下来等工具」那条事件推不出去、工具也
  不在服务端执行，手机等低频补拉 7–21 秒才出卡片，补拉没轮到时卡片一直不出。
"""
import threading
import time
import uuid

import frappe

from pearnly_core.assistant import context, contracts, engine, titles, toolrun

EVENT = contracts.REALTIME_EVENT
LOCK_TTL = 120
READY_WAIT = 3
MAX_SECONDS = 15 * 60
RECONNECTS = 3


def _lock(site: str, remote: str) -> str:
    return f"pearnly_assistant:relay:{site}:{remote}"


def ensure(conversation: str, remote: str, user: str) -> None:
    """确保这段远端会话有一路转发在跑；新起的一路等接上事件流（最多几秒）再返回，之后发出的事件不会漏推。"""
    site = frappe.local.site
    key, token = _lock(site, remote), uuid.uuid4().hex
    if not frappe.cache.set(key, token, nx=True, ex=LOCK_TTL):
        return
    ready = threading.Event()
    threading.Thread(target=_run, name=f"assistant-relay-{remote}", daemon=True,
                     args=(site, frappe.local.sites_path, conversation, remote, user, key, token, ready)).start()
    ready.wait(READY_WAIT)


def finished(event: dict) -> bool:
    """这一轮是否结束：会话终止，或空闲且不是停下来等工具结果 / 用户批准。"""
    kind = event.get("type")
    if kind in ("session.status_terminated", "session.deleted"):
        return True
    return kind == "session.status_idle" and (event.get("stop_reason") or {}).get("type") != "requires_action"


def pending_tools(event: dict) -> list[str]:
    """停下来等结果的调用编号（空闲且原因是等待处理）。"""
    stop = event.get("stop_reason") or {}
    if event.get("type") != "session.status_idle" or stop.get("type") != "requires_action":
        return []
    return [str(i) for i in stop.get("event_ids") or []]


def _turn_ended(conversation):
    """这一轮结束：没答的「允许」卡片作废（停止、出错收尾时引擎不再等它，`toolrun.cancel_waiting`）；再排侧栏标题的概括。
    两件都是附带的：出错不影响收线。"""
    try:
        toolrun.cancel_waiting(conversation)
    except Exception as err:
        frappe.db.rollback()
        print(f"assistant relay {conversation}: cancel {type(err).__name__}", flush=True)
    try:
        titles.turn_ended(conversation)
    except Exception as err:
        print(f"assistant relay {conversation}: title {type(err).__name__}", flush=True)


def _fresh():
    """结束这条线程的读快照：线程里的事务每步都已提交，这里没有要丢的写。之后的写按当前数据来，不撞别的请求在这期间
    提交的改动（见文件头）。"""
    frappe.db.rollback()


def _active(conversation, name):
    """记最后活动时间只是附带的：这一行正被别的事务锁着（例如后台任务挂提醒时写同一行）就这次不记、不等锁
    （`context.active(wait=False)`，等锁会让事件流和逐字预览停住），出错也不打断转发。按记录名写（只清这一条的缓存）。"""
    if not name:
        return
    try:
        _fresh()
        if context.active(name, wait=False):
            frappe.db.commit()
    except Exception as err:
        frappe.db.rollback()
        print(f"assistant relay {conversation}: activity {type(err).__name__}", flush=True)


def _note(conversation, event):
    """记模型请求的输入量（接续判断用）只是附带的：出错回滚、只记类型，不打断转发（见文件头）。"""
    try:
        _fresh()
        if context.note(conversation, [event]):
            frappe.db.commit()
    except Exception as err:
        frappe.db.rollback()
        print(f"assistant relay {conversation}: context {type(err).__name__}", flush=True)


def _execute(conversation, remote, call):
    """在服务端执行停下来等的工具（`toolrun.execute`，它自己先结束旧快照）。出错回滚、只记类型，事件流照常转发：页面兜底
    （`run_tool`）与引擎回调（`webhook`）还会再提醒，同一次调用靠执行记录只执行一遍。"""
    try:
        toolrun.execute(conversation, remote, call)
    except Exception as err:
        frappe.db.rollback()
        print(f"assistant relay {conversation}: tool {type(err).__name__}", flush=True)


def _run(site, sites_path, conversation, remote, user, key, token, ready):
    frappe.init(site=site, sites_path=sites_path)
    try:
        frappe.connect()
        frappe.set_user(user)  # 工具以会话主人的身份执行
        api = engine.adapter()
        name = frappe.db.get_value(context.SESSION, {"session_id": conversation}, "name")  # 记最后活动时间用
        deadline = time.monotonic() + MAX_SECONDS
        calls = {}
        for _attempt in range(RECONNECTS + 1):
            try:
                for event in api.stream(remote, ready.set):
                    frappe.publish_realtime(EVENT, {"session_id": conversation, "event": event}, user=user)
                    frappe.cache.expire(key, LOCK_TTL)
                    if event.get("type") == "span.model_request_end":
                        _note(conversation, event)
                    if event.get("type") == "agent.message":
                        _active(conversation, name)
                    if event.get("type") == "agent.custom_tool_use":
                        calls[event.get("id")] = event
                    for use_id in pending_tools(event):
                        if use_id in calls:
                            _execute(conversation, remote, calls.pop(use_id))
                    if finished(event):
                        _turn_ended(conversation)
                        return
                    if time.monotonic() > deadline:
                        return
            except Exception as err:
                # 只记类型：事件内容与密钥都不进日志。断了就按下面的状态决定是否重接；重接前丢掉这次的事务与快照。
                print(f"assistant relay {remote}: {type(err).__name__}", flush=True)
                try:
                    frappe.db.rollback()
                except Exception:
                    pass
            if time.monotonic() > deadline or api.status(remote) != "running":
                return
    except Exception as err:
        print(f"assistant relay {remote}: {type(err).__name__}", flush=True)
    finally:
        ready.set()
        try:
            if (frappe.cache.get(key) or b"").decode() == token:
                frappe.cache.delete(key)
        finally:
            frappe.destroy()
