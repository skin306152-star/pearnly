# -*- coding: utf-8 -*-
"""同一个外部账号的浏览器会话要**串行**，不能并发。

为什么必须有这个：DMS 的测试账号 `dmstest` **只允许一个活跃登录**
（老产品 `docs/integrations/mrerp-dms-recovery.md:5`）。两个请求同时登，
后一个会把前一个踢下线，前一个的操作就静默失败 —— 这种错最难查。

老产品用的是 Postgres 的 `pg_advisory_xact_lock`（`services/erp/session_lock.py`）。
我们这边是 MariaDB + Redis，用 **Frappe 自带的 Redis 锁**（`frappe.cache().lock`）
表达同一件事，而且更贴合这套框架：

· 锁在整个栈里共享（同一站点的 web/worker/scheduler 容器看到的是同一把锁）；
· 进程崩了锁会按 TTL 自动过期，不会永久卡死（等同于老产品说的"断连自动释放"）。

**拿不到锁怎么办**：默认**直接报错**，不放行。
老产品在云上是"拒绝执行"、只在本机降级放行 —— 我们现在整个栈都跑在容器里，
相当于它说的"云"，所以默认必须是拒绝。真要临时放开，显式设
`PEARNLY_ALLOW_LOCKLESS=1`；这种放行会写 warning 日志，不静默。
"""

from __future__ import annotations

import hashlib
import logging
import os
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger(__name__)

# 锁的自动过期时间（秒）。两个方向都要照顾：
#
# · **太长**：进程被 OOM 杀掉时锁不会释放（`finally` 没机会跑），后面 15 分钟谁都进不来；
# · **太短**：一次正常的会话（登录 + 建档 + 传附件）还没干完锁就过期了，等于没锁。
#
# 取 10 分钟：正常会话远用不到，被杀的代价也还能忍（而且报错里会告诉你还剩多久）。
# 真要调，设 `PEARNLY_SESSION_LOCK_TTL`（秒）。
DEFAULT_LOCK_TTL_SEC = 600


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
    try:
        try:
            # blocking_timeout：最多等多久；timeout：锁自己的自动过期兜底
            lock = frappe.cache().lock(name, timeout=ttl, blocking_timeout=timeout_sec)
            acquired = bool(lock.acquire(blocking=True, blocking_timeout=timeout_sec))
        except Exception as exc:  # noqa: BLE001 — 锁基础设施坏了要如实处理，不能装作没事
            if not _allow_lockless():
                raise SessionLockUnavailable(
                    "取锁失败（Redis 不通？）：%s" % type(exc).__name__
                ) from exc
            logger.warning("[session-lock] 取锁异常 · 已允许降级放行: %s", exc)
            yield False
            return

        if not acquired:
            if not _allow_lockless():
                left = lock_remaining_sec(lock)
                hint = (
                    "锁还剩 %ds（像是上一个进程被杀留下的死锁；确认没人跑的话可以删掉这个 key）"
                    % left
                    if left is not None
                    else "拿不到剩余时间"
                )
                raise SessionLockUnavailable(
                    "等 %.0fs 仍没拿到同一账号的锁（%s）—— 绝不并发登同一个账号。%s"
                    % (timeout_sec, _mask(account_key), hint)
                )
            logger.warning(
                "[session-lock] 等 %.0fs 未取到锁 · 已允许降级放行（可能与他会话并发）",
                timeout_sec,
            )
            yield False
            return

        logger.info("[session-lock] 已持锁 %s", _mask(account_key))
        yield True
    finally:
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
