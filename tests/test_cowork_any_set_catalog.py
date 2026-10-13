"""一台电脑上任一份 Express 账套的主档（2026-10-13 生产录制：推往电脑没选的账套 DEMO02 / DEMO03，CC 拿到 0 个科目、
95 张定时任务票卡在「要人选」）。不连库、不依赖 Frappe：连接域的真代码跑在一个内存里的小 Frappe 替身上。

- 读的请求：电脑选的账套与目录照旧交给任何版本；别的账套只要在 '@endpoint' 目录里、可写，就交给读得了它的 CC 桌面端
  （`refresh.ANY_ACCOUNT_SET_MIN_VERSION`），旧版不交、照实说要升级；目录里没有的不交给电脑、直接失败；同一时刻只租一个；
- 读完：主档存进那份账套自己的目录快照（带读它的凭据与代次），不盖电脑所选账套的公共缓存，上报记录里的旧缓存去掉；
- 读的人：推送准备（含证据）、要人选的项、定时任务的科目 / 部门、存货科目组都按账套从那份快照取；
- 要用时自动请电脑读：同一份账套同一时刻只排一个，不打断正在读的；读到前是「正在读」，不是空列表；
  读过很久了在后台再读、照旧先用；刚读失败的照实说、过一阵再试；电脑离线 / 不在电脑的可写账套里照实说。
"""
import json
import pathlib
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "pearnly_cowork"))

EXP = r"C:\ACCOUNT\70EXP"
SETS = {name: EXP + "\\" + name for name in ("DATAZ", "DEMO01", "DEMO02", "DEMO03")}
KEYS = {name: path.lower() for name, path in SETS.items()}
NOW = datetime(2026, 10, 13, 3, 0, 0)  # 站点存的是 UTC 裸时间


class ValidationError(Exception):
    pass


class Doc(dict):
    """够用的 Frappe 文档：属性读写、`get`、insert / save 落到内存库。"""

    def __getattr__(self, key):
        if key.startswith("__"):
            raise AttributeError(key)
        return self.get(key)

    def __setattr__(self, key, value):
        self[key] = value

    @property
    def flags(self):
        return types.SimpleNamespace()

    def is_new(self):
        return not self.get("name") or self["name"] not in DB.docs.get(self["doctype"], {})

    def insert(self, **_ignored):
        self["name"] = self.get("name") or self["record_key"]
        DB.docs.setdefault(self["doctype"], {})[self["name"]] = self
        return self

    def save(self, **_ignored):
        DB.docs.setdefault(self["doctype"], {})[self["name"]] = self
        return self


class Db:
    def __init__(self):
        self.docs = {}

    def get_value(self, doctype, name, fields, as_dict=False, for_update=False):
        row = self.docs.get(doctype, {}).get(name)
        if row is None:
            return None
        if fields == "name":
            return name
        if isinstance(fields, str):
            return row.get(fields)
        return Doc({field: row.get(field) for field in fields})

    def sql(self, query, values=(), as_dict=False):
        rows = self.docs.get("Cowork ERP Refresh", {}).values()
        if "lease_handle=%s" in query:
            return [(row.name,) for row in rows if row.connection == values[0] and row.lease_handle == values[1]]
        if "status IN" in query:
            connection, statuses = values[0], set(values[1:])
            found = sorted((row for row in rows if row.connection == connection and row.status in statuses),
                           key=lambda row: (row.requested_at, row.name))
            return [(row.name,) for row in found]
        raise AssertionError(query)


DB = Db()


def _throw(message, exc=None):
    raise (exc or ValidationError)(message)


def _get_doc(doctype, name=None, for_update=False):
    if isinstance(doctype, dict):
        return Doc(doctype)
    return DB.docs[doctype][name]


frappe = types.ModuleType("frappe")
frappe.__dict__.update(db=DB, throw=_throw, _=lambda text: text, get_doc=_get_doc, ValidationError=ValidationError,
                       PermissionError=ValidationError, local=types.SimpleNamespace(site="testcowork.pearnly.com"),
                       session=types.SimpleNamespace(user="owner@example.com"))
pymysql = types.ModuleType("pymysql")
pymysql_err = types.ModuleType("pymysql.err")
pymysql_err.OperationalError = type("OperationalError", (Exception,), {})
pymysql_err.ProgrammingError = type("ProgrammingError", (Exception,), {})
# 连接的授权、主档映射、银行流水在别的测试里守着：这里给最小替身，只看连接域自己的逻辑
access = types.ModuleType("pearnly_cowork.connections.access")
access.TYPES = ("Cowork ERP Connection", "Cowork ERP Snapshot", "Cowork ERP Refresh")
access.write = lambda doc: doc.insert() if doc.is_new() else doc.save()
access.require_connection = lambda doc, actor=None, **kwargs: actor or "owner@example.com"
masters = types.ModuleType("pearnly_cowork.masters")
masters.push_inputs = types.SimpleNamespace(mappings=lambda client: {"accounts": [], "clients": []},
                                            profiles=lambda workspace, tax: {})
reconciliation = types.ModuleType("pearnly_cowork.reconciliation")
reconciliation.bank_evidence = types.SimpleNamespace(window=lambda owner, date: [])
sys.modules.update({"frappe": frappe, "pymysql": pymysql, "pymysql.err": pymysql_err,
                    "pearnly_cowork.connections.access": access, "pearnly_cowork.masters": masters,
                    "pearnly_cowork.masters.push_inputs": masters.push_inputs,
                    "pearnly_cowork.reconciliation": reconciliation,
                    "pearnly_cowork.reconciliation.bank_evidence": reconciliation.bank_evidence})

from pearnly_cowork.connections import (  # noqa: E402
    account_masters, collector, model, preparation, refresh, state)
from pearnly_cowork.connections.reports import plan_report  # noqa: E402


def chart(prefix, count):
    return [{"code": f"{prefix}{i:02d}-00", "name": f"Account {prefix}{i}", "type": "5"} for i in range(count)]


class AnySetCatalog(unittest.TestCase):
    def setUp(self):
        DB.docs.clear()
        self.now = NOW
        self.version = "1.5.1"
        self.doc = Doc(doctype="Cowork ERP Connection", name="CONN", workspace="W", client="", adapter="express",
                       enabled=1, binding_generation=0, record_revision=1, projection_heads="{}", report_json="{}",
                       config_json=model.dumps({"account_set": SETS["DEMO01"], "account_dir": SETS["DEMO01"],
                                                "express_root": EXP}))
        DB.docs["Cowork ERP Connection"] = {"CONN": self.doc}
        self.heartbeat(accounts=chart("1111", 149), catalog={"products": [], "customers": [],
                                                              "departments": [{"code": "HQ", "name": "Head"}]})

    # ── 心跳（真 plan_report + collector.ingest）────────────────────────────────────────

    def heartbeat(self, *, accounts=None, catalog=None, refresh=None, error=None, version=None):
        body = {"account_set": SETS["DEMO01"], "account_dir": SETS["DEMO01"], "express_root": EXP,
                "companion_version": version or self.version,
                "account_sets": [{"code": name, "path": path, "root": EXP, "writable": True, "company": name}
                                 for name, path in SETS.items()]}
        if accounts is not None:
            body["accounts"] = accounts
        if catalog is not None:
            body["catalog"] = catalog
        if refresh:
            body.update(master_refresh_request_id=refresh["request_id"], master_refresh_scope=refresh["scope_kind"],
                        master_refresh_account_set=refresh["account_set_key"], master_refresh_error=error)
        previous = {**model.parse(self.doc.report_json, {}), **model.parse(self.doc.config_json, {})}
        plan = plan_report(body, previous, self.now.replace(tzinfo=timezone.utc))
        return collector.ingest(self.doc, plan, credential_id="CRED", token_generation=1, issuer="owner@example.com",
                                now=self.now)

    def refreshes(self, status=None):
        rows = DB.docs.get("Cowork ERP Refresh", {}).values()
        return [row for row in rows if status is None or row.status == status]

    def read_demo02(self):
        """电脑读完 DEMO02：心跳带回它的科目表与目录（顶层所选仍是 DEMO01）。"""
        leased = refresh.lease(self.doc, SETS["DEMO01"], "CRED", 1, self.now)
        self.assertEqual((leased["scope_kind"], leased["account_set_key"]), ("account_set", KEYS["DEMO02"]))
        self.heartbeat(accounts=chart("5300", 37), refresh=leased,
                       catalog={"products": [{"code": "P1", "name": "Soap", "kind": "stock"}], "customers": [],
                                "suppliers": [{"code": "S1", "name": "Kaew", "tax_id": "0105599210021"}],
                                "departments": [{"code": "BKK", "name": "Bangkok"}],
                                "isacc_candidates": [{"code": "ST01", "desc": "Stock", "fit": True}]})
        return leased

    # ── 租读的请求 ─────────────────────────────────────────────────────────────────────

    def test_an_account_set_the_computer_did_not_select_is_read_by_a_new_desktop(self):
        state_ = account_masters.ensure(self.doc, KEYS["DEMO02"], "owner@example.com", self.now)
        self.assertEqual((state_["state"], state_.get("requested")), ("reading", True))
        leased = refresh.lease(self.doc, SETS["DEMO01"], "CRED", 1, self.now)
        self.assertEqual(leased["account_set_key"], KEYS["DEMO02"])
        # 同一时刻只租一个：另一份账套的请求等这一个读完
        account_masters.ensure(self.doc, KEYS["DEMO03"], "owner@example.com", self.now)
        self.assertIsNone(refresh.lease(self.doc, SETS["DEMO01"], "CRED", 1, self.now))
        # 轮询领任务（没有心跳的所选）按连接配置的所选判：一样只租这一个
        self.assertIsNone(refresh.lease(self.doc, "", "CRED", 1, self.now))

    def test_the_poll_lease_hands_it_as_well(self):
        """电脑每拍领任务时（`companion/transport.lease`，没有心跳的所选）同样交出去：一般比下一次心跳早。"""
        account_masters.ensure(self.doc, KEYS["DEMO03"], "owner@example.com", self.now)
        leased = refresh.lease(self.doc, "", "CRED", 1, self.now)
        self.assertEqual((leased["scope_kind"], leased["account_set_key"]), ("account_set", KEYS["DEMO03"]))

    def test_an_old_desktop_keeps_reading_only_its_own_account_set(self):
        self.heartbeat(version="1.2.4")
        state_ = account_masters.ensure(self.doc, KEYS["DEMO02"], "owner@example.com", self.now)
        self.assertEqual(state_["state"], "update_needed")  # 不排请求：排了也不会交给它
        self.assertEqual(self.refreshes(), [])
        # 已经排着的（升级前排的）也不交给旧版；它自己选的那份照旧交
        refresh.create(self.doc, "account_set", KEYS["DEMO02"], state.scope(KEYS["DEMO02"], "express")[2],
                       actor="owner@example.com", identity="R-OLD", signature="x", now=self.now)
        self.assertIsNone(refresh.lease(self.doc, SETS["DEMO01"], "CRED", 1, self.now))
        refresh.create(self.doc, "account_set", KEYS["DEMO01"], state.scope(KEYS["DEMO01"], "express")[2],
                       actor="owner@example.com", identity="R-OWN", signature="y", now=self.now)
        self.assertEqual(refresh.lease(self.doc, SETS["DEMO01"], "CRED", 1, self.now)["account_set_key"], KEYS["DEMO01"])
        self.assertTrue(refresh.reads_any_account_set("1.3.0"))
        self.assertFalse(refresh.reads_any_account_set("1.2.4"))
        self.assertFalse(refresh.reads_any_account_set(""))

    def test_a_request_for_an_account_set_outside_the_directory_never_reaches_the_computer(self):
        other = r"d:\elsewhere\book"
        refresh.create(self.doc, "account_set", other, state.scope(other, "express")[2], actor="owner@example.com",
                       identity="R-OUT", signature="z", now=self.now)
        self.assertIsNone(refresh.lease(self.doc, SETS["DEMO01"], "CRED", 1, self.now))
        row = DB.docs["Cowork ERP Refresh"]["R-OUT"]
        self.assertEqual((row.status, row.error_code), ("failed", "ERR_ACCOUNT_SET_UNAVAILABLE"))

    # ── 读完存哪、谁来读 ───────────────────────────────────────────────────────────────

    def test_a_completed_read_is_stored_with_that_account_sets_own_snapshot(self):
        account_masters.ensure(self.doc, KEYS["DEMO02"], "owner@example.com", self.now)
        self.doc.report_json = model.dumps({**model.parse(self.doc.report_json), "_account_caches": {"x": {}}})
        self.read_demo02()
        done = self.refreshes("succeeded")
        self.assertEqual(len(done), 1)
        heads = state.heads(self.doc)
        head = heads[state.scope(KEYS["DEMO02"], "express")[2]]
        self.assertEqual(head["status"], "fresh")
        snapshot = DB.docs["Cowork ERP Snapshot"][head["snapshot_id"]]
        self.assertEqual((snapshot.scope_kind, snapshot.scope_key), ("account_set", KEYS["DEMO02"]))
        body = model.parse(snapshot.body)
        self.assertEqual(len(body["masters"]["accounts"]), 37)
        self.assertEqual((body["reported"]["_credential"], body["reported"]["_generation"]), ("CRED", 1))
        self.assertEqual(body["reported"]["reported_suppliers"][0]["tax_id"], "0105599210021")
        self.assertEqual(done[0].result_revision, snapshot.projection_revision)
        # 电脑所选账套（DEMO01）的公共缓存不被盖掉；上报记录里不再存别的账套的主档
        report = model.parse(self.doc.report_json)
        self.assertEqual(len(report["reported_accounts"]), 149)
        self.assertNotIn("_account_caches", report)

    def test_every_reader_takes_the_account_sets_own_masters(self):
        account_masters.ensure(self.doc, KEYS["DEMO02"], "owner@example.com", self.now)
        self.read_demo02()
        found = preparation.reported_masters(self.doc, KEYS["DEMO02"])
        self.assertEqual((len(found["reported_accounts"]), found["reported_departments"][0]["code"]), (37, "BKK"))
        self.assertEqual(len(preparation.reported_masters(self.doc, KEYS["DEMO01"])["reported_accounts"]), 149)
        self.assertEqual(preparation.reported_stock_groups(self.doc, KEYS["DEMO02"])[0]["acccod"], "ST01")
        self.assertIsNone(preparation.reported_masters(self.doc, KEYS["DEMO03"]))  # 没读过是「不知道」
        self.assertEqual(account_masters.view(self.doc, KEYS["DEMO02"], self.now)["state"], "ready")
        # 换了凭据（重新配对）：旧凭据读回的不算
        self.doc.report_credential = "NEW"
        self.assertIsNone(preparation.reported_masters(self.doc, KEYS["DEMO02"]))

    def test_push_preparation_for_a_set_the_computer_did_not_select_uses_its_own_masters(self):
        account_masters.ensure(self.doc, KEYS["DEMO02"], "owner@example.com", self.now)
        self.read_demo02()
        loaded = types.SimpleNamespace(doc=types.SimpleNamespace(name="CONN"), revision=1,
                                       access=types.SimpleNamespace(user_id="owner@example.com"))
        target = types.SimpleNamespace(account_set=SETS["DEMO02"], root_key=EXP, endpoint_id="CONN",
                                       user_id="owner@example.com", tenant_id="testcowork.pearnly.com",
                                       binding_generation=0)
        facts, evidence = preparation.collect(loaded, target, seller_tax="0105599210021", invoice_date="2026-10-01")
        self.assertEqual(len(facts["reported_config"]["reported_accounts"]), 37)
        self.assertEqual(facts["reported_config"]["reported_suppliers"][0]["code"], "S1")
        self.assertEqual(facts["reported_config"]["companion_version"], self.version)
        self.assertEqual(evidence["states"]["reported_config"], "present")
        self.assertEqual(evidence["credential"], {"credential_id": "CRED", "generation": 1,
                                                  "issuer": "owner@example.com"})
        # 确认发送时重核：同一份资料，证据一致
        preparation.revalidate(loaded, target, evidence)

    # ── 要用时自动请电脑读 ─────────────────────────────────────────────────────────────

    def test_asking_again_while_reading_does_not_queue_or_interrupt(self):
        first = account_masters.ensure(self.doc, KEYS["DEMO02"], "owner@example.com", self.now)
        again = account_masters.ensure(self.doc, KEYS["DEMO02"], "owner@example.com", self.now)
        self.assertTrue(first["requested"])
        self.assertEqual((again["state"], again.get("requested")), ("reading", None))
        leased = refresh.lease(self.doc, SETS["DEMO01"], "CRED", 1, self.now)
        account_masters.ensure(self.doc, KEYS["DEMO02"], "owner@example.com", self.now)
        self.assertEqual([row.status for row in self.refreshes()], ["leased"])  # 正在读的不被顶掉
        self.assertEqual(self.refreshes()[0].lease_handle, leased["request_id"])

    def test_old_masters_are_read_again_in_the_background_and_still_used(self):
        account_masters.ensure(self.doc, KEYS["DEMO02"], "owner@example.com", self.now)
        self.read_demo02()
        self.assertIsNone(account_masters.ensure(self.doc, KEYS["DEMO02"], "owner@example.com",
                                                 self.now + timedelta(minutes=5)).get("requested"))
        later = account_masters.ensure(self.doc, KEYS["DEMO02"], "owner@example.com", self.now + timedelta(minutes=31))
        self.assertEqual((later["state"], later.get("requested")), ("ready", True))
        self.assertEqual(len(preparation.reported_masters(self.doc, KEYS["DEMO02"])["reported_accounts"]), 37)
        # 电脑所选那份由它自己按期上报，不另排
        self.assertIsNone(account_masters.ensure(self.doc, KEYS["DEMO01"], "owner@example.com",
                                                 self.now + timedelta(hours=5)).get("requested"))

    def test_a_failed_read_is_reported_and_retried_later(self):
        account_masters.ensure(self.doc, KEYS["DEMO02"], "owner@example.com", self.now)
        leased = refresh.lease(self.doc, SETS["DEMO01"], "CRED", 1, self.now)
        self.heartbeat(refresh=leased, error="ERR_TARGET_CATALOG_SCAN_FAILED")
        soon = account_masters.ensure(self.doc, KEYS["DEMO02"], "owner@example.com", self.now + timedelta(minutes=2))
        self.assertEqual((soon["state"], soon["error_code"], soon.get("requested")),
                         ("failed", "ERR_TARGET_CATALOG_SCAN_FAILED", None))
        later = account_masters.ensure(self.doc, KEYS["DEMO02"], "owner@example.com", self.now + timedelta(minutes=11))
        self.assertEqual((later["state"], later.get("requested")), ("reading", True))

    def test_offline_and_unlisted_account_sets_are_said_plainly(self):
        offline = plan_report({"offline": True}, {}, self.now.replace(tzinfo=timezone.utc))
        collector.ingest(self.doc, offline, credential_id="CRED", token_generation=1, issuer="owner@example.com",
                         now=self.now)
        self.assertEqual(account_masters.ensure(self.doc, KEYS["DEMO03"], "owner@example.com", self.now)["state"],
                         "offline")
        self.heartbeat()
        self.assertEqual(account_masters.view(self.doc, r"c:\account\70exp\gone", self.now)["state"], "unavailable")
        self.assertEqual(account_masters.view(self.doc, KEYS["DEMO01"], self.now)["state"], "ready")


if __name__ == "__main__":
    unittest.main()
