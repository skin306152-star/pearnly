"""对账等识别时不占进程（2026-10-01 生产压测：Cowork 全平台只有 4 个 long 进程，一套对账从交识别到拿到结果一直占着一个进程
原地轮询；同时来的对账超过 4 套就排队，一家交多了挡住别家）。

做法：对账后台任务照常从头跑；走到要等识别的地方（``document_ocr.wait`` / ``invoice_ocr.wait`` 的结果、``admission`` 的识别
名额），还没好就抛 ``Pending``：这一轮到此为止——已交的识别照读，对账记下自己在等什么、按什么参数接着跑（``park``），后台任务
结束、交还进程。识别有结论时（共用层 hooks ``pearnly_ocr_finished_handlers`` → ``work_finished``）或到了要再看的时候（每分钟
``tick`` 兜底：时限到了、识别开始读了、识别名额腾出来了），按原参数把对账放回队列（``wake``），从头再跑一轮：读过的识别按参考号
找回已存的结果（不再提交、不再付费），走到下一处要等的地方，或者出结果。

时限照旧（排队多久算繁忙、开始读后多久算超时、到时还有进展就再等），只是不再靠「守在旁边每几秒数一次」：每处等待的起算时间、
延长到哪记在 ``clock``（队列 Redis，按对账号与等待处），开始读了多久、多久没有新进展按识别任务检查点行的时间算
（``work.call_progress``）。

不重复执行：放回队列前确认这套对账没有在排队 / 在跑的后台任务，记下等待与放回队列用同一把锁（按对账号）；被杀续跑
（``jobs.resume_if_abandoned``）也在这把锁里。正在跑的那一轮照旧可能被部署 / OOM 杀掉，后台任务照样是失败 + 弃置，续跑
判断不变；等着的对账没有进程可杀，重启后识别结束的提醒或每分钟的兜底照样把它放回队列。

记录放在队列 Redis（与续跑锁、``slots`` 的排队表同一处，和排着队的后台任务本身同样持久），按站点分开，两天过期。
不在对账后台任务里调用（没有 ``parking``）时退回原来的原地等待：同一套时限判断，每 3 秒看一次。
"""
import json
import time
from contextlib import contextmanager

import frappe

POLL_SECONDS = 3
# 记不下等待（队列 Redis 一时不可用）时原地等多久再跑一轮：不能像原来那样每 3 秒从头跑一遍。
PAUSE_SECONDS = 15
TTL = 2 * 24 * 3600
TERMINAL = ("succeeded", "failed", "cancelled", "uncertain")
ALIVE = ("queued", "started", "deferred", "scheduled")


class Pending(BaseException):
    """这一轮要等识别：到此为止、交还进程。

    继承 ``BaseException``：对账读取链上有按 ``Exception`` / ``ValidationError`` 兜底的地方（几份里一份读不出就跳过、其余照常），
    「还没读好」不能被当成读不出。

    ``works``：在等这几份识别（都有结论才接着跑；``deferred`` + 余额不足也算，交给下一轮报余额不足）；``start``：这几份一开始读
    就接着跑（排队时不知道哪一刻开始读，开始读了才定读取时限）；``room``：在等识别名额（本站点任何一份识别结束就可能腾出来）；
    ``due``：最迟这一刻（epoch 秒）再跑一轮（时限到了判超时 / 繁忙，或者到时还有进展就延长）。"""

    def __init__(self, works=(), due=None, start=False, room=False):
        super().__init__("reconciliation is waiting for recognition")
        self.works, self.due, self.start, self.room = list(works), due, bool(start), bool(room)


# ── 这一轮的上下文 ────────────────────────────────────────────────────────────


@contextmanager
def parking(recon_job):
    """对账后台任务的一轮：这期间等识别抛 ``Pending``，不原地等。"""
    previous = getattr(frappe.local, "pearnly_recon_pass", None)
    frappe.local.pearnly_recon_pass = recon_job
    try:
        yield
    finally:
        frappe.local.pearnly_recon_pass = previous


def current():
    return getattr(frappe.local, "pearnly_recon_pass", None)


def settle(key, evaluate):
    """一处等待：``evaluate(clock, now)`` 回结果，或抛 ``Pending``（还要等），或抛错（时限到了）。``clock`` 是这处等待自己的
    记录（起算时间、延长到哪），在对账后台任务里跨轮保留；不在后台任务里就原地每 3 秒再看一次（原来的做法）。"""
    recon_job = current()
    if not recon_job:
        clock = {}
        while True:
            try:
                return evaluate(clock, time.time())
            except Pending:
                time.sleep(POLL_SECONDS)
    clock = _load_clock(recon_job, key)
    try:
        value = evaluate(clock, time.time())
    except Pending:
        _save_clock(recon_job, key, clock)
        raise
    return value


# 计费服务一时查不到余额（扣费集中时排队、超时）：对账不直接判失败，隔一会儿再查，最多等这么久。等的时候同样不占进程。
BALANCE_RETRY_SECONDS = 30
BALANCE_WAIT_SECONDS = 10 * 60


def balance(check, key):
    """``check()`` 查余额（``ocr.execution.service.balance``）。查不到（``lookup_error``）就交还进程、过一会儿再跑一轮查，
    到 ``BALANCE_WAIT_SECONDS`` 还查不到才把错误抛出去；余额不足照旧马上抛（2026-10-01 三租户半量压测：计费服务排队时
    6 套对账因「暂时查不到账户余额」直接失败）。"""
    from pearnly_core.execution.errors import Deferred

    def evaluate(clock, now):
        try:
            return check()
        except Deferred as error:
            if getattr(error, "reason", "") != "lookup_error":
                raise
            if now - clock.setdefault("entered", now) > BALANCE_WAIT_SECONDS:
                raise
            frappe.db.commit()
            raise Pending(due=now + BALANCE_RETRY_SECONDS)

    return settle(f"balance:{key}", evaluate)


# ── 记录 ─────────────────────────────────────────────────────────────────────


def _conn():
    from frappe.utils.background_jobs import get_redis_conn

    return get_redis_conn()


def _keys(recon_job=""):
    site = frappe.local.site
    return {"parked": f"cowork-recon-parked:{site}", "room": f"cowork-recon-room:{site}",
            "record": f"cowork-recon-park:{site}:{recon_job}", "clock": f"cowork-recon-clock:{site}:{recon_job}",
            "lock": f"cowork-recon-wake:{site}:{recon_job}"}


def _await_key(work):
    return f"cowork-recon-await:{frappe.local.site}:{work}"


def _text(value):
    return value.decode() if isinstance(value, bytes) else value


def _load_clock(recon_job, key):
    try:
        raw = _conn().hget(_keys(recon_job)["clock"], key)
        return json.loads(_text(raw)) if raw else {}
    except Exception:
        return {}  # 读不到就从现在起算：最多多等一些，不会提前判超时


def _save_clock(recon_job, key, clock):
    try:
        conn, name = _conn(), _keys(recon_job)["clock"]
        conn.hset(name, key, json.dumps(clock))
        conn.expire(name, TTL)
    except Exception:
        pass


@contextmanager
def lock(recon_job):
    """这套对账的「记下等待 / 放回队列 / 续跑」用同一把锁，谁都不会在别人放回队列的同时再放一次。"""
    with _conn().lock(_keys(recon_job)["lock"], timeout=30, blocking_timeout=10):
        yield


def record(recon_job):
    """这套对账记下的等待（参数、发起人、在等什么）；没有回 None。"""
    try:
        raw = _conn().get(_keys(recon_job)["record"])
    except Exception:
        return None
    return json.loads(_text(raw)) if raw else None


def _forget(conn, recon_job, saved=None, keep_room=False):
    keys = _keys(recon_job)
    conn.delete(keys["record"])
    conn.zrem(keys["parked"], recon_job)
    if not keep_room:
        conn.zrem(keys["room"], recon_job)
    for work in (saved or {}).get("works") or []:
        if _text(conn.get(_await_key(work))) == recon_job:
            conn.delete(_await_key(work))


def park(recon_job, arguments, pending):
    """记下这套对账在等什么、按什么参数接着跑。回 ``"parked"``（后台任务可以结束了）、``"again"``（等的识别刚好已有结论，
    同一个进程接着跑下一轮）或 ``"failed"``（记不下：调用方原地等一会儿再跑，不能把对账丢了）。"""
    now = time.time()
    saved = {"arguments": arguments, "user": frappe.session.user, "works": pending.works, "start": pending.start,
             "room": pending.room, "due": pending.due if pending.due is not None else now + 600, "parked": now}
    try:
        conn, keys = _conn(), _keys(recon_job)
        with lock(recon_job):
            conn.set(keys["record"], json.dumps(saved), ex=TTL)
            conn.zadd(keys["parked"], {recon_job: saved["due"]})
            for work in pending.works:
                conn.set(_await_key(work), recon_job, ex=TTL)
            if pending.room:
                conn.zadd(keys["room"], {recon_job: now}, nx=True)  # 先来后到：再等名额不改位置
            else:
                conn.zrem(keys["room"], recon_job)
            conn.expire(keys["clock"], TTL)
    except Exception:
        frappe.log_error(title="cowork recon waiting", message=frappe.get_traceback())
        return "failed"
    # 记下之前那一刻等的识别已经有了结论：那时的提醒看到这套对账还在跑、没有放回队列，这里不接着跑就要等每分钟的兜底。
    try:
        if _ready(saved, now, _statuses(pending.works)):
            with lock(recon_job):
                _forget(conn, recon_job, saved)
            return "again"
    except Exception:
        pass  # 判断不了就照常等：识别结束的提醒或每分钟的兜底会放回队列
    return "parked"


def pause():
    """记不下等待：原地等一会儿再从头跑一轮（原来的做法，只是不再每 3 秒跑一遍）。"""
    frappe.db.commit()
    time.sleep(PAUSE_SECONDS)


def drop(recon_job):
    """记下的等待与时钟都不要了（被杀续跑从头来，时限也从头算，与原来一样）。只作清理，失败不影响结果（两天后自行过期）。"""
    try:
        conn = _conn()
        _forget(conn, recon_job, record(recon_job))
        conn.delete(_keys(recon_job)["clock"])
    except Exception:
        pass


def finish(recon_job):
    """这套对账结束了（出了结果或失败）。"""
    from pearnly_cowork.recon_center import slots

    drop(recon_job)
    slots.forget(recon_job)


# ── 放回队列 ─────────────────────────────────────────────────────────────────


def _statuses(works):
    if not works:
        return {}
    from pearnly_cowork.ocr.execution.access import WORK

    return {row.name: row for row in frappe.get_all(WORK, filters={"name": ["in", list(works)]},
                                                    fields=["name", "status", "last_code"])}


def _settled(row):
    """这份识别有结论了（或卡在余额不足：交给下一轮报余额不足）。查不到也算：交给下一轮判断。"""
    return (not row or row.status in TERMINAL
            or (row.status == "deferred" and row.last_code == "insufficient_balance"))


def _ready(saved, now, statuses, started=None):
    if float(saved.get("due") or 0) <= now:
        return True
    works = saved.get("works") or []
    if works and all(_settled(statuses.get(work)) for work in works):
        return True
    return bool(saved.get("start") and started and any(work in started for work in works))


def wake(recon_job):
    """把等着的对账按原参数放回队列。这套对账已经有排着队 / 在跑的后台任务就不放（那一轮会自己接着等或出结果）。"""
    from pearnly_cowork.recon_center import jobs

    conn = _conn()
    with lock(recon_job):
        saved = record(recon_job)
        if not saved:
            conn.zrem(_keys()["parked"], recon_job)
            conn.zrem(_keys()["room"], recon_job)
            return False
        if jobs.job_alive(recon_job):
            return False
        jobs.requeue(recon_job, saved["arguments"], user=saved.get("user"))
        # 等名额的留在等名额的队伍里：这一轮还拿不到名额就排回原位；拿到了、或改等别的，下次记下等待时拿掉。
        _forget(conn, recon_job, saved, keep_room=True)
    return True


def _wake_room(conn, limit=1):
    """按先来后到放等识别名额的对账：腾出一个名额就放一套（放进去的拿不到名额会再排回原位）。"""
    woken = 0
    for member in conn.zrange(_keys()["room"], 0, -1):
        if woken >= limit:
            break
        try:  # 已在排队 / 在跑的（上一次放回来还没跑完）跳过，放下一套
            woken += 1 if wake(_text(member)) else 0
        except Exception:
            frappe.log_error(title="cowork recon waiting", message=frappe.get_traceback())
    return woken


def work_finished(name, kind, status, code):
    """hooks ``pearnly_ocr_finished_handlers``：一份识别有了结论（共用层 ``work._finish``，在识别进程里）。等它的对账放回队列；
    结束了就腾出了识别名额，放等名额的下一套。识别让出续跑（``deferred``）不算，余额不足的 ``deferred`` 算。"""
    if status not in TERMINAL and not (status == "deferred" and code == "insufficient_balance"):
        return
    conn = _conn()
    recon_job = _text(conn.get(_await_key(name)))
    if recon_job:
        saved = record(recon_job)
        if saved and name in (saved.get("works") or []) and _ready(saved, time.time(), _statuses(saved["works"])):
            wake(recon_job)
    if status in TERMINAL:
        _wake_room(conn)


def tick():
    """定时（每分钟）：识别结束的提醒万一没送到、时限到了、在等的识别开始读了、识别名额腾出来了，都在这里把对账放回队列。"""
    from pearnly_core.execution import work as core_work

    conn, now = _conn(), time.time()
    keys = _keys()
    parked = {}
    for member in conn.zrange(keys["parked"], 0, -1):
        recon_job = _text(member)
        saved = record(recon_job)
        if saved:
            parked[recon_job] = saved
        else:
            conn.zrem(keys["parked"], recon_job)
            conn.zrem(keys["room"], recon_job)
    statuses = _statuses({work for saved in parked.values() for work in saved.get("works") or []})
    watching = [work for saved in parked.values() if saved.get("start") for work in saved.get("works") or []]
    started = {name for name, calls in core_work.call_progress(watching).items() if calls.get("since_first") is not None}
    started |= {name for name in watching if statuses.get(name) and statuses[name].status not in ("queued", "deferred")}
    for recon_job, saved in parked.items():
        if _ready(saved, now, statuses, started):
            try:
                wake(recon_job)
            except Exception:
                frappe.log_error(title="cowork recon waiting", message=frappe.get_traceback())
    _wake_room(conn)
