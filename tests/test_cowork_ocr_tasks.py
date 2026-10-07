#!/usr/bin/env python3
"""识别交给 Cloud Tasks / Cloud Workflows（站点配置 ``pearnly_cowork_ocr_tasks``，默认关）：服务器这边的派发、取回与回调。
离线：不连站点、不连谷歌（谷歌接口、检查点、缓存都用替身）。

- 关着（或没开 Cloud Run 识别、不在识别任务里）完全走原来的按行等回复。
- 派发：整份请求存进检查点桶，Cloud Task 只带小信封；直接调 Cloud Run（识别服务账号的身份令牌）或多页对账文档交给
  Workflows；先建任务后记派发号；建不成就暂缓、不记派发号。
- 取回：派发号对得上、结果在 → 交回结果行，只补回调时没送到的进度事件；结果是暂缓 → 下次重新派发；
  还没回来 → 接着等（兜底时间），太久没回来当作丢了重新派发。
- 回调：只认谷歌签发、受众是本站回调地址、签发对象是识别服务账号的身份令牌；只记「结果在哪」并唤醒任务；
  已结束的、别的站点的、对不上的不处理；进度按派发号去重、只作展示。
"""
from datetime import datetime, timedelta, timezone
import base64
import json
from pathlib import Path
import sys
import time
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for app in ("pearnly_core", "pearnly_cowork"):
    sys.path.insert(0, str(ROOT / "apps" / app))

import frappe  # noqa: E402

from pearnly_core.execution.errors import Deferred  # noqa: E402
from pearnly_cowork.ocr.remote import callback, contract, forward, tasks  # noqa: E402

SITE, WORK = "acme.pearnly.com", "a" * 64
REMOTE = {"url": "https://cowork-ocr-abc-as.a.run.app", "rpm": None}
CONFIG = {"enabled": 1, "project": "pearnly", "location": "asia-southeast1", "queue": "cowork-ocr",
          "bucket": "pearnly-ocr-checkpoints-sg", "invoker": "pearnly-ocr-sa@pearnly.iam.gserviceaccount.com"}


class Reply:
    def __init__(self, status=200, body=None):
        self.status_code, self._body, self.headers = status, body or {}, {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(str(self.status_code))


class Google:
    """假的谷歌接口：存储桶上传 / 读取、建 Cloud Task。记下每次请求。"""

    def __init__(self):
        self.objects, self.posts, self.task_status = {}, [], 200

    def post(self, url, params=None, data=None, json=None, headers=None, timeout=None):
        self.posts.append((url, params, data, json))
        if "/upload/storage/" in url:
            self.objects[params["name"]] = data
            return Reply(200, {"generation": "7"})
        if "cloudtasks.googleapis.com" in url:
            return Reply(self.task_status, {})
        raise AssertionError(url)

    def get(self, url, params=None, headers=None, timeout=None):
        name = url.rsplit("/o/", 1)[1].replace("%2F", "/")
        found = self.objects.get(name)
        return Reply(200, found) if found is not None else Reply(404)


class Checkpoints:
    def __init__(self):
        self.values = {}

    def __call__(self, key, state=None, body=None):
        if state is None:
            return self.values.get(key)
        self.values[key] = {"state": state, "body": body}
        return self.values[key]


def document_body(pages=5):
    return contract.document_request(site=SITE, work=WORK, filename="statement.pdf", content=b"%PDF-1.7 x",
                                     page_count=pages, document_type="bank_statement", environment={"A": "b"})


class Cache:
    def __init__(self):
        self.values = {}

    def get_value(self, key):
        return self.values.get(key)

    def set_value(self, key, value, expires_in_sec=None):
        self.values[key] = value

    def delete_value(self, key):
        self.values.pop(key, None)


class Base(unittest.TestCase):
    def setUp(self):
        self.google, self.checkpoints = Google(), Checkpoints()
        frappe.local.site = SITE
        frappe.local.conf = frappe._dict({tasks.CONFIG: dict(CONFIG), forward.CONFIG: {"enabled": 1, "url": REMOTE["url"]}})
        frappe.local.flags = frappe._dict(pearnly_ocr_work=WORK)
        self.reserved, self.released = [], []
        patches = [mock.patch.object(tasks.core_work, "checkpoint", side_effect=self.checkpoints),
                   mock.patch.object(tasks, "_token", return_value="ACCESS"),
                   mock.patch.object(tasks.pool, "release_db"),
                   mock.patch.object(tasks, "_reserve", side_effect=lambda config, work, path, body, bucket=None:
                                     self.reserved.append((work, path)) or tasks._wants_workflow(config, path, body)),
                   mock.patch.object(tasks, "_release", side_effect=self.released.append),
                   mock.patch.object(tasks.frappe, "cache", Cache(), create=True)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(lambda: [delattr(frappe.local, key) for key in ("site", "conf", "flags")])


class Switch(Base):
    def test_only_inside_a_work_with_both_switches_on(self):
        self.assertTrue(tasks.active())
        frappe.local.flags = frappe._dict()
        self.assertFalse(tasks.active())
        frappe.local.flags = frappe._dict(pearnly_ocr_work=WORK)
        frappe.local.conf[forward.CONFIG] = {"enabled": 0, "url": REMOTE["url"]}
        self.assertFalse(tasks.active())

    def test_bad_configuration_is_off(self):
        for change in ({"enabled": 0}, {"project": "Bad Project"}, {"location": "nowhere"}, {"queue": "Q!"},
                       {"bucket": ""}, {"workflow": "Bad Name"}, {"callback_host": "https://x"}):
            frappe.local.conf[tasks.CONFIG] = {**CONFIG, **change}
            self.assertIsNone(tasks.settings(), change)

    def test_off_means_the_old_streaming_path(self):
        frappe.local.conf[tasks.CONFIG] = {**CONFIG, "enabled": 0}
        final = {"event": "result", "status": "done", "pipeline": "{}", "evidence": []}
        with mock.patch.object(forward, "_stream", return_value=final) as stream, \
                mock.patch.object(tasks, "call") as call:
            self.assertIs(forward._call(REMOTE, "/v1/cowork/invoice", {}, None, {}), final)
        stream.assert_called_once()
        call.assert_not_called()


class Dispatch(Base):
    def dispatch(self, body=None, path="/v1/cowork/document", config=None):
        if config:
            frappe.local.conf[tasks.CONFIG] = {**CONFIG, **config}
        with self.assertRaises(Deferred) as caught:
            tasks.call(REMOTE, path, body or document_body(), lambda event: None, http=self.google, now=lambda: 1000.0)
        return caught.exception

    def test_the_request_goes_to_the_bucket_and_the_task_carries_only_a_reference(self):
        body = document_body(pages=1)
        deferred = self.dispatch(body)
        self.assertEqual((deferred.reason, deferred.delay), ("remote_dispatched", tasks.WAIT_SECONDS))
        marker = self.checkpoints.values[tasks.DISPATCH_KEY]
        dispatch = marker["body"]["id"]
        self.assertEqual((marker["state"], marker["body"]["at"]), ("external", 1000.0))
        stored = contract.task_object(SITE, WORK, dispatch, "request.json")
        self.assertEqual(json.loads(self.google.objects[stored]), body)
        url, _params, _data, payload = self.google.posts[-1]
        self.assertEqual(url, "https://cloudtasks.googleapis.com/v2/projects/pearnly/locations/asia-southeast1/queues/cowork-ocr/tasks")
        task = payload["task"]
        self.assertTrue(task["name"].endswith("/tasks/" + WORK[:32] + "-" + dispatch))
        request = task["httpRequest"]
        self.assertEqual(request["url"], REMOTE["url"] + "/v1/cowork/task")
        self.assertEqual(request["oidcToken"], {"serviceAccountEmail": CONFIG["invoker"], "audience": REMOTE["url"]})
        envelope = contract.parse_task(json.loads(base64.b64decode(request["body"])))
        self.assertEqual(envelope["request"], {"object": stored, "generation": 7})
        self.assertEqual(envelope["callback"], "https://" + SITE + contract.CALLBACK_PATH)
        self.assertNotIn("content", json.dumps(envelope))
        self.assertEqual(task["dispatchDeadline"], "1800s")

    def test_a_multi_page_document_goes_to_workflows(self):
        self.dispatch(document_body(pages=5), config={"workflow": "cowork-document"})
        request = self.google.posts[-1][3]["task"]["httpRequest"]
        self.assertEqual(request["url"], "https://workflowexecutions.googleapis.com/v1/projects/pearnly/locations/"
                                         "asia-southeast1/workflows/cowork-document/executions")
        self.assertEqual(request["oauthToken"]["serviceAccountEmail"], CONFIG["invoker"])
        argument = json.loads(json.loads(base64.b64decode(request["body"]))["argument"])
        self.assertEqual((argument["reads"], argument["pages"], argument["service"]),
                         ([1, 2, 3], [1, 2, 3, 4, 5], REMOTE["url"]))
        contract.parse_task(argument["task"])

    def test_short_documents_and_invoices_go_straight_to_cloud_run(self):
        self.dispatch(document_body(pages=2), config={"workflow": "cowork-document"})
        self.assertIn("/v1/cowork/task", self.google.posts[-1][3]["task"]["httpRequest"]["url"])

    def test_a_failed_dispatch_is_deferred_without_a_marker(self):
        self.google.task_status = 403
        deferred = self.dispatch()
        self.assertEqual(deferred.reason, "tasks_unavailable")
        self.assertNotIn(tasks.DISPATCH_KEY, self.checkpoints.values)
        self.assertEqual(self.released, [WORK])          # 在途名额还回去

    def test_repeated_dispatch_failures_fall_back_to_streaming_and_are_logged(self):
        self.google.task_status = 403
        for _attempt in range(tasks.FALLBACK_AFTER - 1):
            self.assertEqual(self.dispatch().reason, "tasks_unavailable")
        with mock.patch.object(tasks.frappe, "log_error") as logged, self.assertRaises(tasks.Fallback):
            tasks.call(REMOTE, "/v1/cowork/document", document_body(), lambda event: None, http=self.google)
        logged.assert_called_once()
        final = {"event": "result", "status": "done", "pipeline": "{}", "evidence": []}
        with mock.patch.object(forward, "_stream", return_value=final) as stream, \
                mock.patch.object(tasks.frappe, "log_error"):
            self.assertIs(forward._call(REMOTE, "/v1/cowork/invoice", document_body(), None,
                                        {"environment": {"GCP_PROJECT": "pearnly"}}), final)
        stream.assert_called_once()
        self.google.task_status = 200
        self.assertEqual(self.dispatch().reason, "remote_dispatched")     # 恢复后照常派发、计数清零
        self.assertIsNone(tasks.frappe.cache.get_value(tasks.FAILURES))

    def test_too_many_workflows_at_once_dispatch_straight_to_cloud_run(self):
        with mock.patch.object(tasks, "_reserve", return_value=False):
            self.dispatch(document_body(pages=5), config={"workflow": "cowork-document"})
        self.assertIn("/v1/cowork/task", self.google.posts[-1][3]["task"]["httpRequest"]["url"])

    def test_an_existing_task_name_counts_as_created(self):
        self.google.task_status = 409
        self.assertEqual(self.dispatch().reason, "remote_dispatched")


class SelfCheck(Base):
    def test_self_check_writes_a_probe_and_creates_a_health_task(self):
        deleted = []
        self.google.delete = lambda url, headers=None, timeout=None: deleted.append(url) or Reply(204)
        result = tasks.self_check(http=self.google)
        self.assertTrue(result["ok"], result)
        task = self.google.posts[-1][3]["task"]
        self.assertEqual(task["httpRequest"]["url"], REMOTE["url"] + "/health")
        self.assertEqual(task["httpRequest"]["httpMethod"], "GET")
        self.assertEqual(len(deleted), 1)
        self.google.task_status = 403
        result = tasks.self_check(http=self.google)
        self.assertEqual((result["ok"], result["bucket"], result["task"]), (False, True, False))
        frappe.local.conf[tasks.CONFIG] = {**CONFIG, "enabled": 0}
        self.assertTrue(tasks.self_check(http=self.google)["bucket"])   # 开关没开也能先自检


class Shards(Base):
    def test_dispatches_spread_over_the_configured_queues(self):
        # 2026-10-02 m6：Cloud Tasks 空闲后刚开始投递时限速，分到几个队列开头的爬坡快几倍；默认 1 个照旧
        self.assertEqual(tasks.settings()["shards"], 1)
        self.assertEqual(tasks.queue_for(tasks.settings(), "ffffffff" + "0" * 24), "cowork-ocr")
        frappe.local.conf[tasks.CONFIG] = {**CONFIG, "shards": 4}
        config = tasks.settings()
        self.assertEqual(tasks.queues(config), ["cowork-ocr", "cowork-ocr-1", "cowork-ocr-2", "cowork-ocr-3"])
        picked = {tasks.queue_for(config, f"{n:08x}" + "0" * 24) for n in range(16)}
        self.assertEqual(picked, set(tasks.queues(config)))
        self.assertEqual(tasks.queue_for(config, "00000005" + "0" * 24), "cowork-ocr-1")   # 同一派发号总在同一队列
        for bad in (0, 9, "4", True):
            frappe.local.conf[tasks.CONFIG] = {**CONFIG, "shards": bad}
            self.assertEqual(tasks.settings()["shards"], 1, bad)

    def test_the_task_goes_to_its_shard_and_self_check_probes_every_queue(self):
        frappe.local.conf[tasks.CONFIG] = {**CONFIG, "shards": 4}
        with self.assertRaises(Deferred):
            tasks.call(REMOTE, "/v1/cowork/document", document_body(), lambda event: None, http=self.google,
                       now=lambda: 1000.0)
        url = self.google.posts[-1][0]
        dispatch = self.checkpoints.values[tasks.DISPATCH_KEY]["body"]["id"]
        self.assertIn("/queues/" + tasks.queue_for(tasks.settings(), dispatch) + "/tasks", url)
        self.google.delete = lambda url, headers=None, timeout=None: Reply(204)
        before = len(self.google.posts)
        result = tasks.self_check(http=self.google)
        self.assertTrue(result["ok"], result)
        probes = [post[0] for post in self.google.posts[before:] if "/tasks" in post[0]]
        self.assertEqual(sorted(p.split("/queues/")[1].split("/")[0] for p in probes), sorted(tasks.queues(tasks.settings())))


class Collect(Base):
    def setUp(self):
        super().setUp()
        self.dispatch = "b" * 32
        self.checkpoints.values[tasks.DISPATCH_KEY] = {"state": "external", "body": {"id": self.dispatch, "at": 1000.0}}

    def reply(self, final, events=()):
        name = contract.task_object(SITE, WORK, self.dispatch, "reply.json")
        self.google.objects[name] = contract.reply(final, list(events))
        self.checkpoints.values[tasks.REPLY_KEY + self.dispatch] = {"state": "reply",
                                                                    "body": {"object": name, "generation": 3}}

    def collect(self, now=1010.0, applied=0):
        seen = []
        with mock.patch.object(tasks, "progress_applied", return_value=applied):
            result = tasks.call(REMOTE, "/v1/cowork/document", document_body(), seen.append, http=self.google,
                                now=lambda: now)
        return result, seen

    def test_the_result_comes_back_with_the_events_the_callbacks_missed(self):
        events = [{"event": "read", "pages": 2}, {"event": "extracted", "page": 1, "text": "{}"},
                  {"event": "unread", "page": 2}]
        final = {"event": "result", "status": "done", "legacy": {"pages": []}}
        self.reply(final, events)
        result, seen = self.collect(applied=2)
        self.assertEqual(result, final)
        self.assertEqual(seen, [{"event": "unread", "page": 2}])

    def test_a_deferred_result_dispatches_again_next_time(self):
        self.reply({"event": "result", "status": "deferred", "reason": "provider_429", "delay": 20})
        with self.assertRaises(Deferred) as caught:
            forward._outcome(self.collect()[0])
        self.assertEqual(caught.exception.reason, "provider_429")
        self.assertTrue(self.checkpoints.values[tasks.DISPATCH_KEY]["body"]["spent"])
        with self.assertRaises(Deferred) as caught:
            tasks.call(REMOTE, "/v1/cowork/document", document_body(), None, http=self.google, now=lambda: 2000.0)
        self.assertEqual(caught.exception.reason, "remote_dispatched")
        self.assertNotEqual(self.checkpoints.values[tasks.DISPATCH_KEY]["body"]["id"], self.dispatch)

    def test_no_result_yet_waits_and_a_lost_dispatch_is_sent_again(self):
        with self.assertRaises(Deferred) as caught:
            self.collect(now=1100.0)
        self.assertEqual((caught.exception.reason, caught.exception.delay), ("remote_waiting", tasks.WAIT_SECONDS))
        self.assertEqual(self.google.posts, [])
        with self.assertRaises(Deferred) as caught:
            self.collect(now=1000.0 + tasks.LOST_SECONDS + 1)
        self.assertEqual(caught.exception.reason, "remote_dispatched")

    def test_a_lost_callback_is_found_in_the_bucket_on_the_next_look(self):
        final = {"event": "result", "status": "done", "legacy": {"pages": []}}
        name = contract.task_object(SITE, WORK, self.dispatch, "reply.json")
        self.google.objects[name] = contract.reply(final, [{"event": "read", "pages": 1}])
        result, seen = self.collect()            # 没有回调那一行：直接去桶里看
        self.assertEqual(result, final)
        self.assertEqual(seen, [{"event": "read", "pages": 1}])
        self.assertEqual(self.released, [WORK])

    def test_a_workflow_failure_report_dispatches_again_at_once(self):
        self.checkpoints.values[tasks.REPLY_KEY + self.dispatch] = {"state": "reply", "body": {"failed": True}}
        with self.assertRaises(Deferred) as caught:
            self.collect()
        self.assertEqual(caught.exception.reason, "remote_failed")
        self.assertTrue(self.checkpoints.values[tasks.DISPATCH_KEY]["body"]["spent"])

    def test_a_reply_read_hiccup_waits_instead_of_failing(self):
        import requests

        self.reply({"event": "result", "status": "done", "legacy": {}})

        class Broken:
            def get(self, *a, **k):
                raise requests.ConnectionError("synthetic")

        with self.assertRaises(Deferred) as caught:
            tasks.call(REMOTE, "/v1/cowork/document", document_body(), None, http=Broken(), now=lambda: 1010.0)
        self.assertEqual(caught.exception.reason, "remote_reply_unavailable")
        with mock.patch.object(tasks, "_token", side_effect=RuntimeError("refresh failed")), \
                self.assertRaises(Deferred):
            tasks.call(REMOTE, "/v1/cowork/document", document_body(), None, http=self.google, now=lambda: 1010.0)

    def test_repeated_deferred_results_back_off(self):
        self.assertEqual(self.back_off("provider_429"), [15, 30, 60, 120, 240])

    def test_a_vision_shortage_backs_off_at_most_a_minute(self):
        # 读字 8 / 14 是谷歌那边短时不足：几十秒就恢复，整份重派最多等 60 秒（2026-10-02 100 人复测）
        self.assertEqual(self.back_off("vision_8"), [15, 30, 60, 60, 60])

    def back_off(self, reason):
        delays = []
        for round_ in range(1, 6):
            self.reply({"event": "result", "status": "deferred", "reason": reason, "delay": 5})
            with mock.patch.object(tasks, "_cool_down"), mock.patch.object(tasks, "progress_applied", return_value=0):
                final = tasks.call(REMOTE, "/v1/cowork/document", document_body(), lambda e: None, http=self.google,
                                   now=lambda: 1010.0)
            delays.append(final["delay"])
            self.assertEqual(self.checkpoints.values[tasks.DISPATCH_KEY]["body"]["rounds"], round_)
            with self.assertRaises(Deferred):        # 下次进来重新派发，带着已暂缓的次数
                tasks.call(REMOTE, "/v1/cowork/document", document_body(), None, http=self.google, now=lambda: 2000.0)
            self.dispatch = self.checkpoints.values[tasks.DISPATCH_KEY]["body"]["id"]
            self.assertEqual(self.checkpoints.values[tasks.DISPATCH_KEY]["body"]["rounds"], round_)
        return delays

    def test_server_storage_reads_ride_out_a_reset(self):
        import requests

        self.reply({"event": "result", "status": "done", "legacy": {}})
        real_get = self.google.get
        failures = {"left": 1}

        def flaky(url, **kwargs):
            if failures["left"]:
                failures["left"] -= 1
                raise requests.ConnectionError("reset by peer")
            return real_get(url, **kwargs)

        self.google.get = flaky
        with mock.patch.object(tasks.time, "sleep"), mock.patch.object(tasks, "progress_applied", return_value=0):
            result = tasks.call(REMOTE, "/v1/cowork/document", document_body(), lambda e: None, http=self.google,
                                now=lambda: 1010.0)
        self.assertEqual(result["status"], "done")

    def test_each_result_adjusts_the_adaptive_dispatch_limit(self):
        self.reply({"event": "result", "status": "done", "legacy": {}, "throttled": 3})
        with mock.patch.object(tasks, "_adapt") as adapt, mock.patch.object(tasks, "progress_applied", return_value=0):
            tasks.call(REMOTE, "/v1/cowork/document", document_body(), lambda e: None, http=self.google, now=lambda: 1010.0)
        adapt.assert_called_once()
        self.assertEqual(adapt.call_args.args[1:3], (3, tasks.PRO))      # 对账文档（3.8 抽取）的 429 调 3.8 池
        self.assertEqual((tasks.pool_of("/v1/cowork/invoice"), tasks.pool_of("/v1/cowork/document")),
                         (tasks.LITE, tasks.PRO))

    def test_pools_follow_the_model_the_job_reads_with(self):
        # 2026-10-02：谷歌共享额度按模型算——发票池跟着站点选的看图模型走，对账文档进 3.8 池
        chosen = {"environment": {"OCR_LLM_BACKEND": "vertex", "OCR_DIRECT_MODEL": tasks.PRO}, "policy": {"mode": "economy"}}
        default = {"environment": {"OCR_LLM_BACKEND": "vertex"}, "policy": {"mode": "economy"}}
        self.assertEqual(tasks.pool_of("/v1/cowork/invoice", chosen), tasks.PRO)
        self.assertEqual(tasks.pool_of("/v1/cowork/invoice", default), tasks.LITE)
        # 按模型带回的各进各的池；读字等（""）和认不出的模型算进这份所在的池；旧版只带总数时整份进所在的池
        final = {"throttled": 4, "calls": 20, "by_model": {tasks.PRO: [10, 3], tasks.LITE: [6, 1], "": [4, 0]}}
        self.assertEqual(tasks._pool_counts(final, tasks.PRO), {tasks.PRO: (14, 3), tasks.LITE: (6, 1)})
        self.assertEqual(tasks._pool_counts({"throttled": 2, "calls": 9}, tasks.LITE), {tasks.LITE: (9, 2)})
        self.reply({"event": "result", "status": "done", "legacy": {}, "throttled": 4, "calls": 20,
                    "by_model": final["by_model"]})
        with mock.patch.object(tasks, "_adapt") as adapt, mock.patch.object(tasks, "progress_applied", return_value=0):
            tasks.call(REMOTE, "/v1/cowork/document", document_body(), lambda e: None, http=self.google, now=lambda: 1010.0)
        self.assertEqual(sorted(call.args[1:4] for call in adapt.call_args_list), sorted([(3, tasks.PRO, 14), (1, tasks.LITE, 6)]))

    def test_the_adaptive_limit_starts_low_and_can_be_switched_off(self):
        self.assertEqual(tasks._adaptive_start({"adaptive": True}, {"concurrency": 160}), tasks.ADAPTIVE_START)
        self.assertEqual(tasks._adaptive_start({"adaptive": True}, {"concurrency": 10}, "gemini-3.5-flash"), 10)
        self.assertEqual(tasks._adaptive_start({"adaptive": True}, {"concurrency": 10}, tasks.LITE), 24)  # flash-lite 下限 24
        self.assertEqual(tasks._adaptive_start({"adaptive": True}, {"concurrency": 10}, tasks.PRO), 16)   # 3.8 下限 16
        self.assertEqual(tasks._adaptive_start({"adaptive": False}, {"concurrency": 160}), 0)
        frappe.local.conf[tasks.CONFIG] = {**CONFIG, "adaptive": 0}
        self.assertFalse(tasks.settings()["adaptive"])

    def test_a_full_capacity_waits_in_place_instead_of_requeueing(self):
        answers = [Deferred("capacity", 3), Deferred("capacity", 3), True]
        slept = []

        def reserve(*args, **kwargs):
            value = answers.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        with mock.patch.object(tasks, "_reserve", side_effect=reserve):
            self.assertTrue(tasks._reserve_waiting(CONFIG, WORK, "/v1/cowork/invoice", {}, sleep=slept.append))
        self.assertEqual(slept, [tasks.CAPACITY_STEP, tasks.CAPACITY_STEP])   # 原地等两秒就派出去，没写库、没重新入队

    def test_waiting_for_capacity_gives_up_after_a_minute_and_is_limited_per_process(self):
        clock = [0.0]
        with mock.patch.object(tasks, "_reserve", side_effect=Deferred("capacity", 3)), self.assertRaises(Deferred):
            tasks._reserve_waiting(CONFIG, WORK, "/v1/cowork/invoice", {},
                                   sleep=lambda s: clock.__setitem__(0, clock[0] + s), clock=lambda: clock[0])
        self.assertGreaterEqual(clock[0], tasks.CAPACITY_WAIT)
        for _waiter in range(tasks.CAPACITY_WAITERS):
            tasks._waiters.acquire()
        try:
            with mock.patch.object(tasks, "_reserve", side_effect=Deferred("capacity", 3)), self.assertRaises(Deferred) as caught:
                tasks._reserve_waiting(CONFIG, WORK, "/v1/cowork/invoice", {}, sleep=lambda s: self.fail("should not wait"))
            self.assertEqual(caught.exception.delay, 10)      # 这个进程已有 4 个在等：照旧暂缓，间隔放长
        finally:
            for _waiter in range(tasks.CAPACITY_WAITERS):
                tasks._waiters.release()

    def test_a_throttled_result_cools_the_shared_budget_down(self):
        self.reply({"event": "result", "status": "deferred", "reason": "provider_429", "delay": 20})
        with mock.patch.object(tasks, "_cool_down") as cool, mock.patch.object(tasks, "progress_applied", return_value=0):
            tasks.call(REMOTE, "/v1/cowork/document", document_body(), lambda e: None, http=self.google,
                       now=lambda: 1010.0, bucket="B1")
        cool.assert_called_once_with("B1", 20)

    def test_outcomes_raise_like_the_streaming_path(self):
        from pearnly_core.execution.errors import Uncertain

        self.reply({"event": "result", "status": "uncertain", "reason": "x"})
        with self.assertRaises(Uncertain):
            forward._outcome(self.collect()[0])


def keypair():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from google.auth import crypt

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "google-test")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .sign(key, hashes.SHA256()))
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    return crypt.RSASigner.from_string(pem, key_id="k1"), {"k1": cert.public_bytes(serialization.Encoding.PEM).decode()}


SIGNER, CERTS = keypair()


def token(audience=None, email=CONFIG["invoker"], issuer="https://accounts.google.com", expires=3600, verified=True):
    from google.auth import jwt

    now = int(time.time())
    return jwt.encode(SIGNER, {"iss": issuer, "aud": audience or ("https://" + SITE + contract.CALLBACK_PATH),
                               "email": email, "email_verified": verified, "iat": now - 10, "exp": now + expires},
                      key_id="k1").decode()


class Verify(unittest.TestCase):
    def test_only_a_google_token_for_this_callback_from_the_ocr_account_passes(self):
        audience = "https://" + SITE + contract.CALLBACK_PATH
        self.assertEqual(callback.verify(token(), audience, CONFIG["invoker"], certs=CERTS)["email"], CONFIG["invoker"])
        for bad in (token(audience="https://other.pearnly.com" + contract.CALLBACK_PATH), token(email="x@evil.test"),
                    token(issuer="https://evil.test"), token(expires=-120), token(verified=False), "not-a-token", None):
            with self.assertRaises(callback.Refused):
                callback.verify(bad, audience, CONFIG["invoker"], certs=CERTS)

    def test_an_unknown_key_id_refreshes_the_certificates_at_most_once_a_minute(self):
        fetched = []

        class Http:
            def get(self, url, timeout=None):
                fetched.append(url)
                reply = Reply(200, CERTS)
                reply.headers = {"Cache-Control": "max-age=3600"}
                return reply

        callback._certs.update(value=None, until=0.0, refreshed=0.0)
        self.addCleanup(callback._certs.update, value=None, until=0.0, refreshed=0.0)
        clock = [100.0]
        callback.google_certs(http=Http(), clock=lambda: clock[0])
        for _forged in range(5):
            callback.google_certs(refresh=True, http=Http(), clock=lambda: clock[0])
        self.assertEqual(len(fetched), 1)
        clock[0] += callback.REFRESH_SECONDS + 1
        callback.google_certs(refresh=True, http=Http(), clock=lambda: clock[0])
        self.assertEqual(len(fetched), 2)

    def test_certificates_are_cached_by_max_age(self):
        fetched = []

        class Http:
            def get(self, url, timeout=None):
                fetched.append(url)
                reply = Reply(200, CERTS)
                reply.headers = {"Cache-Control": "public, max-age=1000, must-revalidate"}
                return reply

        callback._certs.update(value=None, until=0.0, refreshed=0.0)
        self.addCleanup(callback._certs.update, value=None, until=0.0, refreshed=0.0)
        clock = [100.0]
        self.assertEqual(callback.google_certs(http=Http(), clock=lambda: clock[0]), CERTS)
        callback.google_certs(http=Http(), clock=lambda: clock[0])
        clock[0] += 1001
        callback.google_certs(http=Http(), clock=lambda: clock[0])
        self.assertEqual(len(fetched), 2)


class Receive(Base):
    def setUp(self):
        super().setUp()
        frappe.local.response = frappe._dict()
        self.row = frappe._dict(kind="cowork.recon_document", status="deferred", user="a@acme.test",
                                arguments=json.dumps({"recon_job": "R1", "file": "F1", "filename": "s.pdf"}))
        self.dispatch = "c" * 32
        patches = [mock.patch.object(callback, "google_certs", return_value=CERTS),
                   mock.patch.object(callback.core_work, "checkpoint", side_effect=self.checkpoints),
                   mock.patch.object(callback.core_work, "wake"),
                   mock.patch.object(callback.frappe, "db", types.SimpleNamespace(get_value=lambda *a, **k: self.row,
                                                                                   commit=lambda: None, rollback=lambda: None))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.wake = callback.core_work.wake
        self.addCleanup(lambda: delattr(frappe.local, "response"))

    def send(self, body, header=None):
        request = types.SimpleNamespace(get_data=lambda: json.dumps(body).encode())
        with mock.patch.object(callback.frappe, "request", request, create=True), \
                mock.patch.object(callback.frappe, "get_request_header",
                                  side_effect=lambda name: header if name == callback.HEADER else None):
            return callback.receive()

    def reply_body(self, **change):
        return {**contract.callback("reply", site=SITE, work=WORK, dispatch=self.dispatch,
                                    reply={"object": contract.task_object(SITE, WORK, self.dispatch, "reply.json"),
                                           "generation": 4}), **change}

    def test_a_reply_is_recorded_under_its_dispatch_and_wakes_the_work(self):
        self.assertEqual(self.send(self.reply_body(), token()), {"ok": 1})
        self.assertEqual(self.checkpoints.values[tasks.REPLY_KEY + self.dispatch]["body"]["generation"], 4)
        self.wake.assert_called_once_with(WORK)
        self.assertEqual(self.released, [WORK])       # 云上已读完：在途名额回调一到就还
        self.assertEqual(self.send(self.reply_body(), token()), {"ok": 1})   # 重复回调：同一行、同样内容

    def test_a_bad_token_is_401_records_nothing_and_is_logged_once(self):
        callback._refused["at"] = 0.0
        with mock.patch.object(callback.frappe, "log_error") as logged:
            for header in (None, token(audience="https://other.pearnly.com" + contract.CALLBACK_PATH), token(email="x@y.z")):
                frappe.local.response = frappe._dict()
                self.assertEqual(self.send(self.reply_body(), header), {"ok": 0})
                self.assertEqual(frappe.local.response.http_status_code, 401)
        logged.assert_called_once()                     # 记错误日志，但不刷屏
        self.assertEqual(self.checkpoints.values, {})
        self.wake.assert_not_called()

    def test_a_write_clash_is_retried_and_a_heartbeat_clash_is_skipped(self):
        clashes = {"left": 1}
        original = self.checkpoints.__call__

        def clashing(key, state=None, body=None):
            if state is not None and clashes["left"]:
                clashes["left"] -= 1
                raise frappe.QueryDeadlockError("1020 Record has changed since last read")
            return original(key, state, body)

        with mock.patch.object(callback.core_work, "checkpoint", side_effect=clashing), \
                mock.patch.object(callback.frappe, "db", types.SimpleNamespace(
                    get_value=lambda *a, **k: self.row, commit=lambda: None, rollback=lambda: None)):
            self.assertEqual(self.send(self.reply_body(), token()), {"ok": 1})      # 结果：撞上一次，重来写进
            self.assertIn(tasks.REPLY_KEY + self.dispatch, self.checkpoints.values)
            self.wake.assert_called_once_with(WORK)
            clashes["left"] = 5
            body = contract.callback("alive", site=SITE, work=WORK, dispatch=self.dispatch)
            self.assertEqual(self.send(body, token()), {"ok": 1})                   # 心跳：一直撞就跳过，不报错

    def test_a_warm_step_heartbeat_only_records_progress(self):
        body = contract.callback("alive", site=SITE, work=WORK, dispatch=self.dispatch)
        self.assertEqual(self.send(body, token()), {"ok": 1})
        self.assertEqual(list(self.checkpoints.values), [callback.PROGRESS_KEY])
        self.assertEqual(self.checkpoints.values[callback.PROGRESS_KEY]["state"], "progress")
        self.wake.assert_not_called()

    def test_a_failure_report_is_recorded_and_wakes_the_work(self):
        body = contract.callback("failed", site=SITE, work=WORK, dispatch=self.dispatch)
        self.assertEqual(self.send(body, token()), {"ok": 1})
        self.assertEqual(self.checkpoints.values[tasks.REPLY_KEY + self.dispatch]["body"], {"failed": True})
        self.assertEqual(self.checkpoints.values[callback.PROGRESS_KEY]["state"], "progress")
        self.wake.assert_called_once_with(WORK)

    def test_switched_off_is_404(self):
        frappe.local.conf[tasks.CONFIG] = {**CONFIG, "enabled": 0}
        self.assertEqual(self.send(self.reply_body(), token()), {"ok": 0})
        self.assertEqual(frappe.local.response.http_status_code, 404)

    def test_finished_foreign_or_malformed_callbacks_are_ignored_with_200(self):
        self.row = frappe._dict(self.row, status="succeeded")
        self.assertEqual(self.send(self.reply_body(), token())["reason"], "finished")
        self.row = frappe._dict(self.row, status="deferred", kind="erp.purchase")
        self.assertEqual(self.send(self.reply_body(), token())["reason"], "finished")
        self.row = frappe._dict(self.row, kind="cowork.recon_document")
        self.assertEqual(self.send(self.reply_body(site="other.pearnly.com"), token())["reason"], "invalid")
        body = self.reply_body()
        body["reply"] = {"object": "someone/else/reply.json", "generation": 1}
        self.assertEqual(self.send(body, token())["reason"], "invalid")
        self.assertEqual(self.checkpoints.values, {})
        self.wake.assert_not_called()
        self.assertNotIn("http_status_code", frappe.local.response)

    def test_progress_is_applied_once_in_order(self):
        cache, applied = {}, []
        events = [{"event": "read", "pages": 2}, {"event": "extracted", "page": 1, "text": "{}"},
                  {"event": "extracted", "page": 2, "text": "{}"}]

        def progress(seq, items):
            return contract.callback("progress", site=SITE, work=WORK, dispatch=self.dispatch, events=items, seq=seq)

        with mock.patch.object(callback.frappe, "cache", types.SimpleNamespace(
                    get_value=cache.get, set_value=lambda key, value, expires_in_sec=None: cache.__setitem__(key, value))), \
                mock.patch.object(callback.frappe, "get_hooks",
                                  return_value={"cowork.recon_document": ["x.progress"]}), \
                mock.patch.object(callback.frappe, "get_attr", return_value=lambda arguments, items, state, user=None:
                                  applied.append((arguments["file"], [e["event"] for e in items], user))):
            self.send(progress(0, events[:2]), token())
            self.send(progress(0, events[:2]), token())      # 重复：不再应用
            self.send(progress(3, events[2:]), token())      # 跳号（第 2 个还没到）：不应用
            self.send(progress(2, events[2:]), token())
        self.assertEqual(applied, [("F1", ["read", "extracted"], "a@acme.test"), ("F1", ["extracted"], "a@acme.test")])
        self.assertEqual(cache[tasks.progress_key(WORK, self.dispatch)]["seq"], 3)
        self.wake.assert_not_called()


class RefusalCode(unittest.TestCase):
    def test_a_provider_refusal_fails_the_batch_with_its_code_not_as_a_program_error(self):
        from contextlib import nullcontext
        from pearnly_cowork.ocr.execution import workflow
        from pearnly_cowork.ocr.providers.client import ProviderError

        doc = frappe._dict(name="B1", phase="queued", result_json=None, settings_json="{}", examples_json="null",
                           actor="a@acme.test")
        with mock.patch.object(workflow.store, "read", return_value=doc), \
                mock.patch.object(workflow, "_require_work"), mock.patch.object(workflow, "_check"), \
                mock.patch.object(workflow.store, "actor", side_effect=lambda user: nullcontext()), \
                mock.patch.object(workflow.store, "save"), \
                mock.patch.object(workflow.frappe, "db", types.SimpleNamespace(commit=lambda: None, rollback=lambda: None)), \
                mock.patch.object(workflow, "_recognize",
                                  side_effect=ProviderError("ocr.invalid_vision_image", http_status=400)):
            self.assertEqual(workflow.run("B1"), {"failed": "ocr.invalid_vision_image"})


class ReconWaitSeesCloudProgress(unittest.TestCase):
    """对账等识别：开读 25 分钟后，只要云上最近 5 分钟内还有进展（回调写进库的 progress 行），就接着等、不判超时。"""

    def evaluate(self, since_done):
        from pearnly_cowork.recon_center import document_ocr

        captured = {}
        now = 10_000.0
        clock = {"entered": now - 2000, "started": now - 1600, "deadline": now - 100}
        progress = {"done": 0, "since_first": 1600, "since_done": since_done}
        with mock.patch.object(document_ocr.waiting, "settle",
                               side_effect=lambda key, evaluate: captured.setdefault("result", evaluate(clock, now))), \
                mock.patch.object(document_ocr.frappe, "db", types.SimpleNamespace(commit=lambda: None,
                    get_value=lambda *a, **k: frappe._dict(status="deferred", last_code="remote_waiting"))), \
                mock.patch.object(document_ocr.core_work, "call_progress", return_value={"W1": progress}), \
                mock.patch.object(document_ocr.core_work, "saved_checkpoint", return_value=None), \
                mock.patch.object(document_ocr, "_", side_effect=lambda text: text), \
                mock.patch.object(document_ocr.frappe, "throw", side_effect=lambda message, *a, **k: (_ for _ in ()).throw(RuntimeError(message))):
            document_ocr.wait("W1")
        return clock

    def test_recent_cloud_progress_extends_the_wait(self):
        from pearnly_cowork.recon_center import waiting

        with self.assertRaises(waiting.Pending):
            self.evaluate(since_done=30)        # 30 秒前云上还有进展（Workflows 预热做完一步 / 进度回调）

    def test_no_cloud_progress_times_out_as_before(self):
        with self.assertRaises(RuntimeError) as caught:
            self.evaluate(since_done=None)
        self.assertEqual(str(caught.exception), "reconciliation.recognition_timeout")


class Board(unittest.TestCase):
    def test_document_progress_drives_the_board_like_streaming(self):
        from pearnly_cowork.recon_center import document_ocr

        state, calls = {"seq": 0}, []
        with mock.patch.object(document_ocr, "_progress", side_effect=lambda file, **values: calls.append(("p", values))), \
                mock.patch.object(document_ocr.progress, "latest", side_effect=lambda job, item: calls.append(("l", job))), \
                mock.patch.object(document_ocr.progress, "page_summary", return_value={}), \
                mock.patch.object(document_ocr.progress, "unread", side_effect=lambda file, page: calls.append(("u", page))):
            document_ocr.remote_progress({"recon_job": "R1", "file": "F1", "filename": "s.pdf"},
                [{"event": "read", "pages": 2}, {"event": "extracted", "page": 1, "text": "{}"},
                 {"event": "extracted", "page": 1, "text": "{}", "reread": True}, {"event": "unread", "page": 2}], state)
            document_ocr.remote_progress({"recon_job": "R1", "file": "F1", "filename": "s.pdf"},
                [{"event": "read", "pages": 1}], state)
        # 抽完一页也报「读到这页」（不读字的读法 ADR-017 没有读字事件；看板只取较大值，先读字时不倒退）
        self.assertEqual(calls, [("p", {"read": 2, "state": "reading"}), ("p", {"read": 1, "done": 1}), ("l", "R1"), ("u", 2),
                                 ("p", {"read": 3, "state": "reading"})])


if __name__ == "__main__":
    unittest.main()
