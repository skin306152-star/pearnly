"""MR.ERP 直写复用的会话（11-09 风险 7）：把上一次登录留下的 PHPSESSID 记下来，下一次推送接着用，不每推一次就登录
（每登录一次都会踢掉用户自己浏览器里同一账号）。Cowork 与 ERP 的直写共用。

- 存在不淘汰的状态 Redis（`execution.state.store`），键不带站点前缀：同一个 MR.ERP 账号在 Cowork 和 ERP 用的是同一个会话
  （两边各存一份的话，一边登录就把另一边踢掉，违反「同账号两产品排队、不互踢」）。读写都在同账号互斥锁
  （`session_lock.account_lock`）里，不会两边同时用。
- 键 = 摘要（`session.lock_key`（网址 + 用户名）+ 密码）：只有填了同一个账号**和密码**的才接得上这个会话；只填对用户名的别的租户
  拿不到别人的会话。原文不进键名。
- 只存会话号、不存密码；20 分钟没用就丢（短于 PHP 默认会话寿命 24 分钟，过期的由 `session.MrErpSession.prepare` 重登一次）。
- 被踢（同一账号在别处登录了）时调用方 :func:`drop`，并记「被踢」标记（`pearnly_core.mrerp.kick_mark`）：下一次是人在明确的推送入口点了才重新登录。
- 缓存坏了不挡推送：读不到当没有（照常登录），写不进就算了。
"""
from __future__ import annotations

import hashlib

TTL_SECONDS = 20 * 60
_PREFIX = "pearnly:mrerp-session:"


def _store():
    from pearnly_core.execution import state

    return state.store()


def key(account_key, password) -> str:
    raw = f"{account_key or ''}\0{password or ''}".encode("utf-8")
    return _PREFIX + hashlib.sha256(raw).hexdigest()[:40]


def get(name) -> str:
    try:
        value = _store().get(name)
    except Exception:  # noqa: BLE001 — 缓存不可用就当没有，照常登录
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return str(value or "")


def put(name, cookie) -> None:
    if not name or not cookie:
        return
    try:
        _store().set(name, str(cookie), ex=TTL_SECONDS)
    except Exception:  # noqa: BLE001
        pass


def drop(name) -> None:
    try:
        _store().delete(name)
    except Exception:  # noqa: BLE001
        pass


__all__ = ["TTL_SECONDS", "drop", "get", "key", "put"]
