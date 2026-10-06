"""心跳不再每分钟重写整份上报记录（2026-10-07）。

小助手每分钟报到一次；以前每次都把连接这一行（含几百 KB 的上报记录）整行写回，全量进数据库日志。
现在报到时间只记在密钥行，连接这一行只在上报内容、配置或上报凭据变了时才写。在 bench 的 Python 下运行（不连库）。
"""
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "apps" / "pearnly_cowork"))
sys.path.insert(0, str(root / "apps" / "pearnly_core"))

import frappe  # noqa: E402

from pearnly_cowork.connections import collector, model, snapshots  # noqa: E402
from pearnly_cowork.targets.projection import normalize_projection  # noqa: E402
from pearnly_cowork.connections.reports import plan_report  # noqa: E402

BOOK = r"C:\EXP\BOOK"
SETS = [{"code": "BOOK", "path": BOOK, "root": r"C:\EXP", "writable": True}]


def heartbeat(minute, **extra):
    body = {"account_set": BOOK, "account_sets": SETS, "accounts": [{"code": "1100"}], **extra}
    return plan_report(body, {}, datetime(2026, 10, 7, 4, minute, tzinfo=timezone.utc))


class ReportWrites(unittest.TestCase):
    def setUp(self):
        self.doc = frappe._dict(name="CONN", adapter="express", enabled=1, binding_generation=0, record_revision=1,
                                config_json=model.dumps({"account_set": BOOK, "express_root": r"C:\EXP"}),
                                projection_heads="{}", report_credential="CRED", report_generation=2,
                                report_json=model.dumps({"agent_last_seen_at": "2026-10-07T03:59:00+00:00",
                                                         "account_sets_seen_at": "2026-10-07T03:59:00+00:00"}))
        self.writes = []
        patches = [mock.patch.object(collector.access, "write", side_effect=lambda doc: self.writes.append(doc.report_json)),
                   mock.patch.object(collector, "refresh"),
                   mock.patch.object(collector.snapshots, "publish", return_value={"revision": 1}),
                   mock.patch.object(collector.model, "configuration", side_effect=lambda adapter, config: config)]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        collector.refresh.validate_completion.return_value = None
        collector.refresh.lease.return_value = None

    def ingest(self, plan, credential="CRED", generation=2):
        return collector.ingest(self.doc, plan, credential_id=credential, token_generation=generation, issuer="ISSUER")

    def test_unchanged_reports_do_not_rewrite_the_connection(self):
        self.ingest(heartbeat(0))
        self.assertEqual(len(self.writes), 1)  # 升级后第一拍：去掉旧时间戳，写一次
        stored = json.loads(self.doc.report_json)
        for key in collector.VOLATILE_KEYS:
            self.assertNotIn(key, stored)
        self.assertEqual(stored["reported_account_sets"][0]["path"], BOOK)
        for minute in range(1, 6):
            self.ingest(heartbeat(minute))
        self.assertEqual(len(self.writes), 1)  # 之后每分钟内容相同：一行都不写

    def test_changed_content_or_credential_writes_once(self):
        self.ingest(heartbeat(0))
        self.ingest(heartbeat(1, accounts=[{"code": "1100"}, {"code": "2100"}]))
        self.assertEqual(len(self.writes), 2)
        self.assertEqual(len(json.loads(self.doc.report_json)["reported_accounts"]), 2)
        self.ingest(heartbeat(2, accounts=[{"code": "1100"}, {"code": "2100"}]), credential="CRED-NEW", generation=3)
        self.assertEqual(len(self.writes), 3)
        self.assertEqual((self.doc.report_credential, self.doc.report_generation), ("CRED-NEW", 3))

    def test_going_offline_is_recorded_once(self):
        self.ingest(heartbeat(0))
        offline = plan_report({"offline": True}, {}, datetime(2026, 10, 7, 4, 1, tzinfo=timezone.utc))
        self.ingest(offline)
        self.ingest(offline)
        self.assertEqual(len(self.writes), 2)
        self.assertFalse(json.loads(self.doc.report_json)["_preparation_binding"]["online"])



class SnapshotHeads(unittest.TestCase):
    def test_the_same_catalog_does_not_rewrite_the_connection(self):
        normalized = normalize_projection(heartbeat(0).observations[0])
        doc = frappe._dict(name="CONN", adapter="express", record_revision=1, binding_generation=0,
                           workspace="W", client="", projection_heads="{}")
        current = frappe._dict(name="SNAP", connection="CONN", connection_revision=1, projection_revision=4,
                               content_hash=model.fingerprint([normalized.source_hash, model.preparation(None)]))
        writes = []
        with mock.patch.object(snapshots.access, "write", side_effect=writes.append), \
                mock.patch.object(snapshots.state, "read", return_value=current):
            _kind, _key, head_key = snapshots.state.scope(None, "express")  # 例行心跳的目录挂在端点上
            doc.projection_heads = model.dumps({head_key: {"snapshot_id": "SNAP", "revision": 4,
                                                           "request_id": None, "status": "fresh"}})
            result = snapshots.publish(doc, normalized)
            self.assertEqual((result["published"], writes), (False, []))  # 同一份目录：不建快照、不写连接
            doc.projection_heads = model.dumps({head_key: {"snapshot_id": "SNAP", "revision": 4,
                                                           "request_id": None, "status": "pending"}})
            snapshots.publish(doc, normalized)
            self.assertEqual(writes, [doc])  # 指针状态变了才写一次


if __name__ == "__main__":
    unittest.main()
