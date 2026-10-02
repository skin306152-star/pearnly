"""DMS 菜单 5 查询读完推结果（2026-10-02 ris 热修）。在生产镜像里跑（用镜像里的 frappe），不连站点、不登 DMS、不发 LINE。

线上：保存下来的条件按设计不含每页条数 / 页码，推结果时直接拿它当参数 → `_run_records` KeyError: 'limit'，报错只记一行
warning，操作员两次查询都既没结果也没失败提示。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
for app in ("pearnly_core", "pearnly_dms"):
    sys.path.insert(0, str(ROOT / "apps" / app))
for name in [n for n in sys.modules if n.split(".")[0] in ("pearnly_core", "pearnly_dms")]:
    del sys.modules[name]

import frappe  # noqa: E402

from pearnly_dms.query import query_flow, sales_readback as rb, snapshot_sync  # noqa: E402

assert Path(snapshot_sync.__file__).is_relative_to(ROOT), snapshot_sync.__file__
USER = "U-line-operator"
BINDING = {"line_user_id": USER, "user_id": "op@example.invalid", "binding_revision": "r1"}
# 操作员在会话里的那份完整参数（`_begin_records` 写的）
SESSION = {"query_kind": "records", "field": "delivery_date", "query": "01/11/2569", "status": "active", "limit": 10, "page": 1}
CONDITIONS = rb.query_parameters("records", **SESSION)  # 保存下来的条件：没有 limit / page


class Push(unittest.TestCase):
    def setUp(self):
        self.session = {"state": "query_results", "payload": dict(SESSION)}
        for target, kwargs in (
                ("pearnly_dms.snapshots.manual._load", {"return_value": (None, None, {"id": "req-1", "params": {**CONDITIONS, "_binding": BINDING}})}),
                ("pearnly_dms.query.snapshot_sync.current_binding", {"return_value": True}),
                ("pearnly_core.line_session.get_session", {"side_effect": lambda user: self.session}),
                ("pearnly_dms.i18n.line_language", {"return_value": MagicMock(__enter__=lambda s: s, __exit__=lambda s, *a: False)}),
                ("frappe.log_error", {"new": MagicMock()})):
            p = patch(target, **kwargs)
            p.start()
            self.addCleanup(p.stop)

    def test_saved_conditions_without_page_size_still_push_the_result(self):
        self.assertNotIn("limit", CONDITIONS)
        with patch.object(query_flow, "_run_records") as run, patch.object(query_flow, "_push") as push:
            snapshot_sync._push_result("EP-1", "query.x", "req-1", CONDITIONS)
        run.assert_called_once_with(BINDING, USER, SESSION)
        push.assert_not_called()
        frappe.log_error.assert_not_called()

    def test_a_failed_push_tells_the_operator_and_is_logged(self):
        with patch.object(query_flow, "_run_records", side_effect=RuntimeError("card render broke")), \
                patch.object(query_flow, "_push") as push, patch("pearnly_dms.query.query_cards.TXT_QUERY_FAILED", "อ่านไม่สำเร็จ", create=True):
            snapshot_sync._push_result("EP-1", "query.x", "req-1", CONDITIONS)
        push.assert_called_once_with(USER, "อ่านไม่สำเร็จ", "")
        frappe.log_error.assert_called_once()

    def test_changed_conditions_mean_the_old_result_is_not_pushed(self):
        self.session = {"state": "query_results", "payload": {**SESSION, "query": "02/10/2569"}}
        with patch.object(query_flow, "_run_records") as run, patch.object(query_flow, "_push") as push:
            snapshot_sync._push_result("EP-1", "query.x", "req-1", CONDITIONS)
        run.assert_not_called()
        push.assert_not_called()



class Store:
    def __init__(self):
        self.keys = set()

    def make_key(self, key):
        return "site|" + key

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys.add(key)
        return True


class FailedReads(unittest.TestCase):
    """读到最后失败、或卡住没人管时，告诉发起查询的操作员一次（2026-10-02 ris：失败出口全都不发消息）。"""

    def setUp(self):
        self.session = {"state": "query_results", "payload": dict(SESSION)}
        self.store = Store()
        for target, kwargs in (
                ("pearnly_dms.query.snapshot_sync._state_store", {"return_value": self.store}),
                ("pearnly_dms.query.snapshot_sync.current_binding", {"return_value": True}),
                ("pearnly_core.line_session.get_session", {"side_effect": lambda user: self.session}),
                ("pearnly_dms.i18n.line_language", {"return_value": MagicMock(__enter__=lambda s: s, __exit__=lambda s, *a: False)}),
                ("pearnly_dms.query.query_cards._t", {"side_effect": lambda key, **kw: "T:" + key})):
            p = patch(target, **kwargs)
            p.start()
            self.addCleanup(p.stop)
        self.params = {**CONDITIONS, "_binding": BINDING}

    def test_operator_hears_about_a_failed_read_once(self):
        with patch.object(query_flow, "_push") as push:
            self.assertTrue(snapshot_sync.notify_failed(request_id="req-9", params=self.params, error_code="ERR_DMS_TECHNICAL"))
            self.assertFalse(snapshot_sync.notify_failed(request_id="req-9", params=self.params, error_code="ERR_DMS_TECHNICAL"))
        push.assert_called_once_with(USER, "T:MANUAL_FAILED", "")  # query_cards.manual_failed_text()

    def test_no_notice_when_the_operator_moved_on(self):
        self.session = {"state": "query_menu", "payload": {}}
        with patch.object(query_flow, "_push") as push:
            self.assertFalse(snapshot_sync.notify_failed(request_id="req-8", params=self.params, error_code=""))
        push.assert_not_called()

    def test_stale_reads_are_swept_and_fresh_ones_left_alone(self):
        import json as _json
        import time as _time
        now = _time.time()
        rows = [frappe._dict(dataset="query.a", refresh_request=_json.dumps({"id": "old", "state": "running",
                    "updated_at": now - 3600, "params": self.params})),
                frappe._dict(dataset="query.b", refresh_request=_json.dumps({"id": "new", "state": "queued",
                    "updated_at": now - 30, "params": self.params}))]
        with patch.object(frappe, "db", MagicMock(sql=MagicMock(return_value=rows))), \
                patch.object(snapshot_sync, "notify_failed") as notify:
            snapshot_sync.sweep_stale()
        self.assertEqual([c.kwargs["request_id"] for c in notify.call_args_list], ["old"])

    def test_the_query_collector_registers_the_notice_and_the_sweep(self):
        hooks = (ROOT / "apps/pearnly_dms/pearnly_dms/hooks.py").read_text()
        self.assertIn('"on_failed": "pearnly_dms.query.snapshot_sync.notify_failed"', hooks)
        self.assertIn('"pearnly_dms.query.snapshot_sync.sweep_stale"', hooks)
        from pearnly_dms.snapshots import manual
        with patch("frappe.get_attr") as get_attr:
            manual._notify_failed({"on_failed": "pearnly_dms.query.snapshot_sync.notify_failed"}, "EP", "query.a", "r1",
                                  {"params": self.params}, "ERR_X")
            manual._notify_failed({"method": "pearnly_dms.adapter.snapshot_sync.refresh"}, "EP", "customers", "r2", {}, "ERR_X")
        get_attr.assert_called_once_with("pearnly_dms.query.snapshot_sync.notify_failed")


class WhileWaiting(unittest.TestCase):
    """等结果时打的字不掉进建档；日期维度只收一个日期。"""

    def setUp(self):
        p = patch.object(query_flow, "_can_query", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def test_text_while_the_read_runs_says_it_is_still_reading(self):
        sess = {"state": "query_results", "payload": dict(SESSION)}
        with patch.object(query_flow.sales_readback, "_query_endpoint", return_value="EP"), \
                patch.object(query_flow.manual, "status", return_value={"state": "running"}), \
                patch.object(query_flow, "_send") as send, \
                patch.object(query_flow.query_cards, "manual_status_message", return_value={"type": "text", "text": "reading"}):
            self.assertTrue(query_flow.handle_text(BINDING, USER, "tok", sess, "นานจัง"))
        send.assert_called_once_with(USER, {"type": "text", "text": "reading"}, "tok")
        with patch.object(query_flow.sales_readback, "_query_endpoint", return_value="EP"), \
                patch.object(query_flow.manual, "status", return_value={"state": "succeeded"}):
            self.assertFalse(query_flow.handle_text(BINDING, USER, "tok", sess, "นานจัง"))

    def test_a_date_range_is_refused_and_a_single_date_is_searched(self):
        sess = {"state": "query_sales_input", "payload": {"field": "delivery_date"}}
        with patch.object(query_flow, "_push") as push, patch.object(query_flow, "_begin_records") as begin, \
                patch("pearnly_dms.query.query_cards._t", side_effect=lambda key, **kw: "T:" + key):
            self.assertTrue(query_flow.handle_text(BINDING, USER, "tok", sess, "01/11/2569-30/11/2569"))
            push.assert_called_once_with(USER, "T:TXT_DATE_SINGLE", "tok")
            begin.assert_not_called()
            self.assertTrue(query_flow.handle_text(BINDING, USER, "tok", sess, "15/11/2569"))
        self.assertEqual(begin.call_args.kwargs["query"], "15/11/2569")


if __name__ == "__main__":
    unittest.main()
