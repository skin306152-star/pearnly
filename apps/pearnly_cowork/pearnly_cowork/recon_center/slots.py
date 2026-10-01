"""对账按租户（站点）限流（2026-10-01 生产压测 pB：Cowork 只有几个 long 进程、所有租户共用，一套对账从读文件、等识别到比对
全程占着一个进程；一家一次交几十套，别家的对账全排在后面）。

每个站点同一时刻最多 ``pearnly_recon_max_running``（站点配置，默认 6）套对账在跑。超出的对账后台任务开跑时不占进程：
记进本站点的排队表、原样存好参数就结束，页面照旧显示「排队中」。任何一套结束（成功、失败都算）就按先来后到放下一套进来；
定时任务再补一次位，并把占着名额却已经不在跑的（被杀、超时）清掉。识别本身另有按租户的公平名额（共用层 budget）。

名额与排队表放在队列 Redis 里（和续跑锁同一处），按站点分开；只作调度，丢了最多是排队的对账晚一点被放进来
（定时任务补位、页面照旧显示排队中）。
"""
import json
import time

import frappe

DEFAULT_MAX_RUNNING = 6
ARGS_TTL = 2 * 24 * 3600  # 排着队的参数留两天（对账后台任务的状态缓存是一天，过了也就没人等了）
ALIVE = ("queued", "started", "deferred", "scheduled")


def _conn():
    from frappe.utils.background_jobs import get_redis_conn

    return get_redis_conn()


def _keys():
    site = frappe.local.site
    return (f"cowork-recon-running:{site}", f"cowork-recon-waiting:{site}", f"cowork-recon-args:{site}:",
            f"cowork-recon-slots-lock:{site}")


def max_running():
    try:
        value = int(frappe.conf.get("pearnly_recon_max_running") or DEFAULT_MAX_RUNNING)
    except (TypeError, ValueError):
        return DEFAULT_MAX_RUNNING
    return value if value > 0 else DEFAULT_MAX_RUNNING


def _id(member):
    return member.decode() if isinstance(member, bytes) else str(member)


def acquire(recon_job, arguments):
    """开跑前拿名额：已经有（被放进来时预留了）或还有空位就占上、回 True；满了就排进队、存好参数，回 False（这次不跑）。"""
    try:
        running, waiting, args, lock = _keys()
        conn = _conn()
        guard = conn.lock(lock, timeout=10, blocking_timeout=10)
        if not guard.acquire():
            return True
    except Exception:
        return True  # 名额表一时用不了：照常跑（限流只是调度，不能因此挡住对账）
    try:
        if conn.zscore(running, recon_job) is not None:
            return True
        if conn.zcard(running) < max_running():
            conn.zadd(running, {recon_job: time.time()})
            conn.zrem(waiting, recon_job)
            return True
        conn.set(args + recon_job, json.dumps(arguments), ex=ARGS_TTL)
        conn.zadd(waiting, {recon_job: time.time()}, nx=True)  # 先来后到：重复排队不改位置
        return False
    except Exception:
        return True
    finally:
        try:
            guard.release()
        except Exception:
            pass


def release(recon_job):
    """这一套结束：让出名额，放排队的下一套进来。只作调度：名额表一时用不了就算了（定时任务会清掉不在跑却占着名额的、
    再补位），不影响已经写好的结果，也不另记错误。"""
    try:
        running, _waiting, _args, _lock = _keys()
        _conn().zrem(running, recon_job)
        promote()
    except Exception:
        pass


def promote():
    """有空位就按先来后到放排队的进来：先给它预留名额，再排后台任务（它开跑时看到自己已有名额就直接跑）。"""
    from pearnly_cowork.recon_center import jobs

    running, waiting, args, lock = _keys()
    conn = _conn()
    started = []
    with conn.lock(lock, timeout=10, blocking_timeout=10):
        while conn.zcard(running) < max_running():
            head = conn.zrange(waiting, 0, 0)
            if not head:
                break
            recon_job = _id(head[0])
            conn.zrem(waiting, recon_job)
            saved = conn.get(args + recon_job)
            if not saved:
                continue  # 参数过期了：没人在等了，跳过
            conn.zadd(running, {recon_job: time.time()})
            started.append((recon_job, json.loads(saved)))
    for recon_job, arguments in started:
        try:
            jobs.requeue(recon_job, arguments)
            conn.delete(args + recon_job)
        except Exception:
            conn.zrem(running, recon_job)
            conn.zadd(waiting, {recon_job: time.time()}, nx=True)
            frappe.log_error(title="cowork recon slots", message=frappe.get_traceback())


def tick():
    """定时（每 5 分钟）：占着名额却已经不在跑的（被杀、超时、结束时没来得及让出）清掉，再补位。"""
    from frappe.utils.background_jobs import get_job

    running, _waiting, _args, lock = _keys()
    conn = _conn()
    with conn.lock(lock, timeout=10, blocking_timeout=10):
        for member in conn.zrange(running, 0, -1):
            recon_job = _id(member)
            job = get_job(f"cowork-recon-{recon_job}")
            if not job or job.get_status(refresh=False) not in ALIVE:
                conn.zrem(running, recon_job)
    promote()
