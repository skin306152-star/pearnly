# -*- coding: utf-8 -*-
"""LINE 里打开的页面（扫身份证、编辑客户档 / 订车单）做完事后，怎么把结果送回聊天而不占推送额度。

LINE 的免费回覆只能回给"刚发来消息的人"、收到后 1 分钟内用一次；页面直接调本站点接口时聊天里没有消息，
结果只能推播（占每月额度）。所以页面做完后替业务员在聊天里发一句纯文字（`marker`，带 6 位短码；
需要 LIFF 的 `chat_message.write`），webhook 收到这句话就有了回覆令牌，再把结果回给他。
页面发不了消息时（没开权限、不在 LINE 里）调回落接口，结果照旧推播。

这句话只有文字和短码，不加图标（用户 2026-10-05）。认它时按四种语言的原文比对，
不靠符号：业务员手机是什么语言，页面就发什么语言。
"""

from __future__ import annotations

import re
import secrets
from typing import Optional, Tuple

from pearnly_dms.line import cards

CODE_LENGTH = 6
# 页面类型 → 那句话的文案键（`dms.card.<键>`）。
KINDS = {"scan": "TXT_SCAN_SENT", "edit": "TXT_EDIT_SENT"}
_HELD = "pearnly:dms:page-reply:"
_HELD_SECONDS = 15 * 60
_SHAPE = re.compile(r"^(.+?)\s+#([0-9a-f]{%d})$" % CODE_LENGTH, re.S)


def _state():
    from pearnly_core.execution import state

    return state.store()


def marker(kind: str, code: str) -> str:
    """页面替业务员发回聊天的那句话（按当前收件人语言）。"""
    return f"{getattr(cards, KINDS[kind])} #{code}"


def parse(text: str) -> Optional[Tuple[str, str]]:
    """聊天里收到的文字是不是某个页面发的那句话 → `(类型, 短码)`；不是返回 None。"""
    from pearnly_dms import i18n

    match = _SHAPE.match(str(text or "").strip())
    if not match:
        return None
    label = match.group(1).strip()
    for kind, key in KINDS.items():
        if label in set(i18n.all_languages(f"dms.card.{key}").values()):
            return kind, match.group(2)
    return None


def hold(line_user_id: str, messages: list) -> dict:
    """编辑页保存后的新卡先存着（15 分钟），等那句话来了用免费回覆送出。返回页面要发的那句话与短码。"""
    code = secrets.token_hex(CODE_LENGTH // 2)
    _state().set_value(_HELD + code, {"line_user_id": line_user_id, "messages": list(messages)},
                       expires_in_sec=_HELD_SECONDS)
    return {"code": code, "marker": marker("edit", code)}


def _take(line_user_id: str, code: str) -> Optional[list]:
    """取出并删掉存着的消息（同一张卡只送一次）；不是这个人的、过期的返回 None。"""
    client = _state()
    held = client.get_value(_HELD + str(code or ""))
    if not isinstance(held, dict) or held.get("line_user_id") != line_user_id:
        return None
    # 那句话与页面的回落调用可能都会到：只有先抢到的那一个送。
    if not client.set(client.make_key(_HELD + code + ":taken"), "1", ex=_HELD_SECONDS, nx=True):
        return None
    client.delete_value(_HELD + code)
    return list(held.get("messages") or [])


def deliver(line_user_id: str, code: str, reply_token: str = "") -> Optional[bool]:
    """把存着的卡送回聊天：有令牌就免费回覆，没有（页面回落）就推播。
    卡不在了（过期、已送过、不是这个人的）返回 None；发送没成功返回 False。"""
    from pearnly_core import line

    messages = _take(line_user_id, code)
    if not messages:
        return None
    return bool(line.reply_or_push(line_user_id, messages, reply_token))


def route(line_user_id: str, reply_token: str, text: str) -> bool:
    """文本路由最先问这里：是页面发的那句话就处理掉（返回 True），否则返回 False 交给后面。"""
    found = parse(text)
    if not found:
        return False
    kind, code = found
    if kind == "scan":
        from pearnly_dms.intake import scan

        return scan.handle_code(line_user_id, reply_token, code)
    if deliver(line_user_id, code, reply_token) is None:
        from pearnly_core import line

        line.reply(reply_token, [{"type": "text", "text": str(cards.TXT_EXPIRED)}])
    return True


__all__ = ["KINDS", "deliver", "hold", "marker", "parse", "route"]
