"""CC 桌面端按需请求（2026-10 CC 闭环）：纯规则、结果字节的分块存放、建 / 领 / 交 / 取 / 删的服务、两条机器方法的
回包，以及 CC 从已授权文件夹列文件、取文件（`folder_intake/fetch.py`）。不连库、不依赖 Frappe（最小替身）。"""
import base64
import gzip
import hashlib
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_cowork/pearnly_cowork"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


contracts = _load("request_contracts_under_test", APP / "companion/request_contracts.py")
NOW = datetime(2026, 10, 13, 5, 0, 0)


def _b64(data):
    return base64.b64encode(data).decode("ascii")


class ContractTests(unittest.TestCase):
    def test_kinds_keys_and_params(self):
        self.assertEqual(contracts.kind("folder_fetch"), "folder_fetch")
        for bad in ("folder_delete", None, ""):
            with self.assertRaises(contracts.ContractError):
                contracts.kind(bad)
        self.assertEqual(contracts.request_key("folder_fetch:abc"), "folder_fetch:abc")
        for bad in ("", "x" * 141, "a b", None, "a/b"):
            with self.assertRaises(contracts.ContractError):
                contracts.request_key(bad)
        self.assertEqual(contracts.params({"root_id": "r", "path": "ก.pdf"}), {"path": "ก.pdf", "root_id": "r"})
        for bad in ([], "x", {"x": "y" * contracts.PARAMS_MAX}, {"x": float("nan")}):
            with self.assertRaises(contracts.ContractError):
                contracts.params(bad)

    def test_what_a_computer_can_take_depends_on_its_erp(self):
        self.assertEqual(contracts.allowed_kinds("express", ["folder_list", "express_read"]),
                         ["folder_list", "express_read"])
        self.assertEqual(contracts.allowed_kinds("mrerp", ["folder_list", "express_read"]), ["folder_list"])
        self.assertEqual(contracts.allowed_kinds("other", list(contracts.KINDS)), [])
        # 新版小助手声明了这里还不认识的种类：略过那几种，不整个拒绝
        self.assertEqual(contracts.next_request({"version": "1.6.0", "kinds": ["folder_list", "future", "folder_list"],
                                                 "device": "PC-1"}),
                         {"version": "1.6.0", "kinds": ["folder_list"], "device": "PC-1"})
        for bad in ([], {"kinds": []}, {"version": "1", "kinds": "folder_list"}, {"version": "1", "kinds": [],
                                                                                    "device": "x" * 61}):
            with self.assertRaises(contracts.ContractError):
                contracts.next_request(bad)

    def test_completion_messages(self):
        base = {"request": "a" * 32, "handle": "b" * 32}
        self.assertEqual(contracts.complete_request({**base, "ok": False, "code": "root_missing"}),
                         {**base, "ok": False, "code": "root_missing"})
        chunk = contracts.complete_request({**base, "ok": True, "offset": 0, "content": _b64(b"abc"), "final": False})
        self.assertEqual((chunk["content"], chunk["final"]), (b"abc", False))
        last = contracts.complete_request({**base, "ok": True, "offset": 3, "content": _b64(b"d"), "final": True,
                                           "size": 4, "sha256": hashlib.sha256(b"abcd").hexdigest(), "meta": {"n": 1}})
        self.assertEqual((last["size"], last["meta"]), (4, {"n": 1}))
        for bad in ({**base, "ok": False, "code": "bad code"}, {**base, "request": "A" * 32, "ok": False, "code": "x"},
                    {**base, "ok": True, "offset": -1, "content": "", "final": False},
                    {**base, "ok": True, "offset": True, "content": "", "final": False},
                    {**base, "ok": True, "offset": 0, "content": "@@", "final": False},
                    {**base, "ok": True, "offset": 0, "content": "", "final": True},
                    {**base, "ok": True, "offset": 0, "content": "A" * (4 * contracts.CHUNK_BYTES // 3 + 8),
                     "final": False},
                    {**base, "ok": True, "offset": 0, "content": "", "final": True, "size": 0,
                     "sha256": hashlib.sha256(b"").hexdigest(), "meta": {"x": "y" * contracts.META_MAX}},
                    {**base, "ok": "yes"}):
            with self.assertRaises(contracts.ContractError):
                contracts.complete_request(bad)

    def test_waiting_and_leased_requests_expire_when_read(self):
        old = NOW - timedelta(seconds=contracts.CLAIM_SECONDS + 1)
        self.assertEqual(contracts.effective("requested", "", old, None, NOW), ("expired", "not_claimed"))
        self.assertEqual(contracts.effective("requested", "", NOW, None, NOW), ("requested", ""))
        self.assertEqual(contracts.effective("leased", "", old, NOW, NOW), ("expired", "lease_expired"))
        self.assertEqual(contracts.effective("leased", "", old, NOW + timedelta(seconds=1), NOW), ("leased", ""))
        self.assertEqual(contracts.effective("failed", "root_missing", old, None, NOW), ("failed", "root_missing"))
        self.assertTrue(contracts.supports("1.5.0") and contracts.supports("1.10.1"))
        self.assertFalse(contracts.supports("1.4.4") or contracts.supports("") or contracts.supports("x"))
        self.assertTrue(contracts.seen_recently(NOW - timedelta(seconds=contracts.SEEN_SECONDS), NOW))
        self.assertFalse(contracts.seen_recently(NOW - timedelta(seconds=contracts.SEEN_SECONDS + 1), NOW))
        self.assertFalse(contracts.seen_recently(None, NOW))


# ── Frappe 替身 ─────────────────────────────────────────────────────────────


class Row(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value

    def update(self, values=None, **more):
        dict.update(self, values or {}, **more)


class FakeDB:
    def __init__(self, frappe):
        self.frappe = frappe
        self.rows, self.deleted, self.after = {}, [], []
        self.after_commit = types.SimpleNamespace(add=self.after.append)
        self.adapters = {"C1": "express", "M1": "mrerp"}

    def commit(self):
        for callback in self.after:
            callback()
        self.after.clear()

    def savepoint(self, name):
        pass

    def rollback(self, save_point=None):
        pass

    def exists(self, doctype, name):
        return name in self.rows

    def get_value(self, doctype, filters, field):
        if doctype == "Cowork ERP Connection":
            return self.adapters.get(filters)
        found = [row.name for row in self.rows.values() if all(row.get(k) == v for k, v in filters.items())]
        return found[0] if found else None

    def delete(self, doctype, filters):
        self.rows.pop(filters["name"], None)
        self.deleted.append(filters["name"])

    def _ordered(self, rows):
        return sorted(rows, key=lambda row: (row.requested_at, row.name))

    def sql(self, query, values=()):
        query = " ".join(query.split())
        if "COUNT(*)" in query:
            connection, actor = values
            return [[sum(1 for row in self.rows.values() if row.connection == connection and row.actor == actor
                         and row.status in ("requested", "leased"))]]
        if "requested_at <" in query:
            connection, cutoff = values
            return [(row.name,) for row in self.rows.values() if row.connection == connection and row.requested_at < cutoff]
        if "WHERE name = %s" in query:
            return [(values[0],)] if values[0] in self.rows else []
        if "status = 'requested' AND kind IN" in query:
            connection, kinds = values[0], values[1:]
            found = [row for row in self._ordered(self.rows.values()) if row.connection == connection
                     and row.status == "requested" and row.kind in kinds]
            return [(found[0].name,)] if found else []
        if "status IN ('requested', 'leased')" in query:
            return [(row.name,) for row in self._ordered(self.rows.values())
                    if row.connection == values[0] and row.status in ("requested", "leased")]
        raise AssertionError(query)


class FakeState:
    def __init__(self):
        self.values = {}

    def get_value(self, key, use_local_cache=True):
        return self.values.get(key)

    def set_value(self, key, value, expires_in_sec=None):
        self.values[key] = value


def _frappe():
    frappe = types.ModuleType("frappe")

    class ValidationError(Exception):
        pass

    class PermissionError(Exception):
        pass

    class DoesNotExistError(Exception):
        pass

    frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError = (
        ValidationError, PermissionError, DoesNotExistError)
    frappe.UniqueValidationError = type("UniqueValidationError", (ValidationError,), {})
    frappe.DuplicateEntryError = type("DuplicateEntryError", (Exception,), {})
    frappe.session = types.SimpleNamespace(user="acc@x.com")
    frappe._ = lambda text: text

    def throw(message, exc=None):
        raise (exc or ValidationError)(message)

    frappe.throw = throw
    frappe.clear_messages = lambda: None
    frappe.log_error = lambda **kw: None
    frappe.get_traceback = lambda: ""
    frappe.db = FakeDB(frappe)
    frappe.connections = {"C1": Row(name="C1", adapter="express"), "M1": Row(name="M1", adapter="mrerp")}
    frappe.get_doc = lambda doctype, name: frappe.connections[name]

    def new_doc(doctype):
        return Row(doctype=doctype)

    frappe.new_doc = new_doc
    root = tempfile.mkdtemp()
    frappe.get_site_path = lambda *parts: os.path.join(root, *parts)
    frappe.root = root
    utils = types.ModuleType("frappe.utils")
    utils.get_datetime = lambda value: value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    frappe.utils = utils
    return frappe, utils


def _service(clock):
    """request_service 连同替身：允许谁用哪条连接在 `allowed` 里改。"""
    frappe, utils = _frappe()
    state = FakeState()
    allowed = {("acc@x.com", "C1"), ("acc@x.com", "M1"), ("other@x.com", "C1")}
    db = frappe.db

    def require_connection(doc, actor=None, **kwargs):
        actor = actor or frappe.session.user
        if (actor, doc.name) not in allowed:
            raise frappe.PermissionError("Not Permitted")
        return actor

    def read(name, for_update=False):
        if name not in db.rows:
            raise frappe.DoesNotExistError(name)
        return db.rows[name]

    def write(doc):
        if not doc.name:
            doc.name = doc.request_id
        if doc.name not in db.rows and any(row.request_key == doc.request_key for row in db.rows.values()):
            raise frappe.UniqueValidationError(doc.request_key)
        db.rows[doc.name] = doc
        return doc

    access = types.ModuleType("request_access")
    access.REQUEST, access.read, access.write = "Cowork Companion Request", read, write
    execution = types.ModuleType("pearnly_core.execution")
    execution.state = types.SimpleNamespace(store=lambda: state)
    modules = {"frappe": frappe, "frappe.utils": utils, "pearnly_core": types.ModuleType("pearnly_core"),
               "pearnly_core.execution": execution, "pearnly_core.execution.state": execution.state,
               "pearnly_cowork": types.ModuleType("pearnly_cowork"),
               "pearnly_cowork.companion": types.ModuleType("pearnly_cowork.companion"),
               "pearnly_cowork.companion.request_contracts": contracts,
               "pearnly_cowork.companion.request_access": access,
               "pearnly_cowork.connections": types.ModuleType("pearnly_cowork.connections"),
               "pearnly_cowork.connections.access": types.SimpleNamespace(require_connection=require_connection)}
    saved = {name: sys.modules.get(name) for name in list(modules) + ["pearnly_cowork.companion.request_store"]}
    sys.modules.update(modules)
    try:
        store = _load("pearnly_cowork.companion.request_store", APP / "companion/request_store.py")
        modules["pearnly_cowork.companion"].request_contracts = contracts
        modules["pearnly_cowork.companion"].request_store = store
        modules["pearnly_cowork.connections"].access = modules["pearnly_cowork.connections.access"]
        service = _load("request_service_under_test", APP / "companion/request_service.py")
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    service._now = clock
    return service, frappe, state, allowed, store


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


def _machine(connection="C1", credential="CRED-1"):
    return types.SimpleNamespace(connection=connection, credential_id=credential)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.service, self.frappe, self.state, self.allowed, self.store = _service(self.clock)
        self.db = self.frappe.db

    def _lease(self, connection="C1", kinds=("folder_list", "folder_fetch", "express_read"), version="1.5.0"):
        return self.service.lease_next(_machine(connection), {"version": version, "kinds": list(kinds)})

    def _complete(self, leased, content, *, chunk=4, credential="CRED-1"):
        request = leased["request"]
        replies = []
        for offset in range(0, max(len(content), 1), chunk):
            piece = content[offset:offset + chunk]
            body = {"request": request["id"], "handle": request["handle"], "ok": True, "offset": offset,
                    "content": _b64(piece), "final": offset + chunk >= len(content)}
            if body["final"]:
                body.update(size=len(content), sha256=hashlib.sha256(content).hexdigest(), meta={"name": "a.pdf"})
            replies.append(self.service.complete(_machine(credential=credential), body))
        return replies

    def test_create_is_idempotent_per_key_and_only_for_the_actor(self):
        first = self.service.create("folder_fetch", "C1", {"root_id": "r", "path": "a.pdf"}, "folder_fetch:k1")
        self.assertEqual(first["status"], "requested")
        again = self.service.create("folder_fetch", "C1", {"root_id": "r", "path": "a.pdf"}, "folder_fetch:k1")
        self.assertEqual(again["request"], first["request"])
        self.assertEqual(len(self.db.rows), 1)
        self.frappe.session.user = "other@x.com"
        with self.assertRaises(self.frappe.PermissionError):
            self.service.create("folder_fetch", "C1", {"root_id": "r", "path": "a.pdf"}, "folder_fetch:k1")
        with self.assertRaises(self.frappe.DoesNotExistError):
            self.service.status(first["request"])  # 结果只给发起人
        self.frappe.session.user = "nobody@x.com"
        with self.assertRaises(self.frappe.PermissionError):
            self.service.create("folder_list", "C1", {"root_id": "r"}, "folder_list:k2")

    def test_reading_the_books_is_only_for_express_and_open_requests_are_capped(self):
        with self.assertRaises(self.frappe.ValidationError):
            self.service.create("express_read", "M1", {}, "express_read:k")
        for number in range(contracts.OPEN_PER_ACTOR):
            self.service.create("folder_list", "C1", {"n": number}, f"folder_list:{number}")
        with self.assertRaises(self.frappe.ValidationError) as caught:
            self.service.create("folder_list", "C1", {"n": 99}, "folder_list:99")
        self.assertIn("busy", str(caught.exception))

    def test_lease_complete_result_and_drop(self):
        made = self.service.create("folder_fetch", "C1", {"root_id": "r", "path": "a.pdf"}, "folder_fetch:k1")
        leased = self._lease()
        request = leased["request"]
        self.assertEqual((request["id"], request["kind"], request["params"]), (made["request"], "folder_fetch",
                                                                               {"path": "a.pdf", "root_id": "r"}))
        self.assertEqual((request["chunk_bytes"], request["max_bytes"]),
                         (contracts.CHUNK_BYTES, contracts.MAX_BYTES["folder_fetch"]))
        self.assertEqual(self.service.status(made["request"])["status"], "leased")
        self.assertIsNone(self._lease()["request"])  # 一件只给一次
        content = b"%PDF-1.7 statement"
        replies = self._complete(leased, content)
        self.assertEqual(replies[-1], {"status": "done", "received": len(content)})
        self.assertEqual(self.service.status(made["request"])["status"], "done")
        self.assertEqual(self.service.result(made["request"]), (content, {"name": "a.pdf"}))
        # 做完没取走时再问同一件：还是这一件（不让电脑重读）
        self.assertEqual(self.service.create("folder_fetch", "C1", {"root_id": "r", "path": "a.pdf"},
                                             "folder_fetch:k1")["request"], made["request"])
        path = self.frappe.get_site_path("private", self.store.FOLDER, made["request"])
        self.assertTrue(os.path.exists(path))
        self.service.drop(made["request"])
        self.db.commit()
        self.assertFalse(os.path.exists(path))
        self.assertNotIn(made["request"], self.db.rows)
        # 取走以后再问是新的一件
        self.assertNotEqual(self.service.create("folder_fetch", "C1", {"root_id": "r", "path": "a.pdf"},
                                                "folder_fetch:k1")["request"], made["request"])

    def test_a_resent_chunk_is_answered_with_what_was_received(self):
        self.service.create("folder_fetch", "C1", {"p": 1}, "folder_fetch:k1")
        leased = self._lease()
        request = leased["request"]
        ident = {"request": request["id"], "handle": request["handle"], "ok": True}
        self.assertEqual(self.service.complete(_machine(), {**ident, "offset": 0, "content": _b64(b"abcd"),
                                                            "final": False}), {"status": "leased", "received": 4})
        self.assertEqual(self.service.complete(_machine(), {**ident, "offset": 0, "content": _b64(b"abcd"),
                                                            "final": False}),
                         {"ok": False, "code": "companion.on_demand_offset", "received": 4})
        # 上一块写了文件却没提交（库里仍是 4）：重发时从 4 截断重写
        self.store.write(request["id"], 4, b"zz-uncommitted")
        done = self.service.complete(_machine(), {**ident, "offset": 4, "content": _b64(b"ef"), "final": True,
                                                  "size": 6, "sha256": hashlib.sha256(b"abcdef").hexdigest()})
        self.assertEqual(done["status"], "done")
        self.assertEqual(self.service.result(request["id"])[0], b"abcdef")

    def test_only_the_computer_and_key_that_leased_it_may_complete(self):
        self.service.create("folder_fetch", "C1", {"p": 1}, "folder_fetch:k1")
        request = self._lease()["request"]
        body = {"request": request["id"], "handle": request["handle"], "ok": False, "code": "root_missing"}
        for context, changed in ((_machine(connection="M1"), body), (_machine(credential="CRED-2"), body),
                                 (_machine(), {**body, "handle": "c" * 32})):
            with self.assertRaises(contracts.ContractError) as caught:
                self.service.complete(context, changed)
            self.assertEqual(caught.exception.code, "companion.on_demand_stale")
        self.assertEqual(self.service.complete(_machine(), body), {"status": "failed"})
        current = self.service.status(request["id"])
        self.assertEqual((current["status"], current["code"]), ("failed", "root_missing"))
        with self.assertRaises(contracts.ContractError):
            self.service.complete(_machine(), body)  # 已经交过

    def test_too_large_mismatched_and_late_results_are_refused(self):
        self.service.create("folder_list", "C1", {"p": 1}, "folder_list:k1")
        request = self._lease()["request"]
        ident = {"request": request["id"], "handle": request["handle"], "ok": True}
        too_big = contracts.MAX_BYTES["folder_list"] + 1
        self.db.rows[request["id"]].received_bytes = too_big - 1
        self.store.write(request["id"], 0, b"x" * (too_big - 1))
        self.assertEqual(self.service.complete(_machine(), {**ident, "offset": too_big - 1, "content": _b64(b"xy"),
                                                            "final": False}),
                         {"ok": False, "code": "companion.on_demand_too_large"})
        self.assertEqual(self.service.status(request["id"])["code"], "too_large")
        self.service.create("folder_list", "C1", {"p": 2}, "folder_list:k2")
        request = self._lease()["request"]
        ident = {"request": request["id"], "handle": request["handle"], "ok": True}
        self.assertEqual(self.service.complete(_machine(), {**ident, "offset": 0, "content": _b64(b"abc"),
                                                            "final": True, "size": 3, "sha256": "0" * 64}),
                         {"ok": False, "code": "companion.on_demand_mismatch"})
        self.service.create("folder_list", "C1", {"p": 3}, "folder_list:k3")
        request = self._lease()["request"]
        self.clock.now += timedelta(seconds=contracts.LEASE_SECONDS["folder_list"] + 1)
        with self.assertRaises(contracts.ContractError):
            self.service.complete(_machine(), {"request": request["id"], "handle": request["handle"], "ok": True,
                                               "offset": 0, "content": "", "final": False})
        self.assertEqual(self.service.status(request["id"])["status"], "expired")

    def test_leasing_records_presence_expires_stale_rows_and_skips_actors_without_access(self):
        old = self.service.create("folder_list", "C1", {"p": 0}, "folder_list:old")
        self.clock.now += timedelta(seconds=contracts.CLAIM_SECONDS + 5)
        first = self.service.create("folder_list", "C1", {"p": 1}, "folder_list:k1")
        self.clock.now += timedelta(seconds=1)  # 先来先做：first 在前
        self.frappe.session.user = "other@x.com"
        second = self.service.create("folder_fetch", "C1", {"p": 2}, "folder_fetch:k2")
        self.frappe.session.user = "acc@x.com"
        self.allowed.discard(("acc@x.com", "C1"))  # 发起人此刻已不能用这条连接
        leased = self._lease()
        self.assertEqual(leased["request"]["id"], second["request"])
        self.assertEqual(self.db.rows[first["request"]].error_code, "actor_not_allowed")
        self.assertEqual((self.db.rows[old["request"]].status, self.db.rows[old["request"]].error_code),
                         ("expired", "not_claimed"))
        presence = self.service.presence("C1")
        self.assertEqual((presence["seen"], presence["version"]), (True, "1.5.0"))
        self.assertEqual(presence["kinds"], ["folder_list", "folder_fetch", "express_read"])
        self.clock.now += timedelta(seconds=contracts.SEEN_SECONDS + 1)
        self.assertFalse(self.service.presence("C1")["seen"])
        self.assertEqual(self.service.presence("M1"), {"seen": False, "version": "", "kinds": [], "at": None})

    def test_waiting_tells_offline_from_needs_an_update(self):
        self.assertEqual(self.service.waiting("C1", "folder_list"), {"status": "waiting_for_cc", "reason": "offline"})
        self.assertEqual(self.service.waiting("C1", "folder_list", "1.5.2"), {"status": "waiting_for_cc", "reason": "offline"})
        self.assertEqual(self.service.waiting("C1", "folder_list", "1.4.4"),
                         {"status": "waiting_for_cc", "reason": "update_needed", "version": "1.4.4",
                          "minimum": contracts.MIN_COMPANION})
        self._lease(kinds=("folder_list",))
        self.assertEqual(self.service.waiting("C1", "folder_list", "1.4.4"), {})  # 正在来领：版本以它报的为准
        self.assertEqual(self.service.waiting("C1", "express_read")["reason"], "update_needed")  # 来领但不会做这种

    def test_an_mrerp_folder_member_never_gets_a_books_read(self):
        self.allowed.add(("acc@x.com", "M1"))
        self.db.rows["x" * 32] = Row(name="x" * 32, request_id="x" * 32, request_key="express_read:k",
                                      connection="M1", kind="express_read", actor="acc@x.com", status="requested",
                                      requested_at=NOW, params_json="{}")
        self.assertIsNone(self._lease(connection="M1")["request"])
        self.assertEqual(self.service.presence("M1")["kinds"], ["folder_list", "folder_fetch"])

    def test_old_requests_are_swept_with_their_bytes_when_a_new_one_is_made(self):
        made = self.service.create("folder_fetch", "C1", {"p": 1}, "folder_fetch:k1")
        self.store.write(made["request"], 0, b"left over")
        self.clock.now += timedelta(seconds=contracts.KEEP_SECONDS + 1)
        self.service.create("folder_fetch", "C1", {"p": 2}, "folder_fetch:k2")
        self.db.commit()
        self.assertNotIn(made["request"], self.db.rows)
        self.assertIsNone(self.store.read(made["request"]))

    def test_store_refuses_gaps_and_odd_names(self):
        name = "f" * 32
        self.assertEqual(self.store.write(name, 0, b"abc"), 3)
        self.assertEqual(self.store.write(name, 3, b"de"), 5)
        with self.assertRaises(ValueError):
            self.store.write(name, 9, b"x")
        self.assertEqual(self.store.digest(name), (5, hashlib.sha256(b"abcde").hexdigest()))
        for bad in ("../x", "F" * 32, "a" * 31):
            with self.assertRaises(ValueError):
                self.store.read(bad)
        self.store.drop(name)
        self.store.drop(name)
        self.assertIsNone(self.store.read(name))


class MachineReplyTests(unittest.TestCase):
    """两条机器方法：先提交放锁；业务上的「不行」回 ok: false 与原因（续传另带 received）。"""

    def setUp(self):
        frappe, utils = _frappe()
        frappe.whitelist = lambda **kw: (lambda fn: fn)
        frappe.local = types.SimpleNamespace()
        self.commits = []
        frappe.db.commit = lambda: self.commits.append("commit")
        frappe.db.rollback = lambda save_point=None: self.commits.append("rollback")
        auth = types.ModuleType("http_auth")
        auth.current_request = lambda operation: (types.SimpleNamespace(connection="C1", operation=operation), {"x": 1})
        auth.clear_private_messages = lambda: None
        service = types.ModuleType("request_service")
        companion = types.ModuleType("pearnly_cowork.companion")
        companion.http_auth, companion.request_service, companion.request_contracts = auth, service, contracts
        saved = {name: sys.modules.get(name) for name in ("frappe", "pearnly_cowork", "pearnly_cowork.companion")}
        sys.modules.update({"frappe": frappe, "pearnly_cowork": types.ModuleType("pearnly_cowork"),
                            "pearnly_cowork.companion": companion})
        try:
            self.api = _load("request_api_under_test", APP / "companion/request_api.py")
        finally:
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

    def test_replies(self):
        self.assertEqual(self.api._guarded("request_next", lambda context, body: {"request": None}),
                         {"ok": True, "protocol": self.api.PROTOCOL, "request": None})
        self.assertEqual(self.commits, ["commit", "commit"])  # 先提交放掉连接行锁，做完再提交
        self.assertEqual(self.api._guarded("request_complete", lambda c, b: {"ok": False, "code": "x", "received": 4}),
                         {"ok": False, "code": "x", "received": 4})

        def stale(context, body):
            raise contracts.ContractError("companion.on_demand_stale")

        self.assertEqual(self.api._guarded("request_complete", stale), {"ok": False, "code": "companion.on_demand_stale"})
        self.assertEqual(self.commits[-1], "rollback")


# ── CC 从已授权文件夹列文件、取文件（folder_intake/fetch.py）──────────────────────────────


class FetchServiceTests(unittest.TestCase):
    def setUp(self):
        frappe, utils = _frappe()
        self.frappe = frappe
        folder_contracts = _load("folder_contracts_for_fetch", APP / "folder_intake/contracts.py")
        self.created, self.dropped = [], []
        self.waiting_reply, self.asked = {}, []
        self.results = {}
        self.statuses = {}
        service = types.SimpleNamespace(
            waiting=lambda connection, kind, version="": self.asked.append((connection, kind, version))
            or self.waiting_reply,
            create=lambda kind, connection, params, key: self.created.append((kind, connection, params, key))
            or {"request": "r%d" % len(self.created), "status": "requested"},
            status=lambda request: self.statuses[request],
            result=lambda request: self.results[request],
            drop=self.dropped.append)
        self.agent_state = {"version": "1.5.0", "roots": [{"id": "root1", "label": "D:\\Clients", "folders": []}]}
        agent = types.SimpleNamespace(read=lambda connection: self.agent_state)
        self.checked = []
        rules = types.SimpleNamespace(_require=lambda: self.checked.append("scope"),
                                      _connection=lambda name: self.checked.append(name) or types.SimpleNamespace(name=name))
        companion = types.ModuleType("pearnly_cowork.companion")
        companion.request_contracts, companion.request_service = contracts, service
        folder = types.ModuleType("pearnly_cowork.folder_intake")
        folder.agent, folder.contracts, folder.rules = agent, folder_contracts, rules
        saved = {name: sys.modules.get(name) for name in ("frappe", "pearnly_cowork", "pearnly_cowork.companion",
                                                          "pearnly_cowork.folder_intake")}
        sys.modules.update({"frappe": frappe, "pearnly_cowork": types.ModuleType("pearnly_cowork"),
                            "pearnly_cowork.companion": companion, "pearnly_cowork.folder_intake": folder})
        try:
            self.fetch = _load("folder_fetch_under_test", APP / "folder_intake/fetch.py")
        finally:
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

    def test_an_offline_or_old_computer_gets_no_request(self):
        self.waiting_reply = {"status": "waiting_for_cc", "reason": "offline"}
        self.assertEqual(self.fetch.files("C1", "root1"), {"status": "waiting_for_cc", "reason": "offline"})
        self.agent_state["version"] = "1.4.4"  # 文件夹同步报的版本交给通道分辨离线与要升级
        self.fetch.fetch("C1", "root1", "a.pdf")
        self.assertEqual(self.asked, [("C1", "folder_list", "1.5.0"), ("C1", "folder_fetch", "1.4.4")])
        self.assertEqual(self.created, [])
        self.assertEqual(self.checked[:2], ["scope", "C1"])  # 先核权限

    def test_requests_carry_checked_params_and_a_stable_key_per_conversation(self):
        made = self.fetch.files("C1", "root1", "ACME/bank", search=" kbank ", extensions=["PDF"], scope="sesn_1")
        self.assertEqual(made, {"request": "r1", "status": "requested"})
        kind, connection, params, key = self.created[0]
        self.assertEqual((kind, connection), ("folder_list", "C1"))
        self.assertEqual(params, {"root_id": "root1", "folder": "ACME\\bank", "search": "kbank", "extensions": [".pdf"],
                                  "limit": 5000})
        self.fetch.files("C1", "root1", "ACME/bank", search=" kbank ", extensions=["PDF"], scope="sesn_1")
        self.fetch.files("C1", "root1", "ACME/bank", search=" kbank ", extensions=["PDF"], scope="sesn_2")
        self.assertEqual(self.created[1][3], key)
        self.assertNotEqual(self.created[2][3], key)
        self.fetch.fetch("C1", "root1", "ACME/bank/kbank.pdf", scope="sesn_1")
        self.assertEqual(self.created[-1][:3], ("folder_fetch", "C1", {"root_id": "root1", "path": "ACME\\bank\\kbank.pdf"}))
        for bad in (lambda: self.fetch.fetch("C1", "root1", "..\\x.pdf"), lambda: self.fetch.fetch("C1", "root1", "a.exe"),
                    lambda: self.fetch.fetch("C1", "other", "a.pdf"), lambda: self.fetch.files("C1", "bad id")):
            with self.assertRaises(self.frappe.ValidationError):
                bad()

    def test_a_listing_is_checked_sorted_in_bangkok_time_and_dropped(self):
        payload = {"files": [{"path": "A\\old.pdf", "size": 1, "modified": 1_700_000_000},
                             {"path": "A\\new.xlsx", "size": 2, "modified": 1_760_000_000},
                             {"path": "..\\evil.pdf", "size": 3, "modified": 1_800_000_000}],
                   "truncated": False, "scanned": 3}
        self.results["r1"] = (gzip.compress(json.dumps(payload).encode()), {"format": "json.gz"})
        data = self.fetch.listing("r1")
        self.assertEqual([row["path"] for row in data["files"]], ["A\\new.xlsx", "A\\old.pdf"])
        self.assertEqual(data["files"][0]["modified"], "2025-10-09 15:53")
        self.assertEqual(self.dropped, ["r1"])
        self.results["r2"] = (gzip.compress(b"\0" * (self.fetch.LISTING_MAX_BYTES + 10)), {})
        with self.assertRaises(self.frappe.ValidationError):
            self.fetch.listing("r2")  # 解开过大的不收

    def test_content_takes_its_name_from_the_requested_path(self):
        self.statuses["r1"] = {"status": "done", "params": {"root_id": "root1", "path": "ACME\\bank\\kbank.pdf"}}
        self.results["r1"] = (b"%PDF", {"name": "..\\evil.exe"})
        self.assertEqual(self.fetch.content("r1"), ("kbank.pdf", b"%PDF"))
        self.assertEqual(self.dropped, ["r1"])


if __name__ == "__main__":
    unittest.main()
