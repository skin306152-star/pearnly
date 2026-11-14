"""ERP 接 MR.ERP 的主档整读排程（`pearnly_erp_portal.mrerp.masters`，公司接入第 3 批）的离线检查。

- 开关关着（:data:`MRERP_LEDGER_ENABLED` = False）：不排、不读、每小时那次什么都不做；
- 开着：接上 / 换账套、写单后、每小时各排一次整读（同一家公司只排一件，排在 Express 主档同一条队列）；写单后那次 5 分钟内读过
  就不再排；长期没人用（14 天没新单、接上也超过 14 天、读过一次）的每小时那次不读；没读过的总要读一次；
- 后台整读：读成落进 Express 同一个落库（``ledger`` = MR.ERP），状态记在公司上；读不到记原因码、不当成空账套落库。

不连库、不连 MR.ERP：Frappe 与 Express 落库用替身（真落库在实验栈 `scripts/erp-mrerp-masters-lab-verify.py` 核）。
"""
from __future__ import annotations

import datetime
import pathlib
import sys
import types
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
for path in (ROOT / "apps/pearnly_core", ROOT / "apps/pearnly_erp_portal"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

NOW = datetime.datetime(2026, 11, 14, 15, 0, 0)


class Row(dict):
    __getattr__ = dict.get


class Db:
    def __init__(self):
        self.companies, self.docs, self.commits = {}, {}, 0

    def get_value(self, doctype, name, fields, as_dict=False):
        row = self.companies.get(name)
        if row is None:
            return None
        if isinstance(fields, str):
            return row.get(fields)
        return Row({field: row.get(field) for field in fields}) if as_dict else [row.get(f) for f in fields]

    def exists(self, doctype, filters):
        edge = filters["creation"][1]
        return any(when > edge for when in self.docs.get((doctype, filters["company"]), []))

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


def install():
    frappe = types.ModuleType("frappe")
    frappe.db = Db()
    frappe.local = types.SimpleNamespace(site="erp.test")
    frappe.enqueued = []
    frappe.enqueue = lambda method, **kw: frappe.enqueued.append((method, kw))
    frappe.cache = types.SimpleNamespace(hset=lambda *a: None)
    frappe.log_error = lambda **kw: None
    frappe.get_traceback = lambda: ""
    frappe.get_all = lambda doctype, filters=None, fields=None, limit_page_length=None: [
        Row(row) for row in frappe.db.companies.values()
        if row.get("is_active") and row.get("mrerp_enabled") and row.get("mrerp_account_set")]
    utils = types.ModuleType("frappe.utils")
    utils.get_datetime = lambda value: value if isinstance(value, datetime.datetime) else datetime.datetime.fromisoformat(value)
    utils.now_datetime = lambda: NOW
    frappe.utils = utils
    sys.modules["frappe"], sys.modules["frappe.utils"] = frappe, utils

    ledger = types.ModuleType("pearnly_erp_portal.express.masters")
    ledger.QUEUED, ledger.READING, ledger.SYNCING, ledger.READY, ledger.FAILED = (
        "queued", "reading", "syncing", "ready", "failed")
    ledger.MRERP, ledger.NOTIFY_KEY = "MR.ERP", "notify:"
    ledger.lane = lambda: "erp_masters"
    ledger.recorded, ledger.applied, ledger.notified = [], [], []

    def record(company, result):
        ledger.recorded.append((company, result["state"], result.get("note")))
        frappe.db.companies[company]["express_sync_state"] = result["state"]

    ledger._record = record
    ledger._notify = lambda company, result: ledger.notified.append((company, result["state"]))
    ledger.apply = lambda company, book, data, ledger=None: ledger_applied(company, book, data, ledger)

    def ledger_applied(company, book, data, source):
        ledger.applied.append((company, book, source, len(data.get("customers") or [])))
        return {"customers": len(data.get("customers") or [])}

    express_pkg = types.ModuleType("pearnly_erp_portal.express")
    express_pkg.masters = ledger
    sys.modules["pearnly_erp_portal.express"] = express_pkg
    sys.modules["pearnly_erp_portal.express.masters"] = ledger
    connection = types.ModuleType("pearnly_erp_portal.mrerp.connection")
    connection.lease = lambda name: {"system_url": "https://mrerp.example.com", "username": "u", "password": "p"} \
        if name else {}
    sys.modules["pearnly_erp_portal.mrerp.connection"] = connection
    catalog = types.ModuleType("pearnly_erp_portal.mrerp.catalog")
    catalog._sets = lambda connection: [{"source_id": "6:1", "label": "TEST2019"}]
    sys.modules["pearnly_erp_portal.mrerp.catalog"] = catalog
    sys.modules.pop("pearnly_erp_portal.mrerp.masters", None)
    from pearnly_erp_portal import mrerp

    # 包上缓存的子模块属性也换掉（from-import 先认包属性）：每个用例都用新的替身
    mrerp.connection, mrerp.catalog = connection, catalog
    if hasattr(mrerp, "masters"):
        del mrerp.masters
    from pearnly_erp_portal.mrerp import masters

    return frappe, ledger, mrerp, masters


def company(name="CO-1", **values):
    return {"name": name, "company_name": "LAB", "is_active": 1, "mrerp_enabled": 1, "mrerp_connection": "MRERP-1",
            "mrerp_account_set": "6:1", "mrerp_linked_at": NOW - datetime.timedelta(days=60),
            "express_synced_at": NOW - datetime.timedelta(hours=2), "express_sync_state": "ready", **values}


class Base(unittest.TestCase):
    def setUp(self):
        self.frappe, self.ledger, self.mrerp, self.masters = install()

    def switch(self, on=True):
        return patch.object(self.mrerp, "MRERP_LEDGER_ENABLED", on)


class SwitchOffTests(Base):
    def test_nothing_queued_or_read(self):
        self.frappe.db.companies["CO-1"] = company()
        self.assertFalse(self.masters.queue("CO-1"))
        self.assertFalse(self.masters.after_write("CO-1"))
        self.masters.sync_all()
        self.masters.run("CO-1")
        self.assertEqual(self.frappe.enqueued, [])
        self.assertEqual(self.ledger.applied, [])


class QueueTests(Base):
    def test_queue_one_job_on_the_masters_lane(self):
        self.frappe.db.companies["CO-1"] = company()
        with self.switch():
            self.assertTrue(self.masters.queue("CO-1"))
        method, kw = self.frappe.enqueued[0]
        self.assertEqual(method, "pearnly_erp_portal.mrerp.masters.run")
        self.assertEqual((kw["queue"], kw["deduplicate"], kw["job_id"]),
                         ("erp_masters", True, "erp-mrerp-masters:erp.test:CO-1"))
        self.assertEqual(self.ledger.recorded[-1][1], "queued")

    def test_express_company_is_not_queued(self):
        self.frappe.db.companies["CO-1"] = company(mrerp_enabled=0)
        with self.switch():
            self.assertFalse(self.masters.queue("CO-1"))

    def test_after_write_skips_a_fresh_read(self):
        self.frappe.db.companies["CO-1"] = company(express_synced_at=NOW - datetime.timedelta(minutes=2))
        with self.switch():
            self.assertFalse(self.masters.after_write("CO-1"))
            self.frappe.db.companies["CO-1"]["express_synced_at"] = NOW - datetime.timedelta(minutes=20)
            self.assertTrue(self.masters.after_write("CO-1"))
            # 已经排着：不再排
            self.assertFalse(self.masters.after_write("CO-1"))


class IdleTests(Base):
    def test_idle_rules(self):
        db = self.frappe.db
        db.companies["OLD"] = company("OLD")
        db.companies["NEVER"] = company("NEVER", express_synced_at=None)
        db.companies["NEW"] = company("NEW", mrerp_linked_at=NOW - datetime.timedelta(days=3))
        db.companies["BUSY"] = company("BUSY")
        db.docs[("ERP Sales Document", "BUSY")] = [NOW - datetime.timedelta(days=2)]
        verdict = {name: self.masters.idle(Row(row), now=NOW) for name, row in db.companies.items()}
        self.assertEqual(verdict, {"OLD": True, "NEVER": False, "NEW": False, "BUSY": False})

    def test_hourly_reads_active_companies_only(self):
        db = self.frappe.db
        db.companies["OLD"] = company("OLD")
        db.companies["BUSY"] = company("BUSY")
        db.companies["RUNNING"] = company("RUNNING", express_sync_state="reading")
        for name in ("BUSY", "RUNNING"):
            db.docs[("Purchase Document", name)] = [NOW - datetime.timedelta(hours=5)]
        with self.switch():
            self.masters.sync_all()
        self.assertEqual([kw["company"] for _m, kw in self.frappe.enqueued], ["BUSY"])


class RunTests(Base):
    def test_read_lands_through_the_express_landing(self):
        self.frappe.db.companies["CO-1"] = company()
        export = types.ModuleType("pearnly_core.mrerp.master_export")
        export.read = lambda lease, key: ({"customers": [{"code": "C1"}]}, None, {"requests": 10})
        with self.switch(), patch.dict(sys.modules, {"pearnly_core.mrerp.master_export": export}):
            self.masters.run("CO-1")
        self.assertEqual(self.ledger.applied, [("CO-1", "TEST2019", "MR.ERP", 1)])
        self.assertEqual([state for _c, state, _n in self.ledger.recorded], ["reading", "syncing", "ready"])
        self.assertEqual(self.ledger.notified, [("CO-1", "ready")])

    def test_unreadable_is_a_failure_not_an_empty_ledger(self):
        self.frappe.db.companies["CO-1"] = company()
        export = types.ModuleType("pearnly_core.mrerp.master_export")
        export.read = lambda lease, key: (None, "ERR_ACCOUNT_IN_USE", {})
        with self.switch(), patch.dict(sys.modules, {"pearnly_core.mrerp.master_export": export}):
            self.masters.run("CO-1")
        self.assertEqual(self.ledger.applied, [])
        self.assertEqual(self.ledger.recorded[-1], ("CO-1", "failed", "ERR_ACCOUNT_IN_USE"))


if __name__ == "__main__":
    unittest.main()
