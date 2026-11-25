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
from datetime import datetime, timedelta
import hashlib
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "apps/pearnly_core")]

import cc_desktop_fake as fake  # noqa: E402

frappe = fake.install()

from pearnly_core.cc_desktop import books, contracts, devices, documents, http_auth, jobs, requests  # noqa: E402
from pearnly_core.cc_desktop.wire import ack_rules, link_contracts, queue_contracts, route_contracts  # noqa: E402

SITE = "testerp.localhost"
KEY_A, KEY_B = "a" * 43, "b" * 43
TAX = "0105551234567"
PATH = r"C:\PearnlyExpress\70EXP\T9NIEX12"
PATH_B = r"C:\PearnlyExpress\70EXP\T9GIZ"
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

    def test_a_job_the_product_no_longer_wants_is_cancelled_instead_of_leased(self):
        """领单前兜底（11-13 作废单）：产品声明了 ``still_wanted`` 且说这件不要了（来源单据已作废）——不交给电脑，撤掉、原因 voided，
        产品收到状态；没声明的站点照旧领。"""
        fake.STATE.hooks["pearnly_cc_desktop"]["still_wanted"] = ["t.wanted"]
        fake.STATE.attrs["t.wanted"] = lambda view: view["source_name"] != "SD-VOID"
        gone = self.enqueue(name="SD-VOID")
        self.assertEqual(self.lease(self.office)["jobs"], [])
        self.assertEqual((self.changes[-1]["job"], self.changes[-1]["status"], self.changes[-1]["reason"]),
                         (gone["job"], "cancelled", "voided"))
        kept = self.enqueue(name="SD-2")
        self.assertEqual([j["log_id"] for j in self.lease(self.office)["jobs"]], [kept["job"]])

    def test_the_codes_each_line_used_reach_the_product(self):
        job = self.enqueue()
        self.lease(self.office)
        self.ack(self.office, job["job"], line_modes=[
            {"seq": 1, "name": "น้ำแข็ง", "mode": "stock_item", "stkcod": "ST-0099 ", "created": True},
            {"seq": 2, "name": "ค่าขนส่ง", "mode": "direct_account", "stkcod": ""}])
        self.assertEqual(self.changes[-1]["line_codes"], [{"seq": 1, "name": "น้ำแข็ง", "code": "ST-0099"}])

    def test_the_masters_an_operator_job_made_reach_the_product_as_written_codes(self):
        """代录回执只在 meta.operator.masters 里报随单新建的客户 / 存货（11-16 验收：代录新建客户 เ001 写成 IV0000030，ERP 客户的
        码一直空）：作业视图照它给出对方码与 {name, code}（没有序号）；直写回执顶层有的照旧以顶层为准。已发出的 1.12.21 就是这个形状。"""
        job = self.enqueue()
        self.lease(self.office)
        masters = [{"kind": "party", "code": "เ001"}, {"kind": "item", "code": "ST-0100", "name": "น้ำแข็ง"},
                   {"kind": "repaired", "code": "X"}]
        self.ack(self.office, job["job"], meta={"operator": {"masters": masters}})
        self.assertEqual((self.changes[-1]["party_code"], self.changes[-1]["line_codes"]),
                         ("เ001", [{"name": "น้ำแข็ง", "code": "ST-0100"}]))
        direct = self.enqueue(name="SD-2")
        self.lease(self.office)
        self.ack(self.office, direct["job"], meta={"party_code": "C-9", "operator": {"masters": masters}})
        self.assertEqual(self.changes[-1]["party_code"], "C-9")

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

    def stand_in(self, may=lambda user, view: True):
        fake.STATE.hooks["pearnly_cc_desktop"]["stand_in"] = ["t.stand_in"]
        fake.STATE.attrs["t.stand_in"] = may

    def test_a_colleagues_computer_stands_in_when_the_submitter_has_none_online_and_records_who(self):
        # 用户 11-17 定稿（两产品同一条）：提交人 boss 没有在线的电脑 → staff 那台领单时替他写，写账件上提交人仍是 boss、记下替写的 staff
        job = self.enqueue(actor="boss@x")
        self.assertEqual(self.lease(self.office)["jobs"], [])  # 产品没声明替写：照旧只写本人的
        self.stand_in(lambda user, view: user == "staff@x" and view["source_name"] == "SD-1")
        self.assertEqual([j["log_id"] for j in self.lease(self.office)["jobs"]], [job["job"]])
        row = fake.STATE.db.table(documents.JOB)[job["job"]]
        self.assertEqual((row["actor"], row["writer"], row["lease_device"], row["fence"]),
                         ("boss@x", "staff@x", self.office.device, 1))
        self.assertEqual(self.changes[-1]["actor"], "boss@x")

    def test_no_stand_in_while_the_submitters_own_computer_is_online_or_for_a_folder_task_or_without_a_right(self):
        self.stand_in()
        mine = self.register("boss@x", KEY_B)
        boss = self.context(mine["device"], "boss@x")
        self.report(boss)
        job = self.enqueue(actor="boss@x")
        self.assertEqual(self.lease(self.office)["jobs"], [])  # 他自己的电脑在线、够得到：让它写
        self.offline(boss)
        self.stand_in(lambda user, view: False)  # 这台上的人对这家公司没有权限
        self.assertEqual(self.lease(self.office)["jobs"], [])
        self.stand_in()
        fake.STATE.db.table(documents.JOB)[job["job"]]["route_device"] = boss.device  # 定时任务钉在他那台上：不替
        self.assertEqual(self.lease(self.office)["jobs"], [])
        fake.STATE.db.table(documents.JOB)[job["job"]]["route_device"] = None
        self.assertEqual([j["log_id"] for j in self.lease(self.office)["jobs"]], [job["job"]])

    def test_jobs_queued_before_the_switch_wait_for_their_submitter_and_do_not_block_newer_ones(self):
        # 旧件闸（主会话 11-17）：配了切换时刻（UTC），之前排的件不由别人的电脑写；它排在这本账套最前面也不挡后面的新件。
        # 库里的排队时刻是站点时区（Asia/Bangkok，比 UTC 早 7 小时）
        self.stand_in()
        old = self.enqueue(actor="boss@x", name="SD-OLD")
        new = self.enqueue(actor="boss@x", name="SD-NEW")
        table = fake.STATE.db.table(documents.JOB)
        table[old["job"]]["creation"] = datetime(2026, 11, 19, 9, 59)   # 站点时区 = 02:59 UTC
        table[new["job"]]["creation"] = datetime(2026, 11, 19, 10, 1)   # 站点时区 = 03:01 UTC
        frappe.conf["pearnly_writer_rule_since"] = "2026-11-19T03:00:00Z"
        self.assertEqual([j["log_id"] for j in self.lease(self.office)["jobs"]], [new["job"]])
        self.ack(self.office, new["job"])
        self.assertEqual(self.lease(self.office)["jobs"], [])  # 旧件只等 boss 自己的电脑
        frappe.conf["pearnly_writer_rule_since"] = "not a time"  # 配了却读不出：一件都不替
        self.assertEqual(self.lease(self.office)["jobs"], [])
        del frappe.conf["pearnly_writer_rule_since"]  # 用户看过统计定了「旧件也写」：删掉这项即放开
        self.assertEqual([j["log_id"] for j in self.lease(self.office)["jobs"]], [old["job"]])

    def test_a_colleagues_online_computer_that_may_stand_in_counts_as_a_writer(self):
        # 用户 11-17 定稿（两产品同一条）：boss 的电脑关着，同事 staff 的电脑在线、够得到这本、staff 能替他写（产品判）→ 推了就写；
        # 产品不认 staff（eligible 不给 / 为假）→ 只算 boss 自己的，电脑离线、排队。不再有「你的电脑离线」（not_yours）
        mine = self.register("boss@x", KEY_B)
        boss = self.context(mine["device"], "boss@x")
        self.report(boss)
        self.enqueue(actor="boss@x")
        book = self.changes[-1]["book_key"]
        self.assertEqual(jobs.writers("boss@x", book)["state"], "ok")
        self.offline(boss)
        self.assertEqual(jobs.writers("boss@x", book)["state"], "offline")
        self.assertEqual(jobs.writers("boss@x", book, eligible=lambda user: True)["state"], "ok")
        self.assertEqual(jobs.writers("boss@x", book, eligible=lambda user: False)["state"], "offline")
        self.offline(self.office)  # 谁的电脑都不在线：离线，谁先上线谁写
        self.assertEqual(jobs.writers("boss@x", book, eligible=lambda user: True)["state"], "offline")

    def test_the_computers_a_job_waits_for_are_the_owners_offline_ones(self):
        # 代录排着等电脑时页面写「等待电脑 · 名称」（A 线 11-12）：在等的是 boss 自己够得到这本、此刻不在线的电脑；同事的不算
        mine = self.register("boss@x", KEY_B)
        boss = self.context(mine["device"], "boss@x")
        self.report(boss)
        self.enqueue(actor="boss@x")
        book = self.changes[-1]["book_key"]
        self.assertEqual(jobs.waiting_on("boss@x", [book]), [])  # 在线：不在等
        self.offline(boss)
        self.assertEqual(jobs.waiting_on("boss@x", [book]), [devices.label(mine["device"])])
        self.assertEqual(jobs.waiting_on("staff@x", [book]), [])  # staff 的电脑在线
        self.assertEqual(jobs.waiting_on("nobody@x", [book]), [])

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
        fake.STATE.db.table(documents.JOB)[job["job"]]["lease_expires_at"] = devices.now() - timedelta(seconds=1)  # 歇够了
        for _ in range(3):
            self.lease(self.office)
            reply = self.ack(self.office, job["job"], result="failed", express_docnum=None, error="boom")
        self.assertEqual(reply["status"], "manual")
        self.assertEqual(self.changes[-1]["status"], "manual")
        jobs.retry(job["job"])
        self.assertEqual(jobs.find_by_name(job["job"])["status"], "queued")

    def test_a_half_document_finished_by_a_person_is_reported_late_as_written_once(self):
        """代录 held（11-14）：这件按「未完成 + 半截单号」收过回执；人在 Express 里答完框、单存完，同一台电脑用同一件补报
        「已写入 + 那个单号」→ 记成已写入。别的单号、别的电脑、已经再派出去的都不收。"""
        job = self.enqueue()
        self.lease(self.office)
        held = {"operator": {"partial": {"docnum": "IV0000045", "missing": ["total"]}, "held": {"computer": "PC2"}}}
        reply = self.ack(self.office, job["job"], result="failed", express_docnum=None, outcome="needs_review",
                         error="[OPERATOR_PARTIAL_SAVED] partial_saved", meta=held)
        self.assertEqual(reply["status"], "manual")
        with self.assertRaises(frappe.AuthenticationError):
            self.ack(self.office, job["job"], express_docnum="IV0000046")  # 不是记下的半截单号
        other = self.register("other@x", KEY_B)
        with self.assertRaises(frappe.AuthenticationError):
            self.ack(self.context(other["device"], "other@x"), job["job"], express_docnum="IV0000045")  # 别的电脑
        late = self.ack(self.office, job["job"], express_docnum="IV0000045")
        self.assertEqual(late["status"], "success")
        self.assertEqual((self.changes[-1]["status"], self.changes[-1]["docnum"]), ("success", "IV0000045"))
        self.assertEqual(self.ack(self.office, job["job"], express_docnum="IV0000045")["idempotent"], True)

    def test_a_manual_job_without_a_half_document_takes_no_late_receipt(self):
        job = self.enqueue()
        self.lease(self.office)
        self.ack(self.office, job["job"], result="failed", express_docnum=None, outcome="needs_review", error="x")
        with self.assertRaises(frappe.AuthenticationError):
            self.ack(self.office, job["job"], express_docnum="IV691023-001")

    def test_express_open_keeps_the_reason_and_rests_the_book_while_other_books_go_on(self):
        # 11-12：Express 开着这本账套。以前原因清空、放回即可再领——电脑连着领同一张，别的账套的单领不到，界面看不出在等什么
        other = contracts.book_key(contracts.book_identity(PATH_B, "vsn:9999/1"))
        self.report(self.office, rows=[book_row(), book_row(path=PATH_B, uid="vsn:9999/1")])
        job = self.enqueue(name="SD-1")
        later = self.enqueue(name="SD-2")
        self.lease(self.office)
        waiting = self.ack(self.office, job["job"], result="failed", express_docnum=None, outcome="waiting_lock",
                           error="[ACCOUNT_BUSY_LOCKED] the account set is open in Express")
        self.assertEqual(waiting["status"], "pending")
        found = jobs.find_by_name(job["job"])
        self.assertEqual((found["status"], found["reason"], found["attempt"]), ("queued", ack_rules.EXPRESS_OPEN, 0))
        self.assertEqual(self.changes[-1]["reason"], ack_rules.EXPRESS_OPEN)   # 产品收到的状态带着原因
        self.assertEqual(self.lease(self.office)["jobs"], [])                  # 歇着：这张不派，同一本后面的 SD-2 也不派
        elsewhere = self.enqueue(name="SD-9", key=other)
        self.assertEqual([row["log_id"] for row in self.lease(self.office)["jobs"]], [elsewhere["job"]])  # 别的账套照常领
        self.ack(self.office, elsewhere["job"])
        row = fake.STATE.db.table(documents.JOB)[job["job"]]
        row["lease_expires_at"] = devices.now() - timedelta(seconds=1)         # 歇够了
        self.assertEqual([row["log_id"] for row in self.lease(self.office)["jobs"]], [job["job"]])
        self.ack(self.office, job["job"])
        self.assertEqual(jobs.find_by_name(job["job"])["reason"], "")          # 写成后原因清掉
        self.assertEqual([row["log_id"] for row in self.lease(self.office)["jobs"]], [later["job"]])

    def test_another_computer_writing_or_book_unreachable_rests_the_book_like_express_open(self):
        # 11-25 两台电脑实测：别的电脑锁着这本（PEARNLY.LCK）时，回「等账套」后不歇，CC 处理过一件就立刻再领，45 秒派 31 次、
        # fence 白涨。现在与 Express 开着同一套歇法；只有 Express 开着才显示「在等 Express 关闭」
        for code in (ack_rules.WRITER_BUSY, ack_rules.UNREACHABLE):
            with self.subTest(code=code):
                job = self.enqueue(name=f"SD-{code}")
                self.lease(self.office)
                self.ack(self.office, job["job"], result="failed", express_docnum=None, outcome="waiting_lock",
                         error=f"[{code}] retry later")
                found = jobs.find_by_name(job["job"])
                self.assertEqual((found["status"], found["reason"], found["attempt"]), ("queued", code, 0))
                fence = fake.STATE.db.table(documents.JOB)[job["job"]]["fence"]
                for _ in range(30):                                            # 歇着时来领多少次都不派、fence 不涨
                    self.assertEqual(self.lease(self.office)["jobs"], [])
                self.assertEqual(fake.STATE.db.table(documents.JOB)[job["job"]]["fence"], fence)
                self.assertFalse(jobs.express_wait(job["job"])["waiting"])      # 不说成 Express 开着
                fake.STATE.db.table(documents.JOB)[job["job"]]["lease_expires_at"] = devices.now() - timedelta(seconds=1)
                self.assertEqual([row["log_id"] for row in self.lease(self.office)["jobs"]], [job["job"]])  # 歇够了马上接上
                self.assertEqual(fake.STATE.db.table(documents.JOB)[job["job"]]["fence"], fence + 1)
                self.ack(self.office, job["job"])
                self.assertEqual(jobs.find_by_name(job["job"])["reason"], "")

    def test_waiting_codes_outside_the_rest_list_go_back_at_once(self):
        # 文件号对不上：那台电脑的可达行另行处理、单子马上交给别的电脑；代录的等待按代录自己的节奏——都不歇整本
        for error in ("[ERR_ACCOUNT_UID_MISMATCH] the account set on this computer is not the registered one",
                      "[OPERATOR_USER_ACTIVE] someone is using this computer"):
            with self.subTest(error=error):
                job = self.enqueue(name=f"SD-{error[1:8]}")
                self.lease(self.office)
                self.ack(self.office, job["job"], result="failed", express_docnum=None, outcome="waiting_lock", error=error)
                self.assertEqual(jobs.find_by_name(job["job"])["reason"], "")
                self.assertIsNone(fake.STATE.db.table(documents.JOB)[job["job"]]["lease_expires_at"])
                fake.STATE.db.table(documents.JOB)[job["job"]]["status"] = "cancelled"

    def test_waiting_for_express_is_readable_per_job_and_per_book_and_expires(self):
        # 给各产品显示「Express 正在使用此账套…」：读的时候算，不另存；电脑不再回报 90 秒后不再说在等 Express
        key = contracts.book_key(contracts.book_identity(PATH, "vsn:1234/5"))
        job = self.enqueue(name="SD-1")
        other = self.enqueue(name="SD-2")
        self.assertEqual(jobs.express_wait(job["job"]), {"waiting": False, "since": None})
        self.assertEqual(jobs.book_express_wait(key), {"waiting": False, "since": None})
        self.lease(self.office)
        self.ack(self.office, job["job"], result="failed", express_docnum=None, outcome="waiting_lock",
                 error="[ACCOUNT_BUSY_LOCKED] the account set is open in Express")
        now = devices.now()
        state = jobs.express_wait(job["job"], now)
        self.assertTrue(state["waiting"] and state["since"].endswith("Z"))
        self.assertEqual(list(jobs.express_wait_many([job["job"], other["job"], "NOPE"], now)), [job["job"]])
        self.assertTrue(jobs.book_express_wait(key, now)["waiting"])
        shown = timedelta(seconds=ack_rules.EXPRESS_OPEN_SHOWN_SECONDS)
        self.assertTrue(jobs.express_wait(job["job"], now + shown - timedelta(seconds=2))["waiting"])
        self.assertEqual(jobs.express_wait(job["job"], now + shown + timedelta(seconds=1)),
                         {"waiting": False, "since": None})                    # 过期：回到普通的排队中
        self.assertFalse(jobs.book_express_wait(key, now + shown + timedelta(seconds=1))["waiting"])
        fake.STATE.db.table(documents.JOB)[job["job"]]["lease_expires_at"] = devices.now() - timedelta(seconds=1)
        self.lease(self.office)                                                 # 被领走：不再是排队中
        self.assertFalse(jobs.express_wait(job["job"])["waiting"])
        self.ack(self.office, job["job"])
        self.assertFalse(jobs.book_express_wait(key)["waiting"])                # 写成：清掉
        self.assertEqual(jobs.express_wait_many([]), {})
        self.assertEqual(jobs.book_express_wait(""), {"waiting": False, "since": None})

    def test_other_waits_keep_no_reason_and_are_handed_out_again_at_once(self):
        # 不在歇的那一类里的等待（代录自己的等待）：不留原因、马上再派（别的电脑在写 / 够不到见上面那条，11-25 起歇）
        job = self.enqueue()
        self.lease(self.office)
        self.ack(self.office, job["job"], result="failed", express_docnum=None, outcome="waiting_lock",
                 error="[OPERATOR_USER_ACTIVE] someone is using this computer")
        self.assertEqual(jobs.find_by_name(job["job"])["reason"], "")
        self.assertEqual(len(self.lease(self.office)["jobs"]), 1)

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

    def test_a_rescan_rides_on_the_lease_until_the_full_report_comes_back(self):
        # 主会话 11-15：ERP「从连接添加」的刷新请在线电脑整份重扫（同 Cowork 的 master_refresh，CC 桌面版 1.1.77 起认）；
        # 连点只下发一次，电脑带回同一编号的整份名单才算完
        from pearnly_core.cc_desktop import machine, rescan

        first = rescan.request([self.office.device])
        self.assertEqual(rescan.request([self.office.device]), first)       # 没回执：沿用原编号
        sent = jobs.lease(self.office, {"max": 1, "agent_id": "c", "index_pending": []})
        self.assertEqual(sent["master_refresh"], {"request_id": first[self.office.device], "scope_kind": "endpoint",
                                                  "account_set_key": "@endpoint"})
        machine._heartbeat(self.office, {"companion_version": "1.12.21", "account_sets": [book_row()],
                                         "master_refresh_request_id": "other", "master_refresh_scope": "endpoint"})
        self.assertEqual(rescan.pending([self.office.device]), [self.office.device])   # 别的编号不算
        machine._heartbeat(self.office, {"companion_version": "1.12.21", "account_sets": [book_row()],
                                         "master_refresh_request_id": first[self.office.device],
                                         "master_refresh_scope": "endpoint"})
        self.assertEqual(rescan.pending([self.office.device]), [])
        self.assertNotIn("master_refresh", jobs.lease(self.office, {"max": 1, "agent_id": "c", "index_pending": []}))

    def test_a_computer_that_never_takes_the_rescan_counts_as_gone(self):
        # 主会话 11-15 真机：CC 刚退出的几分钟内电脑还算在线，刷新干等满 60 秒。下发后 TAKE_WITHIN 秒还没来领就当不在线；
        # 领走了的（在扫）照旧等
        import time as clock

        from pearnly_core.cc_desktop import rescan

        rescan.request([self.office.device])
        soon, later = clock.time() + 1, clock.time() + rescan.TAKE_WITHIN + 1
        self.assertEqual(rescan.unreached([self.office.device], now=soon), [])
        self.assertEqual(rescan.unreached([self.office.device], now=later), [self.office.device])
        jobs.lease(self.office, {"max": 1, "agent_id": "c", "index_pending": []})          # 来领了：在扫，照旧等
        self.assertEqual(rescan.unreached([self.office.device], now=later), [])


class CloudLink(Base):
    """心跳的 ``link``（云端 Express 的来源标记，11-15 评估第 4、6 节）：经哪台电脑（桥）连上来、云端主机名，记在电脑行上只给
    显示用；只有明确的 ``cloud`` 才写、``none`` 清空，缺省与坏值不改，也绝不因为它拒收心跳。选目标账本的逻辑不看它。"""

    CLOUD = {"kind": "cloud", "via": " ACC-01\n ", "host": "z1.tsplus.example"}

    def setUp(self):
        super().setUp()
        office = self.register("staff@x", KEY_A)
        self.office = self.context(office["device"], "staff@x")

    def stored(self):
        row = fake.STATE.db.table(documents.DEVICE)[self.office.device]
        return tuple(row.get(key) or "" for key in link_contracts.FIELDS)

    def beat(self, **extra):
        from pearnly_core.cc_desktop import machine

        reply = machine._heartbeat(self.office, {"companion_version": "1.12.22", "max_payload_version": 18, **extra})
        self.assertIs(reply["ok"], True)
        return reply

    def test_the_pure_contract(self):
        self.assertEqual(link_contracts.stored(self.CLOUD),
                         {"link_kind": "cloud", "link_via": "ACC-01", "link_host": "z1.tsplus.example"})
        self.assertEqual(link_contracts.stored({"kind": "cloud", "via": 3, "host": ["x"]}),
                         {"link_kind": "cloud", "link_via": "", "link_host": ""})
        self.assertEqual(link_contracts.stored({"kind": "cloud", "via": "x" * 200})["link_via"], "x" * 80)
        self.assertEqual(link_contracts.stored({"kind": "none", "via": "ACC-01"}),
                         {"link_kind": "", "link_via": "", "link_host": ""})
        for value in (None, {}, {"kind": "local"}, {"kind": "Cloud"}, {"kind": None}, "cloud", ["cloud"], 1, True):
            self.assertIsNone(link_contracts.stored(value), value)
        self.assertEqual(link_contracts.view("cloud", "ACC-01", None), {"kind": "cloud", "via": "ACC-01", "host": ""})
        for kind in ("", None, "local"):
            self.assertIsNone(link_contracts.view(kind, "ACC-01", "z1"))
        self.assertFalse(link_contracts.changed(None, {}))
        self.assertTrue(link_contracts.changed(link_contracts.stored(self.CLOUD), {"link_kind": None}))

    def test_a_cloud_link_is_written_and_missing_or_junk_keeps_it(self):
        self.beat(link=self.CLOUD)
        self.assertEqual(self.stored(), ("cloud", "ACC-01", "z1.tsplus.example"))
        for junk in (None, {"kind": "local"}, {"kind": "CLOUD"}, "cloud", ["cloud"], 7, {"via": "ACC-02"}):
            with self.subTest(junk=junk):
                self.beat(link=junk)
                self.assertEqual(self.stored(), ("cloud", "ACC-01", "z1.tsplus.example"))
        self.beat()  # 旧版不报
        self.assertEqual(self.stored(), ("cloud", "ACC-01", "z1.tsplus.example"))

    def test_kind_none_clears_it_and_offline_leaves_it(self):
        self.beat(link=self.CLOUD)
        self.beat(offline=True, link={"kind": "none"})  # 报离线只清报到时间
        self.assertEqual(self.stored(), ("cloud", "ACC-01", "z1.tsplus.example"))
        self.beat(link={"kind": "none"})
        self.assertEqual(self.stored(), ("", "", ""))

    def test_a_cloud_computer_routes_like_any_other(self):
        self.beat()
        before = devices.peers()[self.office.device]
        self.beat(link=self.CLOUD)
        self.assertEqual(devices.peers()[self.office.device], before)


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


    def test_a_request_with_a_later_parameter_goes_only_to_a_version_that_knows_it(self):
        # express_ar_open 带 docnums（按单号查发票状态，1.12.22）：旧版不认多出来的参数会回 params_invalid
        minimum = requests.contracts.minimum
        self.assertEqual(minimum("express_ar_open", {"customer": "C1"}), "1.12.20")
        self.assertEqual(minimum("express_ar_open", {"docnums": ["IV1"]}), "1.12.22")
        self.assertEqual(minimum("express_masters", {"docnums": ["IV1"]}), "1.10.0")
        # 这个替身站点只装了 express_masters 这一种：把同一条规则挂到它身上验派发
        later = {"express_masters": {"docnums": "1.12.22"}}
        made = self.register("staff@x", KEY_A)
        office = self.context(made["device"], "staff@x")
        key = contracts.book_key(contracts.book_identity(PATH, "vsn:1234/5"))
        self.report(office)
        frappe.db.set_value(documents.DEVICE, office.device, "companion_version", "1.12.21")
        params = {"account_set": "X", "account_root": "", "docnums": ["IV1"]}
        with mock.patch.dict(requests.contracts.PARAM_MIN_COMPANION, later):
            self.assertEqual(requests.waiting("express_masters", key, params=params), {"reason": "update_needed"})
            self.assertIsNone(requests.waiting("express_masters", key, params={"account_set": "X", "account_root": ""}))
            created = requests.create("express_masters", "erp_masters:" + key, params, book_key=key, actor="Administrator")
            old = requests.lease_next(office, {"version": "1.12.21", "kinds": ["express_masters"], "device": "PC"})
            self.assertIsNone(old["request"])
            new = requests.lease_next(office, {"version": "1.12.22", "kinds": ["express_masters"], "device": "PC"})
        self.assertEqual((new["request"]["id"], new["request"]["params"]["docnums"]), (created["request"], ["IV1"]))

    def test_a_new_request_wakes_the_computers_that_reach_its_book_once(self):
        # 11-15：LINE 选完公司马上选客户，预热读回要等电脑下一次空闲问（15 秒）。领单（3 秒）回复带 requests_waiting 叫它马上来
        made = self.register("staff@x", KEY_A)
        office = self.context(made["device"], "staff@x")
        key = contracts.book_key(contracts.book_identity(PATH, "vsn:1234/5"))
        self.report(office)
        frappe.db.set_value(documents.DEVICE, office.device, "companion_version", "1.12.22")
        body = {"max": 1, "agent_id": "c", "index_pending": []}
        self.assertNotIn("requests_waiting", jobs.lease(office, body))
        requests.create("express_masters", "erp_masters:" + key, {"account_set": "X", "account_root": ""},
                        book_key=key, actor="Administrator")
        self.assertIs(jobs.lease(office, body)["requests_waiting"], True)
        self.assertNotIn("requests_waiting", jobs.lease(office, body))  # 一次性：叫过就清

    def test_a_choice_for_a_held_operator_box_reaches_only_a_cc_that_reads_it_once(self):
        # 1.12.23 ④：人在对话里选了留着的框的某个钮 → 那台电脑下一次领单带 operator_controls；1.12.22 不认，不发
        from pearnly_core.cc_desktop import operator_controls

        made = self.register("staff@x", KEY_A)
        office = self.context(made["device"], "staff@x")
        self.report(office)
        body = {"max": 1, "agent_id": "c", "index_pending": []}
        item = {"action": "answer", "batch": "b-1", "doc": "PUR-1", "digest": "0123456789abcdef", "button": "แบบ ข"}
        frappe.db.set_value(documents.DEVICE, office.device, "companion_version", "1.12.22")
        self.assertTrue(operator_controls.send(office.device, "b-1", "PUR-1", "answer", "0123456789abcdef", "แบบ ข"))
        self.assertNotIn("operator_controls", jobs.lease(office, body))
        frappe.db.set_value(documents.DEVICE, office.device, "companion_version", "1.12.23")
        self.assertEqual(jobs.lease(office, body)["operator_controls"], [item])
        self.assertNotIn("operator_controls", jobs.lease(office, body))  # 取过就清
        self.assertFalse(operator_controls.send(office.device, "b-1", "PUR-1", "answer", "short", "แบบ ข"))


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

    def test_master_and_stock_refusals_go_to_a_person_without_retries(self):
        """覆盖表 G-B3（引导式录入第 1 期）：主档不在 / 仓不在仓库表 / 入库失败 / 科目码是库存品——重试结论一样，直接转人工，不白重试 3 次。"""
        for code in ("SUPPLIER_NOT_IN_APMAS", "CUSTOMER_NOT_IN_ARMAS", "ERR_LOCATION", "STOCK_IN_FAILED",
                     "ACCOUNT_CODE_IS_STOCK_ITEM"):
            self.assertIn(code, ack_rules.NEEDS_PERSON_CODES)
            state = ack_rules.acknowledgement(branch="legacy", attempt=1, success=False, error=f"[{code}] x")
            self.assertEqual((state.status, state.retry, state.attempt), ("manual", False, 1), code)

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
