"""Atomic cross-bench OCR budgets in a dedicated non-evicting Redis database.

Keys deliberately have no Site prefix. Tenant identifiers come only from server
configuration. Redis TIME avoids disagreements between machines. Waiting tenants
rotate after each grant; stale waiters and crashed request leases expire.
"""
from contextlib import contextmanager
from functools import lru_cache
import hashlib
import json
import secrets

import frappe
from redis import Redis
from redis.exceptions import RedisError

from .errors import Deferred

DEFAULTS = {"concurrency": 4, "tenant_concurrency": 2, "rpm": 30,
            "pending": 300, "tenant_pending": 50, "max_age": 1800}
PREFIX = "pearnly:ocr:v1:"


def config():
    values = {**DEFAULTS, **(frappe.conf.get("pearnly_ocr_budget") or {})}
    if any(type(values[key]) is not int or values[key] < 1 for key in DEFAULTS):
        raise ValueError("Invalid OCR capacity configuration")
    if values["tenant_concurrency"] > values["concurrency"]:
        raise ValueError("Tenant OCR concurrency exceeds global capacity")
    return values


@lru_cache(maxsize=8)
def _client(url):
    return Redis.from_url(url, socket_connect_timeout=2, socket_timeout=3, decode_responses=True)


def redis():
    url = frappe.conf.get("pearnly_ocr_budget_redis")
    if not url:
        raise Deferred("capacity_not_configured", 60)
    return _client(str(url))


def tenant():
    # The same merchant using multiple products shares the tenant budget.
    identity = frappe.conf.get("pearnly_tenant") or frappe.local.site
    return hashlib.sha256(str(identity).encode()).hexdigest()[:24]


def scope(backend, project, location, model):
    return hashlib.sha256(json.dumps([backend, project, location, model]).encode()).hexdigest()[:32]


ACQUIRE = """
local stamp = redis.call('TIME')
local now = tonumber(stamp[1]) + tonumber(stamp[2]) / 1000000
local t, token = ARGV[1], ARGV[2]
local maximum, per_tenant, rpm, lease = tonumber(ARGV[3]), tonumber(ARGV[4]), tonumber(ARGV[5]), tonumber(ARGV[6])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
-- Each waiter must revisit within 30 seconds. No abandoned tenant can block work.
local stale = redis.call('ZRANGEBYSCORE', KEYS[4], '-inf', now - 30)
for _, id in ipairs(stale) do redis.call('ZREM', KEYS[3], id) end
redis.call('ZREMRANGEBYSCORE', KEYS[4], '-inf', now - 30)
redis.call('ZADD', KEYS[4], now, t)
if not redis.call('ZSCORE', KEYS[3], t) then
  redis.call('ZADD', KEYS[3], redis.call('INCR', KEYS[5]), t)
end
local pause = tonumber(redis.call('GET', KEYS[6]) or '0')
local next_at = tonumber(redis.call('GET', KEYS[7]) or '0')
if pause > now then return {0, math.ceil((pause - now) * 1000)} end
if next_at > now then return {0, math.ceil((next_at - now) * 1000)} end
if redis.call('ZCARD', KEYS[1]) >= maximum or redis.call('ZCARD', KEYS[2]) >= per_tenant then
  return {0, 2000}
end
-- Fairness across products/tenants, skipping tenants already at their share.
local first = nil
for _, id in ipairs(redis.call('ZRANGE', KEYS[3], 0, -1)) do
  local active = 0
  for _, member in ipairs(redis.call('ZRANGE', KEYS[1], 0, -1)) do
    if string.sub(member, 1, string.len(id) + 1) == id .. ':' then active = active + 1 end
  end
  if active < per_tenant then first = id; break end
end
if first ~= t then return {0, 1000} end
redis.call('ZREM', KEYS[3], t)
redis.call('ZREM', KEYS[4], t)
redis.call('ZADD', KEYS[1], now + lease, t .. ':' .. token)
redis.call('ZADD', KEYS[2], now + lease, token)
local factor = tonumber(redis.call('GET', KEYS[8]) or '1')
redis.call('SET', KEYS[7], now + (60 / rpm) * factor, 'EX', 600)
redis.call('EXPIRE', KEYS[1], math.ceil(lease + 60))
redis.call('EXPIRE', KEYS[2], math.ceil(lease + 60))
return {1, 0}
"""


SHORTEN = """
local stamp = redis.call('TIME')
local until_at = tonumber(stamp[1]) + tonumber(stamp[2]) / 1000000 + tonumber(ARGV[3])
for index, member in ipairs({ARGV[1], ARGV[2]}) do
  local score = redis.call('ZSCORE', KEYS[index], member)
  if score and tonumber(score) > until_at then redis.call('ZADD', KEYS[index], until_at, member) end
end
return 1
"""


def _keys(bucket, who):
    return [PREFIX + "active", PREFIX + "active:" + who, PREFIX + bucket + ":waiting",
            PREFIX + bucket + ":seen", PREFIX + bucket + ":sequence", PREFIX + bucket + ":pause",
            PREFIX + bucket + ":next", PREFIX + bucket + ":factor"]


@contextmanager
def permit(bucket, timeout):
    cfg, who, token = config(), tenant(), secrets.token_hex(16)
    keys = _keys(bucket, who)
    try:
        client = redis()
        # Reject inconsistent limits between independently configured App benches.
        policy = json.dumps(cfg, sort_keys=True)
        client.set(PREFIX + "policy", policy, nx=True)
        if client.get(PREFIX + "policy") != policy:
            raise Deferred("capacity_policy_mismatch", 60)
        result = client.eval(ACQUIRE, len(keys), *keys, who, token,
                             cfg["concurrency"], cfg["tenant_concurrency"], cfg["rpm"], timeout + 30)
    except RedisError as exc:
        raise Deferred("capacity_unavailable", 15) from exc
    if not result[0]:
        raise Deferred("capacity", min(10, result[1] / 1000))
    uncertain, hold = False, None
    try:
        yield
    except Deferred:
        # An explicit provider rejection is safe to retry after its cooldown.
        raise
    except BaseException as error:
        # Includes native RQ's job timeout and process termination. We cannot
        # assume that an interrupted caller also stopped the upstream request.
        uncertain = True
        # The transport itself gave up (read timeout, reset, our own stop): hold only briefly.
        hold = getattr(error, "lease_seconds", None)
        raise
    finally:
        try:
            if uncertain and hold:
                client.eval(SHORTEN, 2, keys[0], keys[1], who + ":" + token, token, hold)
                pipe = None
            elif uncertain and hold is None:
                # An upstream request may still run; retain its conservative lease.
                pipe = None
            else:
                pipe = client.pipeline()
            if pipe is not None:
                pipe.zrem(keys[0], who + ":" + token)
                pipe.zrem(keys[1], token)
                pipe.execute()
        except RedisError:
            # The conservative lease remains until expiry; never grant extra capacity.
            frappe.logger("pearnly.capacity").warning("OCR permit release unavailable")


def feedback(bucket, status, delay=0):
    """Explicit rejection cools down all products; successes gradually restore pacing."""
    script = """
local stamp = redis.call('TIME')
local now = tonumber(stamp[1]) + tonumber(stamp[2]) / 1000000
local factor = tonumber(redis.call('GET', KEYS[1]) or '1')
if tonumber(ARGV[1]) == 429 or tonumber(ARGV[1]) == 503 then
  factor = math.min(8, factor * 2)
  local until_at = math.max(now + tonumber(ARGV[2]), tonumber(redis.call('GET', KEYS[2]) or '0'))
  redis.call('SET', KEYS[2], until_at, 'EX', 600)
  redis.call('SET', KEYS[3], 0, 'EX', 600)
elseif tonumber(ARGV[1]) == 200 and redis.call('INCR', KEYS[3]) >= 20 then
  factor = math.max(1, factor / 2)
  redis.call('SET', KEYS[3], 0, 'EX', 600)
end
redis.call('SET', KEYS[1], factor, 'EX', 600)
return factor
"""
    try:
        return redis().eval(script, 3, PREFIX + bucket + ":factor", PREFIX + bucket + ":pause",
                            PREFIX + bucket + ":success", status, max(1, min(300, delay)))
    except RedisError:
        frappe.logger("pearnly.capacity").warning("OCR provider feedback unavailable")
