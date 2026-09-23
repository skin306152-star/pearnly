"""Real Redis regression for shared LINE usage; task-owned offline lab only.

Run in the dms-flow-lab bench (PEARNLY_DMS_FLOW_LAB=1):
    env/bin/python /workspace/scripts/test-line-usage-redis.py

No Site/SQL connection or LINE/DMS/OCR HTTP. Uses the lab's internal service
redis:6379 DB15 and unique per-test hash keys, deleted by exact name in cleanup.
Runs actual _count/usage with pinned Frappe RedisWrapper, including concurrent
atomic increments, historical raw integers and two Site namespaces. A missing
lab marker, unavailable Redis, failed assertion or failed cleanup exits nonzero.
"""
from concurrent.futures import ThreadPoolExecutor
import os
import unittest
from unittest.mock import patch
import uuid

import frappe
from frappe.utils.redis_wrapper import RedisWrapper
from pearnly_core import line


class LineUsageRedis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("PEARNLY_DMS_FLOW_LAB") != "1":
            raise RuntimeError("Run only inside the task-owned dms-flow-lab bench")
        cls.redis = RedisWrapper(host="redis", port=6379, db=15, socket_timeout=3)
        cls.redis.ping()

    def setUp(self):
        self.logical = f"pearnly:line:usage:test:{uuid.uuid4().hex}"
        self.sites = ("line-usage-test-a", "line-usage-test-b")
        self.keys = []
        for site in self.sites:
            self.select_site(site)
            key = self.redis.make_key(self.logical)
            # Include the old, erroneously double-prefixed key in leak checks.
            self.keys.extend((key, self.redis.make_key(key)))
        self.assertEqual(self.redis.execute_command("EXISTS", *self.keys), 0)
        self.addCleanup(self.clean_keys)
        for guard in (
            patch.object(line, "USAGE_HASH", self.logical),
            patch.object(frappe, "cache", return_value=self.redis),
            patch.object(frappe.utils, "now", return_value="2026-09-24 10:00:00"),
        ):
            guard.start()
            self.addCleanup(guard.stop)
        self.select_site(self.sites[0])

    def select_site(self, name):
        frappe.local.conf = frappe._dict(db_name=name)

    def clean_keys(self):
        self.redis.execute_command("DEL", *self.keys)
        self.assertEqual(self.redis.execute_command("EXISTS", *self.keys), 0,
                         "Temporary usage hashes were not removed")

    def raw(self):
        return self.redis.execute_command("HGETALL", self.redis.make_key(self.logical))

    def seed_history(self):
        # Match pre-fix HINCRBY's persisted wire format, not wrapper pickle data.
        self.redis.execute_command("HSET", self.redis.make_key(self.logical),
                                   "2026-09-22|push_ok", "12", "2026-09-23|reply_ok", "7")

    def test_actual_count_roundtrips_raw_integer_hash(self):
        line._count("reply_ok")
        line._count("reply_ok")
        line._count("push_ok")
        self.assertEqual(self.raw(), {b"2026-09-24|reply_ok": b"2", b"2026-09-24|push_ok": b"1"})
        self.assertEqual(line.usage(), {"2026-09-24": {"reply_ok": 2, "push_ok": 1}})

    def test_existing_history_is_preserved_and_read_is_nonmutating(self):
        self.seed_history()
        line._count("push_ok")
        before = self.raw()
        self.assertEqual(line.usage(0), {
            "2026-09-22": {"push_ok": 12}, "2026-09-23": {"reply_ok": 7},
            "2026-09-24": {"push_ok": 1},
        })
        self.assertEqual(self.raw(), before)
        self.assertEqual(self.redis.execute_command("EXISTS", self.keys[1]), 0)

    def test_day_window_and_empty_hash_keep_existing_contract(self):
        self.assertEqual(line.usage(), {})
        self.seed_history()
        self.assertEqual(line.usage(1), {"2026-09-23": {"reply_ok": 7}})
        self.assertEqual(line.usage(0), line.usage(-1))
        self.assertEqual(len(line.usage(0)), 2)

    def test_site_namespaces_do_not_share_counts(self):
        line._count("reply_ok")
        self.select_site(self.sites[1])
        self.assertEqual(line.usage(), {})
        line._count("push_ok")
        line._count("push_ok")
        self.assertEqual(line.usage(), {"2026-09-24": {"push_ok": 2}})
        self.select_site(self.sites[0])
        self.assertEqual(line.usage(), {"2026-09-24": {"reply_ok": 1}})
        self.assertNotEqual(self.keys[0], self.keys[2])

    def test_concurrent_real_count_increments_are_not_lost(self):
        def increment(_):
            self.select_site(self.sites[0])  # Frappe's conf is local to each thread.
            line._count("reply_ok")
        with ThreadPoolExecutor(max_workers=8) as workers:
            list(workers.map(increment, range(200)))
        self.assertEqual(line.usage(), {"2026-09-24": {"reply_ok": 200}})
        self.assertEqual(self.raw(), {b"2026-09-24|reply_ok": b"200"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
