"""对账等识别时不占进程（2026-10-01，recon_center/waiting.py）。离线：队列 Redis、RQ、识别任务表都用替身，不连站点。

守住的事：等识别时后台任务结束、不占 long 进程；识别有结论（或到了时限）按原参数、以发起人身份放回队列接着跑；同一套对账
任何时刻最多一个排着队 / 在跑的后台任务（重复提醒、定时兜底、被杀续跑同时发生也一样）；被杀续跑照旧；几十套同时推进。
"""
import json
import random
import unittest
from collections import deque
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import mock

import frappe

from pearnly_cowork.recon_center import admission, document_ocr, invoice_ocr, jobs, slots, waiting


class FakeRedis:
    """队列 Redis 的替身：本测试用到的字符串、哈希、有序集合与锁。锁不可重入，重入即报错（守住「同一把锁」）。"""

    def __init__(self):
        self.values, self.hashes, self.zsets, self.locks = {}, {}, {}, set()
        self.broken = False

    def _check(self):
        if self.broken:
            raise ConnectionError("redis down")

    def lock(self, name, timeout=None, blocking_timeout=None):
        fake = self

        class Lock:
            def acquire(self):
                fake._check()
                if name in fake.locks:
                    raise AssertionError(f"lock {name} taken twice")
                fake.locks.add(name)
                return True

            def release(self):
                fake.locks.discard(name)

            def __enter__(self):
                self.acquire()
                return self

            def __exit__(self, *exc):
                self.release()
                return False
        return Lock()

    def get(self, key):
        self._check()
        value = self.values.get(key)
        return value.encode() if isinstance(value, str) else value

    def set(self, key, value, ex=None, nx=False):
        self._check()
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def delete(self, key):
        self._check()
        self.values.pop(key, None)
        self.hashes.pop(key, None)

    def expire(self, key, seconds):
        self._check()

    def hget(self, key, field):
        self._check()
        value = self.hashes.get(key, {}).get(field)
        return value.encode() if isinstance(value, str) else value

    def hset(self, key, field, value):
        self._check()
        self.hashes.setdefault(key, {})[field] = value

    def zadd(self, key, mapping, nx=False):
        self._check()
        target = self.zsets.setdefault(key, {})
        for member, score in mapping.items():
            if not (nx and member in target):
                target[member] = score

    def zrem(self, key, member):
        self._check()
        self.zsets.get(key, {}).pop(member, None)

    def zscore(self, key, member):
        return self.zsets.get(key, {}).get(member)

    def zcard(self, key):
        return len(self.zsets.get(key, {}))

    def zrange(self, key, start, end):
        ordered = [member.encode() for member, _score in sorted(self.zsets.get(key, {}).items(),
                                                                 key=lambda item: item[1])]
        return ordered[start:] if end == -1 else ordered[start:end + 1]


class FakeJob:
    def __init__(self, rq, job_id, user, kwargs):
        self.rq, self.id, self.user, self.kwargs, self.status, self.exc_info = rq, job_id, user, kwargs, "queued", ""

    def get_status(self, refresh=False):
        return self.status

    def delete(self):
        if self.rq.jobs.get(self.id) is self:
            del self.rq.jobs[self.id]


class FakeRQ:
    """RQ 的替身：同一个任务号还在跑（删掉任务记录也停不了正在跑的进程）或还排着队时再排，就是重复执行，直接判失败。"""

    def __init__(self, session):
        self.jobs, self.queue, self.session, self.enqueued, self.running = {}, deque(), session, [], set()

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def enqueue(self, method, queue=None, timeout=None, job_id=None, enqueue_after_commit=False, **kwargs):
        current = self.jobs.get(job_id)
        if job_id in self.running or (current and current.status in waiting.ALIVE):
            raise AssertionError(f"{job_id} enqueued while still running or queued: it would run twice")
        job = FakeJob(self, job_id, self.session.user, {"user": self.session.user, "kwargs": kwargs})
        self.jobs[job_id] = job
        self.queue.append(job)
        self.enqueued.append((job_id, self.session.user))
        return job


class Harness(unittest.TestCase):
    """对账后台任务、识别任务表、队列都换成替身；``jobs.run`` 本身、``waiting``、``slots`` 用真的。"""

    def setUp(self):
        self.redis = FakeRedis()
        self.session = SimpleNamespace(user="Administrator")
        self.rq = FakeRQ(self.session)
        self.ocr = {}            # Work 名 → 状态
        self.started = set()     # 已开始读的 Work（检查点里有调用）
        self.now = [1_000_000.0]
        self.local = SimpleNamespace(site="t.example", lang="en")
        self.logged = []

        def set_user(user):
            self.session.user = user

        patches = [
            mock.patch.object(waiting, "_conn", return_value=self.redis),
            mock.patch.object(slots, "_conn", return_value=self.redis),
            mock.patch.object(frappe, "local", self.local),
            mock.patch.object(frappe, "session", self.session),
            mock.patch.object(frappe, "set_user", side_effect=set_user),
            mock.patch.object(frappe, "db", mock.MagicMock()),
            mock.patch.object(frappe, "log_error", side_effect=lambda **kw: self.logged.append(kw)),
            mock.patch.object(frappe, "enqueue", side_effect=self.rq.enqueue),
            mock.patch.object(frappe, "conf", frappe._dict(pearnly_recon_max_running=6)),
            mock.patch("frappe.utils.background_jobs.get_job", side_effect=self.rq.get_job),
            mock.patch("frappe.utils.background_jobs.get_redis_conn", return_value=self.redis),
            mock.patch.object(waiting, "_statuses", side_effect=lambda works: {
                name: frappe._dict(name=name, status=self.ocr[name], last_code="") for name in works if name in self.ocr}),
            mock.patch("pearnly_core.execution.work.call_progress", side_effect=lambda names: {
                name: {"done": 0, "since_first": 1 if name in self.started else None, "since_done": None}
                for name in names}),
            mock.patch.object(waiting.time, "time", side_effect=lambda: self.now[0]),
            mock.patch.object(waiting.time, "sleep", side_effect=self.sleep),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.plans, self.owners, self.passes, self.done, self.failed = {}, {}, {}, set(), {}
        self.busy = []  # 每轮跑的时候，同一套对账有几个在跑（应恒为 1）
        patcher = mock.patch.object(jobs, "_run", side_effect=self.fake_pass)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sleep(self, seconds):
        self.now[0] += seconds

    # ── 一套对账：按顺序等几份识别（与 document_ocr.wait 同一种等法：没好就抛 Pending）────────────────

    def fake_pass(self, recon_job, kind, workspace, title, files, options="{}"):
        self.passes[recon_job] = self.passes.get(recon_job, 0) + 1
        assert frappe.session.user == self.owners[recon_job], "a reconciliation ran as someone else"
        running = [job for job in self.rq.jobs.values() if job.id == f"cowork-recon-{recon_job}"
                   and job.status == "started"]
        self.busy.append(len(running))
        try:
            for work in self.plans[recon_job]:
                waiting.settle(f"document:{work}", lambda clock, now, work=work: self.read(work, clock, now))
        except TimeoutError as error:
            self.failed[recon_job] = str(error)
            return
        self.done.add(recon_job)

    def read(self, work, clock, now):
        self.ocr.setdefault(work, "queued")
        if self.ocr[work] in waiting.TERMINAL:
            return self.ocr[work]
        entered = clock.setdefault("entered", now)
        if now - entered > 3600:
            raise TimeoutError("recognition_timeout")
        raise waiting.Pending([work], due=entered + 3600, start=work not in self.started)

    def submit(self, recon_job, plan, user="u@example.com"):
        self.plans[recon_job], self.owners[recon_job] = plan, user
        previous, self.session.user = self.session.user, user
        try:
            frappe.enqueue("pearnly_cowork.recon_center.jobs.run", queue="long", job_id=f"cowork-recon-{recon_job}",
                           recon_job=recon_job, kind="bank", workspace="ws", title=recon_job,
                           files={"left": [], "right": []}, options="{}")
        finally:
            self.session.user = previous

    def work(self, limit=None):
        """long 进程取任务跑（同步跑完一轮）。回跑了几轮。"""
        count = 0
        while self.rq.queue and (limit is None or count < limit):
            job = self.rq.queue.popleft()
            if self.rq.jobs.get(job.id) is not job or job.status != "queued":
                continue
            job.status = "started"
            self.rq.running.add(job.id)
            previous, self.session.user = self.session.user, job.user
            try:
                jobs.run(**job.kwargs["kwargs"])
                job.status = "finished"
            finally:
                self.session.user = previous
                self.rq.running.discard(job.id)
            count += 1
        return count

    def finish(self, work, status="succeeded"):
        """识别有了结论：共用层 _finish 最后调产品钩子（在识别进程里，会话用户是谁都可能）。"""
        self.ocr[work] = status
        previous, self.session.user = self.session.user, "Administrator"
        try:
            waiting.work_finished(work, "cowork.recon_document", status, "")
        finally:
            self.session.user = previous


class ParkAndWake(Harness):
    def test_waiting_for_recognition_gives_the_worker_back_and_the_result_brings_it_back(self):
        self.submit("A", ["W1", "W2"])
        self.assertEqual(self.work(), 1)
        self.assertEqual(self.rq.jobs["cowork-recon-A"].status, "finished")  # 等着的时候不占 long 进程
        self.assertIsNotNone(waiting.record("A"))
        self.assertEqual(self.work(), 0)  # 识别没结论，没人放它回来
        self.finish("W1")
        self.assertEqual(self.work(), 1)  # 第一份读完：接着跑，走到第二份再等
        self.finish("W2")
        self.work()
        self.assertEqual(self.done, {"A"})
        self.assertEqual(self.passes["A"], 3)
        self.assertIsNone(waiting.record("A"))
        self.assertEqual(self.redis.hashes, {})  # 时钟清掉了
        # 放回队列都以发起人身份（放它回来的是识别进程）
        self.assertEqual({user for _job, user in self.rq.enqueued}, {"u@example.com"})

    def test_a_failed_or_unreadable_recognition_also_brings_it_back(self):
        self.submit("A", ["W1"])
        self.work()
        self.finish("W1", "failed")
        self.work()
        self.assertEqual(self.done, {"A"})  # 读不出由对账自己处理（这里的替身照常往下走）

    def test_the_deadline_brings_it_back_without_any_result(self):
        self.submit("A", ["W1"])
        self.work()
        waiting.tick()
        self.assertEqual(self.work(), 0)  # 没到时限、没开始读：不放
        self.started.add("W1")
        waiting.tick()
        self.assertEqual(self.work(), 1)  # 开始读了：放回来定读取时限
        self.now[0] += 3601
        waiting.tick()
        self.work()
        self.assertEqual(self.failed, {"A": "recognition_timeout"})
        self.assertIsNone(waiting.record("A"))

    def test_a_result_that_arrived_while_parking_is_picked_up_in_the_same_worker(self):
        # 记下等待之前那一刻识别有了结论：提醒看到它还在跑、没放回队列；这一轮自己接着跑，不等每分钟的兜底。
        self.submit("A", ["W1"])
        real_park = waiting.park

        def park(recon_job, arguments, pending):
            self.finish("W1")  # 提醒在记下之前到：这时后台任务还在跑，不能放
            return real_park(recon_job, arguments, pending)
        with mock.patch.object(waiting, "park", side_effect=park):
            self.assertEqual(self.work(), 1)
        self.assertEqual(self.done, {"A"})
        self.assertEqual(self.passes["A"], 2)
        self.assertEqual(len(self.rq.enqueued), 1)

    def test_a_result_that_arrives_just_after_the_note_is_taken_does_not_start_a_second_run(self):
        # 等待刚记下、这一轮还没结束时提醒到了：提醒看到后台任务还在跑，不放；这一轮自己看到结论接着跑。
        self.submit("A", ["W1"])
        real = waiting._statuses
        fired = []

        def statuses(works):
            if not fired:
                fired.append(1)
                self.finish("W1")
            return real(works)
        with mock.patch.object(waiting, "_statuses", side_effect=statuses):
            self.work()
        self.assertEqual(self.done, {"A"})
        self.assertEqual(len(self.rq.enqueued), 1)

    def test_when_the_queue_redis_cannot_take_the_note_it_waits_in_place(self):
        self.submit("A", ["W1"])
        real_park = waiting.park
        calls = []

        def park(recon_job, arguments, pending):
            calls.append(1)
            if len(calls) == 1:
                self.redis.broken = True
                try:
                    return real_park(recon_job, arguments, pending)
                finally:
                    self.redis.broken = False
                    self.ocr["W1"] = "succeeded"
            return real_park(recon_job, arguments, pending)
        with mock.patch.object(waiting, "park", side_effect=park):
            self.work()
        self.assertEqual(self.done, {"A"})  # 没丢：原地等了一会儿再跑
        self.assertGreaterEqual(self.now[0], 1_000_000 + waiting.PAUSE_SECONDS)


class NoDuplicateRuns(Harness):
    def test_repeated_reminders_and_the_tick_put_it_back_once(self):
        self.submit("A", ["W1"])
        self.work()
        self.finish("W1")
        waiting.work_finished("W1", "cowork.recon_document", "succeeded", "")
        waiting.tick()
        self.assertEqual([job for job, _user in self.rq.enqueued].count("cowork-recon-A"), 2)  # 提交 + 放回一次

    def test_a_reminder_while_a_round_is_running_does_not_queue_another(self):
        self.submit("A", ["W1"])
        self.work()
        self.finish("W1")
        job = self.rq.jobs["cowork-recon-A"]
        self.assertEqual(job.status, "queued")
        # 记录被另一处（比如续跑）重新写回、任务已在排队：提醒与兜底都不再排
        self.redis.values[waiting._keys("A")["record"]] = json.dumps({"arguments": {}, "works": ["W1"], "due": 0})
        self.assertFalse(waiting.wake("A"))
        waiting.tick()
        self.assertEqual(len(self.rq.enqueued), 2)

    def test_a_round_killed_after_parking_is_put_back_once_by_whoever_comes_first(self):
        # 这一轮记下等待后、后台任务收尾前被杀（部署 / OOM）：RQ 判失败 + 弃置，同时还留着等待记录。
        for first in ("resume", "wake"):
            with self.subTest(first=first):
                self.setUp()
                self.submit("A", ["W1"])
                self.rq.queue.popleft()
                job = self.rq.jobs["cowork-recon-A"]
                job.status = "started"
                self.session.user = "u@example.com"
                jobs.run(**job.kwargs["kwargs"])
                job.status, job.exc_info = "failed", "Moved to FailedJobRegistry, due to AbandonedJobError"
                self.session.user = "Administrator"
                self.ocr["W1"] = "succeeded"
                steps = [lambda: self.resume("A"), lambda: waiting.tick()]
                for step in steps if first == "resume" else reversed(steps):
                    step()
                self.assertEqual([name for name, _user in self.rq.enqueued].count("cowork-recon-A"), 2)
                self.work()
                self.assertEqual(self.done, {"A"})

    def resume(self, recon_job):
        with mock.patch.object(jobs, "_recovered", return_value=None), \
                mock.patch.object(jobs, "_superseded", return_value=False), \
                mock.patch.object(jobs, "_state"), mock.patch.object(jobs.progress, "active_remove"), \
                mock.patch.object(jobs.progress, "active_add"), mock.patch.object(jobs, "_mark_latest"):
            frappe.db.exists.return_value = True
            return jobs.resume_if_abandoned(recon_job)

    def test_resume_only_once_and_it_starts_its_clocks_over(self):
        self.submit("A", ["W1"])
        self.rq.queue.popleft()
        job = self.rq.jobs["cowork-recon-A"]
        job.status = "started"
        self.session.user = "u@example.com"
        jobs.run(**job.kwargs["kwargs"])
        self.session.user = "Administrator"
        job.status, job.exc_info = "failed", "AbandonedJobError"
        self.assertEqual(self.resume("A"), jobs.RESUMED)
        self.assertIsNone(waiting.record("A"))
        self.assertEqual(self.redis.hashes, {})
        self.assertIsNone(self.resume("A"))  # 已在排队：不再续


class WaitingForRoom(Harness):
    def fake_pass(self, recon_job, kind, workspace, title, files, options="{}"):
        self.passes[recon_job] = self.passes.get(recon_job, 0) + 1

        def create():
            if self.room <= 0:
                from pearnly_core.execution.errors import QueueFull
                raise QueueFull("full")
            self.room -= 1
            return "W-" + recon_job
        admission.when_room(create, key=recon_job)
        self.done.add(recon_job)

    def test_each_finished_recognition_lets_the_oldest_waiting_one_in(self):
        self.room = 0
        for name in "ABC":
            self.submit(name, [])
        self.work()
        self.assertEqual(self.done, set())
        self.room = 1
        self.finish("other")  # 本站点任何一份识别结束：腾出名额
        self.work()
        self.assertEqual(self.done, {"A"})  # 先来先放，一次放一套
        self.room = 1
        waiting.tick()  # 提醒没送到也有每分钟的兜底
        self.work()
        self.assertEqual(self.done, {"A", "B"})

    def test_a_reconciliation_that_still_finds_no_room_keeps_its_place(self):
        self.room = 0
        for name in "ABC":
            self.submit(name, [])
        self.work()
        self.finish("other")  # 放 A 回来，但名额已被别处拿走
        self.work()
        self.assertEqual(self.done, set())
        self.room = 1
        self.finish("other2")
        self.work()
        self.assertEqual(self.done, {"A"})  # 仍是 A 先

    def test_no_room_until_the_limit_is_reported_busy(self):
        from pearnly_core.execution.errors import QueueFull

        self.room = 0
        self.submit("A", [])
        self.work()
        self.now[0] += admission.WAIT_SECONDS + 1
        waiting.tick()
        with self.assertRaises(QueueFull):
            self.work()


class ManyAtOnce(Harness):
    def test_forty_reconciliations_on_four_workers_all_finish_without_holding_a_worker(self):
        rng = random.Random(7)
        names = [f"R{index:02d}" for index in range(40)]
        users = [f"u{index % 5}@example.com" for index in range(40)]
        for name, user in zip(names, users):
            self.submit(name, [f"{name}-{part}" for part in range(rng.randint(1, 4))], user=user)
        rounds = 0
        while len(self.done) < len(names) and rounds < 2000:
            rounds += 1
            self.work(limit=4)  # 4 个 long 进程
            self.assertFalse([job for job in self.rq.jobs.values() if job.status == "started"])  # 没有守着等的
            pending = [work for work, status in self.ocr.items() if status not in waiting.TERMINAL]
            for work in rng.sample(pending, min(len(pending), rng.randint(0, 6))):
                self.finish(work)  # 谷歌那边读完（顺序随机）
            if rounds % 7 == 0:
                waiting.tick()
        self.assertEqual(self.done, set(names))
        self.assertEqual(set(self.busy), {1})
        for name in names:
            # 每份识别最多让它多跑一轮：交出 → 有结论 → 接着跑（再加最后出结果那一轮）
            self.assertLessEqual(self.passes[name], len(self.plans[name]) + 1)
        self.assertEqual(self.redis.zsets.get(waiting._keys()["parked"], {}), {})
        self.assertFalse(self.logged)


class StatusWhileParked(Harness):
    def test_a_parked_reconciliation_still_reads_as_running_after_the_cache_was_cleared(self):
        self.submit("A", ["W1"])
        self.work()
        self.session.user = "u@example.com"
        frappe.db.exists.return_value = False
        self.assertEqual(jobs._recovered("A")["state"], "running")
        self.session.user = "other@example.com"
        self.assertIsNone(jobs._recovered("A"))  # 只认本人发起的


class SlotsRunAsTheSubmitter(Harness):
    def test_a_reconciliation_let_in_by_someone_elses_release_runs_as_its_own_user(self):
        frappe.conf.pearnly_recon_max_running = 1
        self.submit("A", ["W1"], user="a@example.com")
        self.submit("B", [], user="b@example.com")
        self.work(limit=1)  # A 拿到名额、等识别、让出名额 → B 被放进来
        self.work()
        self.assertIn("B", self.done)
        self.assertEqual(self.passes["B"], 1)


class ReadingWaits(unittest.TestCase):
    """在对账后台任务里（``waiting.parking``）等账单 / 发票：不睡，抛 ``Pending`` 交还进程；时钟跨轮保留。"""

    def setUp(self):
        self.redis = FakeRedis()
        self.now = [5_000.0]
        for patcher in (mock.patch.object(waiting, "_conn", return_value=self.redis),
                        mock.patch.object(frappe, "local", SimpleNamespace(site="t.example")),
                        mock.patch.object(waiting.time, "time", side_effect=lambda: self.now[0]),
                        mock.patch.object(waiting.time, "sleep", side_effect=AssertionError("must not sleep")),
                        mock.patch.object(document_ocr, "_", lambda text: text),
                        mock.patch.object(document_ocr.frappe, "throw", side_effect=RuntimeError)):
            patcher.start()
            self.addCleanup(patcher.stop)

    @contextmanager
    def document(self, status, since_first=None, since_done=None, result=None):
        db = mock.MagicMock()
        db.get_value.return_value = frappe._dict(status=status, last_code="")
        with mock.patch.object(document_ocr.frappe, "db", db), \
                mock.patch.object(document_ocr.core_work, "call_progress", return_value={
                    "W1": {"done": 1, "since_first": since_first, "since_done": since_done}}), \
                mock.patch.object(document_ocr.core_work, "saved_checkpoint", return_value=result), \
                waiting.parking("J"):
            yield

    def test_a_queued_read_parks_until_it_starts_and_the_reading_clock_starts_then(self):
        with self.document("queued"), self.assertRaises(waiting.Pending) as caught:
            document_ocr.wait("W1")
        self.assertEqual((caught.exception.works, caught.exception.start), (["W1"], True))
        self.assertEqual(caught.exception.due, 5_000 + document_ocr.QUEUE_LIMIT)
        self.now[0] += 600  # 排了 10 分钟，读了 2 分钟
        with self.document("running", since_first=120), self.assertRaises(waiting.Pending) as caught:
            document_ocr.wait("W1")
        self.assertFalse(caught.exception.start)
        self.assertEqual(caught.exception.due, 5_000 + 480 + document_ocr.WAIT_SECONDS)

    def test_the_reading_limit_is_judged_on_the_round_after_it(self):
        with self.document("running", since_first=0), self.assertRaises(waiting.Pending):
            document_ocr.wait("W1")
        self.now[0] += document_ocr.WAIT_SECONDS + 1
        with self.document("running", since_first=document_ocr.WAIT_SECONDS, since_done=60), \
                self.assertRaises(waiting.Pending) as caught:
            document_ocr.wait("W1")  # 一分钟前还有页读完：再看一分钟
        self.assertEqual(caught.exception.due, self.now[0] + document_ocr.PROGRESS_CHECK_SECONDS)
        self.now[0] += document_ocr.PROGRESS_WINDOW + 61
        with self.document("running", since_first=document_ocr.WAIT_SECONDS, since_done=document_ocr.PROGRESS_WINDOW + 61), \
                self.assertRaises(RuntimeError):
            document_ocr.wait("W1")  # 五分钟没有新进展：超时

    def test_a_finished_read_returns_its_result_without_parking(self):
        with self.document("succeeded", result={"body": {"legacy": {"pages": [1]}}}):
            self.assertEqual(document_ocr.wait("W1"), {"pages": [1]})


class SubmitOnce(unittest.TestCase):
    """每轮从头跑：交过的识别直接用，不再查余额、不再冻结设置（读完扣了费的余额不能再拿来判一次）。"""

    def test_a_document_already_submitted_is_reused_without_a_balance_check(self):
        db = mock.MagicMock()
        db.exists.return_value = True
        with mock.patch.object(document_ocr.frappe, "db", db), \
                mock.patch.object(document_ocr.intake, "balance") as balance, \
                mock.patch.object(document_ocr, "inspect_content") as inspect, \
                mock.patch.object(document_ocr, "_progress"):
            name = document_ocr.submit("J", "left0", "F1", "a.jpg", b"x", "bank_statement", "u@example.com")
        self.assertEqual(name, document_ocr.core_work.name_for(document_ocr.KIND, "J:left0:bank_statement"))
        balance.assert_not_called()
        inspect.assert_not_called()

    def test_invoices_check_the_balance_once_and_only_create_the_missing_ones(self):
        info = {"kind": "pdf", "page_count": 2, "sha256": "s"}
        names = [invoice_ocr.core_work.name_for(invoice_ocr.KIND, f"J:{index}") for index in range(3)]
        created = []
        with mock.patch.object(invoice_ocr, "inspect_content", return_value=info), \
                mock.patch.object(invoice_ocr.ocr_settings, "snapshot", return_value={"billing_tier": "t"}), \
                mock.patch.object(invoice_ocr.frappe, "get_all", return_value=names[:2]), \
                mock.patch.object(invoice_ocr.intake, "balance") as balance, \
                mock.patch.object(invoice_ocr.progress, "advance"), \
                mock.patch.object(invoice_ocr, "_create", side_effect=lambda ref, args, user: created.append(ref) or "new"):
            works, rejected = invoice_ocr.submit("J", [(f"F{i}", f"{i}.pdf", b"x") for i in range(3)], "u@example.com")
        balance.assert_not_called()
        self.assertEqual(created, ["J:2"])
        self.assertEqual([work for work, _name in works], names[:2] + ["new"])
        self.assertEqual(rejected, [])


if __name__ == "__main__":
    unittest.main()
