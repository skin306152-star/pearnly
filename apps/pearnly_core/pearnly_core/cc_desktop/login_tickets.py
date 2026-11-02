"""MR.ERP 登录在 CC 桌面版里授权用的一次性票据（多产品共用，ADR-026 第 7 条，评估 §14.4，2026-11-02）。

授权分两段，密码只走第二段：

1. 对话页（Frappe 会话 + CSRF）点「在 CC 桌面版中授权」：产品按自己的规则核这个人能不能改这条连接，再按对话页交来的本机
   设备密钥认出是哪台电脑，:func:`issue` 发一张票（只存摘要、5 分钟、只能用一次），绑住「站点 + 通道 + 连接 + 人 + 电脑」；
   票交给 CC 桌面版（桥），页面不碰密码；
2. CC 桌面版弹原生小窗收用户名和密码，带这张票经机器通道（机器令牌、精确 POST、不带会话）交给站点：:func:`redeem`
   取出并作废这张票，核同一个站点、同一种通道、发票时那台电脑就是令牌认出的这台，产品按当前权限再核一次后写进连接的
   Password 字段。

票只是「这个人此刻在这台电脑上要授权这条连接」的凭证：它本身不含密码，也不能用来读任何东西。Redis 里只存票的摘要做键，
值是不含秘密的绑定信息；用掉、过期都没有了（不淘汰的状态存储，`execution.state`）。
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets

TTL_SECONDS = 300
CHANNELS = ("cowork", "core")
_TICKET = re.compile(r"^mrl_[A-Za-z0-9_-]{32,64}$")
_NAME = re.compile(r"^[A-Za-z0-9._:@-]{1,140}$")
_PREFIX = "pearnly:cc_mrerp_login:"
# 取出并删掉（一条命令）：同一张票两个请求同时来，只有一个拿得到
_TAKE = "local v = redis.call('get', KEYS[1]); if v then redis.call('del', KEYS[1]) end; return v"


class TicketError(ValueError):
    def __init__(self, code="ticket_invalid"):
        self.code = code
        super().__init__(code)


def new_ticket() -> str:
    return "mrl_" + secrets.token_urlsafe(32)


def valid_ticket(value) -> bool:
    return isinstance(value, str) and bool(_TICKET.fullmatch(value))


def key_of(ticket: str) -> str:
    """票在 Redis 里的键：只用摘要，票本身不进键名。"""
    if not valid_ticket(ticket):
        raise TicketError()
    return _PREFIX + hashlib.sha256(ticket.encode("ascii")).hexdigest()


def claim(*, site: str, channel: str, connection: str, user: str, device: str) -> dict:
    """票绑住的内容（纯函数，不含秘密）。"""
    values = {"site": site, "channel": channel, "connection": connection, "user": user, "device": device}
    if channel not in CHANNELS or any(not isinstance(value, str) or not _NAME.fullmatch(value) for value in values.values()):
        raise TicketError("ticket_scope_invalid")
    return values


def matches(found: dict | None, *, site: str, channel: str, device: str) -> dict:
    """取出来的票是不是给这个站点、这种通道、这台电脑的（纯函数）；不是就当票无效（不说哪一项不对）。"""
    if (not isinstance(found, dict) or found.get("site") != site or found.get("channel") != channel
            or not device or found.get("device") != device):
        raise TicketError()
    return found


def issue(**values) -> str:
    """发一张票（:func:`claim` 的字段），回票本身；只在这一次回给对话页。"""
    from pearnly_core.execution import state

    bound = claim(**values)
    ticket = new_ticket()
    store = state.store()
    store.set(store.make_key(key_of(ticket)), json.dumps(bound, sort_keys=True), ex=TTL_SECONDS)
    return ticket


def redeem(ticket) -> dict | None:
    """取出并作废这张票；格式不对、过期、用过了都回 None。"""
    from pearnly_core.execution import state

    if not valid_ticket(ticket):
        return None
    store = state.store()
    raw = store.eval(_TAKE, 1, store.make_key(key_of(ticket)))
    if not raw:
        return None
    try:
        found = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except (TypeError, ValueError):
        return None
    return found if isinstance(found, dict) else None


# 机器通道交来的用户名和密码（纯规则，两种通道同一份）：只收这三个键，长度有界、不能是空的，密码不能全是 *
# （Frappe 的 Password 字段把全 * 当「没改」不存）。报错只回码，从不带原文。
MAX_USERNAME, MAX_PASSWORD = 140, 512


def login_request(body) -> tuple[str, str, str]:
    """``{ticket, username, password}`` → (票, 用户名, 密码)；不对抛 :class:`TicketError`（``login_invalid``）。"""
    if not isinstance(body, dict) or set(body) != {"ticket", "username", "password"}:
        raise TicketError("login_invalid")
    ticket, username, password = body["ticket"], body["username"], body["password"]
    if (not valid_ticket(ticket) or not isinstance(username, str) or not isinstance(password, str)
            or not username.strip() or len(username) > MAX_USERNAME or username != username.strip()
            or any(ord(ch) < 32 for ch in username)
            or not password or len(password) > MAX_PASSWORD or set(password) == {"*"}
            or any(ord(ch) < 32 for ch in password)):
        raise TicketError("login_invalid")
    return ticket, username, password
