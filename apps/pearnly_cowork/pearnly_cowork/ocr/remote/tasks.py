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
# 派发后这么久还没有结果：当作丢了（Cloud Tasks 重试用完、Workflows 执行失败），重新派发。检查点在桶里，不重复付费。
LOST_SECONDS = 3 * 3600
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
logger = logging.getLogger(__name__)


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


def call(remote, path, body, on_event, *, now=time.time, http=None):
    """取回这份识别任务当前那次派发的结果行（还没有就派发 / 接着等，抛 ``Deferred``）。``remote``：``forward.settings()``。"""
    config = settings()
    work = frappe.flags.get("pearnly_ocr_work")
    marker = core_work.checkpoint(DISPATCH_KEY)
    if marker and marker.get("state") == core_work.EXTERNAL and not marker["body"].get("spent"):
        dispatch, at = marker["body"]["id"], float(marker["body"]["at"])
        reply = core_work.checkpoint(REPLY_KEY + dispatch)
        if reply:
            final, events = contract.parse_reply(_read_json(config, reply["body"], http=http))
            applied = progress_applied(work, dispatch)
            for event in events:
                if event["seq"] >= applied:
                    on_event({key: value for key, value in event.items() if key != "seq"})
            if final["status"] == "deferred":
                # 这次没读完（限流超过原地等的上限、另一个实例在跑）：下次进来重新派发
                core_work.checkpoint(DISPATCH_KEY, core_work.EXTERNAL, {**marker["body"], "spent": True})
            return final
        waited = now() - at
        if waited < LOST_SECONDS:
            raise Deferred("remote_waiting", max(1, int(min(WAIT_SECONDS, LOST_SECONDS - waited))))
    dispatch = secrets.token_hex(16)
    pool.release_db()  # 存请求、建任务要几百毫秒到几秒（大原件）：不占数据库连接
    try:
        generation = _create_object(config, contract.task_object(frappe.local.site, work, dispatch, "request.json"),
                                    body, http=http)
        envelope = contract.task(site=frappe.local.site, work=work, dispatch=dispatch, path=path, generation=generation,
                                 callback=callback_url(config),
                                 progress=path == "/v1/cowork/document" or int(body.get("page_count") or 1) > 1)
        _create_task(config, remote, envelope, body, http=http)
    except Exception as exc:  # 连不上、没权限、令牌换不出来：还没派出去，稍后再试（不付费、不判失败）
        logger.warning("Cowork OCR task dispatch deferred: %s", type(exc).__name__)
        raise Deferred("tasks_unavailable", 15) from exc
    # 先建任务、后记派发号：回调比这一步还快时，回调照样把结果记在这个派发号下、唤醒本任务（``work.wake`` 记得住正在跑的）。
    # 状态 ``external``：共用层据此知道这份可能已在云上付费，排队时限按付过费的算（``work.run``）。
    core_work.checkpoint(DISPATCH_KEY, core_work.EXTERNAL, {"id": dispatch, "at": now()})
    raise Deferred("remote_dispatched", WAIT_SECONDS)


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


def _create_object(config, name, value, *, http=None):
    response = (http or requests).post(f"{STORAGE}/upload/storage/v1/b/{config['bucket']}/o",
        params={"uploadType": "media", "name": name, "ifGenerationMatch": "0"},
        data=json.dumps(value, ensure_ascii=False).encode(),
        headers={"Authorization": "Bearer " + _token(), "Content-Type": "application/json"}, timeout=(10, 120))
    response.raise_for_status()
    return int(response.json()["generation"])


def _read_json(config, reference, *, http=None):
    """回调告诉我们的 ``reply.json``（对象 + 版本号）：按版本号读回。对象名必须在本站点本任务目录下（回调已核对）。"""
    response = (http or requests).get(
        f"{STORAGE}/storage/v1/b/{config['bucket']}/o/{quote(reference['object'], safe='')}",
        params={"alt": "media", "generation": str(int(reference["generation"]))},
        headers={"Authorization": "Bearer " + _token()}, timeout=(10, 120))
    if response.status_code in (404, 429) or response.status_code >= 500:
        raise Deferred("remote_reply_unavailable", 15)
    response.raise_for_status()
    return response.json()


def _pages_to_read(body):
    """Document AI 一次读几页（``enterprise.pages.MAX_PAGES_PER_READ``）分几块：与 ``recon_reading.read_document`` 同一分法。"""
    from pearnly_cowork.ocr.enterprise.pages import MAX_PAGES_PER_READ

    pages = int(body["page_count"])
    return -(-pages // MAX_PAGES_PER_READ) if body["filename"].lower().endswith(".pdf") else 1


def _create_task(config, remote, envelope, body, *, http=None):
    """建一个 Cloud Task：直接调 Cloud Run（带识别服务账号的身份令牌），或多页对账文档交给 Workflows 新开一次执行。
    任务名由派发号算出：重复提交同一个只会有一个（409 当作已建好）。"""
    parent = f"projects/{config['project']}/locations/{config['location']}/queues/{config['queue']}"
    account = invoker(config)
    pages = int(body.get("page_count") or 1)
    if config["workflow"] and envelope["path"] == "/v1/cowork/document" and pages >= config["workflow_min_pages"]:
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
    response = (http or requests).post(f"{TASKS}/{parent}/tasks",
        json={"task": {"name": name, "dispatchDeadline": DISPATCH_DEADLINE, "httpRequest": request}},
        headers={"Authorization": "Bearer " + _token()}, timeout=(10, 30))
    if response.status_code == 409:
        return name
    response.raise_for_status()
    return name
