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
import secrets
from typing import Any, Dict, Optional

from pearnly_core import line
from frappe import _

logger = logging.getLogger(__name__)


def send(line_user_id: str, msg: Optional[Dict[str, Any]] = None, reply_token: str = "") -> bool:
    """结构化消息出口：有 reply_token 就 reply，没有就 push。

    订车逐问既可能由 postback 应答（有令牌），也可能由后台任务发起（没有令牌）。
    `msg is None` 明确表示"这一步没有话说"，不算错，回 False 让调用方自己决定。
    """
    if msg is None:
        return False
    if reply_token:
        return line.reply(reply_token, [msg])
    return line.push(line_user_id, [msg])


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
    """把重活丢后台（老产品 `_out.make_spawn` 那一层）。

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
    frappe.enqueue(
        f"{fn.__module__}.{fn.__name__}",
        queue="long",
        timeout=600,
        job_id=f"dms-{job}-{secrets.token_hex(4)}",
        **dict(zip(names, args)),
    )
