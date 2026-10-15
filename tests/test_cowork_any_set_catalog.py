"""一台电脑上任一份 Express 账套的主档（2026-10-13 生产录制：推往电脑没选的账套 DEMO02 / DEMO03，CC 拿到 0 个科目、
95 张定时任务票卡在「要人选」）。不连库、不依赖 Frappe：连接域的真代码跑在一个内存里的小 Frappe 替身上。

- 读的请求：电脑选的账套与目录照旧交给任何版本；别的账套只要在 '@endpoint' 目录里、可写，就交给读得了它的 CC 桌面端
  （`refresh.ANY_ACCOUNT_SET_MIN_VERSION`），旧版不交、照实说要升级；目录里没有的不交给电脑、直接失败；同一时刻只租一个；
- 读完：主档存进那份账套自己的账套资料（ADR-021 第 2 批起，`pool.facts`；以前是那份账套的目录快照），不盖电脑所选账套
  的那份，上报记录里不再存任何账套的主档；
- 读的人：推送准备（含证据）、要人选的项、定时任务的科目 / 部门、存货科目组都按账套从账套资料取；
- 要用时自动请电脑读：同一份账套同一时刻只排一个，不打断正在读的；读到前是「正在读」，不是空列表；
  读过很久了在后台再读、照旧先用；刚读失败的照实说、过一阵再试；电脑离线 / 不在电脑的可写账套里照实说。
"""
import pathlib
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cowork_pool_fake as fake  # noqa: E402

fake.install()
DB, Doc = fake.DB, fake.Doc

from pearnly_cowork.connections import (  # noqa: E402
    account_masters, collector, model, pool, preparation, refresh, state)
from pearnly_cowork.connections.reports import plan_report  # noqa: E402

EXP = r"C:\ACCOUNT\70EXP"
SETS = {name: EXP + "\\" + name for name in ("DATAZ", "DEMO01", "DEMO02", "DEMO03")}
KEYS = {name: path.lower() for name, path in SETS.items()}
NOW = datetime(2026, 10, 13, 3, 0, 0)  # 站点存的是 UTC 裸时间


def chart(prefix, count):
    return [{"code": f"{prefix}{i:02d}-00", "name": f"Account {prefix}{i}", "type": "5"} for i in range(count)]


class AnySetCatalog(unittest.TestCase):
    def setUp(self):
        DB.clear()
        self.now = NOW
        self.version = "1.5.1"
        self.doc = Doc(doctype="Cowork ERP Connection", name="CONN", workspace="W", client="", adapter="express",
                       enabled=1, binding_generation=0, record_revision=1, projection_heads="{}", report_json="{}",
                       config_json=model.dumps({"account_set": SETS["DEMO01"], "account_dir": SETS["DEMO01"],
                                                "express_root": EXP}))
        DB.docs["Cowork ERP Connection"] = {"CONN": self.doc}
        # 补丁建的「旧电脑」：旧版 CC 桌面版，账套按路径认（ADR-021）
        self.pc = fake.Computer(self.doc, "CCD-OLD")
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
        return self.pc.report(body, self.now)

    def lease(self, selected=None, now=None):
        return self.pc.lease(now or self.now, selected)

    def refreshes(self, status=None):
        rows = DB.docs.get("Cowork ERP Refresh", {}).values()
        return [row for row in rows if status is None or row.status == status]

    def read_demo02(self):
        """电脑读完 DEMO02：心跳带回它的科目表与目录（顶层所选仍是 DEMO01）。"""
        leased = self.lease(SETS["DEMO01"])
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
        leased = self.lease(SETS["DEMO01"])
        self.assertEqual(leased["account_set_key"], KEYS["DEMO02"])
        # 同一时刻只租一个：另一份账套的请求等这一个读完
        account_masters.ensure(self.doc, KEYS["DEMO03"], "owner@example.com", self.now)
        self.assertIsNone(self.lease(SETS["DEMO01"]))
        # 轮询领任务（没有心跳的所选）按这台电脑上次报的所选判：一样只租这一个
        self.assertIsNone(self.lease())

    def test_the_poll_lease_hands_it_as_well(self):
        """电脑每拍领任务时（`companion/transport.lease`，没有心跳的所选）同样交出去：一般比下一次心跳早。"""
        account_masters.ensure(self.doc, KEYS["DEMO03"], "owner@example.com", self.now)
        leased = self.lease()
        self.assertEqual((leased["scope_kind"], leased["account_set_key"]), ("account_set", KEYS["DEMO03"]))

    def test_an_old_desktop_keeps_reading_only_its_own_account_set(self):
        self.heartbeat(version="1.2.4")
        state_ = account_masters.ensure(self.doc, KEYS["DEMO02"], "owner@example.com", self.now)
        self.assertEqual(state_["state"], "update_needed")  # 不排请求：排了也不会交给它
        self.assertEqual(self.refreshes(), [])
        # 已经排着的（升级前排的）也不交给旧版；它自己选的那份照旧交
        refresh.create(self.doc, "account_set", KEYS["DEMO02"], state.scope(KEYS["DEMO02"], "express")[2],
                       actor="owner@example.com", identity="R-OLD", signature="x", now=self.now)
        self.assertIsNone(self.lease(SETS["DEMO01"]))
        refresh.create(self.doc, "account_set", KEYS["DEMO01"], state.scope(KEYS["DEMO01"], "express")[2],
                       actor="owner@example.com", identity="R-OWN", signature="y", now=self.now)
        self.assertEqual(self.lease(SETS["DEMO01"])["account_set_key"], KEYS["DEMO01"])
        self.assertTrue(refresh.reads_any_account_set("1.3.0"))
        self.assertFalse(refresh.reads_any_account_set("1.2.4"))
        self.assertFalse(refresh.reads_any_account_set(""))

    def test_a_request_for_an_account_set_outside_the_directory_never_reaches_the_computer(self):
        other = r"d:\elsewhere\book"
        refresh.create(self.doc, "account_set", other, state.scope(other, "express")[2], actor="owner@example.com",
                       identity="R-OUT", signature="z", now=self.now)
        self.assertIsNone(self.lease(SETS["DEMO01"]))
        row = DB.docs["Cowork ERP Refresh"]["R-OUT"]
        self.assertEqual((row.status, row.error_code), ("failed", "ERR_ACCOUNT_SET_UNAVAILABLE"))

    # ── 读完存哪、谁来读 ───────────────────────────────────────────────────────────────

    def test_a_completed_read_is_stored_with_that_account_sets_own_facts(self):
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
        self.assertEqual(len(body["masters"]["accounts"]), 37)  # 网页 / LINE 的下拉照旧
        self.assertNotIn("reported", body)
        self.assertEqual(done[0].result_revision, snapshot.projection_revision)
        facts = pool.facts("CONN", KEYS["DEMO02"])
        self.assertEqual(facts["reported_suppliers"][0]["tax_id"], "0105599210021")
        self.assertEqual((facts["_device"], facts["_routine"]), ("CCD-OLD", False))
        self.assertNotIn("companion_version", facts)  # 版本属于电脑，不属于账套
        # 电脑所选账套（DEMO01）的那份不被盖掉；上报记录里不再存任何账套的主档
        self.assertEqual(len(pool.facts("CONN", KEYS["DEMO01"])["reported_accounts"]), 149)
        report = model.parse(self.doc.report_json)
        self.assertNotIn("reported_accounts", report)
        self.assertNotIn("_account_caches", report)
        # 第 3 批：上报记录也不再存上报绑定与版本（证据 v2 不绑密钥，版本属于各台电脑），两台轮流上报不来回重写连接行
        self.assertEqual(set(report), set())

    def test_every_reader_takes_the_account_sets_own_masters(self):
        account_masters.ensure(self.doc, KEYS["DEMO02"], "owner@example.com", self.now)
        self.read_demo02()
        found = preparation.reported_masters(self.doc, KEYS["DEMO02"])
        self.assertEqual((len(found["reported_accounts"]), found["reported_departments"][0]["code"]), (37, "BKK"))
        self.assertEqual(len(preparation.reported_masters(self.doc, KEYS["DEMO01"])["reported_accounts"]), 149)
        self.assertEqual(preparation.reported_stock_groups(self.doc, KEYS["DEMO02"])[0]["acccod"], "ST01")
        self.assertIsNone(preparation.reported_masters(self.doc, KEYS["DEMO03"]))  # 没读过是「不知道」
        self.assertEqual(account_masters.view(self.doc, KEYS["DEMO02"], self.now)["state"], "ready")
        # 换了密钥（重新配对、换一台电脑）：资料属于这份账套，不属于读它的那把密钥（ADR-021，同一份物理账套）
        self.doc.report_credential = "NEW"
        self.assertEqual(len(preparation.reported_masters(self.doc, KEYS["DEMO02"])["reported_accounts"]), 37)

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
        self.assertNotIn("companion_version", facts["reported_config"])  # 载荷版本在领取时按领取的电脑判
        self.assertEqual(evidence["states"]["reported_config"], "present")
        # 证据 v2 不绑任何电脑或密钥（ADR-021 第 3 批）：只认这份账套
        self.assertEqual((evidence["version"], evidence["route"]), (2, {"connection": "CONN", "account_key": KEYS["DEMO02"]}))
        self.assertNotIn("credential", evidence)
        # 确认发送时重核：同一份资料，证据一致
        preparation.revalidate(loaded, target, evidence)

    # ── 要用时自动请电脑读 ─────────────────────────────────────────────────────────────

    def test_asking_again_while_reading_does_not_queue_or_interrupt(self):
        first = account_masters.ensure(self.doc, KEYS["DEMO02"], "owner@example.com", self.now)
        again = account_masters.ensure(self.doc, KEYS["DEMO02"], "owner@example.com", self.now)
        self.assertTrue(first["requested"])
        self.assertEqual((again["state"], again.get("requested")), ("reading", None))
        leased = self.lease(SETS["DEMO01"])
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
        # 电脑所选那份由它自己按期上报（每 30 分钟带一次目录），不另排
        self.now += timedelta(hours=5)
        self.heartbeat(accounts=chart("1111", 149))
        self.assertIsNone(account_masters.ensure(self.doc, KEYS["DEMO01"], "owner@example.com",
                                                 self.now + timedelta(minutes=40)).get("requested"))

    def test_a_failed_read_is_reported_and_retried_later(self):
        account_masters.ensure(self.doc, KEYS["DEMO02"], "owner@example.com", self.now)
        leased = self.lease(SETS["DEMO01"])
        self.heartbeat(refresh=leased, error="ERR_TARGET_CATALOG_SCAN_FAILED")
        soon = account_masters.ensure(self.doc, KEYS["DEMO02"], "owner@example.com", self.now + timedelta(minutes=2))
        self.assertEqual((soon["state"], soon["error_code"], soon.get("requested")),
                         ("failed", "ERR_TARGET_CATALOG_SCAN_FAILED", None))
        later = account_masters.ensure(self.doc, KEYS["DEMO02"], "owner@example.com", self.now + timedelta(minutes=11))
        self.assertEqual((later["state"], later.get("requested")), ("reading", True))

    def test_offline_and_unlisted_account_sets_are_said_plainly(self):
        self.pc.report({"offline": True}, self.now)
        self.assertEqual(account_masters.ensure(self.doc, KEYS["DEMO03"], "owner@example.com", self.now)["state"],
                         "offline")
        self.heartbeat()
        self.assertEqual(account_masters.view(self.doc, r"c:\account\70exp\gone", self.now)["state"], "unavailable")
        self.assertEqual(account_masters.view(self.doc, KEYS["DEMO01"], self.now)["state"], "ready")


if __name__ == "__main__":
    unittest.main()
