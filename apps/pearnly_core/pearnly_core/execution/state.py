"""不能丢的短期状态放在不淘汰的 Redis（2026-10-02 自检：redis-cache 是 128MB、allkeys-lru，压测时已到上限、按最久未用淘汰）。

锁、一次性票据、登录 state / nonce、防爆破计数、进行中任务的状态、送单 / 对账租约这类「丢了就会重复执行、登录失败、绕过限流」的键，
原先都在 ``frappe.cache``（redis-cache）里，内存一满就可能被挤掉。``store()`` 返回与 ``frappe.cache`` 同一个类（Frappe 的
``RedisWrapper``：键照样按站点 ``db_name`` 加前缀，``get_value`` / ``set_value`` / ``lock`` / ``eval`` 用法一样），只是连到
不淘汰的 Redis：站点配置 ``pearnly_state_redis``，没配就用本站点的 ``redis_queue``（生产是 noeviction）。可以丢、能重算的
缓存仍用 ``frappe.cache``。

这里的键都带过期时间、都很小；redis-queue 满了是写失败而不是淘汰，所以不要往这里放大块或不过期的数据。"""
import threading

import frappe

_clients = {}
_lock = threading.Lock()

# 只删自己持有的锁（值等于自己的 owner 才删），一条命令完成，不会误删别人刚拿到的锁。
_RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"


def url():
    return frappe.conf.get("pearnly_state_redis") or frappe.conf.get("redis_queue")


def store():
    """不淘汰的状态存储（用法同 ``frappe.cache``，Frappe 的 ``RedisWrapper``；用到时才加载，离线契约检查导入本模块不碰 Redis）。"""
    from frappe.utils.redis_wrapper import RedisWrapper

    address = url()
    if not address:
        raise RuntimeError("no state Redis configured: set pearnly_state_redis or redis_queue in the site config")
    client = _clients.get(address)
    if client is None:
        with _lock:
            client = _clients.get(address)
            if client is None:
                client = _clients[address] = RedisWrapper.from_url(address)
    return client


def release(key, owner) -> bool:
    """原子地释放 ``SET NX`` 拿到的锁：值仍是 ``owner`` 才删。``key`` 是已经 ``make_key`` 过的完整键。"""
    return bool(store().eval(_RELEASE, 1, key, owner))
