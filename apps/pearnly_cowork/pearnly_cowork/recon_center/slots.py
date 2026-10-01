"""对账按租户（站点）限流（2026-10-01 生产压测 pB：Cowork 只有几个 long 进程、所有租户共用，一套对账从读文件、等识别到比对
全程占着一个进程；一家一次交几十套，别家的对账全排在后面）。

每个站点同一时刻最多 ``pearnly_recon_max_running``（站点配置，默认 6）套对账在跑。超出的对账后台任务开跑时不占进程：
记进本站点的排队表、原样存好参数就结束，页面照旧显示「排队中」。任何一套结束（成功、失败都算）就按先来后到放下一套进来；
定时任务再补一次位，并把占着名额却已经不在跑的（被杀、超时）清掉。识别本身另有按租户的公平名额（共用层 budget）。

名额只管「在跑」：对账等识别时交还进程（``waiting``），也让出名额；识别有结论、放回队列接着跑时再拿名额。先来后到按这套对账
第一次拿名额的时间排，接着跑的不排到后来交的后面。

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


def _since_key(recon_job):
    return f"cowork-recon-since:{frappe.local.site}:{recon_job}"


def _since(conn, recon_job):
    """这套对账第一次来拿名额的时间（排队先后按它）。"""
    conn.set(_since_key(recon_job), time.time(), nx=True, ex=ARGS_TTL)
    value = conn.get(_since_key(recon_job))
    try:
        return float(value.decode() if isinstance(value, bytes) else value)
    except (TypeError, ValueError):
        return time.time()


def _saved(raw):
    """排队表里存的：``{"arguments", "user"}``；部署前存的只有参数（发起人不知道，按原来的做法排）。"""
    value = json.loads(raw)
    return value if isinstance(value, dict) and "arguments" in value else {"arguments": value, "user": None}


def held(recon_job):
    """排在本站点排队表里的这套对账（参数、发起人）；没有回 None。"""
    try:
        raw = _conn().get(_keys()[2] + recon_job)
    except Exception:
        return None
    return _saved(raw) if raw else None


def forget(recon_job):
    """这套对账结束了：先后顺序的记录不要了（只作清理，两天后自行过期）。"""
    try:
        _conn().delete(_since_key(recon_job))
    except Exception:
        pass


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
            _since(conn, recon_job)
            conn.zadd(running, {recon_job: time.time()})
            conn.zrem(waiting, recon_job)
            return True
        conn.set(args + recon_job, json.dumps({"arguments": arguments, "user": frappe.session.user}), ex=ARGS_TTL)
        conn.zadd(waiting, {recon_job: _since(conn, recon_job)}, nx=True)  # 先来后到：重复排队不改位置
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
            started.append((recon_job, _saved(saved)))
    for recon_job, saved in started:
        try:
            # 刚排进队的那一轮可能还没结束（排进队与这里放行几乎同时）：同一个任务号不能在它还在跑时重排，下一次补位再放。
            alive = jobs.job_alive(recon_job)
            if not alive:
                jobs.requeue(recon_job, saved["arguments"], user=saved["user"])
                conn.delete(args + recon_job)
                continue
        except Exception:
            frappe.log_error(title="cowork recon slots", message=frappe.get_traceback())
        conn.zrem(running, recon_job)
        conn.zadd(waiting, {recon_job: _since(conn, recon_job)}, nx=True)


def tick():
    """定时（每分钟）：占着名额却已经不在跑的（被杀、超时、结束时没来得及让出）清掉，再补位。"""
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
