"""Cowork 通道上的 MR.ERP 代录单：派单、领单、重发、回收、回执与账号租约（ADR-026 第 5、8 条，第 3 步）。

开关是共用层全局常量 `contracts.MRERP_OPERATOR_ENABLED`（主会话 11-03：默认关、不按站点开）。两种状态都锁：

- **关着**：领单从不看 MR.ERP 代录单（不查、不派），Express 领单与改动前一样；
- **开着**（测试里打补丁）：
  - 只派给发起人选定的那台（``route_device``）、它报过 ``mrerp``；旧版（不报目标）/ 只报 Express 的不领、单子等它、不转人工；
    报了 ``mrerp`` 但载荷版本不够转人工（``payload_version_outdated``）；
  - Express 的单先领（这台有 Express 可领就不看 MR.ERP）；没有可写 Express 账套的电脑照样领自己的 MR.ERP 代录单；
  - 领前租账号（「网址 + 用户名」），租不到回滚这次领取、不发；载荷原样交（不做账套位置替换）、投递挂在来领的密钥的连接上；
  - 丢在电脑手里的 MR.ERP 代录单按记录自己的连接重发回原电脑，重发前重新租账号、租不到先不发；
  - 别的电脑过期的：那台在场就续领取与账号，不在就转结果不明；
  - 回执后这批再没有排着 / 领着的单就放开账号（回到排队的这一张自己也算还有单，11-03 修）；Express 的账套登记不碰；
  - 没有 Express 连接的工作空间（11-03 选 A，`MrErpOwnChannel`）：MR.ERP 连接自己的密钥领这条连接上派给这台的代录单、不进 Express
    排头、不领主档读取；重发 / 回收 / 回执都跟着它自己的投递走；
  - Express 的全部领单测试在常量开着时照旧通过（`ExpressUnchangedWithMrErpOn` 重跑 `test_cowork_express_dispatch.Transport`）。
- `companion.mrerp_jobs` 本身：归属核对、用户名先后（电脑报的 → 站点连接上存的 → 都没有不拦）、租 / 续 / 放 / 过期（假的锁仓库按
  ``OPERATOR_LEASE_SEC`` 过期；真 Redis 上同一把锁的语义见 `scripts/test-session-lock.py` 与第 10 步记录）。

不连库、不依赖 Frappe：在替身模块上加载真 `companion/transport.py`、`companion/mrerp_jobs.py`（同 `test_cowork_express_dispatch.py`）。
"""
import json
import pathlib
import sys
import types
import unittest
from datetime import timedelta
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import test_cowork_express_dispatch as base  # noqa: E402  （只用模块名引用，免得 unittest 把它的用例再收一遍）
from pearnly_core.cc_desktop.wire import queue_contracts, route_contracts as rc  # noqa: E402
from pearnly_core.express_operator import contracts  # noqa: E402

Record, A, B, ME, NOW = base.Record, base.A, base.B, base.ME, base.NOW
MRERP_CONN = "CWEC-MRERP"
BATCH = "OPB-1"


def operator_payload(version=18, batch=BATCH):
    return {"payload_version": version, "entry_mode": "operator",
            "operator": {"batch": {"id": batch}, "target": contracts.TARGET_MRERP},
            "mrerp": {"connection": MRERP_CONN, "doc_type": "purchase", "docnum": "690912-000001", "lines": []}}


class FakeJobs:
    """`companion.mrerp_jobs` 的替身：排头按测试给的顺序全回（真查询只回派给这台的，这里让派单规则再挡一次）。"""

    def __init__(self, test):
        self.test = test

    def heads(self, credential, device, own=False):
        self.test.calls.append(("mrerp_heads", device) + (("own",) if own else ()))
        return [Record(row) for row in self.test.mrerp]

    def context(self, row, credential, device):
        if (row.route_device or "") != device:
            raise base._Permission("not this computer")
        return types.SimpleNamespace(connection=row.connection, name=row.name)

    def take(self, row, device):
        self.test.calls.append(("take", row.name, device))
        return self.test.account_free

    def is_job(self, row):
        return row.connection == MRERP_CONN

    def release_if_done(self, row, device):
        self.test.calls.append(("release", row.name, device))
        return True


# 领单编排的替身环境（`test_cowork_express_dispatch.Transport` 的 setUp 与辅助方法，不带它的用例）
Harness = type("Harness", (unittest.TestCase,),
               {key: value for key, value in vars(base.Transport).items() if not key.startswith("test_")})


def _mrerp_setup(test):
    test.mrerp, test.account_free, test.deliveries = [], True, []
    test.transport.mrerp_jobs = FakeJobs(test)
    test.transport.delivery_store = types.SimpleNamespace(
        owner=lambda credential: "companion:owner",
        create=lambda credential, claim, connection=None: test.deliveries.append(connection) or Record(
            name="1b4e28ba-2fa1-11d2-883f-0016d3cca427"))
    def get_doc(doctype, name, for_update=False):  # 记录自己的字段（连接、派给哪台）盖过替身的默认值
        found = dict(name=name, connection="CONN", workspace="WS", client="", branch="legacy", binding_generation=0,
                     actor=ME, history="H-" + name, history_version=1, account_key=test.key_of(name),
                     connection_revision=1)
        found.update(test.records.get(name, {}))
        return Record(found)

    test.transport.frappe.get_doc = get_doc
    db = test.transport.frappe.db
    db.savepoint = lambda name: test.calls.append(("savepoint", name.split("_")[1]))
    db.rollback = lambda save_point=None: test.calls.append(("rollback", str(save_point or "").split("_")[1]))


class ExpressUnchangedWithMrErpOn(base.Transport):
    """常量开着、这台有一张派给它但它还不会的 MR.ERP 代录单：Express 领单的全部用例照旧。"""

    def setUp(self):
        super().setUp()
        _mrerp_setup(self)
        self.enterContext(mock.patch.object(contracts, "MRERP_OPERATOR_ENABLED", True))
        self.mrerp = [base._row("CWP-M0", "", route_device=A, version=18)]
        self.records["CWP-M0"] = {"connection": MRERP_CONN, "route_device": A}


class MrErpLease(Harness):
    def setUp(self):
        Harness.setUp(self)
        _mrerp_setup(self)
        self.enterContext(mock.patch.object(contracts, "MRERP_OPERATOR_ENABLED", True))
        self.peers[A]["targets"] = frozenset({"express", "mrerp"})
        self.leased_on = []

    def job(self, name="CWP-M1", device=A, version=18, created=0):
        row = base._row(name, "", route_device=device, version=version, created=created)
        self.mrerp.append(row)
        self.records[name] = {"connection": MRERP_CONN, "route_device": device}
        return row

    def lease(self, name, *, context, adapter, now=None, redeliver=False, writer=""):
        self.leased_on.append((name, adapter.connection, getattr(context, "connection", None)))
        if name.startswith("CWP-M"):
            self.calls.append(("lease", name, adapter.route, adapter.device) + (("redeliver",) if redeliver else ()))
            outcome = self.outcome.get(name, "leased")
            if isinstance(outcome, Exception):
                raise outcome
            return {"leased": True, "record": {"name": name, "history": "H-" + name, "invoice_no": "INV",
                                               "frozen_payload": operator_payload()}, "fence": 1, "attempt_token": "t"}
        return Harness.lease(self, name, context=context, adapter=adapter, now=now, redeliver=redeliver)

    def leases(self):
        return [call[1] for call in self.calls if call[0] == "lease"]

    # ── 常量关着 ──

    def test_with_the_constant_off_mrerp_jobs_are_never_looked_at(self):
        self.job()
        with mock.patch.object(contracts, "MRERP_OPERATOR_ENABLED", False):
            self.assertEqual(self.run_lease()["jobs"], [])
        self.assertFalse([call for call in self.calls if call[0] in ("mrerp_heads", "lease", "take", "quarantine")])
        self.assertEqual(self.deliveries, [])

    # ── 派给谁 ──

    def test_the_chosen_computer_that_reports_mrerp_takes_its_job_untouched(self):
        self.job()
        jobs = self.run_lease()["jobs"]
        self.assertEqual([call for call in self.calls if call[0] == "lease"], [("lease", "CWP-M1", rc.READER, A)])
        # 账本按记录自己的 MR.ERP 连接核锁与权限；投递挂在来领的密钥的连接上（传给 delivery_store 的是记录的连接，供它核归属）
        self.assertEqual(self.leased_on, [("CWP-M1", MRERP_CONN, MRERP_CONN)])
        self.assertEqual(self.deliveries, [MRERP_CONN])
        self.assertIn(("take", "CWP-M1", A), self.calls)
        self.assertEqual(jobs[0]["payload"], operator_payload())  # 原样交：没有账套位置替换
        self.assertEqual(jobs[0]["task_key"], queue_contracts.task_key("CWP-M1"))
        self.assertNotIn("rollback", [call[0] for call in self.calls])

    def test_another_computers_job_is_not_taken(self):
        self.job(device=B)
        self.assertEqual(self.run_lease()["jobs"], [])
        self.assertFalse([call for call in self.calls if call[0] in ("lease", "take", "quarantine")])

    def test_an_old_cc_or_one_without_mrerp_waits_without_going_to_manual(self):
        self.job()
        for targets in (None, frozenset({"express"})):
            with self.subTest(targets=targets):
                self.calls.clear()
                self.peers[A]["targets"] = targets
                self.assertEqual(self.run_lease()["jobs"], [])
                self.assertFalse([call for call in self.calls if call[0] in ("lease", "take", "quarantine")])

    def test_reporting_mrerp_with_a_too_old_payload_version_goes_to_manual(self):
        self.job()
        self.peers[A]["payload_version"] = 17
        self.assertEqual(self.run_lease()["jobs"], [])
        self.assertIn(("quarantine", "CWP-M1", "payload_version_outdated"), self.calls)
        self.assertFalse(self.leases())

    def test_express_work_goes_first(self):
        self.job()
        self.pending = [base._row("CWP-1", "s1")]
        self.run_lease()
        self.assertEqual(self.leases(), ["CWP-1"])
        self.assertNotIn(("mrerp_heads", A), self.calls)

    def test_a_computer_with_no_writable_express_set_still_takes_its_mrerp_job(self):
        self.reach = {}
        self.job()
        self.assertEqual(len(self.run_lease()["jobs"]), 1)
        self.assertEqual(self.leases(), ["CWP-M1"])

    def test_a_busy_computer_takes_nothing(self):
        self.job()
        self.busy = True
        self.assertEqual(self.run_lease()["jobs"], [])
        self.assertFalse(self.leases())

    # ── 账号租约与领取失败 ──

    def test_an_account_held_elsewhere_rolls_the_claim_back_and_sends_nothing(self):
        self.job()
        self.account_free = False
        self.assertEqual(self.run_lease()["jobs"], [])
        self.assertEqual(self.leases(), ["CWP-M1"])
        savepoints = [call[1] for call in self.calls if call[0] == "savepoint" and call[1] == "mrerp"]
        self.assertEqual(len(savepoints), 1)
        self.assertIn(("rollback", "mrerp"), self.calls)
        self.assertEqual(self.deliveries, [])

    def test_a_locked_head_is_skipped_and_the_next_one_flows(self):
        self.job("CWP-M1")
        self.job("CWP-M2", created=1)
        self.locks_taken = {"CWP-M1"}
        self.run_lease()
        self.assertEqual(self.leases(), ["CWP-M2"])

    def test_lease_failures_roll_back_and_only_a_definite_rejection_goes_to_manual(self):
        self.job("CWP-M1")
        self.job("CWP-M2", created=1)
        self.outcome = {"CWP-M1": base._Unavailable("busy")}
        self.assertEqual(len(self.run_lease()["jobs"]), 1)
        self.assertEqual(self.leases(), ["CWP-M1", "CWP-M2"])
        self.assertFalse([call for call in self.calls if call[0] == "quarantine"])
        self.calls.clear()
        self.outcome = {"CWP-M1": base._Rejected("push.native_target_changed"), "CWP-M2": base._Permission("lost")}
        self.assertEqual(self.run_lease()["jobs"], [])
        self.assertIn(("quarantine", "CWP-M1", "push.native_target_changed"), self.calls)
        self.assertIn(("quarantine", "CWP-M2", "companion.queue_access_revoked"), self.calls)
        self.assertEqual([call for call in self.calls if call[0] == "rollback"], [("rollback", "mrerp")] * 2)

    # ── 丢在电脑手里的、过期的 ──

    def lost_mrerp(self, name="CWP-M9", status="leased"):
        self.version = "1.12.11"
        self.mine = [Record(name=name, account_key="", owner_id="companion:old")]
        self.records[name] = {"connection": MRERP_CONN, "route_device": A, "status": status, "written_by_device": A,
                              "entered_by": ME, "write_route": rc.READER, "result_unknown": int(status == "manual"),
                              "reason": ""}

    def test_a_lost_mrerp_job_goes_back_to_its_computer_on_its_own_connection(self):
        self.lost_mrerp()
        self.reach = {}  # MR.ERP 不看账套可达
        jobs = self.run_lease()["jobs"]
        self.assertEqual([call for call in self.calls if call[0] == "lease"],
                         [("lease", "CWP-M9", rc.READER, A, "redeliver")])
        self.assertEqual(self.leased_on, [("CWP-M9", MRERP_CONN, MRERP_CONN)])
        self.assertIn(("take", "CWP-M9", A), self.calls)
        self.assertEqual((jobs[0]["task_key"], jobs[0]["payload"]), (queue_contracts.task_key("CWP-M9"), operator_payload()))
        self.assertEqual(self.deliveries, [MRERP_CONN])

    def test_a_lost_mrerp_job_is_not_resent_while_its_account_is_held_elsewhere(self):
        self.lost_mrerp()
        self.account_free = False
        self.assertEqual(self.run_lease()["jobs"], [])
        self.assertFalse([call for call in self.calls if call[0] in ("lease", "recover")])

    def test_a_lost_mrerp_job_that_cannot_go_back_is_left_to_people(self):
        self.lost_mrerp()
        self.redeliverable = False
        self.assertEqual(self.run_lease()["jobs"], [])
        self.assertIn(("recover", "CWP-M9", True), self.calls)

    def test_an_expired_mrerp_job_of_a_present_computer_is_extended_with_its_account(self):
        self.expired = [Record(name="CWP-M7", owner_id="companion:b", written_by_device=B),
                        Record(name="CWP-M8", owner_id="companion:c", written_by_device="PC-GONE")]
        self.records["CWP-M7"] = {"connection": MRERP_CONN, "route_device": B}
        self.records["CWP-M8"] = {"connection": MRERP_CONN, "route_device": "PC-GONE"}
        self.present = {B}
        self.run_lease()
        self.assertIn(("extend", "CWP-M7"), self.calls)
        self.assertIn(("take", "CWP-M7", B), self.calls)
        self.assertIn(("recover", "CWP-M8", False), self.calls)
        self.assertNotIn(("take", "CWP-M8", "PC-GONE"), self.calls)  # 电脑不在：账号按 TTL 过期，不续

    def test_an_expired_record_that_is_no_longer_this_computers_is_left_alone(self):
        self.expired = [Record(name="CWP-M6", owner_id="companion:b", written_by_device=B)]
        self.records["CWP-M6"] = {"connection": MRERP_CONN, "route_device": "PC-OTHER"}
        self.present = {B}
        self.run_lease()
        self.assertFalse([call for call in self.calls if call[0] in ("extend", "recover", "take")])

    # ── 回执 ──

    def ack(self, status, connection=MRERP_CONN):
        written = []
        self.transport.account_pool = types.SimpleNamespace(
            mark_stale=lambda *a: written.append("stale"), note_written=lambda *a: written.append("written"))
        self.transport.credentials = types.SimpleNamespace(device_of=lambda name: A, request_full_report=lambda name: None)
        row = Record(name="CWP-M1", connection=connection, account_key="s1")
        self.transport._after_ack(row, Record(credential_id="KEY-A", connection="CONN"), {"error": None},
                                  {"status": status})
        return written

    def test_after_the_receipt_the_batch_is_asked_to_release_and_the_express_register_is_untouched(self):
        # 回执落定后（存上 / 回到排队 / 转人工）都交 `release_if_done` 判这批还有没有单；回到排队的这一张自己算还有单，
        # 由它按回执后的新状态判（`Jobs.test_a_job_sent_back_to_the_queue_keeps_its_batchs_account`）
        for status in ("success", "pending", "manual"):
            with self.subTest(status=status):
                self.calls.clear()
                self.assertEqual(self.ack(status), [])
                self.assertEqual(self.calls, [("release", "CWP-M1", A)])
        self.calls.clear()
        self.assertEqual(self.ack("leased"), [])  # 还在电脑手里（中途汇报）：不放
        self.assertEqual(self.calls, [])
        self.assertEqual(self.ack("success", connection="CONN"), ["written"])  # Express 记录：照旧记账套登记

    def test_acknowledge_builds_the_ledger_context_on_the_records_own_connection(self):
        seen = []
        delivery = Record(name="1b4e28ba-2fa1-11d2-883f-0016d3cca427", push_record="CWP-M1", credential_id="KEY-A",
                          owner_id="companion:owner", fence=1, connection="CONN")
        self.records["CWP-M1"] = {"connection": MRERP_CONN, "route_device": A}
        self.transport.receipt_auth = types.SimpleNamespace(revalidate=lambda context: delivery)
        self.transport.delivery_store = types.SimpleNamespace(secret=lambda doc: "t")
        self.transport.ledger.ack = lambda name, *, context, adapter, **kw: seen.append(
            (name, context.connection, adapter.connection, kw["success"])) or {"status": "success"}
        with mock.patch.object(queue_contracts, "ack_response", lambda result: result):  # 回执回复的形状不是这里要测的
            reply = self.transport.acknowledge(self.credential, {"log_id": delivery.name, "result": "success",
                                                                 "express_docnum": "690912-000001"})
        self.assertEqual(seen, [("CWP-M1", MRERP_CONN, MRERP_CONN, True)])
        self.assertEqual(reply["status"], "success")
        self.assertIn(("release", "CWP-M1", A), self.calls)


class MrErpOwnChannel(MrErpLease):
    """只有 MR.ERP、没有 Express 连接的工作空间（11-03 选 A）：CC 经 MR.ERP 连接自己的密钥来领。"""

    def setUp(self):
        MrErpLease.setUp(self)
        self.credential.connection = MRERP_CONN
        self.refreshes = []
        self.transport.refresh = types.SimpleNamespace(lease=lambda *a, **k: self.refreshes.append(a) or None)
        values = self.transport.frappe.db.get_value

        def get_value(doctype, name, field, as_dict=False):
            if doctype == "Cowork ERP Connection" and field == "adapter":
                return {MRERP_CONN: "mrerp", "CONN": "express"}.get(name)
            return values(doctype, name, field, as_dict=as_dict)

        self.transport.frappe.db.get_value = get_value

    def test_only_with_the_constant_on_is_an_mrerp_key_an_operator_channel(self):
        self.assertTrue(self.transport._operator_channel(self.credential))
        with mock.patch.object(contracts, "MRERP_OPERATOR_ENABLED", False):
            self.assertFalse(self.transport._operator_channel(self.credential))
        express = types.SimpleNamespace(**{**vars(self.credential), "connection": "CONN"})
        self.assertFalse(self.transport._operator_channel(express))

    def test_express_work_goes_first(self):  # 盖掉继承的：MR.ERP 连接的密钥没有 Express 可领，只领自己的
        self.job()
        self.pending = [base._row("CWP-1", "s1")]
        self.run_lease()
        self.assertEqual(self.leases(), ["CWP-M1"])

    def test_its_own_key_takes_its_job_and_never_looks_at_express_heads_or_reads(self):
        self.job()
        self.pending = [base._row("CWP-1", "s1")]  # 就算有一张 Express 排头也不看
        jobs = self.run_lease()["jobs"]
        self.assertEqual(self.leases(), ["CWP-M1"])
        self.assertIn(("mrerp_heads", A, "own"), self.calls)
        self.assertNotIn("heads", self.queries)
        self.assertEqual(self.leased_on, [("CWP-M1", MRERP_CONN, MRERP_CONN)])
        self.assertEqual(self.deliveries, [MRERP_CONN])
        self.assertEqual(jobs[0]["payload"], operator_payload())
        self.assertEqual(self.refreshes, [])

    def test_routing_rules_still_hold_on_its_own_key(self):
        self.job(device=B)
        self.assertEqual(self.run_lease()["jobs"], [])
        self.peers[A]["targets"] = frozenset({"express"})
        self.mrerp.clear()
        self.job()
        self.assertEqual(self.run_lease()["jobs"], [])
        self.assertFalse([call for call in self.calls if call[0] in ("lease", "take", "quarantine")])

    def test_an_account_held_elsewhere_sends_nothing_on_its_own_key(self):
        self.job()
        self.account_free = False
        self.assertEqual(self.run_lease()["jobs"], [])
        self.assertIn(("rollback", "mrerp"), self.calls)
        self.assertEqual(self.deliveries, [])

    def test_a_lost_job_goes_back_on_its_own_key(self):
        self.lost_mrerp()
        jobs = self.run_lease()["jobs"]
        self.assertEqual([call for call in self.calls if call[0] == "lease"],
                         [("lease", "CWP-M9", rc.READER, A, "redeliver")])
        self.assertIn(("take", "CWP-M9", A), self.calls)
        self.assertEqual(jobs[0]["payload"], operator_payload())
        self.assertEqual(self.deliveries, [MRERP_CONN])
        self.calls.clear()
        self.account_free = False
        self.assertEqual(self.run_lease()["jobs"], [])
        self.assertFalse([call for call in self.calls if call[0] in ("lease", "recover")])

    def test_an_expired_job_of_another_present_computer_is_extended_on_its_own_key(self):
        self.expired = [Record(name="CWP-M7", owner_id="companion:b", written_by_device=B),
                        Record(name="CWP-M8", owner_id="companion:c", written_by_device="PC-GONE")]
        self.records["CWP-M7"] = {"connection": MRERP_CONN, "route_device": B}
        self.records["CWP-M8"] = {"connection": MRERP_CONN, "route_device": "PC-GONE"}
        self.present = {B}
        self.run_lease()
        self.assertIn(("extend", "CWP-M7"), self.calls)
        self.assertIn(("take", "CWP-M7", B), self.calls)
        self.assertIn(("recover", "CWP-M8", False), self.calls)

    def test_its_receipt_releases_the_batch_and_never_touches_an_express_register(self):
        written = []
        self.transport.account_pool = types.SimpleNamespace(
            mark_stale=lambda *a: written.append("stale"), note_written=lambda *a: written.append("written"))
        self.transport.credentials = types.SimpleNamespace(device_of=lambda name: A, request_full_report=lambda name: None)
        row = Record(name="CWP-M1", connection=MRERP_CONN, account_key="")
        for status in ("success", "pending", "manual"):
            self.calls.clear()
            self.transport._after_ack(row, Record(credential_id="KEY-M", connection=MRERP_CONN), {"error": None},
                                      {"status": status})
            self.assertEqual(self.calls, [("release", "CWP-M1", A)])
        self.assertEqual(written, [])


# ── `companion.mrerp_jobs` 本身 ─────────────────────────────────────────────────────────────


class FakeLocks:
    """同一把账号锁的替身（`session_lock.lease_account / release_account` 的语义）：值 = 持有者，过期时刻按假时钟。"""

    def __init__(self):
        self.now, self.held = 0, {}

    def _owner(self, key):
        holder, until = self.held.get(key, (None, 0))
        return holder if holder and until > self.now else None

    def lease_account(self, key, holder, ttl=300):
        if self._owner(key) not in (None, "operator:" + holder):
            return False
        self.held[key] = ("operator:" + holder, self.now + ttl)
        return True

    def release_account(self, key, holder):
        if self._owner(key) != "operator:" + holder:
            return False
        self.held.pop(key)
        return True

    def direct(self, key, ttl=120):
        """直写导入拿短锁（`account_lock`）：没人占才拿得到。"""
        if self._owner(key):
            return False
        self.held[key] = ("direct", self.now + ttl)
        return True


class Jobs(unittest.TestCase):
    def setUp(self):
        self.locks = FakeLocks()
        self.connections = {
            MRERP_CONN: {"adapter": "mrerp", "workspace": "WS", "client": "",
                         "config_json": json.dumps({"system_url": "https://www.mrerp4sme.com/"}),
                         "credential_username": "*****"},
            "CWEC-EXP": {"adapter": "express", "workspace": "WS", "client": ""},
            "CWEC-ELSE": {"adapter": "mrerp", "workspace": "WS-2", "client": ""},
        }
        self.secrets = {MRERP_CONN: "site-user"}
        self.device_users = {A: json.dumps({MRERP_CONN: "pc-user"})}
        self.open_rows, self.queries = [], []
        frappe = types.ModuleType("frappe")
        frappe._ = lambda text: text
        frappe.PermissionError = base._Permission
        frappe.throw = lambda message, exc=Exception: (_ for _ in ()).throw(exc(message))
        frappe.local = types.SimpleNamespace(site="testcowork.localhost")
        frappe.db = types.SimpleNamespace(get_value=self.get_value, sql=self.sql)
        self.frappe = frappe
        password = types.ModuleType("frappe.utils.password")
        password.get_decrypted_password = lambda doctype, name, field, raise_exception=False: self.secrets.get(name)
        session = base._load("pearnly_core.mrerp.session", ROOT / "apps/pearnly_core/pearnly_core/mrerp/session.py",
                             {"requests": types.ModuleType("requests")})
        lock_module = types.ModuleType("pearnly_core.session_lock")
        lock_module.lease_account, lock_module.release_account = self.locks.lease_account, self.locks.release_account
        self.enterContext(mock.patch.dict(sys.modules, {
            "frappe": frappe, "frappe.utils": types.ModuleType("frappe.utils"), "frappe.utils.password": password,
            "pearnly_core.mrerp.session": session, "pearnly_core.session_lock": lock_module}))
        self.jobs = base._load("pearnly_cowork.companion._mrerp_jobs_under_test",
                               base.APP / "companion/mrerp_jobs.py", {"frappe": frappe})
        self.credential = types.SimpleNamespace(connection="CWEC-EXP", workspace="WS", client="", credential_id="KEY-A")
        self.key = session.lock_key("https://www.mrerp4sme.com", "pc-user")

    def get_value(self, doctype, name, fields, as_dict=False):
        if doctype == "Cowork CC Desktop Device":
            return self.device_users.get(name)
        found = self.connections.get(name)
        if found is None:
            return None
        return types.SimpleNamespace(**found) if as_dict and fields == ["adapter", "workspace", "client"] else \
            {key: found.get(key) for key in fields}

    def sql(self, query, values=(), as_dict=False):
        q = " ".join(query.split())
        self.queries.append(q)
        if "INNER JOIN `tabCowork ERP Connection`" in q:
            return [Record(row) for row in self.open_rows]
        if "status IN %s" in q:  # 照查询本身的条件回（排不排掉这一张自己由查询决定，不在替身里代判）
            found = [row for row in self.open_rows if row.get("status", "pending") in values[2]]
            if "name<>%s" in q:
                found = [row for row in found if row["name"] != values[3]]
            return [Record(row) for row in found]
        raise AssertionError(q)

    def row(self, name="CWP-M1", batch=BATCH, **values):
        found = Record(name=name, workspace="WS", client="", branch="legacy", binding_generation=0, route_device=A,
                       connection=MRERP_CONN, actor=ME, history="H-1", history_version=3, account_key="",
                       connection_revision=2, payload_json=json.dumps(operator_payload(batch=batch)))
        found.update(values)
        return found

    def test_context_is_built_on_the_records_own_connection(self):
        context = self.jobs.context(self.row(), self.credential, A)
        self.assertEqual((context.connection, context.workspace, context.history_version, context.connection_revision),
                         (MRERP_CONN, "WS", 3, 2))

    def test_records_that_are_not_this_keys_or_this_computers_are_refused(self):
        express = json.dumps({"payload_version": 16, "entry_mode": "operator", "operator": {"batch": {"id": BATCH}}})
        for bad in ({"workspace": "WS-2"}, {"client": "CL-1"}, {"branch": "main"}, {"binding_generation": 1},
                    {"route_device": B}, {"route_device": ""}, {"payload_json": express},
                    {"payload_json": json.dumps({"payload_version": 18})}, {"connection": "CWEC-EXP"},
                    {"connection": "CWEC-ELSE"}, {"connection": "CWEC-GONE"}):
            with self.subTest(bad=bad), self.assertRaises(base._Permission):
                self.jobs.context(self.row(**bad), self.credential, A)
        with self.assertRaises(base._Permission):
            self.jobs.context(self.row(), self.credential, "")

    def test_heads_keep_only_mrerp_operator_jobs_and_never_query_for_a_legacy_key(self):
        self.open_rows = [self.row("CWP-M1"), self.row("CWP-X", payload_json=json.dumps({"payload_version": 16})),
                          self.row("CWP-M2")]
        self.assertEqual([row.name for row in self.jobs.heads(self.credential, A)], ["CWP-M1", "CWP-M2"])
        query = self.queries[-1]
        for part in ("r.route_device=%s", "r.connection<>%s", "c.adapter='mrerp'", "r.status='pending'",
                     "COALESCE(r.result_unknown,0)=0", "ORDER BY r.created_at, r.name LIMIT %s"):
            self.assertIn(part, query)
        self.queries.clear()
        self.assertEqual(self.jobs.heads(self.credential, "KEY-A"), [])  # 没挂电脑的旧密钥：不是任何人选定的电脑
        self.assertEqual(self.jobs.heads(self.credential, ""), [])
        self.assertEqual(self.queries, [])

    def test_an_mrerp_key_looks_only_at_its_own_connection(self):
        self.credential.connection = MRERP_CONN
        self.open_rows = [self.row("CWP-M1")]
        self.assertEqual([row.name for row in self.jobs.heads(self.credential, A, own=True)], ["CWP-M1"])
        self.assertIn("r.connection=%s", self.queries[-1])
        self.assertNotIn("r.connection<>%s", self.queries[-1])

    def test_a_job_sent_back_to_the_queue_keeps_its_batchs_account(self):
        # 11-03 修（记录 §8 第 6 条）：这一张回执后回到排队（pending），这批就还有单，账号不放——放了直写可能趁空档登录同一账号、
        # 把 CC 的网页窗口踢下线。存上 / 转人工 / 撤回（都不在排着 / 领着里）且这批别无他单才放。
        self.assertTrue(self.jobs.take(self.row(), A))
        self.open_rows = [self.row("CWP-M1", status="pending")]  # 就是回执的这一张自己
        self.assertFalse(self.jobs.release_if_done(self.row(), A))
        self.assertIn(self.key, self.locks.held)
        self.assertFalse(self.locks.direct(self.key))  # 直写照旧等账号
        for done in ("success", "manual", "cancelled"):
            with self.subTest(done=done):
                self.open_rows = [self.row("CWP-M1", status=done)]
                self.assertTrue(self.jobs.release_if_done(self.row(), A))
                self.assertNotIn(self.key, self.locks.held)
                self.assertTrue(self.jobs.take(self.row(), A))

    def test_the_account_is_the_computers_own_login_then_the_sites_then_none(self):
        self.assertEqual(self.jobs.account_key(self.row(), A), self.key)
        self.assertEqual(self.jobs.account_key(self.row(), B),
                         "mrerp|https://www.mrerp4sme.com|site-user")  # 那台没报：用站点连接上存的
        self.connections[MRERP_CONN]["credential_username"] = ""
        self.assertEqual(self.jobs.account_key(self.row(), B), "")
        self.assertTrue(self.jobs.take(self.row(), B))  # 不知道用户名：不拦（站点与电脑都没有这个账号）
        self.assertEqual(self.locks.held, {})

    def test_a_batch_holds_its_account_renews_it_and_others_wait(self):
        self.assertEqual(self.jobs.holder(self.row()), "testcowork.localhost:" + BATCH)
        self.assertTrue(self.jobs.take(self.row(), A))
        self.assertTrue(self.jobs.take(self.row("CWP-M2"), A))  # 同一批次：续期，不挡自己
        self.assertFalse(self.jobs.take(self.row("CWP-N1", batch="OPB-2"), A))  # 别的批次：等账号
        self.assertFalse(self.locks.direct(self.key))  # 直写：等账号
        self.locks.now += 299
        self.assertTrue(self.jobs.take(self.row(), A))  # 领单续期
        self.locks.now += 299
        self.assertFalse(self.locks.direct(self.key))

    def test_direct_writes_holding_the_account_keep_the_batch_waiting(self):
        self.assertTrue(self.locks.direct(self.key))
        self.assertFalse(self.jobs.take(self.row(), A))

    def test_a_computer_that_went_away_frees_the_account_after_the_lease(self):
        self.assertTrue(self.jobs.take(self.row(), A))
        self.locks.now += 301
        self.assertTrue(self.locks.direct(self.key))  # 电脑不在：租约过期、直写恢复
        self.assertFalse(self.jobs.take(self.row(), A))  # 回来时直写占着：先不发
        self.locks.held.clear()
        self.assertTrue(self.jobs.take(self.row(), A))  # 放开后重新租上

    def test_the_last_receipt_of_a_batch_releases_the_account(self):
        self.assertTrue(self.jobs.take(self.row(), A))
        self.open_rows = [self.row("CWP-M2"), self.row("CWP-N1", batch="OPB-2")]
        self.assertFalse(self.jobs.release_if_done(self.row(), A))  # 本批还有一张排着
        self.assertIn(self.key, self.locks.held)
        query = self.queries[-1]
        self.assertIn("connection=%s AND route_device=%s AND status IN %s LIMIT 200", query)
        self.assertNotIn("name<>", query)
        self.open_rows = [self.row("CWP-N1", batch="OPB-2")]
        self.assertTrue(self.jobs.release_if_done(self.row(), A))
        self.assertTrue(self.locks.direct(self.key))
        # 别的批次占着时不放别人的
        self.locks.held.clear()
        self.assertTrue(self.jobs.take(self.row("CWP-N1", batch="OPB-2"), A))
        self.open_rows = []
        self.assertFalse(self.jobs.release_if_done(self.row(), A))
        self.assertFalse(self.jobs.release_if_done(self.row(payload_json="{}"), A))  # 不知道批次：不动


if __name__ == "__main__":
    unittest.main()
