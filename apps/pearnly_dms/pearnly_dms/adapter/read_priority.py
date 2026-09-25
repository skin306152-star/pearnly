"""DMS-only foreground intent; shared Core lock semantics remain unchanged."""
from contextlib import contextmanager
import hashlib
import logging
import time
import uuid

import frappe


class ReadDeferred(Exception):
    pass


def _key(account):
    return "dms-read-priority:" + hashlib.sha256((frappe.local.site + "\0" + account).encode()).hexdigest()


def yield_to_foreground(accounts):
    cache = frappe.cache()
    for account in accounts:
        key = _key(account)
        cache.zremrangebyscore(key, "-inf", time.time())
        if cache.zcard(key):
            raise ReadDeferred("An interactive DMS operation has priority")


def wait_for_foreground(accounts, timeout_seconds, poll_seconds=2.0):
    """后台读取**等交互操作做完**再进去，最多等 `timeout_seconds` 秒。

    为什么必须有这个等待（2026-09-25 实测）：以前这里只做一次性检查，撞上就立刻
    退回 `busy`。而 `busy` 之后没有任何东西会重排这次读取 —— 缓存从此不再更新，
    夜间任务还把那条请求当成"正在跑"而跳过这一组，一处卡住等于永久失效。
    操作员自己那一笔通常几秒内就结束，所以有界等待就能让它自然接上。

    仍然**让位**：等待期间交互操作可以随时开始（后台只是重试同一个检查）。
    超时后照旧抛 `ReadDeferred`，由上层如实报"账号忙"。
    """
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while True:
        try:
            yield_to_foreground(accounts)
            return
        except ReadDeferred:
            if time.monotonic() >= deadline:
                raise
            time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))


@contextmanager
def foreground(accounts):
    cache, token = frappe.cache(), uuid.uuid4().hex
    keys = [_key(account) for account in accounts]
    try:
        for key in keys:
            cache.zadd(key, {token: time.time() + 900})
            cache.expire(key, 900)
        yield
    finally:
        for key in keys:
            try:
                cache.zrem(key, token)
            except Exception as exc:
                # A cleanup failure must never reverse a confirmed DMS write.
                # The bounded marker expires on its own if Redis is unavailable.
                logging.getLogger(__name__).warning("DMS priority cleanup failed (%s)", type(exc).__name__)
