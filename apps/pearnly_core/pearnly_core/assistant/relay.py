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
- 顺手记下每次模型请求的输入量（`context.note`），供计价分档的接续判断。
- 智能体停下来等 Pearnly 工具结果时，直接在服务端以会话主人的身份执行（`toolrun`），不靠页面开着。
"""
import threading
import time
import uuid

import frappe

from pearnly_core.assistant import context, engine, toolrun

EVENT = "pearnly_assistant_event"
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


def _run(site, sites_path, conversation, remote, user, key, token, ready):
    frappe.init(site=site, sites_path=sites_path)
    try:
        frappe.connect()
        frappe.set_user(user)  # 工具以会话主人的身份执行
        api = engine.adapter()
        deadline = time.monotonic() + MAX_SECONDS
        calls = {}
        for _attempt in range(RECONNECTS + 1):
            try:
                for event in api.stream(remote, ready.set):
                    frappe.publish_realtime(EVENT, {"session_id": conversation, "event": event}, user=user)
                    frappe.cache.expire(key, LOCK_TTL)
                    if event.get("type") == "span.model_request_end" and context.note(conversation, [event]):
                        frappe.db.commit()
                    if event.get("type") == "agent.custom_tool_use":
                        calls[event.get("id")] = event
                    for use_id in pending_tools(event):
                        if use_id in calls:
                            toolrun.execute(conversation, remote, calls.pop(use_id))
                    if finished(event) or time.monotonic() > deadline:
                        return
            except Exception as err:
                # 只记类型：事件内容与密钥都不进日志。断了就按下面的状态决定是否重接。
                print(f"assistant relay {remote}: {type(err).__name__}", flush=True)
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
