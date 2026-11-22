"""登录 MR.ERP 的唯一入口（多产品共用；11-11 风险 7 真站点实测后，主会话定按类修）。

MR.ERP 一个账号同时只能在一处登录：每登录一次都会把别处（会计自己的浏览器、我们缓存的推送会话）踢下线。所以推送
（`push.transport.MrErpImport`）、读菜单 / 主档（`menu_read.read`）、目录采集（`collect.collect`）以及以后的核账本，
都从 :func:`use` 拿会话，规矩只写这一处：

1. 被踢标记（`kick_mark`）：``manual`` 为真（人在明确的入口点了）先清掉、这次照常登录；否则有标记就不发任何请求，
   直接抛 :class:`Marked`（``ERR_ACCOUNT_IN_USE``）。``manual`` 必须由调用入口显式给（主会话 11-11：「人点的」标在入口上，
   共用层不默认算人点）；
2. 同账号互斥锁（`session_lock.account_lock`，Cowork 与 ERP 同一把）；
3. 接上缓存里上一次登录的会话（`session_cache`，两产品同一份），不每次都登录；过期（被跳回登录页）由会话自己重登一次；
4. 退出时：会话被踢（「มีผู้ Login ซ้ำ」）→ 丢掉缓存、记标记；否则把会话存回缓存给下一次用。
5. ``login=False``（后台探测，11-22）：只接着用缓存里还热的会话，没有或已过期就抛 ``ERR_NO_SESSION``，**不登录**——探测不新增
   任何会顶掉会计的登录次数。

全仓只在这里新建 `MrErpSession`（`tests/test_mrerp_account_session.py` 源码锁住）。
"""
from __future__ import annotations

import logging
from contextlib import contextmanager

from pearnly_core.mrerp import kick_mark, session_cache
from pearnly_core.mrerp.session import NO_SESSION, MrErpSession, SessionError, lock_key
from pearnly_core.session_lock import account_lock

logger = logging.getLogger(__name__)

IN_USE = "ERR_ACCOUNT_IN_USE"


class Marked(SessionError):
    """这个账号被踢过、还没人在明确的入口点过：这次不登录、不发请求。"""

    def __init__(self):
        super().__init__(IN_USE, "signed in elsewhere, waiting for a manual push")


@contextmanager
def use(system_url, username, password, *, manual, lock_wait, login=True):
    """拿一个可用的 MR.ERP 会话（见文件头五条）。``manual`` 没有默认值：调用方必须说清这次是不是人点的。

    锁拿不到抛 `session_lock.SessionLockUnavailable`；登录 / 网络错误照 `MrErpSession` 抛 `SessionError`，由调用方各自映射。"""
    account = lock_key(system_url, username)
    if manual:
        kick_mark.clear(account)
    elif kick_mark.state(account):
        raise Marked()
    cache = session_cache.key(account, password)
    if not login and not session_cache.get(cache):
        raise SessionError(NO_SESSION, "no warm session")
    with account_lock(account, timeout_sec=lock_wait):
        session = MrErpSession(system_url, username, password)
        session.may_login = bool(login)
        if not session.restore(session_cache.get(cache)) and not login:
            session.close()
            raise SessionError(NO_SESSION, "no warm session")
        try:
            yield session
        finally:
            if session.kicked:
                session_cache.drop(cache)
                try:
                    kick_mark.mark(account)
                except Exception:  # noqa: BLE001 — 记不上标记不改这次的结论（这次照样报被踢）
                    logger.exception("MR.ERP kicked mark not stored")
            elif session.cookie() or login:
                session_cache.put(cache, session.cookie())
            else:
                session_cache.drop(cache)  # 会话过期了又不许登录（``login=False``）：丢掉，下次照常判
            try:
                session.close()
            except Exception:  # noqa: BLE001 — 关连接失败不影响结果
                pass


__all__ = ["IN_USE", "Marked", "use"]
