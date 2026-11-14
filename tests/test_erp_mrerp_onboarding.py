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
    utils.nowdate = lambda: NOW.date().isoformat()
    frappe.utils = utils
    sys.modules["frappe"], sys.modules["frappe.utils"] = frappe, utils

    ledger = types.ModuleType("pearnly_erp_portal.express.masters")
    ledger.QUEUED, ledger.READING, ledger.SYNCING, ledger.READY, ledger.FAILED = (
        "queued", "reading", "syncing", "ready", "failed")
    ledger.MRERP, ledger.NOTIFY_KEY = "mrerp", "notify:"
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
    catalog.current = lambda row, day: {"key": "6:1", "label": "TEST2019", "connection": row.get("mrerp_connection")} \
        if row.get("mrerp_account_set") else None
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
        export.read = lambda lease, key, manual: ({"customers": [{"code": "C1"}]}, None, {"requests": 10})
        with self.switch(), patch.dict(sys.modules, {"pearnly_core.mrerp.master_export": export}):
            self.masters.run("CO-1")
        self.assertEqual(self.ledger.applied, [("CO-1", "TEST2019", "mrerp", 1)])
        self.assertEqual([state for _c, state, _n in self.ledger.recorded], ["reading", "syncing", "ready"])
        self.assertEqual(self.ledger.notified, [("CO-1", "ready")])

    def test_unreadable_is_a_failure_not_an_empty_ledger(self):
        self.frappe.db.companies["CO-1"] = company()
        export = types.ModuleType("pearnly_core.mrerp.master_export")
        export.read = lambda lease, key, manual: (None, "ERR_ACCOUNT_IN_USE", {})
        with self.switch(), patch.dict(sys.modules, {"pearnly_core.mrerp.master_export": export}):
            self.masters.run("CO-1")
        self.assertEqual(self.ledger.applied, [])
        self.assertEqual(self.ledger.recorded[-1], ("CO-1", "failed", "ERR_ACCOUNT_IN_USE"))

    def test_only_a_person_s_read_clears_the_kicked_mark(self):
        """人点出来的那次（`catalog.choose` → ``queue(manual=True)``）读时清被踢标记；定时、写单后的不清。"""
        self.frappe.db.companies["CO-1"] = company(express_synced_at=NOW - datetime.timedelta(hours=1))
        seen = []
        export = types.ModuleType("pearnly_core.mrerp.master_export")
        export.read = lambda lease, key, manual: seen.append(manual) or (None, "ERR_TECHNICAL", {})
        with self.switch(), patch.dict(sys.modules, {"pearnly_core.mrerp.master_export": export}):
            self.masters.queue("CO-1", manual=True)
            self.masters.after_write("CO-1")
            for _method, kw in self.frappe.enqueued:
                self.masters.run(kw["company"], manual=kw["manual"])
            self.masters.run("CO-1")
        self.assertEqual([kw["manual"] for _m, kw in self.frappe.enqueued], [True])
        self.assertEqual(seen, [True, False])


class CompanyIdentityTests(unittest.TestCase):
    """各年度账套归成一家（主会话 11-14）：认同一家、法定名称、按日期选哪一本、同期两本问一次（Express 同一个选择与记忆）。
    这里载入真的 `mrerp/catalog.py`，只替身数据库与 Express 的选择 / 对话 / 年度键。"""

    def setUp(self):
        import importlib.util

        self.frappe = frappe = types.ModuleType("frappe")
        frappe._ = lambda text: text
        self.connections_rows = {"MR-1": {"system_url": "https://www.mrerp4sme.com", "enabled": 1}}
        self.catalogs = {}
        self.company = {"name": "CO-1", "mrerp_connection": "MR-1", "mrerp_account_set": "6:1", "mrerp_company": ""}

        class Db:
            def get_value(db, doctype, name, field, as_dict=False):
                if doctype == "ERP MRERP Connection":
                    return (self.connections_rows.get(name) or {}).get(field)
                return Row(self.company) if as_dict else self.company.get(field)

        frappe.db = Db()
        frappe.get_all = lambda doctype, filters=None, pluck=None, order_by=None, limit_page_length=None: [
            name for name, row in self.connections_rows.items() if row["enabled"]]
        utils = types.ModuleType("frappe.utils")
        utils.now_datetime = lambda: NOW
        frappe.utils = utils
        express_pkg = types.ModuleType("pearnly_erp_portal.express")
        spec = importlib.util.spec_from_file_location(
            "pearnly_erp_portal.express.contracts", ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/express/contracts.py")
        contracts = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(contracts)
        self.contracts = contracts
        self.chosen = {}
        choices = types.ModuleType("pearnly_erp_portal.express.choices")
        choices.ACCOUNT_SET = "account_set"
        choices.chosen = lambda company, kind, subject: self.chosen.get((company, kind, subject), "")
        conversation = types.ModuleType("pearnly_erp_portal.express.conversation")
        conversation.LEDGER_NEEDED = "ledger_needed"
        target = types.ModuleType("pearnly_erp_portal.express.target")
        target.year_subject = lambda day, book: f"year:{book.get('period_start') or str(day)[:4]}"
        express_pkg.contracts, express_pkg.choices, express_pkg.conversation, express_pkg.target = (
            contracts, choices, conversation, target)
        connection = types.ModuleType("pearnly_erp_portal.mrerp.connection")
        connection.CONNECTION, connection.DEFAULT_URL = "ERP MRERP Connection", "https://www.mrerp4sme.com"
        modules = {"frappe": frappe, "frappe.utils": utils, "pearnly_erp_portal.express": express_pkg,
                   "pearnly_erp_portal.express.contracts": contracts, "pearnly_erp_portal.express.choices": choices,
                   "pearnly_erp_portal.express.conversation": conversation, "pearnly_erp_portal.express.target": target,
                   "pearnly_erp_portal.mrerp.connection": connection}
        patcher = patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        import pearnly_erp_portal.mrerp as mrerp_pkg
        mrerp_pkg.connection = connection
        spec = importlib.util.spec_from_file_location("lab_mrerp_catalog",
                                                      ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/mrerp/catalog.py")
        self.catalog = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.catalog)
        self.catalog._sets = lambda name: self.catalogs.get(name, [])

    @staticmethod
    def sets(**periods):
        row = {"company": "TEST", "company_name": "1010-01-000006 บริษัท ทดสอบการใช้ จำกัด"}
        out = []
        for key, label, comidyear in (("6:1", "TEST2019", "6"), ("15:1", "TEST2020", "15")):
            attributes = {**row, "comidyear": comidyear, **periods.get(label, {})}
            out.append({"source_id": key, "label": label, "attributes": attributes})
        return out

    def test_identity_survives_a_new_account_or_a_rebuilt_connection(self):
        """没税号：站点主机 + 选公司页那一行（简称 + 全称），不带连接号、不带账号 —— 换 MR.ERP 账号、连接删了重建都认成同一家。"""
        attributes = self.sets()[0]["attributes"]
        first = self.catalog.identity("MR-1", attributes)
        self.connections_rows["MR-9"] = {"system_url": "https://WWW.mrerp4sme.com/", "enabled": 1}
        self.assertEqual(self.catalog.identity("MR-9", attributes), first)
        self.assertEqual(first, "row:www.mrerp4sme.com|test|1010-01-000006 บริษัท ทดสอบการใช้ จำกัด")
        # 同站点别的公司（另一行）、别的站点：不是同一家
        self.assertNotEqual(self.catalog.identity("MR-1", {**attributes, "company_name": "บริษัท อื่น จำกัด"}), first)
        self.connections_rows["MR-X"] = {"system_url": "https://erp.other.example", "enabled": 1}
        self.assertNotEqual(self.catalog.identity("MR-X", attributes), first)
        # 有效税号：按税号认（同 Express），与站点、行无关
        self.assertEqual(self.catalog.identity("MR-X", {**attributes, "tax_id": "0105560000001"}), "tax:0105560000001")

    def test_books_follow_the_company_to_a_rebuilt_connection(self):
        self.catalogs["MR-1"] = self.sets()
        self.company["mrerp_company"] = self.catalog.identity("MR-1", self.sets()[0]["attributes"])
        self.assertEqual([book["key"] for book in self.catalog.books_of(self.company)], ["6:1", "15:1"])
        # 旧连接停用、重建一条（新账号）：目录里同一行 → 照样找到这家公司的各年度
        self.connections_rows["MR-1"]["enabled"] = 0
        self.connections_rows["MR-2"] = {"system_url": "https://www.mrerp4sme.com", "enabled": 1}
        self.catalogs["MR-2"] = self.sets()
        self.assertEqual([(book["key"], book["connection"]) for book in self.catalog.books_of(self.company)],
                         [("6:1", "MR-2"), ("15:1", "MR-2")])

    def test_legal_name_drops_the_internal_code(self):
        self.assertEqual(self.catalog.legal_name(self.sets()[0]["attributes"]), "บริษัท ทดสอบการใช้ จำกัด")
        self.assertEqual(self.catalog.legal_name({"company": "TEST"}), "TEST")

    def test_pick_chooses_by_the_period_read_from_mrerp(self):
        self.catalogs["MR-1"] = self.sets(TEST2019={"period_start": "2026-01-01", "period_end": "2026-12-31"})
        self.company["mrerp_company"] = self.catalog.identity("MR-1", self.sets()[0]["attributes"])
        picked = self.catalog.pick(self.company, "2026-11-14")
        self.assertEqual((picked["book"]["key"], picked["reason"]), ("6:1", ""))
        # 期间读不到的年度库（TEST2020）不参与按日期选：不从名字猜年份 → 没有覆盖这天的账套
        none = self.catalog.pick(self.company, "2020-05-01")
        self.assertEqual((none["book"], none["reason"]), (None, self.contracts.NO_BOOK))
        # 对话定了那本：就用那本；名单里没有 → 没有账套
        self.assertEqual(self.catalog.pick(self.company, "2020-05-01", wanted="15:1")["book"]["key"], "15:1")
        self.assertEqual(self.catalog.pick(self.company, "2020-05-01", wanted="99:1")["reason"], self.contracts.NO_BOOK)

    def test_two_books_for_one_period_ask_once_in_the_express_choice(self):
        same = {"period_start": "2026-01-01", "period_end": "2026-12-31"}
        self.catalogs["MR-1"] = self.sets(TEST2019=same, TEST2020=same)
        self.company["mrerp_company"] = self.catalog.identity("MR-1", self.sets()[0]["attributes"])
        asked = self.catalog.pick(self.company, "2026-11-14")
        self.assertEqual((asked["reason"], [o["code"] for o in asked["options"]], asked["subject"]),
                         (self.contracts.SEVERAL_BOOKS, ["6:1", "15:1"], "year:2026-01-01"))
        # 选过一次（记在 Express 同一处：公司 + account_set + 这个会计年度）：直接用选的
        self.chosen[("CO-1", "account_set", "year:2026-01-01")] = "15:1"
        self.assertEqual(self.catalog.pick(self.company, "2026-11-14")["book"]["key"], "15:1")
        # 主档整读不停下：没选过时取第一本
        self.chosen.clear()
        self.assertEqual(self.catalog.current(self.company, "2026-11-14")["key"], "6:1")


if __name__ == "__main__":
    unittest.main()
