"""服务器这边的任务派发：识别交给 Cloud Tasks 就返回，不占识别线程等 Cloud Run（站点配置 ``pearnly_cowork_ocr_tasks``，默认关）。

开着时，识别任务（``Pearnly OCR Work``）里原来「发给 Cloud Run、按行等回复」那一步（``forward._call``）改成：

1. 把整份请求（与按行发出的同一份，``contract.request`` / ``document_request``）存进检查点桶
   ``<站点>/<任务>/task/<派发号>/request.json``（桶本来就 7 天自动删），只把一个小信封（``contract.task``）交给 Cloud Tasks；
   多页对账文档且配了 ``workflow`` 时，信封交给 Cloud Workflows（先各块读字、各页第一遍抽取并行预热，最后一遍原封不动读整份）。
2. 在这份识别任务的检查点里记下「已派发 + 派发号」，抛 ``Deferred("remote_dispatched", 300)``：识别线程立刻空出来；
   300 秒只是兜底（回调丢了时回来看一眼：取一次库、不付费）。
3. Cloud Run 读完把结果写进 ``reply.json``，回调站点（``ocr.remote.callback``：核对谷歌签发的身份令牌与受众）；
   回调只记下结果在哪、唤醒这份识别任务（``pearnly_core.execution.work.wake``）。
4. 识别任务再进来时走到同一步：派发号对得上、结果在 → 读回结果行，补上回调时没送到的进度事件，交回 ``forward._outcome``
   —— 之后的复核、存结果、扣费、计费参考号与按行等回复时完全相同。

防重复付费不变：付费调用的检查点在 Cloud Run 的检查点桶里（键与本机同一算法）、同一份同时只有一个实例在跑（桶租约）；
Cloud Tasks 重试、回调重复、派发丢了重新派发，都只是从检查点取回已完成的调用。结果是「暂缓」（谷歌限流超过原地等的上限、
另一个实例在跑）时，下次进来重新派发（新派发号）。

关闭（或没开 Cloud Run 识别、不在识别任务里）时完全走原来的按行等回复。
"""
import base64
import json
import logging
import re
import secrets
import threading
import time
from urllib.parse import quote

import frappe
import requests

from pearnly_core import ocr
from pearnly_core.execution import pool, work as core_work
from pearnly_core.execution.errors import Deferred
from pearnly_cowork.ocr.remote import contract

CONFIG = "pearnly_cowork_ocr_tasks"
DISPATCH_KEY = "cowork.remote:dispatch"
REPLY_KEY = "cowork.remote:reply:"
# 回调没来时多久回来看一眼（兜底；正常是回调唤醒）。共用层暂缓最长 300 秒（``errors.Deferred``）。
WAIT_SECONDS = 300
# 派发后这么久还没有结果：当作丢了，重新派发（检查点在桶里，不重复付费）。= 队列最长重试时间（cowork-ocr 队列
# maxRetryDuration 7200 秒）+ 一次派发最长 1800 秒 + 余量。兜底每次回来都先去桶里看结果（回调丢了最多晚 WAIT_SECONDS），
# Workflows 重试用完会回调 ``failed``（立刻重派），所以真正等满这么久的只有队列整个停摆。
LOST_SECONDS = 7200 + 1800 + 600
# 连续这么多次建任务失败（配置写错、权限没开、接口停用）：这一次改走按行等回复，并记错误日志（不悄悄失败）。
FALLBACK_AFTER = 3
# 同时交给 Workflows 分页预热的对账文件最多几份（全站）；满了这份照样派发，只是不预热（一次请求里各页照旧并行）。
WORKFLOW_MAX = 6
# Cloud Tasks 单次派发最长 30 分钟；Cloud Run 读到 ``contract.TASK_YIELD_SECONDS`` 就让出，留出最后一次调用的余量。
DISPATCH_DEADLINE = "1800s"
# 多页对账文档至少几页才交给 Workflows 分页预热（页少时预热省不了多少，多一跳反而慢）。
WORKFLOW_MIN_PAGES = 3
SCOPE = "https://www.googleapis.com/auth/cloud-platform"
STORAGE = "https://storage.googleapis.com"
TASKS = "https://cloudtasks.googleapis.com/v2"
EXECUTIONS = "https://workflowexecutions.googleapis.com/v1"
NAME = re.compile(r"[a-z][a-z0-9-]{0,62}")
LOCATION = re.compile(r"[a-z]+-[a-z]+[0-9]")
PROJECT = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]")
BUCKET = re.compile(r"[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]")
_lock = threading.Lock()
_credentials = {}
_shared = {}
logger = logging.getLogger(__name__)


def _http():
    """全进程共用一个带连接池的会话：存请求、建任务、读结果都复用连接，不再每次新建 TLS 连接（2026-10-01 半量压测：
    识别进程 9–14% 的 CPU 花在 TLS 握手上；原来按线程各一个，识别线程是一份一份新起的，复用不上）。"""
    session = _shared.get("session")
    if session is None:
        with _lock:
            session = _shared.get("session")
            if session is None:
                from requests.adapters import HTTPAdapter

                session = requests.Session()
                session.mount("https://", HTTPAdapter(pool_connections=8, pool_maxsize=64))
                _shared["session"] = session
    return session


def settings():
    """开着且配置合规返回配置，否则 ``None``（照旧按行等回复）。

    ``{"enabled": 1, "project": "pearnly", "location": "asia-southeast1", "queue": "cowork-ocr",
       "bucket": "pearnly-ocr-checkpoints-sg", "workflow": "cowork-document"（可选）, "workflow_min_pages": 3（可选）,
       "callback_host": "<站点域名>"（可选，默认站点名）, "invoker": "<服务账号邮箱>"（可选，默认识别服务账号）}``"""
    value = (frappe.conf or {}).get(CONFIG)
    if not isinstance(value, dict) or not value.get("enabled"):
        return None
    for key, pattern in (("project", PROJECT), ("location", LOCATION), ("queue", NAME), ("bucket", BUCKET)):
        if not isinstance(value.get(key), str) or not pattern.fullmatch(value[key]):
            return None
    workflow = value.get("workflow")
    if workflow is not None and (not isinstance(workflow, str) or not NAME.fullmatch(workflow)):
        return None
    host = value.get("callback_host") or getattr(frappe.local, "site", None)
    if not isinstance(host, str) or not contract.CALLBACK_HOST.fullmatch(host):
        return None
    minimum = value.get("workflow_min_pages", WORKFLOW_MIN_PAGES)
    return {"project": value["project"], "location": value["location"], "queue": value["queue"],
            "bucket": value["bucket"], "workflow": workflow, "callback_host": host,
            "workflow_min_pages": minimum if type(minimum) is int and minimum >= 2 else WORKFLOW_MIN_PAGES,
            "workflow_max": value.get("workflow_max") if type(value.get("workflow_max")) is int else WORKFLOW_MAX,
            "adaptive": bool(value.get("adaptive", True)),
            "invoker": value.get("invoker") or None}


def active():
    """这一次要不要走任务派发：开着、Cloud Run 识别也开着、而且在识别任务里（检查点记在这份任务上）。"""
    from pearnly_cowork.ocr.remote import forward

    try:
        inside = bool(frappe.flags.get("pearnly_ocr_work"))
    except (AttributeError, RuntimeError):  # 没有站点上下文（离线测试、脚本）
        return False
    return inside and settings() is not None and forward.settings() is not None


def invoker(config):
    """Cloud Tasks / Workflows 调 Cloud Run、Cloud Run 回调站点用的身份：识别服务账号（与按行发出时同一个）。"""
    if config.get("invoker"):
        return config["invoker"]
    return _service_account().service_account_email


def callback_url(config):
    return contract.callback_url(config["callback_host"])


# ── 一次识别：派发或取回 ────────────────────────────────────────────────────────────────────────────


class Fallback(Exception):
    """派发一再失败：这一次改走按行等回复（``forward._call``）。"""


def call(remote, path, body, on_event, *, now=time.time, http=None, bucket=None):
    """取回这份识别任务当前那次派发的结果行（还没有就派发 / 接着等，抛 ``Deferred``）。``remote``：``forward.settings()``。"""
    config = settings()
    work = frappe.flags.get("pearnly_ocr_work")
    marker = core_work.checkpoint(DISPATCH_KEY)
    if marker and marker.get("state") == core_work.EXTERNAL and not marker["body"].get("spent"):
        dispatch, at = marker["body"]["id"], float(marker["body"]["at"])
        reply = core_work.checkpoint(REPLY_KEY + dispatch)
        reference = reply["body"] if reply else None
        if reference and reference.get("failed"):
            # Workflows 重试用完也没读完：立刻重新派发（已读完的在检查点里，不重复付费）
            _spend(marker)
            raise Deferred("remote_failed", 15)
        if reference is None:
            # 回调丢了（站点维护 / 部署、开关刚改还在缓存里、验签一时对不上）：结果的对象名是确定的，直接去桶里看一眼
            reference = {"object": contract.task_object(frappe.local.site, work, dispatch, "reply.json"), "generation": None}
        found = _read_json(config, reference, http=http, missing_ok=reply is None)
        if found is not None:
            final, events = contract.parse_reply(found)
            if "throttled" in final:
                _adapt(config, final.get("throttled"), pool_of(path), final.get("calls") or 0)
            applied = progress_applied(work, dispatch)
            for event in events:
                if event["seq"] >= applied:
                    on_event({key: value for key, value in event.items() if key != "seq"})
            if final["status"] == "deferred":
                # 这次没读完（谷歌限流 / 临时故障超过原地重试的上限）：下次进来重新派发；连着暂缓就拉长间隔
                # （15、30、60…最长 300 秒），谷歌整体故障时不每十几秒整份重派一次（2026-10-01 夜 Vision 整体回 14）。
                # 谷歌限流交回的，反馈进与按行等回复同一个冷却（10 秒内 3 次才整体放慢），冷却期间新的派发先暂缓。
                rounds = int(marker["body"].get("rounds") or 0) + 1
                _spend(marker, rounds=rounds)
                final = dict(final, delay=max(float(final.get("delay") or 5), min(WAIT_SECONDS, 15 * 2 ** (rounds - 1))))
                if bucket and str(final.get("reason") or "").startswith(("provider_", "vision_")):
                    _cool_down(bucket, final.get("delay"))
            else:
                _release(work)
            return final
        waited = now() - at
        if waited < LOST_SECONDS:
            raise Deferred("remote_waiting", max(1, int(min(WAIT_SECONDS, LOST_SECONDS - waited))))
    workflow = _reserve_waiting(config, work, path, body, bucket)
    dispatch = secrets.token_hex(16)
    pool.release_db()  # 存请求、建任务要几百毫秒到几秒（大原件）：不占数据库连接
    try:
        generation = _create_object(config, contract.task_object(frappe.local.site, work, dispatch, "request.json"),
                                    body, http=http)
        envelope = contract.task(site=frappe.local.site, work=work, dispatch=dispatch, path=path, generation=generation,
                                 callback=callback_url(config),
                                 progress=path == "/v1/cowork/document" or int(body.get("page_count") or 1) > 1)
        _create_task(config, remote, envelope, body, http=http, workflow=workflow)
    except Exception as exc:  # 连不上、没权限、令牌换不出来：还没派出去（不付费、不判失败）
        logger.warning("Cowork OCR task dispatch deferred: %s", type(exc).__name__)
        _release(work)
        if _failed_again():
            frappe.log_error(title="Cowork OCR task dispatch failing", message=f"{type(exc).__name__}: {exc}"[:1500])
            raise Fallback() from exc
        raise Deferred("tasks_unavailable", 15) from exc
    _dispatched_ok()
    # 先建任务、后记派发号：回调比这一步还快时，回调照样把结果记在这个派发号下、唤醒本任务（``work.wake`` 记得住正在跑的）。
    # 状态 ``external``：共用层据此知道这份可能已在云上付费，排队时限按付过费的算（``work.run``）。``rounds``：前面连着
    # 暂缓了几次（退避用，读完就不再带）。
    rounds = int(marker["body"].get("rounds") or 0) if marker and marker["body"].get("spent") else 0
    core_work.checkpoint(DISPATCH_KEY, core_work.EXTERNAL, {"id": dispatch, "at": now(), "rounds": rounds})
    raise Deferred("remote_dispatched", WAIT_SECONDS)


def _spend(marker, rounds=None):
    body = {**marker["body"], "spent": True}
    if rounds is not None:
        body["rounds"] = rounds
    core_work.checkpoint(DISPATCH_KEY, core_work.EXTERNAL, body)


FAILURES = "cowork:tasks:dispatch-failures"


def _failed_again():
    """记一次建任务失败；连续 ``FALLBACK_AFTER`` 次返回 True（这一次改走按行等回复）。记不住就不退回。"""
    try:
        count = int(frappe.cache.get_value(FAILURES) or 0) + 1
        frappe.cache.set_value(FAILURES, count, expires_in_sec=900)
        return count >= FALLBACK_AFTER
    except Exception:
        return False


def _dispatched_ok():
    try:
        frappe.cache.delete_value(FAILURES)
    except Exception:
        pass


# ── 名额：任务派发时服务器不再按次拿名额（不等），改为按「在途派发」计数 ─────────────────────────────────────────
# 与按行等回复同一份配置（``budget.config()`` 的 concurrency / tenant_concurrency），同一个非淘汰 Redis：全站、每租户在途
# 派发数到上限就像拿不到名额一样暂缓；另记同时交给 Workflows 的文件数，满了这份不预热（2026-10-01 复查：派发路径跳过了名额，
# Workflows 不受队列并发约束）。在途记录在结果回调到达时释放（云上已读完；不等服务器读回，服务器忙时读回会晚几分钟，
# 2026-10-01 半量压测），取回时再释放一次兜底，最长 ``LOST_SECONDS`` 自动过期。
OUTSTANDING_KEY = "cowork-tasks"
WORKFLOWS_KEY = "cowork-workflows"
RESERVE = """
local now, member, expiry = tonumber(ARGV[1]), ARGV[2], tonumber(ARGV[3])
local pause = tonumber(redis.call('GET', KEYS[3]) or '0')
if pause > now then return -1000 - math.ceil(pause - now) end
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
redis.call('ZREMRANGEBYSCORE', KEYS[5], '-inf', now)
if not redis.call('ZSCORE', KEYS[1], member) then
  if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[4]) then return -1 end
  if tonumber(ARGV[9]) > 0 and redis.call('ZCARD', KEYS[5]) >= math.floor(tonumber(redis.call('GET', KEYS[4]) or ARGV[9])) then
    return -1
  end
  local prefix, mine = ARGV[5], 0
  for _, other in ipairs(redis.call('ZRANGE', KEYS[1], 0, -1)) do
    if string.sub(other, 1, string.len(prefix)) == prefix then mine = mine + 1 end
  end
  if mine >= tonumber(ARGV[6]) then return -1 end
end
redis.call('ZADD', KEYS[1], expiry, member)
redis.call('ZADD', KEYS[5], expiry, member)
if tonumber(ARGV[7]) == 1 and (redis.call('ZSCORE', KEYS[2], member) or redis.call('ZCARD', KEYS[2]) < tonumber(ARGV[8])) then
  redis.call('ZADD', KEYS[2], expiry, member)
  return 2
end
redis.call('ZREM', KEYS[2], member)
return 1
"""


def _member(work):
    from pearnly_core.execution import budget

    return budget.tenant() + ":" + frappe.local.site + ":" + work


def _wants_workflow(config, path, body):
    return bool(config["workflow"] and path == "/v1/cowork/document"
                and int(body.get("page_count") or 1) >= config["workflow_min_pages"])


def _reserve(config, work, path, body, bucket=None):
    """占一个在途派发名额；拿不到暂缓（谷歌限流冷却中也暂缓）。返回这份要不要交给 Workflows（想要且全站在预热的不足
    ``WORKFLOW_MAX``）。"""
    from pearnly_core.execution import budget
    from redis.exceptions import RedisError

    cfg = budget.config()
    try:
        client = budget.redis()
        stamp = client.time()
        now = int(stamp[0]) + int(stamp[1]) / 1_000_000
        pause = budget.PREFIX + (bucket or "cowork-tasks-none") + ":pause"
        pool = pool_of(path)
        granted = client.eval(RESERVE, 5, budget.PREFIX + OUTSTANDING_KEY, budget.PREFIX + WORKFLOWS_KEY, pause,
            budget.PREFIX + ADAPTIVE_KEY + ":" + pool, budget.PREFIX + OUTSTANDING_KEY + ":" + pool,
            now, _member(work), now + LOST_SECONDS, cfg["concurrency"],
            budget.tenant() + ":", cfg["tenant_concurrency"], 1 if _wants_workflow(config, path, body) else 0,
            config.get("workflow_max") or WORKFLOW_MAX, _adaptive_start(config, cfg, pool))
    except RedisError as exc:
        raise Deferred("capacity_unavailable", 15) from exc
    if int(granted) <= -1000:
        budget.note("deferred:provider_cooldown", "cloudtasks")
        raise Deferred("provider_cooldown", min(60, -int(granted) - 1000))
    if int(granted) < 0:
        budget.note("deferred:capacity", "cloudtasks")
        raise Deferred("capacity", 3)
    return int(granted) == 2


# ── 按谷歌 429 自适应的在途派发上限（按额度池分开、按 30 秒窗口内的 429 比例；2026-10-01 / 02 半量压测）───────────
# 谷歌共享额度大约稳定承受每分钟 150–250 次 Gemini 调用、同时 30–40 个；开局一次涌入几百份会冲过去，429 后的重试再放大
# （第二轮一分钟 902 次调用、62% 是 429）。每份结果带回这次发给谷歌几次、其中几次 429 / 503（``runner`` 的 ``calls`` /
# ``throttled``），服务器按窗口内的比例调各池上限（见 ``ADAPT``）。发票（gemini-3.1-flash-lite + Vision）与对账文档
# （Document AI + gemini-3.8-flash）是谷歌不同的额度，各一个上限、各按自己的 429 收缩；全站名额与租户上限照旧跨池。
# 站点配置 ``pearnly_cowork_ocr_tasks.adaptive`` 写 0 关掉（只按名额配置）。
ADAPTIVE_KEY = "cowork-tasks-adaptive"
POOLS = {"/v1/cowork/invoice": "invoice", "/v1/cowork/document": "document"}


def pool_of(path):
    return POOLS.get(path, "invoice")
ADAPTIVE_START, ADAPTIVE_SHRINK = 32, 0.8
# 各池下限：发票用 flash-lite（额度独立，拐点是全站同时 30–40 个调用），不低于 24；对账文档每份同时调用多，下限 8。
ADAPTIVE_FLOOR = {"invoice": 24, "document": 8}
# 30 秒一个窗口（一份读取的典型耗时）：窗口里 429 / 503 占谷歌调用超过 15% 才收缩，一个窗口最多缩一次；没超就放开——
# 没碰过收缩线前慢启动（翻倍），之后每窗口加 2。上限存在 Redis 里（30 天），下次接着用，不每次从 32 冷启动。
# 2026-10-02 第三轮：原来「一份结果带 429 就乘 0.8、每份加 1/上限」，429 只占 2–10% 也几乎每 5 秒缩一次，发票池长期压在下限 8，
# 吞吐降了近一半。
ADAPTIVE_WINDOW, ADAPTIVE_RATIO = 30, 0.15
ADAPT = """
local start, throttled, calls, maximum, now, minimum = tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3]), tonumber(ARGV[4]), tonumber(ARGV[5]), tonumber(ARGV[6])
local window, ratio, shrink = tonumber(ARGV[7]), tonumber(ARGV[8]), tonumber(ARGV[9])
local cap = tonumber(redis.call('GET', KEYS[1]) or start)
local id = math.floor(now / window)
local current = tonumber(redis.call('HGET', KEYS[2], 'id') or '-1')
if current ~= id then
  local seen = tonumber(redis.call('HGET', KEYS[2], 'calls') or '0')
  local hit = tonumber(redis.call('HGET', KEYS[2], 'throttled') or '0')
  if current >= 0 and seen > 0 then
    if hit / seen > ratio then
      cap = cap * shrink
      redis.call('SET', KEYS[3], cap, 'EX', 2592000)
    else
      local threshold = tonumber(redis.call('GET', KEYS[3]) or maximum)
      if cap < threshold then cap = math.min(threshold, cap * 2) else cap = cap + 2 end
    end
  end
  redis.call('HSET', KEYS[2], 'id', id, 'calls', 0, 'throttled', 0)
  redis.call('EXPIRE', KEYS[2], 3600)
end
redis.call('HINCRBY', KEYS[2], 'calls', calls)
redis.call('HINCRBY', KEYS[2], 'throttled', throttled)
cap = math.max(minimum, math.min(maximum, cap))
redis.call('SET', KEYS[1], cap, 'EX', 2592000)
return tostring(cap)
"""


def _adaptive_start(config, cfg, pool="invoice"):
    """起步上限（0 = 关掉自适应）。"""
    if not config.get("adaptive", True):
        return 0
    return max(ADAPTIVE_FLOOR.get(pool, 8), min(int(cfg["concurrency"]), ADAPTIVE_START))


def _adapt(config, throttled, pool="invoice", calls=0):
    """一份结果回来：按它挨了几次 429 调全站上限。记不住就算了（下一份再调）。返回调整后的上限。"""
    from pearnly_core.execution import budget

    try:
        cfg = budget.config()
        start = _adaptive_start(config, cfg, pool)
        if not start:
            return None
        client = budget.redis()
        stamp = client.time()
        now = int(stamp[0]) + int(stamp[1]) / 1_000_000
        key = budget.PREFIX + ADAPTIVE_KEY + ":" + pool
        floor = min(int(cfg["concurrency"]), ADAPTIVE_FLOOR.get(pool, 8))
        cap = client.eval(ADAPT, 3, key, key + ":window", key + ":threshold", start, int(throttled or 0),
                          max(int(calls or 0), int(throttled or 0)), int(cfg["concurrency"]), now, floor,
                          ADAPTIVE_WINDOW, ADAPTIVE_RATIO, ADAPTIVE_SHRINK)
        return float(cap)
    except Exception:
        return None


# 名额满时在识别线程里原地等（每秒看一次、最多 ``CAPACITY_WAIT`` 秒，等的时候不占数据库连接），名额一空出来（回调到达即释放）
# 就派发；不再「暂缓 → 写库 → 3 秒后重新入队 → 再跑一遍」（2026-10-02 第三轮：每分钟 277–590 次这样空转，ocr-worker 多花
# 约 600 CPU 秒、吞吐反降）。每进程最多 ``CAPACITY_WAITERS`` 个线程这样等，其余照旧暂缓（留线程给取回结果的任务）。
CAPACITY_WAIT, CAPACITY_STEP, CAPACITY_WAITERS = 60, 1.0, 4
_waiters = threading.BoundedSemaphore(CAPACITY_WAITERS)


def _reserve_waiting(config, work, path, body, bucket=None, *, sleep=time.sleep, clock=time.monotonic):
    try:
        return _reserve(config, work, path, body, bucket)
    except Deferred as first:
        if first.reason not in ("capacity", "provider_cooldown") or not _waiters.acquire(blocking=False):
            raise Deferred(first.reason, max(float(first.delay or 3), 10 if first.reason == "capacity" else 0))
    try:
        started = clock()
        while True:
            pool.release_db()
            sleep(CAPACITY_STEP)
            try:
                return _reserve(config, work, path, body, bucket)
            except Deferred as again:
                if again.reason not in ("capacity", "provider_cooldown") or clock() - started >= CAPACITY_WAIT:
                    raise
    finally:
        _waiters.release()


def _cool_down(bucket, delay):
    from pearnly_core.execution import budget

    budget.feedback(bucket, 429, max(5, min(60, float(delay or 15))), True)


def release(work):
    """回调到了：这份在云上已读完，在途名额先还回去（``callback.receive``）。"""
    _release(work)


def _release(work):
    from pearnly_core.execution import budget

    try:
        client = budget.redis()
        member = _member(work)
        client.zrem(budget.PREFIX + OUTSTANDING_KEY, member)
        client.zrem(budget.PREFIX + WORKFLOWS_KEY, member)
        for pool in set(POOLS.values()):
            client.zrem(budget.PREFIX + OUTSTANDING_KEY + ":" + pool, member)
    except Exception:
        pass  # 记录到期自动释放


def progress_key(work, dispatch):
    return f"cowork:remote-progress:{work}:{dispatch}"


def progress_applied(work, dispatch):
    """回调已经送到并应用的进度事件到第几个（顺序号）；记不住就当 0（重复的进度只影响展示）。"""
    try:
        return int((frappe.cache.get_value(progress_key(work, dispatch)) or {}).get("seq") or 0)
    except Exception:
        return 0


# ── 谷歌接口（识别服务账号；签名 / 令牌在本机换） ───────────────────────────────────────────────────────


def _service_account():
    from google.oauth2 import service_account

    path = ocr.credentials_path()
    with _lock:
        credentials = _credentials.get(path)
        if credentials is None:
            credentials = service_account.Credentials.from_service_account_file(path, scopes=[SCOPE])
            _credentials[path] = credentials
        return credentials


def _token():
    from google.auth.transport.requests import Request

    credentials = _service_account()
    with _lock:
        if not credentials.valid:
            credentials.refresh(Request())
        return credentials.token


STORAGE_ATTEMPTS = 3


def _storage(method, url, *, http=None, sleep=time.sleep, **kwargs):
    """一次存储请求，与 Cloud Run 上 ``gcs.Bucket._call`` 同一套规则：连接被重置 / 超时 / 谷歌回 429、5xx 时原地再试
    （最多 3 次，间隔 0.5、1 秒）。返回 ``(回复, 前面是否有一次结果不明)``。"""
    unsure = False
    for attempt in range(STORAGE_ATTEMPTS):
        if attempt:
            sleep(0.5 * attempt)
        try:
            response = getattr(http or _http(), method)(url, **kwargs)
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError):
            unsure = True
            if attempt == STORAGE_ATTEMPTS - 1:
                raise
            continue
        if response.status_code in (429, 500, 502, 503, 504) and attempt < STORAGE_ATTEMPTS - 1:
            continue
        return response, unsure


def _create_object(config, name, value, *, http=None):
    """存请求（只新建）：重试时回 412 而前面断过连接，说明前一次其实写进去了——同一派发号的对象名唯一，取它的版本号。"""
    data = json.dumps(value, ensure_ascii=False).encode()
    response, unsure = _storage("post", f"{STORAGE}/upload/storage/v1/b/{config['bucket']}/o", http=http,
        params={"uploadType": "media", "name": name, "ifGenerationMatch": "0"}, data=data,
        headers={"Authorization": "Bearer " + _token(), "Content-Type": "application/json"}, timeout=(10, 120))
    if response.status_code == 412 and unsure:
        found, _unsure = _storage("get", f"{STORAGE}/storage/v1/b/{config['bucket']}/o/{quote(name, safe='')}",
            http=http, params={"fields": "generation"}, headers={"Authorization": "Bearer " + _token()}, timeout=(10, 30))
        found.raise_for_status()
        return int(found.json()["generation"])
    response.raise_for_status()
    return int(response.json()["generation"])


def _read_json(config, reference, *, http=None, missing_ok=False):
    """读回 ``reply.json``：回调给了版本号就按版本号读；兜底去桶里看时不带版本号（同一派发号只写一次）。对象名都在
    本站点本任务目录下（回调已核对、兜底是自己算的）。``missing_ok``：还没有就返回 ``None``（兜底时）。
    连不上、令牌一时换不出来：暂缓稍后再读（结果在桶里不会丢；2026-10-01 复查：原来会判程序错误、已付的结果作废）。"""
    params = {"alt": "media"}
    if reference.get("generation") is not None:
        params["generation"] = str(int(reference["generation"]))
    try:
        response, _unsure = _storage("get", f"{STORAGE}/storage/v1/b/{config['bucket']}/o/{quote(reference['object'], safe='')}",
            http=http, params=params, headers={"Authorization": "Bearer " + _token()}, timeout=(10, 120))
    except Exception as exc:  # requests 的传输错误、google-auth 换令牌失败
        raise Deferred("remote_reply_unavailable", 15) from exc
    if response.status_code == 404 and missing_ok:
        return None
    if response.status_code in (404, 429) or response.status_code >= 500:
        raise Deferred("remote_reply_unavailable", 15)
    response.raise_for_status()
    return response.json()


def _pages_to_read(body):
    """Document AI 一次读几页（``enterprise.pages.MAX_PAGES_PER_READ``）分几块：与 ``recon_reading.read_document`` 同一分法。"""
    from pearnly_cowork.ocr.enterprise.pages import MAX_PAGES_PER_READ

    pages = int(body["page_count"])
    return -(-pages // MAX_PAGES_PER_READ) if body["filename"].lower().endswith(".pdf") else 1


def _create_task(config, remote, envelope, body, *, http=None, workflow=False):
    """建一个 Cloud Task：直接调 Cloud Run（带识别服务账号的身份令牌），或多页对账文档交给 Workflows 新开一次执行。
    任务名由派发号算出：重复提交同一个只会有一个（409 当作已建好）。"""
    parent = f"projects/{config['project']}/locations/{config['location']}/queues/{config['queue']}"
    account = invoker(config)
    pages = int(body.get("page_count") or 1)
    if workflow:
        argument = {"task": envelope, "service": remote["url"], "reads": list(range(1, _pages_to_read(body) + 1)),
                    "pages": list(range(1, pages + 1))}
        request = {"url": (f"{EXECUTIONS}/projects/{config['project']}/locations/{config['location']}"
                           f"/workflows/{config['workflow']}/executions"),
                   "httpMethod": "POST", "headers": {"Content-Type": "application/json"},
                   "body": base64.b64encode(json.dumps({"argument": json.dumps(argument)}).encode()).decode(),
                   "oauthToken": {"serviceAccountEmail": account, "scope": SCOPE}}
    else:
        request = {"url": remote["url"] + "/v1/cowork/task", "httpMethod": "POST",
                   "headers": {"Content-Type": "application/json"},
                   "body": base64.b64encode(json.dumps(envelope).encode()).decode(),
                   "oidcToken": {"serviceAccountEmail": account, "audience": remote["url"]}}
    name = f"{parent}/tasks/{envelope['work'][:32]}-{envelope['dispatch']}"
    response = (http or _http()).post(f"{TASKS}/{parent}/tasks",
        json={"task": {"name": name, "dispatchDeadline": DISPATCH_DEADLINE, "httpRequest": request}},
        headers={"Authorization": "Bearer " + _token()}, timeout=(10, 30))
    if response.status_code == 409:
        return name
    response.raise_for_status()
    return name


def self_check(*, http=None):
    """开开关前（或开着时）自检，真走一遍要用的外部接口，不碰任何识别任务：检查点桶写一个探测对象再删掉；建一个只访问
    Cloud Run 健康检查（GET /health）的 Cloud Task（验证建任务权限、代签身份、队列）。返回 ``{"ok": bool, …}``。
    用法：``bench --site <站点> execute pearnly_cowork.ocr.remote.tasks.self_check``（开关要先写进站点配置；结果不 ok 就关掉）。"""
    from pearnly_cowork.ocr.remote import forward

    value = (frappe.conf or {}).get(CONFIG)
    config = settings() or (_lenient(value) if isinstance(value, dict) else None)
    remote = forward.settings()
    if config is None or remote is None:
        return {"ok": False, "reason": "not_configured", "tasks": bool(config), "remote": bool(remote)}
    result = {"ok": False, "bucket": False, "task": False, "callback": callback_url(config)}
    client = http or requests
    probe = f"{frappe.local.site}/selfcheck/{secrets.token_hex(8)}.json"
    try:
        _create_object(config, probe, {"probe": True}, http=client)
        client.delete(f"{STORAGE}/storage/v1/b/{config['bucket']}/o/{quote(probe, safe='')}",
                      headers={"Authorization": "Bearer " + _token()}, timeout=(10, 30))
        result["bucket"] = True
        parent = f"projects/{config['project']}/locations/{config['location']}/queues/{config['queue']}"
        response = client.post(f"{TASKS}/{parent}/tasks", json={"task": {"httpRequest": {
            "url": remote["url"] + "/health", "httpMethod": "GET",
            "oidcToken": {"serviceAccountEmail": invoker(config), "audience": remote["url"]}}}},
            headers={"Authorization": "Bearer " + _token()}, timeout=(10, 30))
        response.raise_for_status()
        result["task"] = True
    except Exception as exc:  # noqa: BLE001 - 自检把原因交回，不抛
        result["error"] = f"{type(exc).__name__}: {exc}"[:300]
    result["ok"] = result["bucket"] and result["task"]
    return result


def _lenient(value):
    """自检时开关可以还没打开（``enabled`` 为 0）：照同样的规则校验其余各项。"""
    with_switch = dict(value, enabled=1)
    previous = frappe.conf.get(CONFIG)
    frappe.conf[CONFIG] = with_switch
    try:
        return settings()
    finally:
        frappe.conf[CONFIG] = previous
