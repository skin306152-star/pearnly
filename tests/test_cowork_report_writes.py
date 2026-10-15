"""心跳不再每分钟重写整份上报记录（2026-10-07），多台电脑后更不写连接配置（ADR-021 第 2 批）。

小助手每分钟报到一次；以前每次都把连接这一行（含几百 KB 的上报记录）整行写回，全量进数据库日志。报到时间只记在密钥行。
第 2 批起账套名单进可达行、主档进账套资料，连接的上报记录只剩当前上报那把密钥的绑定、版本与被拦的更新：内容没变、
上报的密钥没变就不写连接这一行；名单、主档变了也只写它们自己的行。不连库、不依赖 Frappe（`cowork_pool_fake`）。
"""
import json
import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cowork_pool_fake as fake  # noqa: E402

fake.install()

from pearnly_cowork.connections import access, collector, model, pool, snapshots, state  # noqa: E402
from pearnly_cowork.connections.reports import plan_report  # noqa: E402
from pearnly_cowork.targets.projection import normalize_projection  # noqa: E402

BOOK = r"C:\EXP\BOOK"
SETS = [{"code": "BOOK", "path": BOOK, "root": r"C:\EXP", "writable": True}]
NOW = datetime(2026, 10, 7, 4, 0)


def heartbeat(**extra):
    return {"account_set": BOOK, "account_sets": SETS, "accounts": [{"code": "1100"}], "companion_version": "1.5.1",
            **extra}


class ReportWrites(unittest.TestCase):
    def setUp(self):
        fake.DB.clear()
        self.doc = fake.Doc(doctype="Cowork ERP Connection", name="CONN", workspace="W", client="", adapter="express",
                            enabled=1, binding_generation=0, record_revision=1, projection_heads="{}",
                            config_json=model.dumps({"account_set": BOOK, "express_root": r"C:\EXP"}),
                            report_credential="CRED", report_generation=1,
                            report_json=model.dumps({"agent_last_seen_at": "2026-10-07T03:59:00+00:00",
                                                     "reported_account_sets": SETS,
                                                     "account_sets_seen_at": "2026-10-07T03:59:00+00:00"}))
        fake.DB.docs["Cowork ERP Connection"] = {"CONN": self.doc}
        self.writes = []
        original = access.write

        def write(doc):
            # 只数上报记录这一列的写入（目录指针 `projection_heads` 变了另写，见下面 SnapshotHeads）
            if doc.get("doctype") == "Cowork ERP Connection" and doc.report_json != self.last:
                self.writes.append(doc.report_json)
                self.last = doc.report_json
            return original(doc)

        self.last = self.doc.report_json

        access.write = write
        self.addCleanup(setattr, access, "write", original)
        self.pc = fake.Computer(self.doc, "PC")

    def report(self, body, minute=0, **kwargs):
        return self.pc.report(body, NOW + timedelta(minutes=minute), **kwargs)

    def test_unchanged_reports_do_not_rewrite_the_connection(self):
        self.report(heartbeat())
        self.assertEqual(len(self.writes), 1)  # 升级后第一拍：去掉旧时间戳与旧名单，写一次
        stored = json.loads(self.doc.report_json)
        self.assertEqual(set(stored), {"_preparation_binding", "companion_version"})
        for minute in range(1, 6):
            self.report(heartbeat(), minute)
        self.assertEqual(len(self.writes), 1)  # 之后每分钟内容相同：一行都不写

    def test_changed_masters_go_to_the_account_sets_facts_not_the_connection(self):
        self.report(heartbeat())
        self.report(heartbeat(accounts=[{"code": "1100"}, {"code": "2100"}]), 1)
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(len(pool.facts("CONN", BOOK)["reported_accounts"]), 2)
        # 换了上报的密钥（同一台电脑重新配对）：连接这一行写一次
        self.pc.credential, self.pc.generation = "CRED-NEW", 3
        self.report(heartbeat(), 2)
        self.assertEqual(len(self.writes), 2)
        self.assertEqual((self.doc.report_credential, self.doc.report_generation), ("CRED-NEW", 3))

    def test_heartbeats_never_rewrite_the_configuration(self):
        before = (self.doc.config_json, self.doc.record_revision)
        self.report(heartbeat(account_set=r"C:\EXP\OTHER", mapping={"revenue_acc": "4999"}))
        self.assertEqual((self.doc.config_json, self.doc.record_revision), before)
        self.assertEqual(self.pc.state["revenue_acc"], "4999")  # 记在这台电脑上（服务端在电脑行，`devices.record_report`）

    def test_an_unchanged_heartbeat_keeps_the_catalog_and_writes_nothing(self):
        """心跳瘦身（2026-10-08）：小助手只报「账套名单没变」时，可达行与目录原样保留，不发目录观测，连接这一行也不写。"""
        self.report(heartbeat())
        published = len(fake.DB.rows("Cowork ERP Snapshot"))
        slim = plan_report({"account_set": BOOK, "account_sets_unchanged": True}, {}, NOW.replace(tzinfo=timezone.utc))
        self.assertEqual((slim.observations, slim.account_sets), ([], None))
        for minute in range(1, 4):
            self.report({"account_set": BOOK, "account_sets_unchanged": True, "companion_version": "1.5.1"}, minute)
        self.assertEqual(list(pool.local_paths("CONN", "PC")), [BOOK.lower()])
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(len(fake.DB.rows("Cowork ERP Snapshot")), published)

    def test_going_offline_is_recorded_once(self):
        self.report(heartbeat())
        self.report({"offline": True}, 1)
        self.report({"offline": True}, 2)
        self.assertEqual(len(self.writes), 2)
        self.assertFalse(json.loads(self.doc.report_json)["_preparation_binding"]["online"])


class SnapshotHeads(unittest.TestCase):
    def test_the_same_catalog_does_not_rewrite_the_connection(self):
        plan = plan_report(heartbeat(), {}, NOW.replace(tzinfo=timezone.utc))
        normalized = normalize_projection(plan.observations[0])
        doc = fake.Doc(name="CONN", adapter="express", record_revision=1, binding_generation=0,
                       workspace="W", client="", projection_heads="{}")
        current = fake.Doc(name="SNAP", connection="CONN", connection_revision=1, projection_revision=4,
                           content_hash=model.fingerprint([normalized.source_hash, model.preparation(None)]))
        writes = []
        original_write, original_read = snapshots.access.write, snapshots.state.read
        snapshots.access.write, snapshots.state.read = writes.append, lambda *a, **k: current
        try:
            _kind, _key, head_key = state.scope(None, "express")  # 例行心跳的目录挂在端点上
            doc.projection_heads = model.dumps({head_key: {"snapshot_id": "SNAP", "revision": 4,
                                                           "request_id": None, "status": "fresh"}})
            result = snapshots.publish(doc, normalized)
            self.assertEqual((result["published"], writes), (False, []))  # 同一份目录：不建快照、不写连接
            doc.projection_heads = model.dumps({head_key: {"snapshot_id": "SNAP", "revision": 4,
                                                           "request_id": None, "status": "pending"}})
            snapshots.publish(doc, normalized)
            self.assertEqual(writes, [doc])  # 指针状态变了才写一次
        finally:
            snapshots.access.write, snapshots.state.read = original_write, original_read


if __name__ == "__main__":
    unittest.main()
