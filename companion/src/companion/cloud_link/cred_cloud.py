# -*- coding: utf-8 -*-
"""云端那份 CC 这一侧的凭据交接（``cred_handoff`` 的云端用法）：向桥要某条连接的 Express 登录凭据，解开后写进**云端本用户**
的凭据管理器（Generic，系统 DPAPI 加密），目标名与本地代录同格式 ``express_operator.credentials.target(连接)``；以后代录照
本地做法从本机凭据取、填登录框。收到桥的撤销就删掉那条并写回执。

现在还没有调用方（云端模式的代录宿主接上时调）。``device_key`` 与 ``epoch`` 由调用方给：``epoch`` 是云端读到的
``ctl\\session.json``（``tunnel_protocol.decode_session``）里的会话号；``device_key`` 是桥为这台云端电脑生成、存在桥本机的
那把（``link_store.LinkStore.device_key``）——**本分支里云端那份 CC 还拿不到它**（桥只拿它去站点登记，不交给云端），
交给云端的那一步要另做。

凭据管理器、时钟、等待都可注入；日志只有事件名、原因码、连接号。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, MutableSet, Optional

from ..logger import get_logger
from . import cred_handoff as handoff

log = get_logger(__name__)

DEFAULT_TIMEOUT = 30.0
POLL_SECONDS = 0.5
TIMEOUT = "timeout"


def _store(connection: str, user: str, password: str) -> None:
    from ..express_operator import credentials

    credentials.store(credentials.target(connection), user, password)


def _forget(connection: str) -> bool:
    from ..express_operator import credentials

    return credentials.forget(credentials.target(connection))


def fetch(ctl_dir: Path, *, device_key, epoch: str, connection: str, timeout: float = DEFAULT_TIMEOUT,
          poll: float = POLL_SECONDS, now: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
          seen_ids: Optional[MutableSet] = None, store: Callable[[str, str, str], None] = _store) -> str:
    """写 ``cred-req.json``，等桥写回 ``cred-resp.json``（最多 ``timeout`` 秒），解开后 ``store(连接, 用户名, 密码)``。
    回原因码：``ok``，桥不回应（没授权、不在）是 ``timeout``，其余见 ``cred_handoff.CODES``。结束时请求、回应两个文件都删掉并回读。"""
    ctl_dir = Path(ctl_dir)
    try:
        pending, data = handoff.make_request(device_key, epoch, connection, now())
    except handoff.Refused as exc:
        return exc.code
    request_path, response_path = ctl_dir / handoff.REQUEST_FILE, ctl_dir / handoff.RESPONSE_FILE
    handoff.remove_confirmed(response_path)   # 上一次没取走的旧回应不算
    handoff.write_atomic(request_path, data)
    deadline = time.monotonic() + max(0.0, float(timeout))
    try:
        while True:
            reply = handoff.take(response_path)
            if reply is not None:
                try:
                    user, password = handoff.open_response(reply, pending, device_key, now(), seen_ids)
                except handoff.Refused as exc:
                    if exc.code in (handoff.REPLAYED,):   # 别人的 / 旧的回应：接着等这一份
                        continue
                    return exc.code
                try:
                    store(pending.connection, user, password)
                finally:
                    user = password = ""
                log.info("云端凭据交接 · 事件=stored 原因=ok 连接=%s", pending.connection)
                return handoff.OK
            if time.monotonic() >= deadline or now() > pending.expires:
                log.info("云端凭据交接 · 事件=timeout 原因=%s 连接=%s", TIMEOUT, pending.connection)
                return TIMEOUT
            sleep(poll)
    finally:
        handoff.sweep(ctl_dir, (handoff.REQUEST_FILE, handoff.RESPONSE_FILE))


def handle_forget(ctl_dir: Path, *, device_key, epoch: str, seen_ids: MutableSet,
                  now: Callable[[], float] = time.time, forget: Callable[[str], object] = _forget) -> Optional[str]:
    """看一眼 ``cred-forget.json``：有就校验、删本机那条凭据、写 ``cred-forget-ack.json``。没有回 None，否则回原因码。"""
    ctl_dir = Path(ctl_dir)
    data = handoff.take(ctl_dir / handoff.FORGET_FILE)
    if data is None:
        return None
    code, _, ack = handoff.answer_forget(data, device_key, epoch, now(), seen_ids, forget)
    if ack is not None:
        handoff.write_atomic(ctl_dir / handoff.FORGET_ACK_FILE, ack)
    return code


__all__ = ["DEFAULT_TIMEOUT", "TIMEOUT", "fetch", "handle_forget"]
