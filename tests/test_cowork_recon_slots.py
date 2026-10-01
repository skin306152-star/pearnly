"""对账按租户限流（2026-10-01 生产压测 pB）。离线：Redis 用替身，不连站点。"""
import json
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import mock

from pearnly_cowork.recon_center import jobs, slots


class FakeRedis:
    def __init__(self):
        self.zsets, self.values = {}, {}

    @contextmanager
    def _held(self):
        yield

    def lock(self, name, timeout=None, blocking_timeout=None):
        fake = self

        class Lock:
            def acquire(self):
                return True

            def release(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False
        return Lock()

    def zscore(self, key, member):
        return self.zsets.get(key, {}).get(member)

    def zcard(self, key):
        return len(self.zsets.get(key, {}))

    def zadd(self, key, mapping, nx=False):
        target = self.zsets.setdefault(key, {})
        for member, score in mapping.items():
            if not (nx and member in target):
                target[member] = score

    def zrem(self, key, member):
        self.zsets.get(key, {}).pop(member, None)

    def zrange(self, key, start, end):
        ordered = sorted(self.zsets.get(key, {}).items(), key=lambda item: item[1])
        members = [member.encode() for member, _score in ordered]
        return members[start:] if end == -1 else members[start:end + 1]

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    def delete(self, key):
        self.values.pop(key, None)


class Slots(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.requeued, self.statuses = [], {}
        clock = iter(range(1, 1000))
        patches = [
            mock.patch.object(slots, "_conn", return_value=self.redis),
            mock.patch.object(slots.frappe, "local", SimpleNamespace(site="t.example"), create=True),
            mock.patch.object(slots.frappe, "session", SimpleNamespace(user="u@example.com"), create=True),
            mock.patch.object(slots.frappe, "conf", {"pearnly_recon_max_running": 2}, create=True),
            mock.patch.object(slots.time, "time", side_effect=lambda: next(clock)),
            mock.patch.object(jobs, "requeue",
                              side_effect=lambda job, args, user=None: self.requeued.append((job, args))),
            mock.patch("frappe.utils.background_jobs.get_job",
                       side_effect=lambda job_id: SimpleNamespace(get_status=lambda refresh=False: self.statuses[job_id])
                       if job_id in self.statuses else None),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def args(self, job):
        return {"recon_job": job, "kind": "bank", "workspace": "ws", "title": job, "files": {}, "options": "{}"}

    def test_a_site_runs_at_most_its_limit_and_queues_the_rest(self):
        self.assertTrue(slots.acquire("a", self.args("a")))
        self.assertTrue(slots.acquire("b", self.args("b")))
        self.assertFalse(slots.acquire("c", self.args("c")))
        self.assertFalse(slots.acquire("d", self.args("d")))
        self.assertEqual(self.requeued, [])

    def test_a_finished_one_lets_the_oldest_waiting_in_with_its_own_arguments(self):
        for job in "abcd":
            slots.acquire(job, self.args(job))
        slots.release("a")
        self.assertEqual(self.requeued, [("c", self.args("c"))])
        # 被放进来的那套开跑时看到自己已有名额，直接跑
        self.assertTrue(slots.acquire("c", self.args("c")))
        slots.release("b")
        self.assertEqual([job for job, _args in self.requeued], ["c", "d"])

    def test_the_tick_frees_slots_held_by_jobs_that_are_gone(self):
        for job in "abc":
            slots.acquire(job, self.args(job))
        self.statuses = {"cowork-recon-a": "failed", "cowork-recon-b": "started"}
        slots.tick()
        self.assertEqual(self.requeued, [("c", self.args("c"))])

    def test_without_redis_a_reconciliation_still_runs(self):
        with mock.patch.object(slots, "_conn", side_effect=RuntimeError("no redis")):
            self.assertTrue(slots.acquire("x", self.args("x")))

    def test_run_releases_its_slot_even_when_it_fails(self):
        with mock.patch.object(slots, "acquire", return_value=True), \
                mock.patch.object(slots, "release") as release, \
                mock.patch.object(jobs, "_run", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                jobs.run("j", "bank", "ws", "t", {"left": [], "right": []})
        release.assert_called_once_with("j")

    def test_a_queued_reconciliation_does_not_run_now(self):
        with mock.patch.object(slots, "acquire", return_value=False), \
                mock.patch.object(jobs, "_run") as inner, mock.patch.object(slots, "release") as release:
            jobs.run("j", "bank", "ws", "t", {"left": [], "right": []})
        inner.assert_not_called()
        release.assert_not_called()


if __name__ == "__main__":
    unittest.main()
