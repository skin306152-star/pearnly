"""录入列表的推送状态一变就推实时事件给上传人与推送发起人（列表收到就重拉，见 product.js ``p.live``）。镜像里跑，不连站点。"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "pearnly_cowork"))
for name in [name for name in sys.modules if name.startswith("pearnly_cowork")]:
    del sys.modules[name]

import frappe  # noqa: E402

from pearnly_cowork.history import push_state  # noqa: E402

assert Path(push_state.__file__).is_relative_to(ROOT)


class IntakeChangedTest(unittest.TestCase):
    def run_with(self, fn, rows):
        sent = []
        db = SimpleNamespace(sql=lambda *a, **k: rows, set_value=lambda *a, **k: None,
                             get_value=lambda doctype, name, field: "uploader@x")
        with patch.object(frappe, "db", db, create=True), \
                patch.object(frappe, "session", SimpleNamespace(user="pusher@x"), create=True), \
                patch.object(frappe, "publish_realtime", side_effect=lambda event, data, user, after_commit:
                             sent.append((event, data["history"], user, after_commit))):
            fn()
        return sorted(sent)

    def test_status_change_reaches_uploader_and_pusher_after_commit(self):
        row = frappe._dict(name="REC-1", history="H-1", status="success", workspace="W", client="", reason="",
                           owner="clicker@x")
        sent = self.run_with(lambda: push_state.mirror(row, "2026-10-01"),
                             [frappe._dict(workspace="W", client="", last_push_record=None, last_push_status=None)])
        self.assertEqual(sent, [("cowork_intake_changed", "H-1", user, True)
                                for user in ("clicker@x", "pusher@x", "uploader@x")])

    def test_unchanged_status_sends_nothing_and_failures_are_announced(self):
        row = frappe._dict(name="REC-1", history="H-1", status="success", workspace="W", client="", reason="")
        same = [frappe._dict(workspace="W", client="", last_push_record="REC-1", last_push_status="success")]
        self.assertEqual(self.run_with(lambda: push_state.mirror(row, "t"), same), [])
        sent = self.run_with(lambda: push_state.record_failure("H-2", "target offline"), [])
        self.assertEqual({item[1] for item in sent}, {"H-2"})

    def test_the_push_target_is_written_to_a_document_that_had_none(self):
        # 2026-10-15 测试机：CC 先「仅识别」再推，票上推送目标空着，公司对话按账套查不到已推的票
        written = []
        db = SimpleNamespace(sql=lambda *a, **k: [frappe._dict(workspace="W", client="", last_push_record=None,
                                                               last_push_status=None, push_connection="",
                                                               push_account_set="")],
                             set_value=lambda doctype, name, values, **k: written.append(values),
                             get_value=lambda *a, **k: "u@x")
        row = frappe._dict(name="REC-9", history="H-9", status="success", workspace="W", client="", reason="",
                           connection="CONN-1", account_key="c:\\70exp\\t9sincer")
        with patch.object(frappe, "db", db, create=True), \
                patch.object(frappe, "session", SimpleNamespace(user="p@x"), create=True), \
                patch.object(frappe, "publish_realtime", side_effect=lambda *a, **k: None):
            push_state.mirror(row, "t")
        self.assertEqual((written[0]["push_connection"], written[0]["push_account_set"]), ("CONN-1", "c:\\70exp\\t9sincer"))
        # 已有目标、又不是用户这次重新选的：不改
        previous = frappe._dict(push_connection="CONN-1", push_account_set="c:\\70exp\\other")
        self.assertEqual(push_state._target(previous, row, False), {})
        self.assertEqual(push_state._target(previous, row, True),
                         {"push_connection": "CONN-1", "push_account_set": "c:\\70exp\\t9sincer"})

    def test_a_broken_realtime_never_breaks_the_push(self):
        db = SimpleNamespace(get_value=lambda *a: "u@x")
        with patch.object(frappe, "db", db, create=True), \
                patch.object(frappe, "session", SimpleNamespace(user="p@x"), create=True), \
                patch.object(frappe, "publish_realtime", side_effect=RuntimeError("redis down")):
            push_state._changed("H-3")


if __name__ == "__main__":
    unittest.main()
