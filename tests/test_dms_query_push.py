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


if __name__ == "__main__":
    unittest.main()
