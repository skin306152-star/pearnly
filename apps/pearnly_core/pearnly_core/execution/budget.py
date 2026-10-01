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
def permit(bucket, timeout, rpm=None):
    """``rpm``：这个桶自己的每分钟上限（Document AI 各区域配额不同，用户 2026-09-30「每个地区一个桶」）；
    不给就用共用配置的 ``rpm``。并发上限照旧是全局的。"""
    cfg, who, token = config(), tenant(), secrets.token_hex(16)
    pace = rpm if type(rpm) is int and rpm >= 1 else cfg["rpm"]
    keys = _keys(bucket, who)
    try:
        client = redis()
        # Reject inconsistent limits between independently configured App benches.
        policy = json.dumps(cfg, sort_keys=True)
        client.set(PREFIX + "policy", policy, nx=True)
        if client.get(PREFIX + "policy") != policy:
            raise Deferred("capacity_policy_mismatch", 60)
        result = client.eval(ACQUIRE, len(keys), *keys, who, token,
                             cfg["concurrency"], cfg["tenant_concurrency"], pace, timeout + 30)
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


# 共享额度（Gemini 按量付费的 DSQ）零星的 429 不让整个模型停下来（2026-10-01 生产压测：128 张里谷歌只拒 16 次，原来每次都让
# 这个模型所有请求停 5–7 秒、再放慢）：谷歌说明 429 是那一刻的资源争用、不是额度用完，建议被拒的请求自己退避重试。
# 10 秒内被拒到 SHARED_BURST 次才算真的在压流量，照原规则整体停顿、放慢。Document AI 这类固定额度照旧每次都整体冷却。
SHARED_BURST, SHARED_WINDOW = 3, 10


def feedback(bucket, status, delay=0, shared=False):
    """Explicit rejection cools down all products; successes gradually restore pacing.

    ``shared``：共享额度（DSQ），零星拒绝只让被拒的请求自己退避（调用方抛出的 Deferred 带着它的等待时间）。"""
    script = """
local stamp = redis.call('TIME')
local now = tonumber(stamp[1]) + tonumber(stamp[2]) / 1000000
local factor = tonumber(redis.call('GET', KEYS[1]) or '1')
local rejected = tonumber(ARGV[1]) == 429 or tonumber(ARGV[1]) == 503
if rejected and tonumber(ARGV[3]) > 0 then
  local burst = redis.call('INCR', KEYS[4])
  if burst == 1 then redis.call('EXPIRE', KEYS[4], tonumber(ARGV[4])) end
  if burst < tonumber(ARGV[3]) then return factor end
end
if rejected then
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
        return redis().eval(script, 4, PREFIX + bucket + ":factor", PREFIX + bucket + ":pause",
                            PREFIX + bucket + ":success", PREFIX + bucket + ":burst", status,
                            max(1, min(300, delay)), SHARED_BURST if shared else 0, SHARED_WINDOW)
    except RedisError:
        frappe.logger("pearnly.capacity").warning("OCR provider feedback unavailable")


# ── 限流看得见（用户 2026-09-30：压测时 429 不进 Error Log，看不到被限流了多少）──────────────────────────────
# 每分钟一张计数表：{事件}|{标签} → 次数；标签由调用方给（如 ``documentai:asia-southeast1``）。只作观察，写不进就算了。
STATS_KEEP_SECONDS = 2 * 24 * 3600


def note(event, label, amount=1):
    try:
        client = redis()
        stamp = client.time()[0]
        key = PREFIX + "stats:" + str(int(stamp) // 60)
        pipe = client.pipeline()
        pipe.hincrbyfloat(key, f"{event}|{label or 'other'}", amount)
        pipe.expire(key, STATS_KEEP_SECONDS)
        pipe.execute()
    except Exception:
        pass


def stats(minutes=60):
    """最近 ``minutes`` 分钟的限流计数，按事件和标签合计（``bench --site <站点> execute pearnly_core.execution.budget.stats``）。"""
    client = redis()
    now = int(client.time()[0]) // 60
    total = {}
    for minute in range(now - max(1, int(minutes)) + 1, now + 1):
        for field, value in (client.hgetall(PREFIX + "stats:" + str(minute)) or {}).items():
            total[field] = round(total.get(field, 0) + float(value), 3)
    return dict(sorted(total.items()))
