"""LINE 按用户排队第二期（④ 11-28）：后台路径在外部动作之后加锁重读、只并自己那几项，不拿旧读到的会话整份写回。

- DMS 身份证识别完（`intake.flow._recognize_image`）：识别结果先进执行器检查点；写会话在串行块里加锁读此刻的会话，仍在收这张图才并进去，
  识别期间刚到的手机号留着、接着查重；换了新图按最后一张为准；检查点里有结果的重跑不再付费识别。
- ERP 识别完出题（`line_questions.begin`）整段在这个人的串行块里。

Run with Frappe installed. No Site, Redis or network.
"""
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

import frappe

from pearnly_dms.intake import flow
from pearnly_erp_portal.integrations import line_questions

USER = "U" + "c" * 32
OCR = {"id_card": {"people_id": "1100000000001", "first_name": "lab"}, "needs_review": False}


class IdCardMerge(unittest.TestCase):
    def setUp(self):
        self.session = {"state": "collecting", "payload": {"mode": "customer", "pending_image": "M1"}}
        self.written, self.pushed, self.dedup, self.paid, self.saved = [], [], [], [], {}
        patches = [
            patch.object(frappe, "db", Mock(), create=True),
            patch.object(flow, "_image_draft", side_effect=lambda b, u, m: self.session if
                         self.session["payload"].get("pending_image") == m else None),
            patch.object(flow, "current_binding", return_value=True),
            patch.object(flow.store, "update_session", side_effect=self.update_session),
            patch.object(flow, "run_dedup", side_effect=lambda *a, **k: self.dedup.append(a)),
            patch.object(flow, "_push", side_effect=lambda uid, text, token="": self.pushed.append(text)),
            patch.object(flow._id_ocr, "editable_id_card", side_effect=lambda card: dict(card)),
            patch.object(flow._id_ocr, "recognize_id_card", side_effect=self.recognize),
            patch("pearnly_core.execution.inputs.read", return_value=b"image"),
            patch("pearnly_core.execution.work.checkpoint", side_effect=self.checkpoint),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.during = None

    def checkpoint(self, key, state=None, body=None):
        if state is None:
            return self.saved.get(key)
        self.saved[key] = {"state": state, "body": body}
        return self.saved[key]

    def recognize(self, *args, **kwargs):
        self.paid.append(kwargs["billing_reference"])
        if self.during:
            self.during()  # 识别这十几秒里本人在 LINE 上又发了东西
        return {"id": "EP1"}, dict(OCR), 0

    def update_session(self, user, change, ttl_minutes=None):
        outcome = change(self.session)
        if outcome is None:
            return self.session
        state, payload = outcome
        self.written.append(payload)
        self.session = {"state": state, "payload": payload}
        return self.session

    def run_image(self, mid="M1"):
        return flow._recognize_image({"user_id": "sales@x"}, USER, mid, "tok")

    def test_a_phone_that_arrives_during_recognition_is_kept_and_the_lookup_runs(self):
        def phone():
            self.session = {"state": "collecting",
                            "payload": {**self.session["payload"], "phone": "0812345678"}}

        self.during = phone
        self.run_image()
        self.assertEqual(self.written[-1]["phone"], "0812345678")
        self.assertEqual(self.written[-1]["id_card"]["people_id"], "1100000000001")
        self.assertEqual(len(self.dedup), 1)
        self.assertEqual(self.paid, ["idcard:line:M1"])

    def test_a_newer_image_wins_and_the_older_result_is_not_written(self):
        def newer():
            self.session = {"state": "collecting", "payload": {**self.session["payload"], "pending_image": "M2"}}

        self.during = newer
        self.assertEqual(self.run_image(), {"cancelled": "draft_changed"})
        self.assertEqual(self.written, [])
        self.assertEqual(self.dedup, [])

    def test_a_rerun_of_the_same_task_takes_the_paid_result_from_the_checkpoint(self):
        self.saved[flow.RESULT_CHECKPOINT] = {"state": flow.RESULT_STATE, "body": {"endpoint_id": "EP1", "ocr": OCR}}
        self.run_image()
        self.assertEqual(self.paid, [])
        self.assertEqual(self.written[-1]["endpoint_id"], "EP1")
        self.assertEqual(self.pushed, [flow.cards.TXT_ASK_PHONE])

    def test_a_first_run_stores_the_result_before_writing_the_session(self):
        order = []
        real = self.checkpoint
        self.checkpoint = lambda key, state=None, body=None: (order.append("checkpoint") if state else None,
                                                               real(key, state, body))[1]
        with patch("pearnly_core.execution.work.checkpoint", side_effect=self.checkpoint), \
                patch.object(flow.store, "update_session",
                             side_effect=lambda *a, **k: (order.append("session"), self.update_session(*a, **k))[1]):
            self.run_image()
        self.assertEqual(order, ["checkpoint", "session"])


class ErpBeginSerialized(unittest.TestCase):
    def test_the_whole_begin_runs_inside_the_users_serial_block(self):
        events = []

        @contextmanager
        def serialized(user, **kw):
            events.append(("enter", user))
            yield
            events.append(("exit", user))

        with patch("pearnly_core.line_session.serialized", serialized), \
                patch.object(line_questions.line_flow, "choices",
                             side_effect=lambda user: events.append("read") or {"direction": "purchase"}), \
                patch.object(line_questions, "_drive", side_effect=lambda payload, messages=(): ["card"]), \
                patch.object(line_questions.line_flow, "keep", side_effect=lambda user, payload: events.append("write")):
            self.assertEqual(line_questions.begin(USER, ["LI-1"]), ["card"])
        self.assertEqual(events, [("enter", USER), "read", "write", ("exit", USER)])


if __name__ == "__main__":
    unittest.main()
