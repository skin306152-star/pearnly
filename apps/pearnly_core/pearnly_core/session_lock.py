# -*- coding: utf-8 -*-
"""同一个外部账号的浏览器会话要**串行**，不能并发。

为什么必须有这个：DMS 的测试账号 `dmstest` **只允许一个活跃登录**
（老产品 `docs/integrations/mrerp-dms-recovery.md:5`）。两个请求同时登，
后一个会把前一个踢下线，前一个的操作就静默失败 —— 这种错最难查。

老产品用的是 Postgres 的 `pg_advisory_xact_lock`（`services/erp/session_lock.py`）。
我们这边是 MariaDB + Redis，用 **Frappe 自带的 Redis 锁**（`frappe.cache().lock`）
表达同一件事，而且更贴合这套框架：

· 锁在整个栈里共享（同一站点的 web/worker/scheduler 容器看到的是同一把锁）；
· 进程崩了锁会按 TTL 自动过期，不会永久卡死（等同于老产品说的"断连自动释放"）；
  持锁期间后台线程按心跳续期，所以 TTL 可以取短：进程被杀，心跳随之停止，锁最多一个 TTL 就放开。

**拿不到锁怎么办**：默认**直接报错**，不放行。
老产品在云上是"拒绝执行"、只在本机降级放行 —— 我们现在整个栈都跑在容器里，
相当于它说的"云"，所以默认必须是拒绝。真要临时放开，显式设
`PEARNLY_ALLOW_LOCKLESS=1`；这种放行会写 warning 日志，不静默。
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import Optional
from frappe import _
from pearnly_core.execution import state as pearnly_state

logger = logging.getLogger(__name__)

# 锁的自动过期时间（秒）。持锁期间每 TTL/4 续一次期（心跳线程），所以正常会话再长也不会过期；
# 进程被 OOM / 任务超时杀掉时心跳一起停，锁最多 TTL 秒后放开。
#
# 2026-09-27 实测：原先固定 600 秒、不续期，一个被 OOM 杀掉的评测进程让同账号的读取排了近 10 分钟。
# 取 120 秒：心跳 30 秒一次，可以连丢三次；被杀后两分钟内恢复。
# 真要调，设 `PEARNLY_SESSION_LOCK_TTL`（秒）。
DEFAULT_LOCK_TTL_SEC = 120

# 心跳续期的总时长上限：进程没死但卡住时，不能靠心跳永远占着账号。
# 远长于任何一步正常会话（DMS 单步读取租约 120 秒，最长的队列任务超时 10 分钟）。
MAX_HOLD_SEC = 1800


def lock_ttl_sec() -> int:
    try:
        v = int(os.environ.get("PEARNLY_SESSION_LOCK_TTL") or DEFAULT_LOCK_TTL_SEC)
    except (TypeError, ValueError):
        return DEFAULT_LOCK_TTL_SEC
    return v if v > 0 else DEFAULT_LOCK_TTL_SEC


def lock_remaining_sec(lock) -> Optional[int]:
    """锁还剩多久过期。用于区分"别人正干着活"和"上一个进程被杀了留下的死锁" ——
    这两种情况处理方式完全不同，报错里必须能看出来。"""
    try:
        ttl = lock.redis.ttl(lock.name)
        return int(ttl) if ttl is not None and ttl >= 0 else None
    except Exception:
        return None


class SessionLockUnavailable(RuntimeError):
    """拿不到锁 —— 不允许在没有互斥保证的情况下开浏览器会话。"""


def _allow_lockless() -> bool:
    return str(os.environ.get("PEARNLY_ALLOW_LOCKLESS") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _lock_name(account_key: str) -> str:
    """账号串 → 定长键名（原文不进键名，省得把账号名写进缓存）。"""
    digest = hashlib.sha256(account_key.encode("utf-8")).hexdigest()[:32]
    return f"pearnly:sessionlock:{digest}"


def _mask(account_key: str) -> str:
    """日志里只留地址，账号打码。"""
    url, _, user = account_key.partition("|")
    if not user:
        return url
    shown = (user[:2] + "***") if len(user) > 3 else "***"
    return f"{url}|{shown}"


class _Heartbeat(threading.Thread):
    """持锁期间定时把锁的剩余时间重置为 TTL；锁丢了、到了总时长上限或被叫停就结束。"""

    def __init__(self, lock, ttl: int, max_hold: float = MAX_HOLD_SEC):
        super().__init__(name="pearnly-session-lock-heartbeat", daemon=True)
        self.lock, self.ttl, self.max_hold = lock, ttl, max_hold
        self.stopped = threading.Event()

    def run(self):
        deadline = time.monotonic() + self.max_hold
        while not self.stopped.wait(max(1.0, self.ttl / 4)):
            if time.monotonic() > deadline:
                logger.warning("[session-lock] 持锁超过 %ss，停止续期（锁将按 TTL 过期）", self.max_hold)
                return
            try:
                if not self.lock.extend(self.ttl, replace_ttl=True):
                    logger.warning("[session-lock] 续期失败：锁已不属于本进程")
                    return
            except Exception as exc:  # noqa: BLE001 — 续不上就交给 TTL，不能把业务打断
                logger.warning("[session-lock] 续期异常（锁将按 TTL 过期）: %s", exc)
                return


@contextmanager
def account_lock(account_key: str, timeout_sec: float = 180.0):
    """对同一个外部账号的浏览器会话加互斥锁。

    `account_key` 典型是 `f"{login_url}|{username}"`。

    yields：

    · `True` —— 拿到了锁，正常干活；
    · `False` —— **没拿到但被允许降级**（只在显式设了 `PEARNLY_ALLOW_LOCKLESS`
      时才会走到这里）。调用方应把这次操作当成"可能与别人并发"。
    """
    import frappe

    name = _lock_name(account_key)
    ttl = lock_ttl_sec()
    lock = None
    acquired = False
    heartbeat = None
    try:
        try:
            # blocking_timeout：最多等多久；timeout：锁自己的自动过期兜底。
            # thread_local=False：心跳线程要用同一个令牌续期。
            lock = pearnly_state.store().lock(name, timeout=ttl, blocking_timeout=timeout_sec,
                                       thread_local=False)
            acquired = bool(lock.acquire(blocking=True, blocking_timeout=timeout_sec))
        except Exception as exc:  # noqa: BLE001 — 锁基础设施坏了要如实处理，不能装作没事
            if not _allow_lockless():
                logger.warning("[session-lock] could not acquire %s (Redis unreachable?): %s", name, exc)
                raise SessionLockUnavailable(
                    _("Could not acquire the lock: {0}").format(type(exc).__name__)
                ) from exc
            logger.warning("[session-lock] 取锁异常 · 已允许降级放行: %s", exc)
            yield False
            return

        if not acquired:
            if not _allow_lockless():
                left = lock_remaining_sec(lock)
                if left is not None:
                    # 给运维：多半是被杀掉的进程留下的锁；确认没有任务在跑，可删掉这个键
                    logger.warning("[session-lock] %s still held for %ss; if nothing is running the key can be deleted",
                                   name, left)
                hint = (
                    _("Locked; released in {0}s").format(left)
                    if left is not None
                    else _("Cannot read the remaining time")
                )
                raise SessionLockUnavailable(
                    _("Waited {0}s and still could not take the lock for the same account ({1}) — never sign in to one account concurrently. {2}").format(
                        int(timeout_sec), _mask(account_key), hint
                    )
                )
            logger.warning(
                "[session-lock] 等 %.0fs 未取到锁 · 已允许降级放行（可能与他会话并发）",
                timeout_sec,
            )
            yield False
            return

        logger.info("[session-lock] 已持锁 %s", _mask(account_key))
        heartbeat = _Heartbeat(lock, ttl)
        heartbeat.start()
        yield True
    finally:
        if heartbeat is not None:
            heartbeat.stopped.set()
            heartbeat.join(timeout=5)
        if acquired and lock is not None:
            try:
                lock.release()
            except Exception as exc:  # noqa: BLE001
                # 没释放掉不是灾难（TTL 到点自动过期），但要说出来，不然有泄漏没人知道
                logger.warning("[session-lock] 释放锁异常（TTL 到点会自动过期）: %s", exc)


def _normalize_base(system_url: str) -> str:
    """服务地址归一：去尾斜杠、剥掉 `index.php` —— 两种写法要落成同一把锁。"""
    u = (system_url or "").strip().rstrip("/")
    if u.lower().endswith("/index.php"):
        u = u[: -len("/index.php")]
    return u


def dms_login_key(system_url: str, username: str) -> str:
    """DMS 登录锁键：**按账号**（`dmstest` 只允许一个活跃登录）。"""
    return f"{_normalize_base(system_url)}|{username}"


def dms_booking_key(system_url: str) -> str:
    """DMS 建单锁键：**按服务地址**，不带账号。

    建单为什么不按账号锁：同一账套下多个销售账号并发取单号（autonum）会撞号。
    理由与老产品 `services/erp/session_lock.py:dms_booking_scope_key` 一致。
    """
    return f"booking|{_normalize_base(system_url)}"
