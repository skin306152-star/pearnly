# -*- coding: utf-8 -*-
"""LINE 结构化消息出口（quickReply / Flex 必须走 reply 或 push 接口）。

老产品同样有一层 `services/line_dms/_out.py`：纯文本走 `_reply`/`_push`，结构化消息
统一走 `_send` —— 它**按有没有 reply_token 自己分流**，调用方不再各写一份。

新平台少了两样东西，都是架构使然：

· **没有 channel 参数**。老产品一个库挂多个 OA，发送时要指定渠道；新平台
  **一个站点就是一家租户、一条 OA**（`pearnly_core.line` 读站点配置），不用传。
· **没有 `_thr` / `_spawn`**。那是 asyncio 的线程/后台包装；新平台是 Frappe 的同步栈，
  重活靠 `frappe.enqueue`（见 `dms_flow._enqueue`），这一层不需要。
"""

from __future__ import annotations

import logging
import copy
import secrets
from typing import Any, Dict, Optional

from pearnly_core import line
from frappe import _

logger = logging.getLogger(__name__)


def _snapshot_notice(msg):
    """**不再往消息里拼"资料时间 / 地址资料时间"**（用户 2026-09-25 明确要求）。

    以前这里会把「ข้อมูล ณ …」「ข้อมูลที่อยู่ ณ …」补到每一条 LINE 回话后面（纯文本拼一行、
    Flex 卡在正文末尾加一小行灰字）。操作员说这行没用、还占地方，所以**消息正文不再带时间**。
    时间本身照旧记在快照元数据里（连接页、查询结果卡按需显示），只是不再注入对方的消息。

    这里保留两个 flag 的消费：一次请求里读过的快照标记仍按"本消息消费一次"处理，
    免得残留在后面某条消息上（将来若要恢复显示，直接从这里接着写即可）。
    """
    import frappe

    frappe.flags.pop("dms_snapshot_status", None)
    frappe.flags.pop("dms_snapshot_geo_status", None)
    return msg


def send(line_user_id: str, msg: Optional[Dict[str, Any]] = None, reply_token: str = "") -> bool:
    """结构化消息出口：有 reply_token 就 reply，没有就 push。

    现在的口径是 **`reply_or_push`**（2026-09-23）：有令牌就先试**免费回覆**，被明确拒绝
    才转推播 —— 后台任务因此也能蹭免费窗口，而不是"手上没令牌就只能花钱"。见
    `pearnly_core.line.reply_or_push` 里那三条规则（尤其"超时也转推播、宁可极小概率重复"
    的取舍）。

    订车逐问既可能由 postback 应答（有令牌），也可能由后台任务发起（没有令牌）。
    `msg is None` 明确表示"这一步没有话说"，不算错，回 False 让调用方自己决定。
    """
    if msg is None:
        return False
    return line.reply_or_push(line_user_id, [_snapshot_notice(msg)], reply_token)


def push_or_reply_text(line_user_id: str, text: str, reply_token: str = "") -> bool:
    """纯文本的同一个口径：能免费回覆就免费，不行才推播。

    给"原本写死 `_push`（纯文本）"的那些地方用 —— 它们同样带着令牌，只是以前没用。
    """
    return line.reply_or_push(line_user_id, [_snapshot_notice({"type": "text", "text": text})], reply_token)


def reply_text(reply_token: str, text: str) -> bool:
    """纯文本回复（老产品 `_out._reply`）。"""
    if not reply_token:
        return False
    return line.reply(reply_token, [{"type": "text", "text": text}])


def push_text(line_user_id: str, text: str) -> bool:
    """纯文本推送（老产品 `_out._push`）。"""
    return line.push(line_user_id, [{"type": "text", "text": text}])


def start_loading(line_user_id: str, seconds: int = 30) -> bool:
    """重活开始前给对方一个"正在输入"（老产品 `_out.start_loading`）。"""
    return line.start_loading(line_user_id, seconds)


def download_content(message_id: str) -> Optional[bytes]:
    """下载消息内容（图片/凭证）。取不到就 None —— 调用方按"附件失败"如实告知。"""
    if not message_id:
        return None
    try:
        return line.download_message_content(message_id)
    except Exception:  # noqa: BLE001
        logger.warning("[dms out] 下载消息内容失败：%s", message_id, exc_info=True)
        return None


def enqueue(job: str, fn, *args) -> None:
    """识别/写入进后台；纯快照查重保留身份守卫并直接完成。

    webhook 必须立刻返回，否则 LINE 会超时重发 —— 而重发会让同一个按钮被点两次。

    ⚠️ **参数按形参名传**：后台 worker 是 `method(**kwargs)`（frappe 的
    `background_jobs.execute_job`），写成 `arg0/arg1/...` 会在 worker 里 `TypeError`，
    而 webhook 这边只看到"已入队"（用户表现为一直没有回卡）。点号路径取自函数自身，
    不写死模块名。
    """
    import inspect

    import frappe

    names = [name for name in inspect.signature(fn).parameters if name != "self"]
    if len(names) != len(args):
        # 参数对不上是**写代码时**的错：当场炸，别留成 worker 里的静默失败
        raise TypeError(
            _("{0} takes {1} arguments, but {2} were passed to the queue").format(
                fn.__name__, len(names), len(args)
            )
        )
    from pearnly_dms.line.identity import current

    arguments = dict(zip(names, args))
    identity = current(arguments.get("line_user_id"))
    if not identity or identity["user"] != (arguments.get("binding") or {}).get("user_id"):
        frappe.throw("dms_booking.not_bound", frappe.PermissionError)
    arguments["binding"] = {**arguments["binding"], "binding_revision": identity["binding_revision"]}
    if job == "dedup":
        # Cache-only read: never wait behind OCR, writes or background imports.
        if fn.__module__ != "pearnly_dms.intake.flow" or fn.__name__ != "run_dedup":
            raise ValueError("Invalid cache-only dedup target")
        return run_guarded(f"{fn.__module__}.{fn.__name__}", arguments)
    # Show activity while the job is waiting, then the worker renews it on start.
    start_loading(arguments["line_user_id"], 60)
    if job == "image":
        from pearnly_core.execution.work import submit
        from pearnly_core.execution.errors import QueueFull, Deferred
        try:
            return submit("dms.id_card", str(arguments["message_id"]), arguments, identity["user"])
        except (QueueFull, Deferred):
            push_text(arguments["line_user_id"], _("The recognition queue is busy. Please send the image again shortly."))
            raise
    frappe.enqueue(
        "pearnly_dms.line.out.run_guarded",
        queue="long",
        timeout=600,
        job_id=f"dms-{job}-{secrets.token_hex(4)}",
        target=f"{fn.__module__}.{fn.__name__}", arguments=arguments,
    )


def run_guarded(target, arguments):
    """Persisted queue entry: a rebind/revocation while waiting cancels stale work."""
    import frappe
    from pearnly_dms.line.identity import matches

    if not str(target).startswith("pearnly_dms."):
        raise ValueError("Invalid DMS job target")
    if not matches(arguments.get("binding") or {}, arguments.get("line_user_id")):
        return {"cancelled": "binding_changed"}
    # 一个后台任务 = 一次反应：发给本人的消息攒到任务结束一次发出，带着令牌就免费回覆，
    # 没令牌（或已过期）也只算一条推播。见 `pearnly_core.line.batch`。
    user = arguments.get("line_user_id") or ""
    # 排队等 DMS 账号时，每隔一会儿续一次"正在输入"（免费），别让对方以为没反应。
    frappe.local.dms_lock_wait_hook = (lambda: start_loading(user, 60)) if user else None
    from pearnly_dms import i18n

    try:
        # 后台任务同样按这个人手机上的语言说话（问过的语言有一小时缓存，不会每次都问 LINE）。
        with i18n.line_language(user), line.batch(user, arguments.get("reply_token") or ""):
            return frappe.get_attr(target)(**arguments)
    finally:
        frappe.local.dms_lock_wait_hook = None


def run_image(binding, line_user_id, message_id, reply_token=""):
    """Registered resumable OCR entry; order writers are deliberately excluded."""
    return run_guarded("pearnly_dms.intake.flow.process_image", {
        "binding": binding, "line_user_id": line_user_id, "message_id": message_id,
        "reply_token": reply_token})


def ocr_failed(arguments, code):
    from pearnly_dms.line.identity import matches
    from pearnly_core import line_session

    user = arguments.get("line_user_id")
    payload = (line_session.get_session(user) or {}).get("payload") or {}
    if (matches(arguments.get("binding") or {}, user)
            and payload.get("pending_image") == arguments.get("message_id")):
        text = (_("The recognition result needs checking. Please do not resend this image yet.")
                if code == "provider_outcome_unknown"
                else _("Recognition could not finish. Please try again shortly."))
        # 发图那口气的令牌还在就免费回覆（识别失败往往在一分钟内就能确定）
        push_or_reply_text(user, text, arguments.get("reply_token") or "")
