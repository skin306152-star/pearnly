"""CC 桌面版共用机器通道（`pearnly_core.cc_desktop`，ADR-025）的离线检查：

- 纯约定：令牌与设备密钥摘要、心跳账套名单 → 账套行（按文件号 / 路径认一份账套）；
- 登记电脑：新电脑发令牌、再登记只记登录人、本机丢了令牌换一把、签发人失权后自动换、被移除过的只有亲手点才回来；
- 写账队列：谁的活谁的电脑做（录入人登录过 CC 的电脑；同事的电脑不替他写）、同一份账套同时一件（写者锁）、每台同时一件、
  回执口径（成功、等账套不计次数、失败 3 次转人工）、租约过期钉回原来那台（它本机记着写没写过）、迟到的回执照收；
- 按需请求：共享种类够得到的电脑谁先来谁读，结果分块交回、核大小与摘要；
- 机器方法与 Cowork 互不相认：只认 ``pcc_`` 令牌与精确路径，CC 桌面版的路径与服务端那张表一致。
"""
import ast
import base64
from datetime import timedelta
import hashlib
import pathlib
import sys
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "apps/pearnly_core")]

import cc_desktop_fake as fake  # noqa: E402

frappe = fake.install()

from pearnly_core.cc_desktop import books, contracts, devices, documents, http_auth, jobs, requests  # noqa: E402
from pearnly_core.cc_desktop.wire import ack_rules, queue_contracts, route_contracts  # noqa: E402

SITE = "testerp.localhost"
KEY_A, KEY_B = "a" * 43, "b" * 43
TAX = "0105551234567"
PATH = r"C:\PearnlyExpress\70EXP\T9NIEX12"
COMPANION = ROOT / "companion/src/companion"


def book_row(path=PATH, uid="vsn:1234/5", writable=True, tax=TAX, branch="00012"):
    return {"code": "T9", "name": "นิภา", "company": "นิภา เอ็กเช้นจ์", "tax_id": tax, "branch": branch, "path": path,
            "root": r"C:\PearnlyExpress\70EXP", "row": 3, "writable": writable, "uid": uid,
            "period_start": "2026-01-01", "period_end": "2026-12-31", "next_end": "2027-12-31",
            "mapping": {"revenue_acc": "41-01-00-00", "ap_acc": "21-01-00-00"}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        fake.reset(self.tmp.name)
        fake.STATE.users = {"boss@x": {"enabled": 1}, "staff@x": {"enabled": 1}, "other@x": {"enabled": 1}}
        fake.STATE.hooks["pearnly_cc_desktop"] = {"allowed": ["t.allowed"], "job_changed": ["t.changed"],
                                                  "kinds": ["express_masters"]}
        self.allowed = {"boss@x", "staff@x", "other@x"}
        self.changes = []
        fake.STATE.attrs.update({"t.allowed": lambda user: user in self.allowed,
                                 "t.changed": lambda view: self.changes.append(view)})

    def tearDown(self):
        self.tmp.cleanup()

    def register(self, user, key, **kw):
        frappe.session.user = user
        return devices.register("r1", key, "OFFICE-PC", **kw)

    def context(self, device, issuer):
        return devices.Context(SITE, device, issuer)

    def report(self, context, rows=None, version="1.11.0", payload_version=4):
        books.replace(context.device, [book_row()] if rows is None else rows, devices.now())
        devices.record_report(context, version=version, payload_version=payload_version, full=True)

    def enqueue(self, actor="staff@x", key=None, name="SD-1", payload_version=4):
        key = key or contracts.book_key(contracts.book_identity(PATH, "vsn:1234/5"))
        return jobs.enqueue(source_doctype="ERP Sales Document", source_name=name, request_key="erp:" + name,
                            actor=actor, book_key=key, payload={"direction": "sales", "account_set": "X",
                                                                "payload_version": payload_version},
                            payload_version=payload_version, label=name)


class Contracts(unittest.TestCase):
    def test_tokens_and_device_digests(self):
        device = contracts.new_device_name()
        token = contracts.new_token(device)
        self.assertTrue(token.startswith("pcc_" + device + "_"))
        self.assertEqual(contracts.parse_token(token), (device, contracts.token_digest(token)))
        for bad in ("exp_CWEC-1_secret", "pcc_PCD-xyz_secret", "pcc_" + device, token + "@site", ""):
            self.assertIsNone(contracts.parse_token(bad), bad)
        self.assertNotEqual(contracts.device_digest("a.site", KEY_A), contracts.device_digest("b.site", KEY_A))
        with self.assertRaises(contracts.ChannelError):
            contracts.device_key("short")

    def test_books_from_a_heartbeat(self):
        rows = contracts.books([book_row(), book_row(path=r"\\SRV\S$\T9", uid="VSN:1234/5"),
                                book_row(path=r"C:\X\RO", uid="", writable=False), {"path": ""}])
        self.assertEqual(len(rows), 2)  # 同一个文件号是同一份账套
        first, second = rows
        self.assertEqual((first["tax_id"], first["branch"], first["period_start"], first["writable"]),
                         (TAX, "00012", "2026-01-01", True))
        self.assertEqual(first["book_key"], contracts.book_key("uid:vsn:1234/5"))
        self.assertEqual(second["book_key"], contracts.book_key("path:" + r"c:\x\ro"))
        self.assertIn('"revenue_acc": "41-01-00-00"', first["mapping"])
        self.assertTrue(contracts.same_book({**first, "writable": 1}, first))


class Register(Base):
    def test_new_computer_then_exists_then_rekey(self):
        made = self.register("boss@x", KEY_A)
        self.assertEqual(made["status"], "created")
        device = made["device"]
        self.assertEqual(devices.authenticate(made["token"]), self.context(device, "boss@x"))
        again = self.register("staff@x", KEY_A)
        self.assertEqual(again, {"status": "exists", "device": device})  # 只记登录人，不换令牌
        self.assertEqual(devices.signed_in(device), frozenset({"boss@x", "staff@x"}))
        fresh = self.register("staff@x", KEY_A, rekey=True)
        self.assertEqual(fresh["status"], "created")
        with self.assertRaises(frappe.AuthenticationError):
            devices.authenticate(made["token"])  # 旧令牌作废
        self.assertEqual(devices.authenticate(fresh["token"]).issuer, "staff@x")

    def test_a_lapsed_issuer_is_replaced_by_who_signs_in(self):
        made = self.register("boss@x", KEY_A)
        self.allowed.discard("boss@x")  # 签发人没了权限：令牌不再认
        with self.assertRaises(frappe.AuthenticationError):
            devices.authenticate(made["token"])
        again = self.register("staff@x", KEY_A)
        self.assertEqual(again["status"], "created")
        self.assertEqual(devices.authenticate(again["token"]).issuer, "staff@x")

    def test_not_allowed_and_removed(self):
        self.allowed.clear()
        with self.assertRaises(frappe.PermissionError):
            self.register("boss@x", KEY_A)
        self.allowed.add("boss@x")
        made = self.register("boss@x", KEY_A)
        frappe.db.set_value(documents.DEVICE, made["device"], "retired_at", devices.now())
        self.assertEqual(self.register("boss@x", KEY_A)["status"], "removed")
        back = self.register("boss@x", KEY_A, rejoin=True)
        self.assertEqual(back["status"], "created")
        self.assertNotEqual(back["device"], made["device"])


class Leave(Base):
    def test_a_computer_that_leaves_takes_its_books_with_it(self):
        # 10-23：断开后名单还留着，产品照它挑账套（只有这台离线电脑够得到）、建公司
        made = self.register("staff@x", KEY_A)
        context = self.context(made["device"], "staff@x")
        self.report(context)
        self.assertEqual(len(books.readable(made["device"])), 1)
        self.assertEqual(devices.leave(context), {"status": "left"})
        self.assertEqual(books.readable(made["device"]), {})
        self.assertIsNone(frappe.db.get_value(documents.DEVICE, made["device"], "token_digest"))


class Queue(Base):
    def setUp(self):
        super().setUp()
        office = self.register("staff@x", KEY_A)
        self.office = self.context(office["device"], "staff@x")
        self.report(self.office)

    def lease(self, context):
        return jobs.lease(context, {"max": 1, "agent_id": "c", "index_pending": []})

    def ack(self, context, log_id, **kw):
        body = {"log_id": log_id, "result": "success", "express_docnum": "IV691023-001", "error": None,
                "agent_id": "c", "meta": {"verify": {"state": "ok"}}}
        body.update(kw)
        return jobs.acknowledge(context, body)

    def test_written_by_the_computer_the_actor_signed_in_on(self):
        job = self.enqueue()
        self.assertEqual(self.changes[-1]["status"], "queued")
        leased = self.lease(self.office)["jobs"]
        self.assertEqual(len(leased), 1)
        self.assertEqual(leased[0]["log_id"], job["job"])
        self.assertEqual(leased[0]["payload"]["account_dir"], PATH)  # 位置换成领取这台的写法
        self.assertTrue(leased[0]["attended"])
        # 每台同时一件：手上这件没回报又来领，说明这件丢在电脑手里（CC 被结束 / 重启），重发的还是同一件（ADR-021 11-02 修订）
        self.assertEqual([j["log_id"] for j in self.lease(self.office)["jobs"]], [job["job"]])
        reply = self.ack(self.office, job["job"])
        self.assertEqual(reply, {"ok": True, "status": "success", "express_docnum": "IV691023-001"})
        self.assertEqual((self.changes[-1]["status"], self.changes[-1]["docnum"], self.changes[-1]["verify_state"]),
                         ("success", "IV691023-001", "ok"))
        self.assertEqual(self.ack(self.office, job["job"]), {"ok": True, "status": "success", "idempotent": True})

    def test_the_codes_each_line_used_reach_the_product(self):
        job = self.enqueue()
        self.lease(self.office)
        self.ack(self.office, job["job"], line_modes=[
            {"seq": 1, "name": "น้ำแข็ง", "mode": "stock_item", "stkcod": "ST-0099 ", "created": True},
            {"seq": 2, "name": "ค่าขนส่ง", "mode": "direct_account", "stkcod": ""}])
        self.assertEqual(self.changes[-1]["line_codes"], [{"seq": 1, "name": "น้ำแข็ง", "code": "ST-0099"}])

    def test_a_receipts_balances_reach_the_product_and_other_jobs_carry_none(self):
        """收款单 RE（载荷 20，11-11）：电脑回执 meta.receipt 原样带到产品的作业视图；别的单是空字典。"""
        receipt = {"docnum": "RE2403080", "invoices": [{"docnum": "IVP42400239", "remaining_after": "0.00"}]}
        job = self.enqueue()
        self.lease(self.office)
        self.ack(self.office, job["job"], express_docnum="RE2403080",
                 meta={"verify": {"state": "ok"}, "doc_type": "receipt", "receipt": receipt})
        self.assertEqual(self.changes[-1]["receipt"], receipt)
        self.assertEqual(jobs.find_by_name(job["job"])["receipt"], receipt)
        self.assertEqual(jobs.view({"name": "x", "meta_json": '{"receipt": "bad"}'})["receipt"], {})
        self.assertEqual(jobs.view({"name": "x"})["receipt"], {})

    def test_a_colleagues_computer_never_writes_my_job(self):
        other = self.register("other@x", KEY_B)
        colleague = self.context(other["device"], "other@x")
        self.report(colleague)
        self.enqueue(actor="staff@x")
        self.assertEqual(self.lease(colleague)["jobs"], [])  # staff 没在这台登录过 CC
        self.assertEqual(len(self.lease(self.office)["jobs"]), 1)
        self.assertEqual(jobs.writers("boss@x", self.changes[-1]["book_key"])["state"], "missing")

    def test_my_computer_offline_while_a_colleagues_is_online_is_said_as_mine_offline(self):
        # A 线 11-12 测试机1：作业归 boss，boss 的电脑关着，同事 staff 的电脑在线、够得到这本——不派给同事（对的），但原来只说
        # 「电脑离线」，用户看见电脑明明在线。分出 not_yours：这位用户自己的电脑离线、在线的那台不归他
        mine = self.register("boss@x", KEY_B)
        boss = self.context(mine["device"], "boss@x")
        self.report(boss)
        self.enqueue(actor="boss@x")
        book = self.changes[-1]["book_key"]
        self.assertEqual(jobs.writers("boss@x", book)["state"], "ok")
        self.offline(boss)
        self.assertEqual(jobs.writers("boss@x", book)["state"], "not_yours")
        self.offline(self.office)  # 谁的电脑都不在线：照旧是离线
        self.assertEqual(jobs.writers("boss@x", book)["state"], "offline")

    def test_a_job_nobody_can_write_does_not_hold_up_the_book(self):
        # 验收 10-25：排在最前的单录入人名下没有电脑（Administrator），同一份账套后面 staff 的单原来一直领不到
        self.enqueue(actor="boss@x", name="SD-1")
        mine = self.enqueue(actor="staff@x", name="SD-2")
        leased = self.lease(self.office)["jobs"]
        self.assertEqual([job["log_id"] for job in leased], [mine["job"]])
        self.assertEqual(jobs.writers("boss@x", self.changes[-1]["book_key"])["state"], "missing")

    def test_one_job_per_account_set_at_a_time(self):
        self.register("staff@x", KEY_B)  # 第二台也是 staff 的
        second = self.register("staff@x", KEY_B)
        pc2 = self.context(second["device"], "staff@x")
        self.report(pc2)
        self.enqueue(name="SD-1")
        self.enqueue(name="SD-2")
        self.assertEqual(len(self.lease(self.office)["jobs"]), 1)
        self.assertEqual(self.lease(pc2)["jobs"], [])  # 同一份账套正在写：等它写完

    def test_the_heartbeat_records_whether_the_index_tool_runs(self):
        devices.record_report(self.office, runner="broken")
        self.assertEqual(frappe.db.get_value(documents.DEVICE, self.office.device, "reindex_runner"), "broken")
        devices.record_report(self.office, runner="whatever")  # 不认的值不写
        self.assertEqual(frappe.db.get_value(documents.DEVICE, self.office.device, "reindex_runner"), "broken")

    def test_a_missing_index_tool_goes_to_a_person(self):
        job = self.enqueue()
        self.lease(self.office)
        reply = self.ack(self.office, job["job"], result="failed", express_docnum=None,
                         error="[REINDEX_UNAVAILABLE] runner missing")
        self.assertEqual(reply["status"], "manual")

    def test_failures_waiting_and_manual(self):
        job = self.enqueue()
        self.lease(self.office)
        waiting = self.ack(self.office, job["job"], result="failed", express_docnum=None,
                           error="[ACCOUNT_WRITER_BUSY] busy", outcome="waiting_lock")
        self.assertEqual(waiting["status"], "pending")
        self.assertEqual(jobs.find_by_name(job["job"])["attempt"], 0)  # 等账套不计次数
        for _ in range(3):
            self.lease(self.office)
            reply = self.ack(self.office, job["job"], result="failed", express_docnum=None, error="boom")
        self.assertEqual(reply["status"], "manual")
        self.assertEqual(self.changes[-1]["status"], "manual")
        jobs.retry(job["job"])
        self.assertEqual(jobs.find_by_name(job["job"])["status"], "queued")

    def offline(self, context):
        """这台电脑不再来报到（CC 被结束、关机）：最后报到时间推到在线时限之前。"""
        fake.STATE.db.table(documents.DEVICE)[context.device]["last_seen_at"] = \
            devices.now() - timedelta(seconds=contracts.ONLINE_SECONDS + 60)

    def test_an_expired_lease_comes_back_only_to_the_same_computer(self):
        other = self.register("staff@x", KEY_B)
        pc2 = self.context(other["device"], "staff@x")
        self.report(pc2)
        job = self.enqueue()
        self.lease(self.office)
        row = fake.STATE.db.table(documents.JOB)[job["job"]]
        row["lease_expires_at"] = devices.now() - timedelta(seconds=1)
        self.offline(self.office)
        self.assertEqual(self.lease(pc2)["jobs"], [])  # 收回后钉在原来那台：结果不明，不交给别的电脑
        self.assertEqual(jobs.find_by_name(job["job"])["reason"], jobs.LOST)
        again = self.lease(self.office)["jobs"]
        self.assertEqual(again[0]["log_id"], job["job"])  # 同一个编号：它本机记着写过没有
        # 迟到的回执（收回之后才回报）也照收
        row["lease_expires_at"] = devices.now() - timedelta(seconds=1)
        self.offline(self.office)
        self.lease(pc2)
        self.assertEqual(self.ack(self.office, job["job"])["status"], "success")

    def test_a_long_job_on_a_computer_still_online_is_not_taken_back(self):
        """代录一张单暂停超过租约（人在用 Express）：写账线这期间不来报到，但这台电脑的按需请求线程还在报到（在线）——别的电脑
        领单时续租约，不收回、不计次数（ADR-021 11-02 修订）。"""
        other = self.register("staff@x", KEY_B)
        pc2 = self.context(other["device"], "staff@x")
        self.report(pc2)
        job = self.enqueue()
        self.lease(self.office)
        row = fake.STATE.db.table(documents.JOB)[job["job"]]
        row["lease_expires_at"] = devices.now() - timedelta(seconds=300)
        self.assertEqual(self.lease(pc2)["jobs"], [])
        found = fake.STATE.db.table(documents.JOB)[job["job"]]
        self.assertEqual((found["status"], found["lease_device"], found.get("attempt") or 0, found.get("reason") or ""),
                         ("leased", self.office.device, 0, ""))
        self.assertGreater(found["lease_expires_at"], devices.now())
        self.assertEqual(self.ack(self.office, job["job"])["status"], "success")

    def test_cc_ended_and_restarted_gets_the_same_job_back_and_writes_it_once(self):
        """CC 被结束、重启后第一次来领：手上那件还「领取中」，直接重发同一件给它（同一个编号、新 fence、计一次），不等租约到期；
        它本机记着写过没有（`task_journal`）。丢满 3 次转人工、结果不明，不再重发。"""
        job = self.enqueue()
        first = self.lease(self.office)["jobs"][0]
        fence = fake.STATE.db.table(documents.JOB)[job["job"]]["fence"]
        again = self.lease(self.office)["jobs"]
        self.assertEqual([j["log_id"] for j in again], [first["log_id"]])
        self.assertEqual(again[0]["payload"], first["payload"])
        found = fake.STATE.db.table(documents.JOB)[job["job"]]
        self.assertEqual((found["status"], found["fence"], found["attempt"]), ("leased", fence + 1, 1))
        self.lease(self.office)
        self.assertEqual(self.lease(self.office)["jobs"], [])  # 第 3 次丢：转人工
        found = fake.STATE.db.table(documents.JOB)[job["job"]]
        self.assertEqual((found["status"], found["reason"], found["route_device"]), ("manual", jobs.LOST, self.office.device))
        self.assertEqual(self.ack(self.office, job["job"])["status"], "success")  # 迟到的回执照收

    def test_old_cc_desktop_does_not_take_a_newer_payload(self):
        self.report(self.office, payload_version=2)
        job = self.enqueue(payload_version=4)
        self.assertEqual(self.lease(self.office)["jobs"], [])
        self.assertEqual(jobs.find_by_name(job["job"])["reason"], jobs.OUTDATED)

    def test_a_receipt_from_another_computer_is_refused(self):
        other = self.register("other@x", KEY_B)
        job = self.enqueue()
        self.lease(self.office)
        with self.assertRaises(frappe.AuthenticationError):
            self.ack(self.context(other["device"], "other@x"), job["job"])


class OperatorTargets(Base):
    """代录的执行目标（ADR-026，第 1 步）：心跳报 ``operator_targets`` 存在电脑行上、带进派单的 Peer；Express 以外目标的代录单
    只派给选定的那台（``route_device``）且它报过这个目标，不看账套可达；Express 的单规则不变。"""

    NOW = devices.now()

    def setUp(self):
        super().setUp()
        office = self.register("staff@x", KEY_A)
        self.office = self.context(office["device"], "staff@x")
        books.replace(self.office.device, [book_row()], devices.now())

    def test_the_heartbeat_records_the_targets_and_old_versions_keep_express_only(self):
        devices.record_report(self.office, version="1.12.11", payload_version=17)  # 旧版不报
        self.assertIsNone(devices.peers()[self.office.device].targets)
        devices.record_report(self.office, version="1.12.12", payload_version=18, targets=["express"])
        self.assertEqual(frappe.db.get_value(documents.DEVICE, self.office.device, "operator_targets"), "express")
        self.assertEqual(devices.peers()[self.office.device].targets, frozenset({"express"}))
        devices.record_report(self.office, targets=["mrerp", "express", "mrerp", "Bad Name", 3])
        self.assertEqual(frappe.db.get_value(documents.DEVICE, self.office.device, "operator_targets"), "express,mrerp")
        for junk in (None, "mrerp", [], ["Bad Name"]):  # 不报 / 认不出：保留上次
            devices.record_report(self.office, targets=junk)
        self.assertEqual(devices.peers()[self.office.device].targets, frozenset({"express", "mrerp"}))

    def test_heartbeat_body_reaches_the_device(self):
        from pearnly_core.cc_desktop import machine

        machine._heartbeat(self.office, {"companion_version": "1.12.12", "max_payload_version": 18,
                                         "operator_targets": ["express"]})
        self.assertEqual(frappe.db.get_value(documents.DEVICE, self.office.device, "operator_targets"), "express")

    def decide(self, device, job, reachers=(), peers=None):
        return route_contracts.decide(device, job, list(reachers), peers or {}, route_contracts.Account(), self.NOW)

    def test_an_mrerp_job_goes_only_to_the_chosen_computer_that_reports_mrerp(self):
        Peer, Job = route_contracts.Peer, route_contracts.Job
        able = Peer(users=frozenset({"staff@x"}), payload_version=18, targets=frozenset({"express", "mrerp"}))
        job = Job("J1", "", "staff@x", 18, route_device="PC-A", target="mrerp")
        # 选定的那台、报过 mrerp：不看账套可达（没有一台够得到「账套」）也领
        self.assertEqual(self.decide("PC-A", job, peers={"PC-A": able}), route_contracts.Decision(True, route_contracts.READER))
        # 别的电脑（哪怕录入人登录过、报过 mrerp）不领；没选定电脑的不派给任何一台
        self.assertEqual(self.decide("PC-B", job, ["PC-B"], {"PC-B": able}).reason, route_contracts.NOT_MINE)
        self.assertEqual(self.decide("PC-A", job._replace(route_device=""), ["PC-A"], {"PC-A": able}).reason,
                         route_contracts.NOT_MINE)
        # 旧版（不报目标）、这一版（只报 Express）：不派，单子等它（不转人工）
        for peer in (Peer(users=frozenset({"staff@x"}), payload_version=17),
                     Peer(users=frozenset({"staff@x"}), payload_version=18, targets=frozenset({"express"}))):
            self.assertEqual(self.decide("PC-A", job, ["PC-A"], {"PC-A": peer}).reason, route_contracts.NO_TARGET)
        # 报了 mrerp、载荷版本却更新：需更新
        newer = job._replace(payload_version=19)
        self.assertEqual(self.decide("PC-A", newer, peers={"PC-A": able}).reason, route_contracts.NEEDS_UPDATE)
        self.assertFalse(route_contracts.attended(job, able))  # 选定电脑的代录单不弹「账套被占用」

    def test_express_jobs_ignore_the_targets(self):
        Peer, Job = route_contracts.Peer, route_contracts.Job
        job = Job("J1", "book", "staff@x", 16)
        self.assertEqual(job.target, route_contracts.EXPRESS)
        for targets in (None, frozenset(), frozenset({"mrerp"}), frozenset({"express"})):
            peer = Peer(users=frozenset({"staff@x"}), payload_version=18, targets=targets)
            self.assertTrue(self.decide("PC-A", job, ["PC-A"], {"PC-A": peer}).take, targets)
            self.assertEqual(self.decide("PC-A", job, [], {"PC-A": peer}).reason, route_contracts.UNREACHABLE)
        explicit = job._replace(target="express")
        peer = Peer(users=frozenset({"staff@x"}), payload_version=18)
        self.assertEqual(self.decide("PC-A", explicit, ["PC-A"], {"PC-A": peer}), self.decide("PC-A", job, ["PC-A"], {"PC-A": peer}))

    def test_an_express_job_still_leases_as_before_with_targets_reported(self):
        devices.record_report(self.office, version="1.12.12", payload_version=18, targets=["express"], full=True)
        job = self.enqueue(payload_version=16)
        got = jobs.lease(self.office, {"max": 1, "agent_id": "c", "index_pending": []})["jobs"]
        self.assertEqual([item["log_id"] for item in got], [job["job"]])


class Requests(Base):
    def test_shared_kind_read_by_a_reaching_computer(self):
        made = self.register("staff@x", KEY_A)
        office = self.context(made["device"], "staff@x")
        key = contracts.book_key(contracts.book_identity(PATH, "vsn:1234/5"))
        self.assertEqual(requests.waiting("express_masters", key), {"reason": "unreachable"})
        self.report(office)
        frappe.db.set_value(documents.DEVICE, office.device, "companion_version", "1.11.0")
        self.assertIsNone(requests.waiting("express_masters", key))
        created = requests.create("express_masters", "erp_masters:" + key, {"account_set": "X", "account_root": ""},
                                  book_key=key, actor="Administrator")
        lease = requests.lease_next(office, {"version": "1.11.0", "kinds": ["express_masters"], "device": "PC"})
        item = lease["request"]
        self.assertEqual((item["id"], item["params"]["account_set"]), (created["request"], PATH))
        content = b"hello masters"
        done = requests.complete(office, {"request": item["id"], "handle": item["handle"], "ok": True, "offset": 0,
                                          "content": base64.b64encode(content).decode(), "final": True,
                                          "size": len(content), "sha256": hashlib.sha256(content).hexdigest(),
                                          "meta": {}})
        self.assertEqual(done["status"], "done")
        self.assertEqual(requests.result(item["id"])[0], content)


class Machine(unittest.TestCase):
    def test_only_exact_pcc_requests_are_machine_requests(self):
        frappe.session.user = "Guest"
        token = contracts.new_token(contracts.new_device_name())

        def request(path=http_auth.PATHS["lease"], method="POST", header="Bearer " + token, cookies=None):
            return types.SimpleNamespace(path=path, method=method, headers={"Authorization": header},
                                         cookies=cookies or {}, args={}, form={})

        self.assertEqual(http_auth._envelope(request(), {}), token)
        for bad in (request(method="GET"), request(header="Bearer exp_CWEC-1_secret"),
                    request(path="/api/method/frappe.client.get_list"), request(cookies={"sid": "x"})):
            with self.assertRaises(http_auth.RequestContractError):
                http_auth._envelope(bad, {})

    def test_the_cc_desktop_calls_exactly_these_paths(self):
        tree = ast.parse((COMPANION / "channel.py").read_text(encoding="utf-8"))
        namespace = {}
        exec(compile(tree, "channel.py", "exec"), namespace)
        core = namespace["PATHS"]["core"]
        self.assertEqual({core[op] for op in ("heartbeat", "lease", "ack", "request_next", "request_complete",
                                               "leave", "mrerp_login", "operator_ai")}, set(http_auth.PATHS.values()))
        machine = ast.parse((ROOT / "apps/pearnly_core/pearnly_core/cc_desktop/machine.py").read_text(encoding="utf-8"))
        posted = {node.name for node in machine.body if isinstance(node, ast.FunctionDef)
                  and any("frappe.whitelist" in ast.unparse(d) and "'POST'" in ast.unparse(d)
                          for d in node.decorator_list)}
        self.assertEqual(posted, {method.rsplit(".", 1)[1] for method in http_auth.METHODS.values()})

    def test_the_wire_shape_is_the_cowork_one(self):
        job = queue_contracts.job("3c0d8c86-3f6b-4b1e-9d6f-8b7a9d9c1c11", {"history": "SD-1", "invoice_no": "SD-1",
                                                                          "frozen_payload": {"a": 1}}, True)
        self.assertEqual(set(job), {"log_id", "history_id", "invoice_no", "payload", "attended"})


class BookIdentity(unittest.TestCase):
    def test_the_same_folder_reported_under_another_spelling_is_the_same_book(self):
        # 10-25：同一本账套换了路径写法（自己的 C$ 共享 ↔ 本地路径、映射盘 ↔ UNC），ERP 公司选定的账套照样认得出：有文件号时只认文件号
        uid = "1416030B-000800000003B132"
        spellings = [r"C:\ACCOUNT\70EXP\DEMO01", r"\\DESKTOP-8BRD5AV\C$\account\70exp\demo01", r"S:\70EXP\DEMO01"]
        keys = {contracts.book_key(contracts.book_identity(path, uid)) for path in spellings}
        self.assertEqual(len(keys), 1)
        # 没有文件号（旧版 CC）才按路径
        self.assertNotEqual(contracts.book_identity(spellings[0]), contracts.book_identity(spellings[1]))


def _operator_contracts():
    """代录契约（纯常量，不经 express_operator 包的 __init__）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "operator_contracts_for_receipts", ROOT / "apps/pearnly_core/pearnly_core/express_operator/contracts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OperatorReasonTests(unittest.TestCase):
    """代录回执（``[OPERATOR_<原因>] <说明>``，CC 桌面版 1.12.15 起说明以 AI 接手的结论原文开头、最多 2000 字）整段存下，
    站点卡片才看得到完整结论（11-05 真机：截到 500 / 140 / 80 只剩「无法识别的 Express 画面」）；其它回报仍截 500。"""

    def test_an_operator_receipt_keeps_the_whole_ai_conclusion_and_others_still_stop_at_500(self):
        conclusion = "ai: " + "未能打开单据窗口。" * 180  # 约 1600 字
        error = "[OPERATOR_UNKNOWN_SCREEN] " + conclusion
        for outcome in ("needs_review", None):
            state = ack_rules.acknowledgement(branch="legacy", attempt=3, success=False, outcome=outcome, error=error)
            self.assertEqual(state.reason, error)
        self.assertEqual(len(ack_rules.reason_text("[OPERATOR_X] " + "y" * 3000)), ack_rules.OPERATOR_REASON_MAX)
        for other in ("x" * 600, "[DBF_WRITE_FAILED] " + "x" * 600, "[FIELD_TOO_LONG] " + "x" * 600):
            self.assertEqual(ack_rules.acknowledgement(branch="legacy", attempt=3, success=False, outcome="needs_review",
                                                       error=other).reason, other[:500])
            self.assertEqual(ack_rules.acknowledgement(branch="legacy", attempt=3, success=False, error=other).reason,
                             other[:500])

    def test_the_receipt_refusals_go_to_a_person_without_retries(self):
        """收款单 RE 的四个拒收码（契约小写）：带 needs_review 或只带码（老写法）都直接转人工，不按普通失败重试。"""
        for code in ("already_collected", "invoice_not_found", "receipt_type_invalid", "cash_account_missing"):
            self.assertIn(code, ack_rules.NEEDS_PERSON_CODES)
            reason = f"[{code}] receipt ERC-1 refused"
            self.assertEqual(ack_rules.error_code(reason), code)
            for outcome in ("needs_review", None):
                state = ack_rules.acknowledgement(branch="legacy", attempt=1, success=False, outcome=outcome, error=reason)
                self.assertEqual((state.status, state.retry, state.attempt), ("manual", False, 1))
        state = ack_rules.acknowledgement(branch="legacy", attempt=1, success=False, error="[DBF_WRITE_FAILED] x")
        self.assertEqual((state.status, state.retry), ("pending", True))  # 普通失败照旧重试

    def test_every_store_on_the_way_to_the_card_holds_the_whole_receipt(self):
        operator = _operator_contracts()

        root = pathlib.Path(__file__).resolve().parents[1] / "apps"
        self.assertEqual(operator.REASON_MAX, ack_rules.OPERATOR_REASON_MAX)
        for path in ("pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_push_record/cowork_push_record.json",
                     "pearnly_core/pearnly_core/pearnly_core/doctype/pearnly_express_operator_task_document/"
                     "pearnly_express_operator_task_document.json",
                     "pearnly_core/pearnly_core/pearnly_core/doctype/pearnly_cc_job/pearnly_cc_job.json",
                     "pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_ocr_history/cowork_ocr_history.json",
                     "pearnly_erp_portal/pearnly_erp_portal/pearnly_erp_portal/doctype/purchase_document/purchase_document.json"):
            fields = {f["fieldname"]: f for f in __import__('json').loads((root / path).read_text(encoding="utf-8"))["fields"]}
            field = fields.get("reason") or fields.get("last_push_reason") or fields["ledger_reason"]
            self.assertIn(field["fieldtype"], ("Small Text", "Text", "Long Text"), path)
        push_state = (root / "pearnly_cowork/pearnly_cowork/history/push_state.py").read_text(encoding="utf-8")
        self.assertIn(f"REASON_MAX = {ack_rules.OPERATOR_REASON_MAX}\n", push_state)
        self.assertIn("reason = (row.reason or '')[:REASON_MAX]", push_state)
        erp = (root / "pearnly_erp_portal/pearnly_erp_portal/express/push.py").read_text(encoding="utf-8")
        self.assertIn('str(view.get("reason") or "")[:ack_rules.OPERATOR_REASON_MAX]', erp)




class Installer(unittest.TestCase):
    """安装包地址共用一份（盘点 2026-11-08 P2-2）：共用通道与 Cowork 的接口出同一个结果；对话页（Windows 浏览器的「下载 CC 桌面版」、
    设置里没有电脑时的下载按钮）一律调共用通道的，ERP 员工也看得到。"""

    def test_one_installer_for_both_products(self):
        from pearnly_core.cc_desktop import release

        self.assertEqual(release.installer({}), {"url": release.INSTALLER_URL, "version": "", "configured": True})
        self.assertEqual(release.installer({"pearnly_cowork_agent_installer": " https://x/y.exe ",
                                            "pearnly_cowork_agent_version": "1.2.3"}),
                         {"url": "https://x/y.exe", "version": "1.2.3", "configured": True})
        core = (ROOT / "apps/pearnly_core/pearnly_core/cc_desktop/api.py").read_text(encoding="utf-8")
        self.assertIn('@frappe.whitelist(methods=["GET"])\ndef installer():', core)
        self.assertIn("return release.installer(frappe.conf)", core)
        cowork = (ROOT / "apps/pearnly_cowork/pearnly_cowork/companion/api.py").read_text(encoding="utf-8")
        self.assertIn("return release.installer(frappe.conf)", cowork)
        self.assertNotIn("conf.get('pearnly_cowork_agent_installer')", cowork)  # 不再各读一份配置
        src = ROOT / "frontend/assistant/src"
        install = (src / "lib/install.ts").read_text(encoding="utf-8")
        self.assertIn('(isWindows() && (product === "cowork" || product === "erp")) return "desktop"', install)
        for path in ("lib/install.ts", "components/settings.tsx"):
            text = (src / path).read_text(encoding="utf-8")
            self.assertIn('"pearnly_core.cc_desktop.api.installer"', text)
            self.assertNotIn("pearnly_cowork.companion.api.installer", text)

if __name__ == "__main__":
    unittest.main()
