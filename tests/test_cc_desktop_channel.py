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
from pearnly_core.cc_desktop.wire import queue_contracts  # noqa: E402

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
        self.assertEqual(self.lease(self.office)["jobs"], [])  # 每台同时一件
        reply = self.ack(self.office, job["job"])
        self.assertEqual(reply, {"ok": True, "status": "success", "express_docnum": "IV691023-001"})
        self.assertEqual((self.changes[-1]["status"], self.changes[-1]["docnum"], self.changes[-1]["verify_state"]),
                         ("success", "IV691023-001", "ok"))
        self.assertEqual(self.ack(self.office, job["job"]), {"ok": True, "status": "success", "idempotent": True})

    def test_a_colleagues_computer_never_writes_my_job(self):
        other = self.register("other@x", KEY_B)
        colleague = self.context(other["device"], "other@x")
        self.report(colleague)
        self.enqueue(actor="staff@x")
        self.assertEqual(self.lease(colleague)["jobs"], [])  # staff 没在这台登录过 CC
        self.assertEqual(len(self.lease(self.office)["jobs"]), 1)
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

    def test_an_expired_lease_comes_back_only_to_the_same_computer(self):
        other = self.register("staff@x", KEY_B)
        pc2 = self.context(other["device"], "staff@x")
        self.report(pc2)
        job = self.enqueue()
        self.lease(self.office)
        row = fake.STATE.db.table(documents.JOB)[job["job"]]
        row["lease_expires_at"] = devices.now() - timedelta(seconds=1)
        self.assertEqual(self.lease(pc2)["jobs"], [])  # 收回后钉在原来那台：结果不明，不交给别的电脑
        self.assertEqual(jobs.find_by_name(job["job"])["reason"], jobs.LOST)
        again = self.lease(self.office)["jobs"]
        self.assertEqual(again[0]["log_id"], job["job"])  # 同一个编号：它本机记着写过没有
        # 迟到的回执（收回之后才回报）也照收
        row["lease_expires_at"] = devices.now() - timedelta(seconds=1)
        self.lease(pc2)
        self.assertEqual(self.ack(self.office, job["job"])["status"], "success")

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
                                               "leave")}, set(http_auth.PATHS.values()))
        machine = ast.parse((ROOT / "apps/pearnly_core/pearnly_core/cc_desktop/machine.py").read_text(encoding="utf-8"))
        posted = {node.name for node in machine.body if isinstance(node, ast.FunctionDef)
                  and any("frappe.whitelist" in ast.unparse(d) and "'POST'" in ast.unparse(d)
                          for d in node.decorator_list)}
        self.assertEqual(posted, {method.rsplit(".", 1)[1] for method in http_auth.METHODS.values()})

    def test_the_wire_shape_is_the_cowork_one(self):
        job = queue_contracts.job("3c0d8c86-3f6b-4b1e-9d6f-8b7a9d9c1c11", {"history": "SD-1", "invoice_no": "SD-1",
                                                                          "frozen_payload": {"a": 1}}, True)
        self.assertEqual(set(job), {"log_id", "history_id", "invoice_no", "payload", "attended"})


if __name__ == "__main__":
    unittest.main()
