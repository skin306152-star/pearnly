"""CC 对话的用量记账（2026-11-11）：每轮结束读一次远端会话的累计用量，交账本写「当天增量」（`ai_usage.ledger.write_session`）。

- 谁来叫：转发线程看到这一轮结束（`relay`）、引擎回调叫醒本站（`webhook.receive`，页面关着时）。两处都只排任务，不在
  转发线程里读引擎、不写库，回复不受影响。同一段远端会话排着的不重复排。
- 只读引擎的会话对象（与 `status` 同一个接口），不读事件列表；累计数由引擎算好，漏了哪一轮下一轮自然补齐。
- 出错只记日志：会话删了、引擎一时连不上，这一轮不记，下一轮再记。
"""
import frappe

from pearnly_core.ai_usage import ledger
from pearnly_core.assistant import engine

JOB = "pearnly_core.assistant.usage.sync"
QUEUE = "short"
JOB_TIMEOUT = 120


def request(conversation: str, remote: str, user: str) -> None:
    """这段远端会话刚结束一轮：排任务记它的用量（附带的：入队失败只记日志）。"""
    if not remote:
        return
    try:
        frappe.enqueue(JOB, queue=QUEUE, timeout=JOB_TIMEOUT, job_id=f"assistant-usage-{remote}", deduplicate=True,
                       enqueue_after_commit=False, conversation=conversation, remote=remote, user=user)
    except Exception as err:  # noqa: BLE001
        _note(f"assistant usage request failed: {type(err).__name__}")


def sync(conversation: str, remote: str, user: str) -> None:
    """后台任务：读累计用量、写账。"""
    try:
        session = engine.adapter().session_usage(remote)
    except Exception as err:  # noqa: BLE001
        _note(f"session usage {remote} unavailable: {type(err).__name__}")
        return
    if session:
        ledger.write_session(conversation, remote, user, session)


def _note(text: str) -> None:
    """记一行日志；记不下也不抛（附带的）。"""
    try:
        frappe.logger("pearnly.ai_usage").info(text)
    except Exception:  # noqa: BLE001
        pass
