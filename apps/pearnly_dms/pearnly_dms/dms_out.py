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

from typing import Any, Dict, Optional

from pearnly_core import line


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
