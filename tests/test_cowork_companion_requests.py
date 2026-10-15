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
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_cowork/pearnly_cowork"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


contracts = _load("request_contracts_under_test", APP / "companion/request_contracts.py")
route_contracts = _load("route_contracts_for_requests", APP / "companion/route_contracts.py")
identity = _load("identity_for_requests", APP / "targets/identity.py")
NOW = datetime(2026, 10, 13, 5, 0, 0)


def _b64(data):
    return base64.b64encode(data).decode("ascii")


class ContractTests(unittest.TestCase):
    def test_the_request_table_accepts_every_kind_and_status(self):
        """表的下拉选项要含契约里的每一种：Frappe 写入时按选项校验，漏一项那种请求就建不出来（2026-10-15 生产：
        `account_history` 没进 `kind` 选项，科目历史请求全被 `_validate_selects` 拒掉，替身测试没拦住）。"""
        doctype = json.loads((APP / "pearnly_cowork/doctype/cowork_companion_request/cowork_companion_request.json")
                             .read_text(encoding="utf-8"))
        options = {field["fieldname"]: field.get("options", "").split("\n") for field in doctype["fields"]
                   if field.get("fieldtype") == "Select"}
        self.assertEqual(sorted(options["kind"]), sorted(contracts.KINDS))
        self.assertEqual(sorted(options["status"]), sorted(contracts.STATUSES))

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
        self.expire_scans, self.locked = [], []
        self.snapshot = set()
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
        # 普通读看本事务快照：`snapshot` 里登记的请求号读不到（别的事务刚提交的行）
        if filters.get("request_key") in self.snapshot:
            return None
        found = [row.name for row in self.rows.values() if all(row.get(k) == v for k, v in filters.items())]
        return found[0] if found else None

    def delete(self, doctype, filters):
        self.rows.pop(filters["name"], None)
        self.deleted.append(filters["name"])

    def _ordered(self, rows):
        return sorted(rows, key=lambda row: (row.requested_at, row.name))

    def sql(self, query, values=(), as_dict=False):
        query = " ".join(query.split())
        if "request_key = %s LOCK IN SHARE MODE" in query:
            # 加锁读：看最新提交的（不受 `snapshot` 影响）
            assert as_dict
            return [row for row in self.rows.values() if row.request_key == values[0]]
        if "status = 'leased' AND lease_expires_at > %s" in query:
            # 这台电脑手上没到期的租约（按电脑，ADR-021 第 4 批）
            connection, now, device = values
            found = sorted((row for row in self.rows.values() if row.connection == connection
                            and row.status == "leased" and row.lease_expires_at and row.lease_expires_at > now
                            and (row.lease_device or "") == device),
                           key=lambda row: row.lease_expires_at, reverse=True)
            return [(found[0].kind, found[0].companion_version)] if found else []
        if "COUNT(*)" in query:
            connection, actor = values
            return [[sum(1 for row in self.rows.values() if row.connection == connection and row.actor == actor
                         and row.status in ("requested", "leased"))]]
        if "(status = 'requested' AND requested_at < %s)" in query:
            # 到期的先不加锁挑出来
            assert "FOR UPDATE" not in query
            connection, cutoff, now = values
            self.expire_scans.append(connection)
            return [(row.name,) for row in self._ordered(self.rows.values()) if row.connection == connection and (
                (row.status == "requested" and row.requested_at < cutoff)
                or (row.status == "leased" and (not row.lease_expires_at or row.lease_expires_at <= now)))]
        if "WHERE name = %s AND status" in query and "FOR UPDATE" in query:
            # 逐件加锁复核
            self.locked.append(values[0])
            row = self.rows.get(values[0])
            wanted = ("requested",) if "status = 'requested'" in query else ("requested", "leased")
            return [(values[0],)] if row and row.status in wanted else []
        if "requested_at <" in query:
            connection, cutoff = values
            return [(row.name,) for row in self.rows.values() if row.connection == connection and row.requested_at < cutoff]
        if "WHERE name = %s" in query:
            return [(values[0],)] if values[0] in self.rows else []
        if "status = 'requested' AND kind IN" in query:
            # 领取：只看定向给这台的与没定向的、读账套的只看它够得到的几份，先不加锁，最多 LEASE_SCAN 件
            assert as_dict and "FOR UPDATE" not in query
            count = query.split("kind IN (")[1].split(")")[0].count("%s")
            connection, kinds, device = values[0], values[1:1 + count], values[1 + count]
            reach = set(values[2 + count:])
            assert "IFNULL(account_key, '') = ''" in query and (not reach or "OR account_key IN" in query)
            limit = int(query.rsplit("LIMIT ", 1)[1])
            return [row for row in self._ordered(self.rows.values()) if row.connection == connection
                    and row.status == "requested" and row.kind in kinds and (row.target_device or "") in (device, "")
                    and (not row.account_key or row.account_key in reach)][:limit]
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


class Pool:
    """池的替身（ADR-021 第 4 批）：密钥 → 电脑、各条连接上的电脑（`credentials.pool_peers`）、谁够得到哪份账套
    （`connections.pool`）。默认每条连接上一台电脑 PC1（密钥 CRED-1；CRED-2 是另一台 PC2）。"""

    def __init__(self):
        self.devices = {"CRED-1": "PC1", "CRED-2": "PC2", "CRED-3": "PC3"}
        self.peers = {"C1": {"PC1": {"user": "", "always_on": False, "version": ""}},
                      "M1": {"PC1": {"user": "", "always_on": False, "version": ""}}}
        # {(连接, 电脑, 规范键): 可达行（本机写法）}
        self.reach = {}

    def readable_keys(self, connection, device):
        return {key for (conn, name, key) in self.reach if (conn, name) == (connection, device)}

    def readable_reach(self, connection, device, keys):
        return {key: row for (conn, name, key), row in self.reach.items()
                if (conn, name) == (connection, device) and key in set(keys)}

    def reachers(self, connection, key, writable=False):
        return sorted(name for (conn, name, found) in self.reach if (conn, found) == (connection, key))

    def reachers_of(self, connection, keys, writable=False):
        return {key: self.reachers(connection, key) for key in keys}


def _service(clock):
    """request_service 连同替身：允许谁用哪条连接在 `allowed` 里改。"""
    frappe, utils = _frappe()
    state = FakeState()
    allowed = {("acc@x.com", "C1"), ("acc@x.com", "M1"), ("other@x.com", "C1")}
    db = frappe.db
    pool = Pool()

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
               "pearnly_cowork.connections.access": types.SimpleNamespace(require_connection=require_connection),
               "pearnly_cowork.companion.credentials": types.SimpleNamespace(
                   device_id=lambda credential: pool.devices.get(credential, credential),
                   pool_peers=lambda connection: {name: dict(peer) for name, peer in pool.peers.get(connection, {}).items()}),
               "pearnly_cowork.companion.route_contracts": route_contracts,
               "pearnly_cowork.connections.pool": pool,
               "pearnly_cowork.targets": types.ModuleType("pearnly_cowork.targets"),
               "pearnly_cowork.targets.identity": identity}
    saved = {name: sys.modules.get(name) for name in list(modules) + ["pearnly_cowork.companion.request_store"]}
    sys.modules.update(modules)
    try:
        store = _load("pearnly_cowork.companion.request_store", APP / "companion/request_store.py")
        modules["pearnly_cowork.companion"].request_contracts = contracts
        modules["pearnly_cowork.companion"].request_store = store
        modules["pearnly_cowork.companion"].credentials = modules["pearnly_cowork.companion.credentials"]
        modules["pearnly_cowork.companion"].route_contracts = route_contracts
        modules["pearnly_cowork.connections"].access = modules["pearnly_cowork.connections.access"]
        modules["pearnly_cowork.connections"].pool = pool
        service = _load("request_service_under_test", APP / "companion/request_service.py")
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    service._now = clock
    service.test_pool = pool
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
        presence = self.service.presence("C1", "PC1")
        self.assertEqual((presence["seen"], presence["version"]), (True, "1.5.0"))
        self.assertEqual(presence["kinds"], ["folder_list", "folder_fetch", "express_read"])
        # 领到的那件交了（手上没有没做完的）、之后 SEEN_SECONDS 没再来领：离线
        self.service.complete(_machine(), {"request": second["request"], "handle": leased["request"]["handle"],
                                           "ok": False, "code": "root_missing"})
        self.clock.now += timedelta(seconds=contracts.SEEN_SECONDS + 1)
        self.assertFalse(self.service.presence("C1", "PC1")["seen"])
        self.assertEqual(self.service.presence("M1", "PC1"), {"seen": False, "version": "", "kinds": [], "at": None})

    def test_waiting_tells_offline_from_needs_an_update(self):
        self.assertEqual(self.service.waiting("C1", "folder_list"), {"status": "waiting_for_cc", "reason": "offline"})
        self.assertEqual(self.service.waiting("C1", "folder_list", "1.5.2"), {"status": "waiting_for_cc", "reason": "offline"})
        self.assertEqual(self.service.waiting("C1", "folder_list", "1.4.4"),
                         {"status": "waiting_for_cc", "reason": "update_needed", "version": "1.4.4",
                          "minimum": contracts.MIN_COMPANION})
        self._lease(kinds=("folder_list",))
        self.assertEqual(self.service.waiting("C1", "folder_list", "1.4.4"), {})  # 正在来领：版本以它报的为准
        self.assertEqual(self.service.waiting("C1", "express_read")["reason"], "update_needed")  # 来领但不会做这种

    def test_a_computer_busy_with_a_long_request_still_counts_as_online(self):
        """小助手一次只做一件、做完才再来领：传 30MB、读账套的那几分钟里它没来领，但手上有租约没到期，就还算在线。"""
        made = self.service.create("folder_fetch", "C1", {"root_id": "r", "path": "a.pdf"}, "folder_fetch:k1")
        leased = self._lease()
        self.clock.now += timedelta(seconds=contracts.SEEN_SECONDS + 10)
        request = leased["request"]
        self.assertEqual(self.service.complete(_machine(), {"request": request["id"], "handle": request["handle"],
                                                            "ok": True, "offset": 0, "content": _b64(b"abcd"),
                                                            "final": False}), {"status": "leased", "received": 4})
        self.assertTrue(self.service.presence("C1", "PC1")["seen"])
        self.assertEqual(self.service.waiting("C1", "folder_fetch", "1.5.0"), {})
        self.assertEqual(self.service.waiting("C1", "folder_list"), {})  # 别的请求也照常建，等它做完这件再领
        # 原样重调：同一件，接着等
        self.assertEqual(self.service.create("folder_fetch", "C1", {"root_id": "r", "path": "a.pdf"},
                                             "folder_fetch:k1")["request"], made["request"])
        # 记的「来过」丢了（缓存清过）：按手上那一件的版本与种类
        self.state.values.clear()
        presence = self.service.presence("C1", "PC1")
        self.assertEqual((presence["seen"], presence["version"], presence["kinds"]), (True, "1.5.0", ["folder_fetch"]))
        # 租约到期（电脑真的不在了）：离线
        self.clock.now += timedelta(seconds=contracts.LEASE_SECONDS["folder_fetch"] + 1)
        self.assertFalse(self.service.presence("C1", "PC1")["seen"])
        self.assertEqual(self.service.waiting("C1", "folder_fetch", "1.5.0")["reason"], "offline")

    def test_find_returns_the_actors_open_request_without_making_one(self):
        params = {"root_id": "r", "path": "a.pdf"}
        self.assertIsNone(self.service.find("folder_fetch", "C1", "folder_fetch:k1"))
        made = self.service.create("folder_fetch", "C1", params, "folder_fetch:k1")
        self.assertEqual(self.service.find("folder_fetch", "C1", "folder_fetch:k1")["request"], made["request"])
        self.assertIsNone(self.service.find("folder_list", "C1", "folder_fetch:k1"))  # 种类、连接不对不算
        self.assertIsNone(self.service.find("folder_fetch", "M1", "folder_fetch:k1"))
        self.frappe.session.user = "other@x.com"
        self.assertIsNone(self.service.find("folder_fetch", "C1", "folder_fetch:k1"))  # 别人的不给
        self.frappe.session.user = "acc@x.com"
        leased = self._lease()
        self._complete(leased, b"%PDF")
        self.assertEqual(self.service.find("folder_fetch", "C1", "folder_fetch:k1")["status"], "done")
        self.service.drop(made["request"])
        self.assertIsNone(self.service.find("folder_fetch", "C1", "folder_fetch:k1"))
        self.service.create("folder_fetch", "C1", params, "folder_fetch:k2")
        self.clock.now += timedelta(seconds=contracts.CLAIM_SECONDS + 1)
        self.assertIsNone(self.service.find("folder_fetch", "C1", "folder_fetch:k2"))  # 过期的不算
        self.assertEqual(len(self.db.rows), 1)
        with self.assertRaises(self.frappe.ValidationError):
            self.service.find("folder_fetch", "C1", "bad key")

    def test_the_same_key_made_at_the_same_moment_returns_the_first_row(self):
        """两个相同的工具调用同时建同一件：后到的撞唯一键，普通读在可重复读快照里看不到先建成的行，要用加锁读。"""
        first = self.service.create("folder_fetch", "C1", {"root_id": "r", "path": "a.pdf"}, "folder_fetch:k1")
        self.db.snapshot.add("folder_fetch:k1")  # 后到的事务开头时那一行还没提交
        again = self.service.create("folder_fetch", "C1", {"root_id": "r", "path": "a.pdf"}, "folder_fetch:k1")
        self.assertEqual((again["request"], again["status"]), (first["request"], "requested"))
        self.assertEqual(len(self.db.rows), 1)
        self.frappe.session.user = "other@x.com"
        with self.assertRaises(self.frappe.PermissionError):
            self.service.create("folder_fetch", "C1", {"root_id": "r", "path": "a.pdf"}, "folder_fetch:k1")
        self.frappe.session.user = "acc@x.com"
        with mock.patch.object(self.service, "_committed", return_value=None), \
                self.assertRaises(self.frappe.ValidationError) as caught:
            self.service.create("folder_fetch", "C1", {"root_id": "r", "path": "a.pdf"}, "folder_fetch:k1")
        self.assertIn("busy", str(caught.exception))  # 先建成的又被删了：稍后再试

    def test_the_account_history_belongs_to_the_connection_not_to_who_asked(self):
        """科目建议的历史（``SHARED_KINDS``）：同一请求号谁建都是同一件，此刻能用这条连接的人都能看、取、删；
        不能用这条连接的人照样拿不到。回读、取文件仍只给发起人（上面几条）。"""
        self.assertTrue(contracts.shared("account_history"))
        self.assertFalse(contracts.shared("express_read") or contracts.shared("folder_fetch"))
        params = {"account_set": "S:\\EXP69\\DN", "account_root": "S:\\EXP69", "since": "2025-10-15"}
        made = self.service.create("account_history", "C1", params, "account_history:k1")
        self.frappe.session.user = "other@x.com"
        again = self.service.create("account_history", "C1", params, "account_history:k1")
        self.assertEqual(again["request"], made["request"])
        self.assertEqual(len(self.db.rows), 1)
        self.assertEqual(self.service.find("account_history", "C1", "account_history:k1")["request"], made["request"])
        self.assertIsNone(self.service.find("account_history", "M1", "account_history:k1"))  # 连接不对不算
        leased = self._lease(kinds=("account_history",))
        self.assertEqual((leased["request"]["kind"], leased["request"]["max_bytes"]),
                         ("account_history", contracts.MAX_BYTES["account_history"]))
        self._complete(leased, b"history")
        self.assertEqual(self.service.status(made["request"])["status"], "done")
        self.assertEqual(self.service.result(made["request"])[0], b"history")  # 不是发起人也能取
        self.frappe.session.user = "nobody@x.com"  # 不能用这条连接
        with self.assertRaises(self.frappe.DoesNotExistError):
            self.service.status(made["request"])
        with self.assertRaises(self.frappe.DoesNotExistError):
            self.service.result(made["request"])
        self.frappe.session.user = "other@x.com"
        self.service.drop(made["request"])
        self.assertNotIn(made["request"], self.db.rows)
        # 旧版 CC 桌面版没声明这种请求：不交给它
        self.service.create("account_history", "C1", params, "account_history:k2")
        self.assertIsNone(self._lease(kinds=("express_read", "folder_list"))["request"])
        self.assertEqual(self.service.waiting("C1", "account_history")["reason"], "update_needed")

    def test_an_mrerp_folder_member_never_gets_a_books_read(self):
        self.allowed.add(("acc@x.com", "M1"))
        self.db.rows["x" * 32] = Row(name="x" * 32, request_id="x" * 32, request_key="express_read:k",
                                      connection="M1", kind="express_read", actor="acc@x.com", status="requested",
                                      requested_at=NOW, params_json="{}")
        self.assertIsNone(self._lease(connection="M1")["request"])
        self.assertEqual(self.service.presence("M1", "PC1")["kinds"], ["folder_list", "folder_fetch"])

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


class PoolRequestTests(unittest.TestCase):
    """多台电脑（ADR-021 第 4 批，方案 2.7）：读文件夹定向给持有根目录的那台；读账套按账套键分派（发起人自己的电脑 →
    常驻 → 其他，排 120 秒谁都行），交出去的位置换成领取那台的写法；在场与「手上有一件」按电脑记。"""

    KEY = "\\\\nas\\exp\\data\\acme69"

    def setUp(self):
        self.clock = Clock()
        self.service, self.frappe, self.state, self.allowed, self.store = _service(self.clock)
        self.db = self.frappe.db
        self.pool = self.service.test_pool
        self.pool.peers["C1"] = {"PC1": {"user": "acc@x.com", "always_on": False, "version": "1.6.0"},
                                 "PC2": {"user": "other@x.com", "always_on": False, "version": "1.6.0"},
                                 "PC3": {"user": "", "always_on": False, "version": "1.6.0"}}
        # PC1 映射成 S:，PC2 用网络路径，PC3 够不到这份账套
        self.pool.reach[("C1", "PC1", self.KEY)] = Row(path="S:\\EXP\\DATA\\ACME69", root="S:\\EXP")
        self.pool.reach[("C1", "PC2", self.KEY)] = Row(path="\\\\nas\\exp\\data\\acme69", root="\\\\nas\\exp")

    def lease(self, credential, kinds=("folder_list", "folder_fetch", "express_read", "account_history")):
        return self.service.lease_next(_machine(credential=credential), {"version": "1.6.0", "kinds": list(kinds)})

    def test_contract_targets_only_the_matching_kind_and_localizes_a_copy(self):
        self.assertEqual(contracts.target("folder_fetch", "PC1", "K"), ("PC1", ""))
        self.assertEqual(contracts.target("express_read", "PC1", "K"), ("", "K"))
        self.assertEqual(contracts.target("account_history", "", "K"), ("", "K"))
        with self.assertRaises(contracts.ContractError):
            contracts.target("folder_fetch", "x" * 141)
        params = {"account_set": "\\\\nas\\a", "account_root": "\\\\nas", "period": "2026-09"}
        local = contracts.localize(params, "S:\\A", "S:\\")
        self.assertEqual(local, {"account_set": "S:\\A", "account_root": "S:\\", "period": "2026-09"})
        self.assertEqual(params["account_set"], "\\\\nas\\a")  # 原件不动
        self.assertEqual(contracts.localize(params, ""), params)  # 没有写法不编
        self.assertEqual(set(contracts.ACCOUNT_KINDS) | set(contracts.FOLDER_KINDS), set(contracts.KINDS))

    def test_a_folder_request_goes_only_to_the_computer_holding_the_root(self):
        made = self.service.create("folder_list", "C1", {"root_id": "r"}, "folder_list:k1", target_device="PC2")
        self.assertEqual(self.db.rows[made["request"]].target_device, "PC2")
        self.assertIsNone(self.lease("CRED-1")["request"])  # 同一个池里的 PC1 领不到
        self.assertEqual(self.lease("CRED-2")["request"]["id"], made["request"])
        self.assertEqual(self.db.rows[made["request"]].lease_device, "PC2")
        # 部署前建的（没定向）照旧谁领都行
        legacy = self.service.create("folder_list", "C1", {"root_id": "q"}, "folder_list:k2")
        self.assertEqual(self.lease("CRED-1")["request"]["id"], legacy["request"])

    def test_presence_and_the_open_lease_are_kept_per_computer(self):
        made = self.service.create("folder_fetch", "C1", {"root_id": "r", "path": "a.pdf"}, "folder_fetch:k1",
                                   target_device="PC2")
        self.lease("CRED-2")
        self.assertTrue(self.service.presence("C1", "PC2")["seen"])
        self.assertFalse(self.service.presence("C1", "PC1")["seen"])  # PC2 来领不算 PC1 在线
        self.assertEqual(self.service.waiting("C1", "folder_list", device="PC1")["reason"], "offline")
        self.assertEqual(self.service.waiting("C1", "folder_list", device="PC2"), {})
        self.clock.now += timedelta(seconds=contracts.SEEN_SECONDS + 5)
        self.state.values.clear()
        self.assertTrue(self.service.presence("C1", "PC2")["seen"])  # 手上那一件租约没到期：仍在线
        self.assertFalse(self.service.presence("C1", "PC1")["seen"])
        self.assertEqual(self.db.rows[made["request"]].lease_device, "PC2")

    def test_a_books_read_is_routed_to_the_askers_computer_first_with_its_own_writing(self):
        params = {"account_set": self.KEY, "account_root": "\\\\nas\\exp", "period": "2026-09", "docnums": []}
        self.assertIsNone(self.lease("CRED-1")["request"])  # PC1（发起人 acc 的电脑）此刻在、会读账套
        made = self.service.create("express_read", "C1", params, "readback:k1", account_key=self.KEY.upper())
        self.assertEqual(self.db.rows[made["request"]].account_key, self.KEY)  # 存规范键
        self.assertIsNone(self.lease("CRED-2")["request"])  # PC2 不是发起人的电脑，PC1 又在线：先让着
        self.assertIsNone(self.lease("CRED-3")["request"])  # PC3 够不到这份账套，永远领不到
        leased = self.lease("CRED-1")["request"]  # 发起人自己的电脑
        self.assertEqual(leased["id"], made["request"])
        self.assertEqual((leased["params"]["account_set"], leased["params"]["account_root"]),
                         ("S:\\EXP\\DATA\\ACME69", "S:\\EXP"))  # 换成 PC1 自己的写法
        self.assertEqual(json.loads(self.db.rows[made["request"]].params_json)["account_set"], self.KEY)  # 存着的不改

    def test_a_books_read_moves_on_after_two_minutes_or_when_the_askers_computer_is_away(self):
        params = {"account_set": self.KEY, "account_root": "", "since": "2025-10-01"}
        made = self.service.create("account_history", "C1", params, "account_history:k1", account_key=self.KEY)

        def asker_here():  # PC1（发起人的电脑）此刻在、会读账套，只是这一轮还没来领
            self.service._remember("C1", "PC1", "1.6.0", ["account_history"], self.clock.now)

        asker_here()
        self.assertIsNone(self.lease("CRED-2")["request"])
        self.clock.now += timedelta(seconds=route_contracts.HANDOFF_SECONDS + 1)
        asker_here()
        leased = self.lease("CRED-2")["request"]  # 排了 120 秒：够得到的谁都行
        self.assertEqual(leased["id"], made["request"])
        self.assertEqual(leased["params"]["account_set"], "\\\\nas\\exp\\data\\acme69")
        # 发起人的电脑不在（SEEN_SECONDS 内没来领过）：够得到的另一台立刻领
        other = self.service.create("account_history", "C1", params, "account_history:k2", account_key=self.KEY)
        self.clock.now += timedelta(seconds=contracts.SEEN_SECONDS + 1)
        self.assertEqual(self.lease("CRED-2")["request"]["id"], other["request"])

    def test_waiting_for_a_books_read_asks_the_computers_that_reach_it(self):
        self.assertEqual(self.service.waiting("C1", "express_read", account_key=self.KEY)["reason"], "offline")
        self.lease("CRED-3")  # 在线但够不到这份账套
        self.assertEqual(self.service.waiting("C1", "express_read", account_key=self.KEY)["reason"], "offline")
        self.lease("CRED-2")
        self.assertEqual(self.service.waiting("C1", "express_read", account_key=self.KEY), {})
        self.assertEqual(self.service.waiting("C1", "express_read", account_key="\\\\nas\\none")["reason"],
                         "offline")  # 没有电脑够得到

    def test_books_reads_another_computer_cannot_take_do_not_fill_its_scan(self):
        """第 4 批审查 L1：PC1 是唯一够得到账套 X 的电脑；排着的 X 读请求比一次扫描的名额还多时，PC2 照样领得到后面
        定向给它的取文件请求（领取只扫这台够得到的那几份账套）。"""
        self.pool.reach.pop(("C1", "PC2", self.KEY))
        self.allowed.add(("third@x.com", "C1"))
        for index in range(self.service.LEASE_SCAN + 5):
            self.frappe.session.user = ("acc@x.com", "other@x.com", "third@x.com")[index % 3]
            self.service.create("account_history", "C1", {"account_set": self.KEY, "account_root": "", "n": index},
                                f"account_history:x{index}", account_key=self.KEY)
            self.clock.now += timedelta(seconds=1)
        self.frappe.session.user = "acc@x.com"
        mine = self.service.create("folder_fetch", "C1", {"root_id": "r", "path": "a.pdf"}, "folder_fetch:late",
                                   target_device="PC2")
        self.assertEqual(self.lease("CRED-2")["request"]["id"], mine["request"])

    def test_an_unknown_computer_is_never_asked_through_the_old_presence(self):
        """第 4 批审查 L2：认不出电脑（空）时不问部署前按连接记的在场、不算没记电脑的旧租约。"""
        self.service._remember("C1", "", "1.6.0", ["folder_list"], self.clock.now)
        self.assertEqual(self.service.waiting("C1", "folder_list", "1.6.0", device="")["reason"], "offline")
        self.assertEqual(self.service._candidates("C1", ""), {})

    def test_expiry_scans_without_locks_and_locks_only_what_expired(self):
        old = self.service.create("folder_list", "C1", {"p": 0}, "folder_list:old", target_device="PC1")
        self.clock.now += timedelta(seconds=contracts.CLAIM_SECONDS + 5)
        fresh = self.service.create("folder_list", "C1", {"p": 1}, "folder_list:new", target_device="PC2")
        self.db.locked.clear()
        self.lease("CRED-1")
        self.assertEqual(self.db.rows[old["request"]].status, "expired")
        self.assertEqual(self.db.rows[fresh["request"]].status, "requested")
        self.assertEqual(self.db.locked, [old["request"]])  # 只锁到期的那件，PC2 的那件不碰


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
        self.open, self.found = {}, []
        self.targets = []
        service = types.SimpleNamespace(
            find=lambda kind, connection, key: self.found.append((kind, connection, key)) or self.open.get(key),
            waiting=lambda connection, kind, version="", device=None: self.asked.append((connection, kind, version))
            or self.devices_asked.append(device) or self.waiting_reply,
            create=lambda kind, connection, params, key, target_device="": (
                self.created.append((kind, connection, params, key)), self.targets.append(target_device))
            and {"request": "r%d" % len(self.created), "status": "requested"},
            status=lambda request: self.statuses[request],
            result=lambda request: self.results[request],
            drop=self.dropped.append)
        self.agent_state = {"version": "1.5.0", "roots": [{"id": "root1", "label": "D:\\Clients", "folders": []}]}
        self.devices_asked = []

        def resolve(value, root_id=None):
            device, connection = folder_contracts.parse_reader(value)
            # 只给连接的旧写法：按根目录认出是 PC1；CX / CY 上没有一台报过这个根目录
            return device or ("" if connection in ("CX", "CY") else "PC1"), connection

        # CX 上有电脑（只是没报过这个根目录），CY 上一台电脑都没有
        readers = types.SimpleNamespace(resolve=resolve, state=lambda device, connection: self.agent_state,
                                        members=lambda connection: {} if connection == "CY" else {"PC1": {}})
        self.checked = []
        rules = types.SimpleNamespace(_require=lambda: self.checked.append("scope"),
                                      _connection=lambda name: self.checked.append(name) or types.SimpleNamespace(name=name))
        companion = types.ModuleType("pearnly_cowork.companion")
        companion.request_contracts, companion.request_service = contracts, service
        folder = types.ModuleType("pearnly_cowork.folder_intake")
        folder.readers, folder.contracts, folder.rules = readers, folder_contracts, rules
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
        self.assertEqual(self.devices_asked, ["PC1", "PC1"])  # 只问读这个文件夹的那台电脑（ADR-021 第 4 批）
        self.assertEqual(self.created, [])
        self.assertEqual(self.checked[:2], ["scope", "C1"])  # 先核权限

    def test_an_unknown_computer_gets_no_untargeted_request(self):
        """第 4 批审查 L2：这条连接上有电脑、却认不出是哪台（没有一台报过这个根目录）时直接拒，不建池里谁都能领的请求，
        也不按部署前按连接记的在场算在线；一台电脑都没有时照旧回「CC 桌面版离线」。"""
        for call in (lambda: self.fetch.files("CX", "root1"), lambda: self.fetch.fetch("CX", "root1", "a.pdf")):
            with self.assertRaises(self.frappe.ValidationError):
                call()
        self.assertEqual((self.created, self.asked), ([], []))
        self.waiting_reply = {"status": "waiting_for_cc", "reason": "offline"}
        self.assertEqual(self.fetch.files("CY", "root1"), {"status": "waiting_for_cc", "reason": "offline"})
        self.assertEqual(self.devices_asked, [""])
        self.assertEqual(self.created, [])

    def test_an_open_request_is_picked_up_before_asking_whether_the_computer_is_there(self):
        """电脑正忙着传这一件（好一阵没来领）：原样重调接着等这一件，不判离线、不另建。"""
        self.fetch.fetch("C1", "root1", "ACME/a.pdf", scope="sesn_1")
        key = self.created[0][3]
        self.assertEqual(self.found[0], ("folder_fetch", "C1", key))  # 找的是建时用的同一请求号
        self.open[key] = {"request": "r1", "status": "leased", "code": "", "params": {}}
        self.waiting_reply = {"status": "waiting_for_cc", "reason": "offline"}
        self.asked.clear()
        self.assertEqual(self.fetch.fetch("C1", "root1", "ACME\\a.pdf", scope="sesn_1"),
                         {"request": "r1", "status": "leased"})
        self.assertEqual((self.asked, len(self.created)), ([], 1))
        self.fetch.files("C1", "root1", scope="sesn_1")
        list_key = self.found[-1][2]
        self.open[list_key] = {"request": "r9", "status": "done", "code": "", "params": {}}
        self.assertEqual(self.fetch.files("C1", "root1", scope="sesn_1"), {"request": "r9", "status": "done"})
        # 换了一段对话是另一件：照常先问电脑在不在
        self.assertEqual(self.fetch.fetch("C1", "root1", "ACME\\a.pdf", scope="sesn_2"),
                         {"status": "waiting_for_cc", "reason": "offline"})

    def test_requests_carry_checked_params_and_a_stable_key_per_conversation(self):
        made = self.fetch.files("C1", "root1", "ACME/bank", search=" kbank ", extensions=["PDF"], scope="sesn_1")
        self.assertEqual(made, {"request": "r1", "status": "requested"})
        kind, connection, params, key = self.created[0]
        self.assertEqual((kind, connection), ("folder_list", "C1"))
        self.assertEqual(params, {"root_id": "root1", "folder": "ACME\\bank", "search": "kbank", "extensions": [".pdf"],
                                  "limit": 5000})
        self.assertEqual(self.targets[0], "PC1")  # 定向给读这个文件夹的电脑，池里别的电脑领不到
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
