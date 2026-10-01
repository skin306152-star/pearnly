"""发票识别的授权复核点（2026-10-01 用户同意合并同一时刻的重复复核）。

守住：付费调用（发给 Cloud Run）之前一次、结果回来存之前一次，都连冻结配置一起复核；结果回来那次被拦下时结果不存；
用户点「重试记账」照旧复核。离线：任务单、存储、Cloud Run 都用替身。"""
import json
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import mock

import frappe

from pearnly_cowork.ocr.execution import service, workflow


class Doc(frappe._dict):
    def get(self, key, default=None):
        return super().get(key, default)


class Checks(unittest.TestCase):
    def run_once(self, deny_after_result=False):
        events = []
        doc = Doc(name="B1", work_name="W1", phase="admitted", result_json=None, actor="u@example.com",
                  settings_json=json.dumps({"billing_tier": "B", "environment": {}}), examples_json="null",
                  page_count=1, anchor_date="2026-10-01", source_file="S1", workspace="ws", client="",
                  direction="purchase", posting_kind="", payment=None, reference_kind="physical_page")

        def check(current, frozen=None, examples=None):
            events.append(("check", frozen is not None))
            if deny_after_result and any(e[0] == "cloud_run" for e in events):
                raise frappe.PermissionError("revoked")
            return SimpleNamespace(kind="image", filename="a.jpg")

        def read(name, lock=False, authorize=True, write=False):
            events.append(("read", authorize))
            return doc

        def save(current):
            events.append(("save", current.phase))
            return current

        @contextmanager
        def actor(user):
            yield

        def recognize(*args, **kwargs):
            events.append(("cloud_run",))
            return "{}", []

        with mock.patch.object(workflow, "_require_work"), mock.patch.object(workflow, "_check", side_effect=check), \
                mock.patch.object(workflow.store, "read", side_effect=read), \
                mock.patch.object(workflow.store, "save", side_effect=save), \
                mock.patch.object(workflow.store, "actor", side_effect=actor), \
                mock.patch.object(workflow.store, "encode", side_effect=json.dumps), \
                mock.patch.object(workflow.service, "billing_basis", return_value=("pdf", 1)), \
                mock.patch.object(workflow.service, "balance"), \
                mock.patch.object(workflow.forward, "settings", return_value={"url": "x"}), \
                mock.patch.object(workflow.forward, "recognize", side_effect=recognize), \
                mock.patch.object(workflow, "read_bytes", return_value=b"x"), \
                mock.patch.object(workflow.PipelineResult, "model_validate_json",
                                  return_value=SimpleNamespace(engine="engine")), \
                mock.patch.object(workflow, "prepare_history_batch", return_value={"groups": [], "requires_review": False}), \
                mock.patch.object(workflow, "_announce"), \
                mock.patch.object(workflow.frappe, "db", mock.MagicMock()):
            try:
                result = workflow.run("B1")
            except frappe.PermissionError:
                result = None
        return events, result

    def test_one_check_before_the_paid_call_and_one_after_the_result(self):
        events, result = self.run_once()
        checks = [i for i, e in enumerate(events) if e[0] == "check"]
        cloud = events.index(("cloud_run",))
        self.assertEqual(len(checks), 2)
        self.assertLess(checks[0], cloud)
        self.assertGreater(checks[1], cloud)
        self.assertTrue(all(events[i][1] for i in checks))  # 两次都连冻结配置一起复核
        # 复核之后紧接着的加锁读不再重复同一刻的复核
        self.assertEqual([e for e in events if e[0] == "read"][1:], [("read", False), ("read", False)])
        self.assertEqual(result["state"], "not_invoice")

    def test_a_revocation_while_reading_stops_before_the_result_is_saved(self):
        events, result = self.run_once(deny_after_result=True)
        self.assertIsNone(result)
        self.assertNotIn(("save", "recognized"), events)
        self.assertNotIn(("save", "not_invoice"), events)

    def test_a_user_retrying_billing_is_still_checked(self):
        with mock.patch.object(service.store, "read", side_effect=frappe.PermissionError("no")) as read:
            with self.assertRaises(frappe.PermissionError):
                service.retry_billing("B1")
        self.assertTrue(read.call_args.kwargs["authorize"])


if __name__ == "__main__":
    unittest.main()
