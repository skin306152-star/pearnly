"""LINE 事件按用户一条道（`pearnly_core.line_lane`，④ 11-28）与会话根函数（`line_session.serialized / set_session / update_session`）。

Run with Frappe installed. No Site, Redis or network: the handful of lane / event statements run against an in-memory table,
LINE requests are recorded. Real threads against MariaDB are the lab flow's job (`scripts/flows/line_lane_lab.py`).
"""
import base64
import hashlib
import hmac
import json
import threading
import types
import unittest
from contextlib import ExitStack, nullcontext
from datetime import datetime, timedelta
from unittest.mock import patch

import frappe

from pearnly_core import line, line_session
from pearnly_core.line_lane import lane as line_lane

USER = "U" + "a" * 32
OTHER = "U" + "b" * 32
SECRET = "lane-secret"  # scan: fake


class Cursor:
    rowcount = 0


class LaneDB:
    """Only the statements line_lane issues; anything else fails the test."""

    def __init__(self):
        self.events, self.lanes, self._cursor = {}, {}, Cursor()
        self.commits = self.rollbacks = 0
        self.log = []

    # native IO
    def exists(self, doctype, name):
        table = self.events if doctype == line_lane.EVENT else self.lanes
        return name in table

    def get_value(self, doctype, name, fields, as_dict=False):
        row = self.events.get(name) or {}
        return frappe._dict({key: row.get(key) for key in fields}) if as_dict else None

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1
        self.log.append("rollback")

    def sql(self, query, values=(), as_dict=False):
        q = " ".join(query.split())
        cur = self._cursor
        if q.startswith("update `tabPearnly LINE Lane` set holder=%s, lease_until=%s"):
            holder, lease, _now, name, now = values
            lane = self.lanes[name]
            cur.rowcount = 0
            if lane.get("lease_until") is None or lane["lease_until"] < now:
                lane.update(holder=holder, lease_until=lease)
                cur.rowcount = 1
            return ()
        if q.startswith("update `tabPearnly LINE Lane` set lease_until=%s"):
            lease, _now, name, holder = values
            lane = self.lanes.get(name) or {}
            cur.rowcount = int(lane.get("holder") == holder)
            if cur.rowcount:
                lane["lease_until"] = lease
            return ()
        if q.startswith("update `tabPearnly LINE Lane` set holder=null"):
            _now, name, holder = values
            lane = self.lanes.get(name) or {}
            if lane.get("holder") == holder:
                lane.update(holder=None, lease_until=None)
            return ()
        if q.startswith("select name from `tabPearnly LINE Event` where line_user_id=%s and status='queued'"):
            rows = sorted((row for row in self.events.values()
                           if row["line_user_id"] == values[0] and row["status"] == "queued"),
                          key=lambda row: (row["line_timestamp"], row["set_index"], row["arrived_at"], row["name"]))
            return [(rows[0]["name"],)] if rows else []
        if q.startswith("update `tabPearnly LINE Event` set status='failed', detail='interrupted"):
            _now, user, now = values
            hit = [row for row in self.events.values() if row["line_user_id"] == user
                   and row["status"] == "processing" and row["lease_until"] and row["lease_until"] < now]
            for row in hit:
                row.update(status="failed", detail="interrupted: not replayed", content=None, lease_until=None)
            cur.rowcount = len(hit)
            return ()
        if q.startswith("update `tabPearnly LINE Event` set status=%s, attempts=attempts+1"):
            status, lease, _now, name = values
            row = self.events[name]
            cur.rowcount = int(row["status"] == "queued")
            if cur.rowcount:
                row.update(status=status, lease_until=lease, attempts=row["attempts"] + 1)
            return ()
        if q.startswith("update `tabPearnly LINE Event` set status=%s, detail=%s, content=null"):
            status, detail, _now, name = values
            row = self.events[name]
            if row["status"] == "processing":
                row.update(status=status, detail=detail, content=None, lease_until=None)
            return ()
        if q.startswith("update `tabPearnly LINE Event` set content=null where name=%s"):
            self.events[values[0]]["content"] = None
            return ()
        raise AssertionError("Unexpected SQL: " + q)


class Doc(frappe._dict):
    def insert(self, **kwargs):
        db = frappe.db
        if self.doctype == line_lane.EVENT:
            name = kwargs.get("set_name") or self.event_id
            if name in db.events:
                return self
            db.events[name] = {"name": name, "lease_until": None, "detail": "", **{
                key: value for key, value in self.items() if key != "doctype"}}
        else:
            db.lanes.setdefault(self.line_user_id, {"holder": None, "lease_until": None})
        return self


class LaneCase(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.db = LaneDB()
        self.dispatched, self.pushed, self.logged = [], [], []
        self.local = types.SimpleNamespace(site="lane.test", lang="th", response=frappe._dict(), request=None, db=self.db)
        self.patch(frappe, "local", self.local, create=True)
        self.patch(frappe, "db", self.db, create=True)
        self.patch(frappe, "get_doc", lambda data: Doc(data))
        self.patch(frappe, "set_user", lambda user: setattr(self, "user", user))
        self.patch(frappe, "session", types.SimpleNamespace(user="Guest"), create=True)
        self.patch(frappe, "log_error", lambda **kwargs: (self.db.log.append("log_error"), self.logged.append(kwargs)))
        self.patch(frappe, "_", lambda text, *a, **k: text)
        self.patch(line, "user_language", lambda user, default=None: "th")
        self.patch(line, "push", lambda to, messages, retry_key="": self.pushed.append((to, messages, retry_key)) or True)
        self.patch(line, "_count", lambda kind: self.dispatched.append(("count", kind)))
        self.patch(line_session, "serialized", lambda user, **kw: nullcontext())
        self.patch(line, "_dispatch_with_feedback", self.dispatch)
        self.fail_on = set()

    def patch(self, owner, name, value, **kwargs):
        return self.stack.enter_context(patch.object(owner, name, value, **kwargs))

    def dispatch(self, event, waited=0.0):
        mid = (event.get("message") or {}).get("id") or (event.get("postback") or {}).get("data")
        self.dispatched.append(mid)
        if mid in self.fail_on:
            self.db.log.append("handler-raised")
            raise RuntimeError("handler broke")
        return True

    def event(self, mid, ts, user=USER, index=None, **extra):
        message = {"id": mid, "type": "image"}
        if index is not None:
            message["imageSet"] = {"id": "set", "index": index, "total": 3}
        return {"type": "message", "webhookEventId": "evt-" + mid, "timestamp": ts, "replyToken": "rt-" + mid,
                "source": {"type": "user", "userId": user}, "message": message, **extra}

    def queue(self, *events, arrived=None):
        arrived = arrived or line_lane.utcnow()
        for i, event in enumerate(events):
            line_lane.store(event, line_lane.user_of(event), arrived + timedelta(microseconds=i))


class Receiving(LaneCase):
    def test_only_the_fields_handlers_read_are_kept(self):
        event = self.event("m1", 1000, index=2)
        event["message"].update(contentProvider={"type": "line"}, duration=12, text="hi")
        event.update(deliveryContext={"isRedelivery": True}, extra="x")
        content = line_lane.content_of(event)
        self.assertEqual(set(content), {"type", "timestamp", "webhookEventId", "replyToken", "source", "message",
                                        "deliveryContext"})
        self.assertNotIn("contentProvider", content["message"])
        self.assertEqual(content["message"]["imageSet"]["index"], 2)

    def test_a_redelivered_event_id_is_stored_once(self):
        self.assertTrue(line_lane.store(self.event("m1", 1000), USER))
        self.assertFalse(line_lane.store(self.event("m1", 1000), USER))
        row = self.db.events["evt-m1"]
        self.assertEqual((row["status"], row["line_timestamp"], row["set_index"]), ("queued", 1000, 0))

    def test_group_events_have_no_lane(self):
        self.assertEqual(line_lane.user_of({"source": {"type": "group", "userId": USER}}), "")
        self.assertEqual(line_lane.user_of({"source": {"userId": USER}}), USER)


class Draining(LaneCase):
    def test_events_run_one_by_one_in_line_time_then_image_set_order(self):
        self.queue(self.event("c", 3000), self.event("b2", 2000, index=2), self.event("a", 1000),
                   self.event("b1", 2000, index=1))
        line_lane.start(USER)  # no after_response: drained in place
        self.assertEqual(self.dispatched, ["a", "b1", "b2", "c"])
        self.assertEqual({row["status"] for row in self.db.events.values()}, {"done"})
        self.assertTrue(all(row["content"] is None for row in self.db.events.values()))
        self.assertIsNone(self.db.lanes[USER]["holder"])

    def test_a_held_lane_is_left_to_its_holder(self):
        self.queue(self.event("a", 1000))
        line_lane.acquire(USER, "other-run")
        line_lane.start(USER)
        self.assertEqual(self.dispatched, [])
        self.assertEqual(self.db.events["evt-a"]["status"], "queued")

    def test_a_failing_handler_rolls_back_before_logging_and_the_next_event_still_runs(self):
        self.fail_on.add("a")
        self.queue(self.event("a", 1000), self.event("b", 2000))
        line_lane.start(USER)
        self.assertEqual(self.dispatched, ["a", "b"])
        self.assertEqual(self.db.events["evt-a"]["status"], "failed")
        self.assertEqual(self.db.events["evt-b"]["status"], "done")
        order = self.db.log
        self.assertLess(order.index("handler-raised"), order.index("rollback"))
        self.assertLess(order.index("rollback"), order.index("log_error"))

    def test_an_interrupted_event_is_marked_failed_and_never_replayed(self):
        self.queue(self.event("a", 1000), self.event("b", 2000))
        self.db.events["evt-a"].update(status="processing", lease_until=line_lane.utcnow() - timedelta(seconds=1))
        line_lane.start(USER)
        self.assertNotIn("a", self.dispatched)
        self.assertIn(("count", "lane_interrupted"), self.dispatched)
        self.assertEqual(self.db.events["evt-a"]["status"], "failed")
        self.assertEqual(self.db.events["evt-b"]["status"], "done")

    def test_an_event_queued_too_long_is_expired_and_the_sender_is_told_once(self):
        self.queue(self.event("old", 1000), arrived=line_lane.utcnow() - timedelta(seconds=line_lane.EXPIRE_SECONDS + 5))
        self.queue(self.event("new", 2000))
        line_lane.start(USER)
        self.assertEqual([d for d in self.dispatched if isinstance(d, str)], ["new"])
        self.assertIn(("count", "lane_expired"), self.dispatched)
        self.assertEqual(self.db.events["evt-old"]["status"], "expired")
        self.assertEqual(len(self.pushed), 1)
        to, messages, key = self.pushed[0]
        self.assertEqual((to, messages[0]["text"]), (USER, "Please try again later"))
        self.assertTrue(key)

    def test_an_event_that_lands_while_the_lane_is_released_is_picked_up(self):
        self.queue(self.event("a", 1000))
        real_release = line_lane.release

        def release_then_arrive(user, holder):
            real_release(user, holder)
            if "evt-late" not in self.db.events:
                self.queue(self.event("late", 5000))

        with patch.object(line_lane, "release", release_then_arrive):
            line_lane.start(USER)
        self.assertEqual(self.dispatched, ["a", "late"])

    def test_the_lane_stops_when_another_run_took_it_over(self):
        self.queue(self.event("a", 1000), self.event("b", 2000))

        def steal(event, waited=0.0):
            self.dispatched.append(event["message"]["id"])
            self.db.lanes[USER].update(holder="sweeper")
            return True

        with patch.object(line, "_dispatch_with_feedback", steal):
            line_lane.start(USER)
        self.assertEqual(self.dispatched, ["a"])
        self.assertEqual(self.db.events["evt-b"]["status"], "queued")

    def test_after_a_real_request_the_lane_drains_in_its_own_thread(self):
        callbacks = []
        self.local.request = types.SimpleNamespace(after_response=types.SimpleNamespace(add=callbacks.append))
        self.queue(self.event("a", 1000))
        started = []
        with patch.object(line_lane, "_spawn", lambda *args: started.append(args)):
            line_lane.start(USER)
            self.assertEqual((self.dispatched, started), ([], []))  # nothing runs before the response is out
            callbacks[0]()
        site, _path, user, holder = started[0]
        self.assertEqual((site, user, self.db.lanes[USER]["holder"]), ("lane.test", USER, holder))

    def test_a_full_thread_pool_hands_the_lane_to_the_short_queue(self):
        queued = []
        self.patch(frappe, "enqueue", lambda method, **kw: queued.append((method, kw)))
        line_lane.acquire(USER, "h")
        slots = threading.BoundedSemaphore(1)
        slots.acquire()
        with patch.object(line_lane, "_slots", slots):
            line_lane._spawn("lane.test", ".", USER, "h")
        self.assertIsNone(self.db.lanes[USER]["holder"])
        self.assertEqual(queued[0][0], "pearnly_core.line_lane.lane.run")
        self.assertTrue(queued[0][1]["deduplicate"])


class Webhook(LaneCase):
    def post(self, events, signature=None):
        body = json.dumps({"events": events}).encode()
        sig = signature or base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()
        self.patch(frappe, "request", types.SimpleNamespace(get_data=lambda: body), create=True)
        self.patch(frappe, "get_request_header", lambda name: sig if name == "X-Line-Signature" else None)
        self.patch(frappe, "conf", {"pearnly_line_channel_secret": SECRET, "pearnly_line_channel_id": "1"})
        return line.webhook()

    def test_one_to_one_events_are_only_stored_then_drained_per_user(self):
        starts = []
        with patch.object(line_lane, "start", starts.append):
            result = self.post([self.event("a", 1000), self.event("b", 2000, user=OTHER), self.event("c", 3000)])
        self.assertEqual(result, {"ok": True})
        self.assertEqual(self.dispatched, [])
        self.assertEqual(starts, [USER, OTHER])
        self.assertEqual({row["status"] for row in self.db.events.values()}, {"queued"})
        arrived = [self.db.events["evt-a"]["arrived_at"], self.db.events["evt-c"]["arrived_at"]]
        self.assertLess(arrived[0], arrived[1])

    def test_a_bad_signature_stores_nothing(self):
        result = self.post([self.event("a", 1000)], signature="bad")
        self.assertEqual(result, {"ok": False, "error": "bad_signature"})
        self.assertEqual(self.db.events, {})

    def test_a_redelivered_event_is_not_queued_again(self):
        starts = []
        with patch.object(line_lane, "start", starts.append):
            self.post([self.event("a", 1000)])
            self.db.events["evt-a"]["status"] = "done"
            self.post([self.event("a", 1000, deliveryContext={"isRedelivery": True})])
        self.assertEqual(starts, [USER])
        self.assertEqual(self.db.events["evt-a"]["status"], "done")


class Loading(unittest.TestCase):
    def test_an_event_that_waited_in_the_lane_shows_the_dots_at_once(self):
        seen = []
        with patch.object(line, "_feedback_begin", lambda event, delay=line.FEEDBACK_DELAY: seen.append(delay)), \
                patch.object(line, "_dispatch", lambda event: True), patch.object(line, "_feedback_settle", lambda: None), \
                patch.object(frappe, "local", types.SimpleNamespace(), create=True):
            line._dispatch_with_feedback({})
            line._dispatch_with_feedback({}, waited=5)
        self.assertEqual(seen[0], line.FEEDBACK_DELAY)
        self.assertLessEqual(seen[1], 0)


class SessionDB:
    """`Pearnly LINE Session` with a version column, plus a read view that can lag behind (MariaDB without snapshot isolation)."""

    def __init__(self):
        self.rows, self.version, self.reads = {}, 0, []

    def put(self, user, state, payload):
        self.version += 1
        self.rows[user] = {"name": user, "line_user_id": user, "state": state, "payload": json.dumps(payload),
                           "expires_at": "2999-01-01 00:00:00", "modified": "v%d" % self.version, "creation": "c"}

    def exists(self, doctype, name):
        return name in self.rows

    def sql(self, query, values, as_dict=False):
        self.reads.append("for update" in query)
        row = self.rows.get(values[0])
        return [frappe._dict(row)] if row else []

    def get_value(self, doctype, filters, fields="name", as_dict=False):
        user = filters.get("line_user_id") if isinstance(filters, dict) else filters
        row = self.rows.get(user)
        if not row:
            return None
        return row.get(fields) if isinstance(fields, str) else tuple(row.get(f) for f in fields)

    def set_value(self, doctype, name, values):
        self.version += 1
        self.rows[name].update(values, modified="v%d" % self.version)

    def delete(self, doctype, filters):
        self.rows.pop(filters["line_user_id"], None)

    def commit(self):
        pass


class SessionRoot(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.db = SessionDB()
        self.locks = []
        self.local = types.SimpleNamespace()
        self.stack.enter_context(patch.object(frappe, "local", self.local, create=True))
        self.stack.enter_context(patch.object(frappe, "db", self.db, create=True))
        store = types.SimpleNamespace(make_key=lambda key: key, lock=self.lock)
        self.stack.enter_context(patch.object(line_session.pearnly_state, "store", lambda: store))
        self.stack.enter_context(patch.object(frappe.utils, "get_datetime", lambda value=None: (
            datetime.fromisoformat(str(value)) if value else datetime(2026, 11, 28))))
        self.stack.enter_context(patch.object(line_session, "_expires_at", lambda ttl: "2999-01-01 00:00:00"))

    def lock(self, key, timeout, blocking_timeout):
        test = self

        class Lock:
            def acquire(self):
                test.locks.append(("acquire", key))
                return True

            def release(self):
                test.locks.append(("release", key))

        return Lock()

    def stale_doc(self, user):
        """A normal read from an older read view: the version it saw is behind the row."""
        old = dict(self.db.rows[user])
        doc = types.SimpleNamespace(**old)
        return doc

    def test_the_block_is_reentrant_and_reads_with_a_lock_inside_only(self):
        self.db.put(USER, "s", {"a": 1})
        with patch.object(frappe, "get_doc", lambda dt, name: types.SimpleNamespace(**self.db.rows[name])):
            line_session.get_session(USER)
            with line_session.serialized(USER):
                with line_session.serialized(USER):
                    line_session.get_session(USER)
                line_session.get_session(USER)
        self.assertEqual(self.db.reads, [True, True])  # outside: plain get_value + get_doc, no SQL
        self.assertEqual([kind for kind, _ in self.locks], ["acquire", "release"])

    def test_a_write_after_a_stale_read_is_refused_not_silently_overwriting(self):
        self.db.put(USER, "s", {"mine": 1})
        stale = self.stale_doc(USER)
        with patch.object(frappe, "get_doc", lambda dt, name: stale):
            session = line_session.get_session(USER)  # normal read, records v1
        self.db.put(USER, "s", {"mine": 1, "theirs": 2})  # someone else commits v2
        with self.assertRaises(frappe.TimestampMismatchError):
            line_session.set_session(USER, "s", {**session["payload"], "more": 3})
        self.assertIn("theirs", json.loads(self.db.rows[USER]["payload"]))

    def test_inside_the_block_the_latest_value_is_read_and_written_back(self):
        self.db.put(USER, "s", {"a": 1})
        self.db.put(USER, "s", {"a": 1, "b": 2})
        with line_session.serialized(USER):
            session = line_session.get_session(USER)
            line_session.set_session(USER, "s", {**session["payload"], "c": 3})
            again = line_session.get_session(USER)
            line_session.set_session(USER, "s", {**again["payload"], "d": 4})
        self.assertEqual(json.loads(self.db.rows[USER]["payload"]), {"a": 1, "b": 2, "c": 3, "d": 4})

    def test_update_session_changes_under_the_lock(self):
        self.db.put(USER, "s", {"queue": ["x"]})
        out = line_session.update_session(USER, lambda s: ("s", {"queue": s["payload"]["queue"] + ["y"]}))
        self.assertEqual(out["payload"]["queue"], ["x", "y"])
        self.assertEqual(json.loads(self.db.rows[USER]["payload"])["queue"], ["x", "y"])
        untouched = line_session.update_session(USER, lambda s: None)
        self.assertEqual(untouched["payload"]["queue"], ["x", "y"])
        with patch.object(line_session, "clear_session", lambda user: self.db.rows.pop(user)):
            self.assertIsNone(line_session.update_session(USER, lambda s: line_session.CLEAR))
        self.assertNotIn(USER, self.db.rows)


if __name__ == "__main__":
    unittest.main()
