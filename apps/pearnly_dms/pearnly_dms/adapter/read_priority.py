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
