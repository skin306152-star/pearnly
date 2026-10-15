"""CC 桌面版多台电脑第 1 批（ADR-021）：电脑记录、按电脑的密钥、按摘要认证、register v2、认电脑的两条机器方法、
补丁第 1 步。

真跑 `companion/credentials.py`、`devices.py`、`documents.py`、`device_documents.py` 和补丁，只把 Frappe 与连接域的
授权换成内存替身（同一事务内的保存点按快照回退；唯一字段按 MariaDB 规则：NULL 可重复）。不连库，CI 离线跑。
真实 Site 上的加锁、并发与 HTTP 链在测试环境联调（方案第 7 批）里验。
"""
import ast
import copy
import importlib
import importlib.util
import itertools
import json
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_cowork/pearnly_cowork"
DOMAIN = APP / "companion"
SITE = "cowork.example"
CONNECTION = "Cowork ERP Connection"
CREDENTIAL = "Cowork Express Agent Credential"
DEVICE = "Cowork CC Desktop Device"
UNIQUE = {CREDENTIAL: ("record_id", "token_digest", "active_key", "generation_key", "issued_request_digest"),
          DEVICE: ("secret_digest",)}
ADMIN, ENTRY, OTHER, OUTSIDER = "admin@example.com", "entry@example.com", "other@example.com", "outsider@example.com"


# ── Frappe 替身 ─────────────────────────────────────────────────────────────


class Row(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


class ValidationError(Exception):
    pass


class PermissionError_(Exception):
    pass


class AuthenticationError(Exception):
    pass


class DoesNotExistError(ValidationError):
    pass


class UniqueValidationError(ValidationError):
    pass


class DuplicateEntryError(Exception):
    pass


class QueryDeadlockError(Exception):
    pass


STORE = {}
_ORDER = itertools.count()
LOCKS = []  # (doctype, name) of each row lock taken by a locking read, in order


def _lock(doctype, names):
    LOCKS.extend((doctype, name) for name in names)


class Document:
    def __init__(self, data, new=False):
        object.__setattr__(self, "_data", dict(data))
        object.__setattr__(self, "flags", Row())
        object.__setattr__(self, "_new", new)
        object.__setattr__(self, "_before", None)

    def __getattr__(self, key):
        if key.startswith("__"):
            raise AttributeError(key)
        return self._data.get(key)

    def __setattr__(self, key, value):
        self._data[key] = value

    def get(self, key, default=None):
        return self._data.get(key, default)

    def is_new(self):
        return self._new

    def get_doc_before_save(self):
        return self._before

    def _hook(self, name, *args):
        method = getattr(type(self), name, None)
        if method is not None:
            method(self, *args)

    def _unique(self):
        for field in UNIQUE.get(self.doctype, ()):
            value = self._data.get(field)
            if value is None:
                continue
            for name, row in STORE.get(self.doctype, {}).items():
                if name != self.name and row.get(field) == value:
                    raise UniqueValidationError(field)

    def insert(self, ignore_permissions=False):
        if hasattr(type(self), "autoname"):
            self.autoname()
        elif not self.name:
            self.name = "N-%d" % next(_ORDER)
        if self.name in STORE.get(self.doctype, {}):
            raise DuplicateEntryError(self.name)
        for hook in ("before_insert", "before_validate", "validate", "before_save"):
            self._hook(hook)
        self._unique()
        self._data.setdefault("creation", next(_ORDER))
        STORE.setdefault(self.doctype, {})[self.name] = copy.deepcopy(self._data)
        object.__setattr__(self, "_new", False)
        return self

    def save(self, ignore_permissions=False, ignore_version=False):
        object.__setattr__(self, "_before", Document(copy.deepcopy(STORE[self.doctype][self.name])))
        for hook in ("before_validate", "before_save", "validate"):
            self._hook(hook)
        self._unique()
        STORE[self.doctype][self.name] = copy.deepcopy(self._data)
        return self

    def reload(self):
        object.__setattr__(self, "_data", copy.deepcopy(STORE[self.doctype][self.name]))


CLASSES = {}


def _get_doc(value, name=None, for_update=False):
    if isinstance(value, dict):
        return CLASSES.get(value["doctype"], Document)(value, new=True)
    row = STORE.get(value, {}).get(name)
    if row is None:
        raise DoesNotExistError(name)
    if for_update:
        _lock(value, [name])
    return CLASSES.get(value, Document)(copy.deepcopy(row))


def _throw(message, exc=ValidationError):
    raise exc(message)


def _rows(doctype):
    return list(STORE.get(doctype, {}).values())


def _active_row(row):
    return not row.get("revoked_at") and bool(row.get("active_key"))


class FakeDB:
    def __init__(self):
        self.points = {}
        self.committed = {}

    def savepoint(self, name):
        self.points[name] = copy.deepcopy(STORE)

    def rollback(self, save_point=None):
        STORE.clear()
        STORE.update(copy.deepcopy(self.points[save_point] if save_point else self.committed))

    def commit(self):
        self.committed = copy.deepcopy(STORE)

    def get_value(self, doctype, name, field, for_update=False, as_dict=False, **ignored):
        if doctype == "Cowork Workspace":
            return name
        row = STORE.get(doctype, {}).get(name)
        if isinstance(field, (list, tuple)):
            return Row({key: row.get(key) for key in field}) if row is not None else None
        return (row or {}).get(field)

    def set_value(self, doctype, name, field, value=None, update_modified=True):
        STORE[doctype][name].update(field if isinstance(field, dict) else {field: value})

    def table_exists(self, doctype, **ignored):
        return True

    def has_column(self, doctype, column):
        return True

    def sql(self, query, values=(), as_dict=False):
        q = " ".join(query.split())
        out = self._sql(q, values)
        if q.endswith("FOR UPDATE"):
            names = [r[0] if isinstance(r, tuple) else r["name"] for r in out]
            if "d ON d.name = k.device" in q:  # one statement locks the keys, then their computers
                _lock(CREDENTIAL, names)
                _lock(DEVICE, [STORE[CREDENTIAL][n]["device"] for n in names])
            else:
                _lock(DEVICE if "WHERE secret_digest=%s" in q else CREDENTIAL, names)
        return out

    def _sql(self, q, values):
        if "`tabDocShare`" in q:
            return []
        # 第 2 批：心跳里的电脑（`devices.member`）、池里的电脑（`credentials.pool_peers`）、最新版本
        if "SELECT name, host_names FROM `tabCowork CC Desktop Device`" in q:
            return [Row(name=r["name"], host_names=r.get("host_names")) for r in _rows(DEVICE)
                    if r["workspace"] == values[0] and not r.get("retired_at")]
        if "SELECT d.label FROM" in q and "ORDER BY k.last_seen_at DESC" in q:
            devices_ = STORE.get(DEVICE, {})
            found = [(r, devices_[r["device"]]) for r in _rows(CREDENTIAL)
                     if r["connection"] == values[0] and _active_row(r) and r.get("device") in devices_
                     and not devices_[r["device"]].get("retired_at") and r.get("last_seen_at")]
            found.sort(key=lambda pair: pair[0]["last_seen_at"], reverse=True)
            return [(d.get("label"),) for _, d in found[:1]]
        if "LEFT JOIN `tabCowork CC Desktop Device` d" in q:
            devices_ = STORE.get(DEVICE, {})
            found = [(r, devices_.get(r.get("device") or "", {})) for r in _rows(CREDENTIAL)
                     if r["connection"] == values[0] and _active_row(r)
                     and (not r.get("device") or not devices_.get(r["device"], {}).get("retired_at"))]
            return [Row(name=k["name"], device=k.get("device"), issuer=k.get("issuer"),
                        token_generation=k["token_generation"], last_seen_at=k.get("last_seen_at"),
                        last_seen_generation=k.get("last_seen_generation"), signed_in_user=d.get("signed_in_user"),
                        always_on=d.get("always_on"), companion_version=d.get("companion_version"),
                        max_payload_version=d.get("max_payload_version"),
                        index_pending_uids=d.get("index_pending_uids")) for k, d in found]
        if "SELECT d.companion_version" in q:
            devices_ = STORE.get(DEVICE, {})
            return [(devices_[r["device"]].get("companion_version"),) for r in _rows(CREDENTIAL)
                    if r["connection"] == values[0] and _active_row(r) and r.get("device") in devices_
                    and not devices_[r["device"]].get("retired_at")]
        if "JOIN `tabCowork ERP Connection` c ON c.name = k.connection" in q:
            out = []
            for row in sorted(_rows(CREDENTIAL), key=lambda r: (r["creation"], r["name"])):
                if _active_row(row) and not row.get("device"):
                    conn = STORE[CONNECTION][row["connection"]]
                    out.append(Row(name=row["name"], connection=row["connection"], workspace=row["workspace"],
                                   issuer=row["issuer"], report_json=conn.get("report_json"), label=conn.get("label")))
            return out
        if "FROM `tabCowork ERP Connection`" in q and "adapter IN" in q:
            workspace, adapters = values
            rows = [r for r in sorted(_rows(CONNECTION), key=lambda r: (r["creation"], r["name"]))
                    if r["workspace"] == workspace and not r.get("client") and r.get("enabled")
                    and not r.get("binding_generation") and r["adapter"] in adapters]
            return [Row(name=r["name"], adapter=r["adapter"]) for r in rows]
        if "WHERE secret_digest=%s" in q:
            return [(r["name"],) for r in _rows(DEVICE) if r.get("secret_digest") == values[0]]
        if "d ON d.name = k.device" in q:
            devices = STORE.get(DEVICE, {})
            return [(r["name"],) for r in _rows(CREDENTIAL)
                    if r["connection"] == values[0] and _active_row(r) and r.get("device") in devices
                    and devices[r["device"]].get("secret_digest") and not devices[r["device"]].get("retired_at")][:1]
        if "WHERE connection=%s AND device=%s AND name<>%s" in q:
            found = [r for r in _rows(CREDENTIAL) if r["connection"] == values[0] and r.get("device") == values[1]
                     and r["name"] != values[2] and _active_row(r)]
            return [(r["name"],) for r in sorted(found, key=lambda r: -r["token_generation"])]
        if "WHERE device=%s AND revoked_at IS NULL" in q:
            return [(r["name"],) for r in _rows(CREDENTIAL) if r.get("device") == values[0] and _active_row(r)][:1]
        if "WHERE connection=%s AND active_key=%s" in q:
            return [(r["name"],) for r in _rows(CREDENTIAL)
                    if r["connection"] == values[0] and r.get("active_key") == values[1]]
        if "ORDER BY token_generation DESC, name DESC LIMIT 1" in q:
            found = sorted((r for r in _rows(CREDENTIAL) if r["connection"] == values[0]),
                           key=lambda r: (r["token_generation"], r["name"]), reverse=True)
            return [(r["name"],) for r in found[:1]]
        if "WHERE name=%s AND connection=%s AND revoked_at IS NULL" in q:
            return [(r["name"],) for r in _rows(CREDENTIAL)
                    if r["name"] == values[0] and r["connection"] == values[1] and _active_row(r)]
        if "WHERE token_digest=%s" in q:
            return [(r["name"],) for r in _rows(CREDENTIAL) if r["token_digest"] == values[0]]
        if "JSON_EXTRACT(request_receipts" in q:
            operation = values[1][3:-1]
            return [(r["name"],) for r in _rows(CREDENTIAL)
                    if r["connection"] == values[0] and operation in json.loads(r.get("request_receipts") or "{}")]
        raise AssertionError("unexpected query: " + q)


class Conf(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


def _install_frappe():
    frappe = types.ModuleType("frappe")
    frappe._ = lambda text: text
    frappe._dict = Row
    frappe.throw = _throw
    frappe.clear_messages = lambda: None
    frappe.whitelist = lambda **ignored: (lambda function: function)
    for error in (ValidationError, AuthenticationError, DoesNotExistError, UniqueValidationError,
                  DuplicateEntryError, QueryDeadlockError):
        setattr(frappe, error.__name__, error)
    frappe.PermissionError = PermissionError_
    frappe.local = types.SimpleNamespace(site=SITE)
    frappe.session = types.SimpleNamespace(user=ADMIN)
    frappe.conf = Conf(pearnly_product="cowork", pearnly_cowork_erp_push_enabled=True)
    frappe.db = FakeDB()
    frappe.get_doc = _get_doc
    # 电脑改挂时文件夹域的钩子（`cowork_reader_device_moved`，ADR-021 第 4 批）：记进池替身的事件里，与可达行转移排在一起
    frappe.get_hooks = lambda name: ["folder.readers.moved"] if name == "cowork_reader_device_moved" else []
    frappe.get_attr = lambda path: (lambda *args: sys.modules["pearnly_cowork.connections.pool"].EVENTS.append(
        ("readers", *args)))
    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")
    document.Document = Document
    model.document = document
    frappe.model = model
    sys.modules.update({"frappe": frappe, "frappe.model": model, "frappe.model.document": document})
    return frappe


def _install_connections(frappe):
    connections = types.ModuleType("pearnly_cowork.connections")
    connections.__path__ = []
    access = types.ModuleType("pearnly_cowork.connections.access")
    roles = {ADMIN: {"Cowork Admin"}, ENTRY: {"Cowork Intake"}, OTHER: {"Cowork Intake"}, OUTSIDER: set()}

    def deny():
        raise PermissionError_("Not Permitted")

    def require_actor(actor=None, *, management=False, lock=True):
        actor = actor or frappe.session.user
        granted = roles.get(actor, set())
        if management and "Cowork Admin" not in granted or not granted & {"Cowork Admin", "Cowork Intake"}:
            deny()
        return actor

    def require_connection(doc, *, actor=None, management=False, enabled=True, lock=True):
        actor = require_actor(actor, management=management)
        if enabled and not doc.enabled:
            raise PermissionError_("connection.disabled")
        if not management and actor not in json.loads(doc.allowed_users or "[]"):
            deny()
        return actor

    def usable_by(doc, actor):
        try:
            require_connection(doc, actor=actor, lock=False)
        except PermissionError_:
            return False
        return True

    access.roles, access.deny, access.require_actor = roles, deny, require_actor
    access.require_connection, access.default_workspace, access.usable_by = require_connection, lambda: "WS", usable_by
    model = types.ModuleType("pearnly_cowork.connections.model")

    class ConnectionError_(ValueError):
        code = "connection.invalid"

    model.ConnectionError = ConnectionError_
    model.parse = lambda value, default=None: json.loads(value) if isinstance(value, str) else (
        value if value is not None else default)
    service = types.ModuleType("pearnly_cowork.connections.service")
    service.MAIN_ADAPTERS = ("express", "mrerp")

    def main_connections(workspace, *, enabled=True):
        # 同 `connections.service.main_connections`：最早建、启用、不挂客户（FakeDB 认这句只回启用的）
        assert enabled
        found = {}
        for row in frappe.db.sql("""SELECT name, adapter FROM `tabCowork ERP Connection`
                WHERE workspace=%s AND COALESCE(client,'')='' AND enabled=1 AND binding_generation=0
                AND adapter IN %s ORDER BY creation, name""", (workspace, service.MAIN_ADAPTERS), as_dict=True):
            found.setdefault(row.adapter, row.name)
        return found

    service.main_connections = main_connections

    def configure(workspace, adapter, request_key, label, config, **ignored):
        actor = require_actor(management=True)
        return {"name": add_connection(adapter, users=[actor], label=label)}

    service.configure = configure
    connections.access, connections.model, connections.service = access, model, service
    # 第 2 批：电脑退役时连接域删它的可达行（`pool.forget_device`）；心跳里的电脑（`member.Member`）与所选账套的键
    pool = types.ModuleType("pearnly_cowork.connections.pool")
    pool.FORGOTTEN, pool.EVENTS = [], []

    def forget_device(device, *, connection=None):
        pool.FORGOTTEN.append(device)
        pool.EVENTS.append(("forget", device, connection))

    def move_device(connection, old, new):
        pool.EVENTS.append(("move", connection, old, new))

    pool.forget_device, pool.move_device = forget_device, move_device
    spec = importlib.util.spec_from_file_location("pearnly_cowork.connections.member", APP / "connections/member.py")
    member = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(member)
    reports = types.ModuleType("pearnly_cowork.connections.reports")
    reports.SELECTED_KEYS = frozenset(("account_set", "account_dir", "account_company", "account_set_row",
                                       "express_root"))
    reports.MAPPING_KEYS = frozenset(("revenue_acc", "ar_acc", "vat_output_acc", "fallback_acc", "ap_acc",
                                      "vat_input_acc"))
    connections.pool, connections.member, connections.reports = pool, member, reports
    sys.modules.update({"pearnly_cowork.connections": connections, "pearnly_cowork.connections.access": access,
                        "pearnly_cowork.connections.model": model, "pearnly_cowork.connections.service": service,
                        "pearnly_cowork.connections.pool": pool, "pearnly_cowork.connections.member": member,
                        "pearnly_cowork.connections.reports": reports})
    return access


def add_connection(adapter, *, users, label=None, report_json=None):
    name = "CWEC-%04d" % next(_ORDER)
    STORE.setdefault(CONNECTION, {})[name] = {
        "doctype": CONNECTION, "name": name, "adapter": adapter, "workspace": "WS", "client": "", "enabled": 1,
        "binding_generation": 0, "allowed_users": json.dumps(users), "label": label or adapter,
        "report_json": report_json, "creation": next(_ORDER)}
    return name


frappe = _install_frappe()
package = types.ModuleType("pearnly_cowork")
package.__path__ = [str(APP)]
sys.modules["pearnly_cowork"] = package
access = _install_connections(frappe)
contracts = importlib.import_module("pearnly_cowork.companion.contracts")
device_contracts = importlib.import_module("pearnly_cowork.companion.device_contracts")
documents = importlib.import_module("pearnly_cowork.companion.documents")
device_documents = importlib.import_module("pearnly_cowork.companion.device_documents")
devices = importlib.import_module("pearnly_cowork.companion.devices")
credentials = importlib.import_module("pearnly_cowork.companion.credentials")
CLASSES.update({CREDENTIAL: documents.CredentialDocument, DEVICE: device_documents.DeviceDocument})
KEY_A, KEY_B, KEY_C = "A" * 43, "B" * 43, "C" * 43


def hello_body(key, name="ACC-01", **extra):
    return {"device_key": key, "name": name, "always_on": False, "host_names": [name], **extra}


class Base(unittest.TestCase):
    def setUp(self):
        STORE.clear()
        LOCKS.clear()
        frappe.db.points.clear()
        frappe.db.committed = {}
        sys.modules["pearnly_cowork.connections.pool"].FORGOTTEN.clear()
        sys.modules["pearnly_cowork.connections.pool"].EVENTS.clear()
        frappe.session.user = ADMIN
        self.requests = itertools.count()
        self.express = add_connection("express", users=[ADMIN, ENTRY, OTHER])

    def retrying(self, call):
        """Like `api._local_transaction` / `device_api._guarded`: a deadlock rolls the whole unit back and runs it
        again (up to three times)."""
        frappe.db.commit()
        for attempt in range(3):
            LOCKS.append(("BEGIN", None))
            try:
                return call()
            except QueryDeadlockError:
                frappe.db.rollback()
                if attempt == 2:
                    raise

    def register(self, user=ADMIN, request=None, **kwargs):
        frappe.session.user = user
        kwargs.setdefault("express", True)
        request = request or "rk-%d" % next(self.requests)
        return self.retrying(lambda: credentials.register(request, **kwargs))

    def row(self, token):
        return next(r for r in _rows(CREDENTIAL) if r["token_digest"] == contracts.token_digest(token))

    def auth(self, token):
        LOCKS.append(("BEGIN", None))
        return credentials.authenticate(token)

    def hello(self, token, body):
        values = device_contracts.hello_request(body)  # `device_api._hello`: the body is checked before any write
        context = self.auth(token)
        return self.retrying(lambda: credentials.hello(context, values))

    def before_deploy(self):
        """Keys as production has them before batch 1: no computer records, no `device` links."""
        STORE.pop(DEVICE, None)
        for row in _rows(CREDENTIAL):
            row["device"] = None

    def device_of(self, token):
        return STORE[DEVICE][self.row(token)["device"]]

    def assert_lock_order(self):
        """Connection -> Credential -> Device: once a computer row is locked, no key row not already held is locked."""
        held, device_seen = set(), False
        for doctype, name in LOCKS:
            if doctype == "BEGIN":
                held, device_seen = set(), False
            elif doctype == DEVICE:
                device_seen = True
            elif doctype == CREDENTIAL:
                self.assertFalse(device_seen and name not in held, LOCKS)
                held.add(name)

    def active_keys(self):
        return sorted(r["name"] for r in _rows(CREDENTIAL) if _active_row(r))


# ── 纯契约 ─────────────────────────────────────────────────────────────────


class PureContracts(unittest.TestCase):
    def test_old_slot_and_device_slot_formulas(self):
        old = contracts.active_identity(SITE, "C1")
        self.assertEqual(old, contracts.fingerprint([SITE, "C1"]))
        self.assertEqual(contracts.active_identity(SITE, "C1", None), old)
        self.assertEqual(contracts.active_identity(SITE, "C1", "CCD-1"), contracts.fingerprint([SITE, "C1", "CCD-1"]))
        self.assertNotEqual(contracts.active_identity(SITE, "C1", "CCD-1"), contracts.active_identity(SITE, "C1", "CCD-2"))
        # 迁移来的密钥挂上电脑后仍按原公式核；按电脑发的只认自己那台
        self.assertTrue(contracts.active_matches(old, SITE, "C1", "CCD-1"))
        self.assertTrue(contracts.active_matches(old, SITE, "C1"))
        slot = contracts.active_identity(SITE, "C1", "CCD-1")
        self.assertTrue(contracts.active_matches(slot, SITE, "C1", "CCD-1"))
        for args in (("CCD-2",), (None,)):
            self.assertFalse(contracts.active_matches(slot, SITE, "C1", *args))
        self.assertFalse(contracts.active_matches(slot, SITE, "C2", "CCD-1"))
        for stored in (None, "", slot.upper(), 1):
            self.assertFalse(contracts.active_matches(stored, SITE, "C1", "CCD-1"))

    def test_device_key_digest_and_issue_intent(self):
        self.assertEqual(contracts.device_key(KEY_A), KEY_A)
        for bad in (None, "", "short", "A" * 129, "A" * 42 + "=", 7, "Ａ" * 43):
            with self.assertRaises(contracts.CredentialError):
                contracts.device_key(bad)
        digest = contracts.device_digest(SITE, KEY_A)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertNotEqual(digest, contracts.device_digest("other.example", KEY_A))
        self.assertNotIn(KEY_A, digest)
        self.assertEqual(contracts.device_issue_intent("CCD-1", True), {"operation": "issue", "device": "CCD-1", "rekey": True})
        with self.assertRaises(contracts.CredentialError):
            contracts.device_issue_intent("CCD-1", "true")

    def test_hello_request_keeps_host_names_as_given(self):
        hosts = ["DESKTOP-MN2BRKR", "192.168.0.78", "172.27.224.1", "100.67.246.0"]  # 第 0 批：含 WSL、Tailscale
        value = device_contracts.hello_request({"device_key": KEY_A, "name": "PC", "always_on": True,
                                                "host_names": hosts, "future": 1})
        self.assertEqual(value, {"device_key": KEY_A, "name": "PC", "always_on": True, "host_names": hosts})
        self.assertEqual(device_contracts.hello_request({"device_key": KEY_A}),
                         {"device_key": KEY_A, "name": "", "always_on": False, "host_names": []})
        for bad in ([], {}, {"device_key": KEY_A, "always_on": "1"}, {"device_key": KEY_A, "host_names": "PC"},
                    {"device_key": KEY_A, "host_names": [""]}, {"device_key": KEY_A, "host_names": ["x"] * 33},
                    {"device_key": KEY_A, "name": "x" * 141}):
            with self.assertRaises(contracts.CredentialError):
                device_contracts.hello_request(bad)

    def test_pure_modules_import_no_framework(self):
        for name, allowed in (("contracts.py", {"dataclasses", "hashlib", "hmac", "json", "re"}),
                              ("device_contracts.py", {"contracts"})):
            tree = ast.parse((DOMAIN / name).read_text())
            imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
            self.assertEqual(imported, allowed, name)


class Schema(unittest.TestCase):
    def doctype(self, folder):
        return json.loads((APP / "pearnly_cowork/doctype" / folder / (folder + ".json")).read_text())

    def test_device_record_fields(self):
        doc = self.doctype("cowork_cc_desktop_device")
        fields = {f["fieldname"]: f for f in doc["fields"]}
        self.assertEqual(doc["name"], "Cowork CC Desktop Device")
        self.assertEqual(set(fields), {"workspace", "device_owner", "signed_in_user", "label", "secret_digest",
                                       "always_on", "companion_version", "max_payload_version", "selection_json",
                                       "host_names", "index_pending", "retired_at", "retired_by", "index_pending_uids"})
        self.assertEqual(fields["secret_digest"]["unique"], 1)
        self.assertEqual(fields["secret_digest"].get("reqd", 0), 0)  # 补丁建的旧电脑留空，等认领
        self.assertEqual(doc["field_order"], [f["fieldname"] for f in doc["fields"]])
        self.assertFalse(doc.get("track_changes"))

    def test_credential_gets_its_computer_and_unique_keys_stay(self):
        fields = {f["fieldname"]: f for f in self.doctype("cowork_express_agent_credential")["fields"]}
        self.assertEqual((fields["device"]["fieldtype"], fields["device"]["options"]), ("Link", DEVICE))
        self.assertEqual(fields["full_report_due"]["fieldtype"], "Check")
        for field in ("active_key", "generation_key", "issued_request_digest", "token_digest"):
            self.assertEqual(fields[field]["unique"], 1)

    def test_device_records_are_reachable_only_through_the_domain(self):
        hooks = (APP / "hooks.py").read_text()
        self.assertIn('"Cowork CC Desktop Device": "pearnly_cowork.companion.device_documents.native_query"', hooks)
        self.assertIn('"Cowork CC Desktop Device": "pearnly_cowork.companion.device_documents.native_permission"', hooks)
        self.assertEqual(hooks.count('"pearnly_cowork.companion.device_documents.reject_share"'), 2)
        with self.assertRaises(PermissionError_):
            _get_doc({"doctype": DEVICE, "workspace": "WS"}).insert()

    def test_patch_is_registered(self):
        lines = (APP / "patches.txt").read_text().splitlines()
        # 第 2 批的补丁（账套登记，`test_cowork_express_account_sets.py`）跟在它后面：要先有电脑才有旧电脑的可达行；
        # 第 4 批（读文件夹的电脑、全所可用）再在后面
        self.assertEqual(lines[-4:], ["pearnly_cowork.patches.v0_0_19_companion_devices",
                                      "pearnly_cowork.patches.v0_0_20_express_account_sets",
                                      "pearnly_cowork.patches.v0_0_21_reader_devices",
                                      "pearnly_cowork.patches.v0_0_22_express_all_members"])


# ── register v2 ─────────────────────────────────────────────────────────────


class OldClientRegister(Base):
    """不带设备密钥（≤1.5.0）且池里还没有认领过的电脑：一条连接一个槽位，逐字照旧。"""

    def test_old_slot_created_then_in_use_then_replace(self):
        first = self.register()
        self.assertEqual((first["status"], first["connection"], first["adapter"]), ("created", self.express, "express"))
        self.assertNotIn("device", first)
        row = self.row(first["token"])
        self.assertEqual(row["active_key"], contracts.active_identity(SITE, self.express))
        legacy = self.device_of(first["token"])  # an unclaimed old computer, as the patch makes them
        self.assertEqual((legacy["device_owner"], legacy["secret_digest"], legacy["label"]), (ADMIN, None, "express"))
        held = self.register()
        self.assertEqual(held, {"status": "in_use", "connection": self.express, "adapter": "express", "generation": 1,
                                "online": False, "issuer": ADMIN, "self": True})
        moved = self.register(replace="true", expected_generation=1)
        self.assertEqual(moved["status"], "created")
        with self.assertRaises(AuthenticationError):
            self.auth(first["token"])
        # Review finding 3: the displaced key's old computer is retired, the new key has its own old computer
        self.assertTrue(STORE[DEVICE][row["device"]]["retired_at"])
        fresh = self.device_of(moved["token"])
        self.assertNotEqual(fresh["name"], row["device"])
        self.assertIsNone(fresh.get("retired_at"))
        self.assertFalse(devices.pool_claimed(self.express))
        self.assert_lock_order()

    def test_a_claim_committed_before_the_connection_lock_is_seen(self):
        """Review finding 1: a new CC Desktop claims the old slot key while an old client is on its way to `replace`."""
        first = self.register()
        real = credentials._use

        def racing(name):
            credentials._use = real
            self.hello(first["token"], hello_body(KEY_A))  # commits before the old client locks the connection
            frappe.session.user = ADMIN
            return real(name)

        credentials._use = racing
        try:
            result = self.register(replace=True, expected_generation=1)
        finally:
            credentials._use = real
        self.assertEqual(result, {"status": "update_needed", "connection": self.express, "adapter": "express"})
        self.assertEqual(self.auth(first["token"]).issuer, ADMIN)
        self.assertEqual(len(self.active_keys()), 1)

    def test_update_needed_only_after_the_use_grant(self):
        self.register(device_key=KEY_A)
        STORE[CONNECTION][self.express]["allowed_users"] = json.dumps([ADMIN])
        with self.assertRaises(PermissionError_):
            self.register(ENTRY)

    def test_entry_role_may_replace_only_its_own_old_slot(self):
        admin = self.register()
        with self.assertRaises(PermissionError_):
            self.register(ENTRY, replace=True, expected_generation=1)
        self.assertEqual(self.auth(admin["token"]).issuer, ADMIN)
        STORE.clear()
        self.express = add_connection("express", users=[ADMIN, ENTRY])
        own = self.register(ENTRY)
        self.assertEqual(self.register(ENTRY, replace=True, expected_generation=1)["status"], "created")
        with self.assertRaises(AuthenticationError):
            self.auth(own["token"])


class DeviceRegister(Base):
    def test_joining_does_not_displace(self):
        a = self.register(device_key=KEY_A, device_name="ACC-01")
        b = self.register(ENTRY, device_key=KEY_B, device_name="ACC-02")
        self.assertEqual((a["status"], b["status"]), ("created", "created"))
        self.assertNotEqual(a["device"], b["device"])
        self.assertEqual(self.auth(a["token"]).issuer, ADMIN)
        self.assertEqual(self.auth(b["token"]).issuer, ENTRY)
        rows = {r["device"]: r for r in _rows(CREDENTIAL)}
        self.assertEqual(rows[a["device"]]["active_key"], contracts.active_identity(SITE, self.express, a["device"]))
        self.assertEqual(sorted(r["token_generation"] for r in rows.values()), [1, 2])  # 代数仍按连接递增
        self.assertEqual(STORE[DEVICE][b["device"]]["device_owner"], ENTRY)

    def test_exists_records_the_signed_in_user_only(self):
        a = self.register(device_key=KEY_A)
        before = self.active_keys()
        again = self.register(ENTRY, device_key=KEY_A)
        self.assertEqual(again, {"status": "exists", "connection": self.express, "adapter": "express",
                                 "device": a["device"], "generation": 1})
        device = STORE[DEVICE][a["device"]]
        self.assertEqual((device["device_owner"], device["signed_in_user"]), (ADMIN, ENTRY))
        self.assertEqual(self.active_keys(), before)
        self.assertEqual(self.auth(a["token"]).issuer, ADMIN)

    def test_rekey_rotates_this_computer_in_one_step(self):
        a = self.register(ENTRY, device_key=KEY_A)
        frappe.session.user = ENTRY
        again = credentials.register("rekey-1", express=True, device_key=KEY_A, rekey="1")
        self.assertEqual((again["status"], again["device"]), ("created", a["device"]))
        with self.assertRaises(AuthenticationError):
            self.auth(a["token"])
        self.assertEqual(self.auth(again["token"]).token_generation, 2)
        self.assertEqual(len(self.active_keys()), 1)
        replay = credentials.register("rekey-1", express=True, device_key=KEY_A, rekey="1")
        self.assertEqual(replay["status"], "exists")
        self.assertNotIn("token", replay)
        self.assertEqual(self.auth(again["token"]).token_generation, 2)

    def test_rekey_needs_the_owner_or_management(self):
        a = self.register(ENTRY, device_key=KEY_A)
        with self.assertRaises(PermissionError_):
            self.register(OTHER, device_key=KEY_A, rekey=True)
        self.assertEqual(self.auth(a["token"]).issuer, ENTRY)
        self.assertEqual(self.register(ADMIN, device_key=KEY_A, rekey=True)["status"], "created")

    def test_computer_without_express_never_revokes_the_writer(self):
        writer = self.register()  # 旧槽位的写账电脑
        reader = self.register(ENTRY, express=False, device_key=KEY_B)
        self.assertEqual((reader["status"], reader["connection"]), ("created", self.express))
        self.assertEqual(self.auth(writer["token"]).issuer, ADMIN)
        mrerp = add_connection("mrerp", users=[ADMIN, ENTRY])
        third = self.register(ENTRY, express=False, device_key=KEY_C)
        self.assertEqual((third["status"], third["connection"], third["adapter"]), ("created", mrerp, "mrerp"))
        self.assertEqual(self.auth(writer["token"]).issuer, ADMIN)
        self.assertEqual(self.auth(reader["token"]).issuer, ENTRY)

    def test_old_client_gets_update_needed_once_a_computer_is_claimed(self):
        a = self.register(device_key=KEY_A)
        before = copy.deepcopy(STORE[CREDENTIAL])
        for kwargs in ({}, {"replace": True, "expected_generation": 1}):
            self.assertEqual(self.register(**kwargs), {"status": "update_needed", "connection": self.express,
                                                       "adapter": "express"})
        self.assertEqual(STORE[CREDENTIAL], before)
        self.assertEqual(self.auth(a["token"]).issuer, ADMIN)

    def test_two_members_adding_the_same_computer_at_once_end_on_one(self):
        """Nit: register v2 colliding on the unique digest with a member that just added the same computer."""
        mrerp = add_connection("mrerp", users=[ADMIN, ENTRY])
        folder = self.register(ENTRY, express=False, device_key=KEY_A)
        self.assertEqual(folder["connection"], mrerp)
        real, calls = devices.find, []

        def late(digest, *, lock=False):
            calls.append(digest)
            return None if len(calls) == 1 else real(digest, lock=lock)

        devices.find = late
        try:
            writer = self.register(device_key=KEY_A)
        finally:
            devices.find = real
        self.assertEqual((writer["status"], writer["device"]), ("created", folder["device"]))
        self.assertEqual(len(_rows(DEVICE)), 1)
        self.assertEqual(len(calls), 2)  # the whole unit ran again
        self.assert_lock_order()

    def test_register_takes_key_rows_before_the_computer_row(self):
        """Review finding 4: Connection -> Credential -> Device in every register v2 branch."""
        a = self.register(ENTRY, device_key=KEY_A)
        self.register(ENTRY, device_key=KEY_A)  # exists
        self.register(ENTRY, device_key=KEY_A, rekey=True)
        devices.retire(devices.read(a["device"]), ADMIN)
        self.register(ENTRY, device_key=KEY_A)  # a removed computer joins again
        self.assert_lock_order()

    def test_no_pool_only_management_creates_it(self):
        STORE.clear()
        self.assertEqual(self.register(ENTRY, device_key=KEY_A), {"status": "no_connection"})
        self.assertEqual(self.register(ENTRY), {"status": "no_connection"})
        self.assertNotIn(CONNECTION, STORE)
        made = self.register(ADMIN, device_key=KEY_A)
        self.assertEqual(made["status"], "created")
        self.assertEqual(STORE[CONNECTION][made["connection"]]["adapter"], "express")

    def test_entry_issuer_key_lives_and_dies_with_the_entry_grants(self):
        mine = self.register(ENTRY, device_key=KEY_A)
        self.assertEqual(self.auth(mine["token"]).issuer, ENTRY)
        STORE[CONNECTION][self.express]["allowed_users"] = json.dumps([ADMIN])
        with self.assertRaises(AuthenticationError):
            self.auth(mine["token"])
        STORE[CONNECTION][self.express]["allowed_users"] = json.dumps([ADMIN, ENTRY])
        access.roles[ENTRY] = set()
        try:
            with self.assertRaises(AuthenticationError):
                self.auth(mine["token"])
        finally:
            access.roles[ENTRY] = {"Cowork Intake"}
        with self.assertRaises(PermissionError_):
            self.register(OUTSIDER, device_key=KEY_B)

    def test_a_removed_computer_joins_again_as_a_new_one(self):
        a = self.register(device_key=KEY_A)
        old = devices.read(a["device"])
        devices.retire(old, ADMIN)
        # 第 2 批：它够得到的账套不再算进池目录（连接域删它的可达行）
        self.assertIn(a["device"], sys.modules["pearnly_cowork.connections.pool"].FORGOTTEN)
        with self.assertRaises(AuthenticationError):
            self.auth(a["token"])
        again = self.register(device_key=KEY_A)
        self.assertEqual(again["status"], "created")
        self.assertNotEqual(again["device"], a["device"])
        self.assertIsNone(STORE[DEVICE][a["device"]]["secret_digest"])


# ── 按摘要认证 ───────────────────────────────────────────────────────────────


class DigestAuthentication(Base):
    def test_old_slot_and_device_slot_keys_both_authenticate(self):
        old = self.register()
        new = self.register(ENTRY, express=False, device_key=KEY_B)
        self.assertEqual(self.auth(old["token"]).credential_id, self.row(old["token"])["name"])
        self.assertEqual(self.auth(new["token"]).credential_id, self.row(new["token"])["name"])

    def test_token_must_name_its_own_connection(self):
        a = self.register(device_key=KEY_A)
        other = add_connection("express", users=[ADMIN])
        forged = a["token"].replace(self.express, other)
        with self.assertRaises(AuthenticationError):
            self.auth(forged)
        for token in ("", "exp_" + self.express + "_missing", a["token"] + "x"):
            with self.assertRaises(AuthenticationError):
                self.auth(token)

    def test_retired_computer_and_revoked_key_are_refused(self):
        a = self.register(device_key=KEY_A)
        b = self.register(device_key=KEY_B)
        context = self.auth(b["token"])
        devices.retire(devices.read(b["device"]), ADMIN)
        for action in (lambda: self.auth(b["token"]), lambda: credentials.revalidate(context)):
            with self.assertRaises(AuthenticationError):
                action()
        self.assertEqual(self.auth(a["token"]).issuer, ADMIN)
        credentials.leave(self.auth(a["token"]))
        with self.assertRaises(AuthenticationError):
            self.auth(a["token"])

    def test_management_role_is_no_longer_required_of_an_admin_issued_key(self):
        a = self.register(device_key=KEY_A)
        access.roles[ADMIN] = {"Cowork Intake"}
        try:
            self.assertEqual(self.auth(a["token"]).issuer, ADMIN)
        finally:
            access.roles[ADMIN] = {"Cowork Admin"}

    def test_each_computer_is_online_by_its_own_key_and_confirmation_needs_no_key(self):
        # 第 3 批：在线不再看连接当前的上报凭据，池里每台电脑看自己的密钥；确认不再要哪台电脑的密钥（ADR-021）
        a = self.register(device_key=KEY_A)
        b = self.register(device_key=KEY_B)
        row = self.row(b["token"])
        credentials.record_seen(row["name"], row["token_generation"])
        peers = credentials.pool_peers(self.express)
        self.assertEqual((peers[b["device"]]["online"], peers[a["device"]]["online"]), (True, False))
        self.assertEqual(peers[b["device"]]["issuers"], {ADMIN})
        self.assertEqual((peers[b["device"]]["payload_version"], peers[b["device"]]["index_pending"]), (None, None))
        self.assertFalse(hasattr(credentials, "for_confirmation"))
        self.assertFalse(hasattr(credentials, "online"))
        credentials.leave(self.auth(b["token"]))
        self.assertNotIn(b["device"], credentials.pool_peers(self.express))
        self.assertEqual(self.auth(a["token"]).issuer, ADMIN)
        doc = STORE[CONNECTION][self.express]
        self.assertTrue(credentials.issuer_valid(Document(doc), ADMIN))
        self.assertFalse(credentials.issuer_valid(Document(doc), OUTSIDER))

    def test_index_pending_is_kept_on_the_computer_only_when_it_changes(self):
        a = self.register(device_key=KEY_A)
        self.assertFalse(devices.note_index_pending(a["device"], None))  # 旧版不报：不动
        self.assertIsNone(credentials.pool_peers(self.express)[a["device"]]["index_pending"])
        self.assertTrue(devices.note_index_pending(a["device"], ["c8d5876c:1", "c8d5876c:2"]))
        self.assertFalse(devices.note_index_pending(a["device"], ["c8d5876c:2", "c8d5876c:1"]))
        self.assertEqual(credentials.pool_peers(self.express)[a["device"]]["index_pending"],
                         frozenset({"c8d5876c:1", "c8d5876c:2"}))
        self.assertEqual(STORE[DEVICE][a["device"]]["index_pending"], 1)
        self.assertTrue(devices.note_index_pending(a["device"], []))
        self.assertEqual((STORE[DEVICE][a["device"]]["index_pending"],
                          credentials.pool_peers(self.express)[a["device"]]["index_pending"]), (0, frozenset()))

    def test_a_uid_mismatch_asks_the_express_key_for_one_full_report(self):
        a = self.register(device_key=KEY_A)
        row = self.row(a["token"])
        self.assertTrue(credentials.request_full_report(row["name"]))
        self.assertFalse(credentials.request_full_report(row["name"]))  # 已经在等整份
        self.assertTrue(credentials.full_report_due(row["name"], full_report=False))


# ── device_hello / device_leave ──────────────────────────────────────────────


class MigratedPool(Base):
    """生产那样的老池：旧槽位的 Express 密钥 + MR.ERP 密钥，补丁给每把建了一台旧电脑。"""

    def setUp(self):
        super().setUp()
        self.mrerp = add_connection("mrerp", users=[ADMIN])
        STORE[CONNECTION][self.express]["report_json"] = json.dumps({"agent_device_name": "ACC-01"})
        self.express_key = self.register()["token"]
        self.mrerp_key = self.register(express=False)["token"]
        self.before_deploy()
        self.assertEqual(devices.seed_legacy(lambda connection: ""), 2)
        self.keys_before = {r["name"]: r["active_key"] for r in _rows(CREDENTIAL)}

    def assert_active_keys_unchanged(self):
        self.assertEqual({r["name"]: r["active_key"] for r in _rows(CREDENTIAL)}, self.keys_before)

    def test_first_hello_claims_the_old_computer(self):
        row = self.row(self.express_key)
        result = self.hello(self.express_key, hello_body(KEY_A, always_on=True))
        self.assertEqual(result, {"status": "attached", "device": row["device"]})
        device = STORE[DEVICE][row["device"]]
        self.assertEqual(device["secret_digest"], contracts.device_digest(SITE, KEY_A))
        self.assertEqual((device["label"], device["always_on"], json.loads(device["host_names"])),
                         ("ACC-01", 1, ["ACC-01"]))
        self.assertTrue(self.row(self.express_key)["full_report_due"])
        self.assert_active_keys_unchanged()
        again = self.hello(self.express_key, hello_body(KEY_A))
        self.assertEqual(again["device"], row["device"])
        self.assert_active_keys_unchanged()

    def test_the_mrerp_member_of_the_same_computer_merges_into_it(self):
        first = self.hello(self.express_key, hello_body(KEY_A))
        old_mrerp_device = self.row(self.mrerp_key)["device"]
        merged = self.hello(self.mrerp_key, hello_body(KEY_A))
        self.assertEqual(merged, {"status": "attached", "device": first["device"]})
        self.assertEqual(self.row(self.mrerp_key)["device"], first["device"])
        self.assertTrue(STORE[DEVICE][old_mrerp_device]["retired_at"])
        self.assert_active_keys_unchanged()
        self.assertEqual(self.auth(self.mrerp_key).issuer, ADMIN)

    def test_a_copied_pairing_code_is_claimed_elsewhere(self):
        self.hello(self.express_key, hello_body(KEY_A))
        before = copy.deepcopy(STORE)
        self.assertEqual(self.hello(self.express_key, hello_body(KEY_B, name="ACC-02")),
                         {"status": "claimed_elsewhere"})
        self.assertEqual(STORE, before)
        self.assert_active_keys_unchanged()

    def test_old_clients_are_told_to_update_once_claimed(self):
        self.assertEqual(self.register()["status"], "in_use")
        self.hello(self.express_key, hello_body(KEY_A))
        self.assertEqual(self.register()["status"], "update_needed")

    def test_leave_revokes_only_this_key(self):
        self.hello(self.express_key, hello_body(KEY_A))
        self.hello(self.mrerp_key, hello_body(KEY_A))
        other = self.register(ENTRY, device_key=KEY_B)
        credentials.leave(self.auth(self.mrerp_key))
        with self.assertRaises(AuthenticationError):
            self.auth(self.mrerp_key)
        self.assertEqual(self.auth(self.express_key).issuer, ADMIN)
        self.assertEqual(self.auth(other["token"]).issuer, ENTRY)
        self.assertIsNone(STORE[DEVICE][self.row(self.express_key)["device"]].get("retired_at"))
        self.assert_lock_order()

    def test_leaving_from_an_unclaimed_old_computer_retires_it(self):
        """Review finding 3: no ghost computer stays behind an old client that disconnects."""
        legacy = self.row(self.mrerp_key)["device"]
        credentials.leave(self.auth(self.mrerp_key))
        self.assertTrue(STORE[DEVICE][legacy]["retired_at"])
        self.assertIsNone(STORE[DEVICE][self.row(self.express_key)["device"]].get("retired_at"))

    def test_register_first_then_hello_supersedes_the_old_slot_key(self):
        """Review finding 2: the new CC Desktop registered before saying hello with its old slot key."""
        legacy = self.row(self.express_key)["device"]
        mine = self.register(device_key=KEY_A, device_name="ACC-01")
        self.assertEqual(mine["status"], "created")
        result = self.hello(self.express_key, hello_body(KEY_A))
        self.assertEqual(result, {"status": "superseded", "device": mine["device"]})
        with self.assertRaises(AuthenticationError):
            self.auth(self.express_key)
        self.assertEqual(self.row(self.express_key)["revocation_reason"], "superseded")
        self.assertTrue(STORE[DEVICE][legacy]["retired_at"])
        self.assertEqual(self.auth(mine["token"]).issuer, ADMIN)
        self.assertEqual(self.auth(self.mrerp_key).issuer, ADMIN)
        live = [d for d in _rows(DEVICE) if not d.get("retired_at")]
        self.assertEqual(len(live), 2)  # this computer and the MR.ERP member's old computer, no ghost
        self.assert_lock_order()

    def test_a_moved_key_hands_its_reach_to_the_computer_before_the_old_one_retires(self):
        """第 2 批审查 1：MR.ERP 成员先认领，Express 的旧槽位密钥改挂过去：旧电脑在这条连接上的可达行与本机登记先转给
        那台（同一事务、电脑行之前），旧电脑才退役。"""
        pool = sys.modules["pearnly_cowork.connections.pool"]
        claimed = self.hello(self.mrerp_key, hello_body(KEY_A))["device"]
        old = self.row(self.express_key)["device"]
        pool.EVENTS.clear()
        self.assertEqual(self.hello(self.express_key, hello_body(KEY_A))["device"], claimed)
        self.assertEqual(pool.EVENTS[0], ("move", self.express, old, claimed))
        # 读文件夹的任务、在途文件、公司文件夹随之转过去（第 4 批，文件夹域的钩子）
        self.assertEqual(pool.EVENTS[1], ("readers", self.express, old, claimed))
        self.assertIn(("forget", old, None), pool.EVENTS[1:])
        self.assertTrue(STORE[DEVICE][old]["retired_at"])

    def test_a_keyless_old_slot_key_hands_its_items_to_the_claimed_computer(self):
        """第 4 批审查 L6：补丁漏掉、没挂电脑的旧槽位密钥（池里按密钥编号算一台）改挂到同一台电脑已认领的记录上时，
        按密钥编号接手的任务、在途文件、可达行一并转过去。"""
        pool = sys.modules["pearnly_cowork.connections.pool"]
        claimed = self.hello(self.mrerp_key, hello_body(KEY_A))["device"]
        row = self.row(self.express_key)
        row["device"] = None
        pool.EVENTS.clear()
        self.assertEqual(self.hello(self.express_key, hello_body(KEY_A))["device"], claimed)
        self.assertEqual(pool.EVENTS[:2], [("move", self.express, row["name"], claimed),
                                           ("readers", self.express, row["name"], claimed)])
        self.assert_lock_order()

    def test_a_superseded_key_hands_its_reach_to_the_computers_own_key(self):
        pool = sys.modules["pearnly_cowork.connections.pool"]
        old = self.row(self.express_key)["device"]
        mine = self.register(device_key=KEY_A, device_name="ACC-01")
        pool.EVENTS.clear()
        self.hello(self.express_key, hello_body(KEY_A))
        self.assertEqual(pool.EVENTS[0], ("move", self.express, old, mine["device"]))
        self.assertEqual(pool.EVENTS[1], ("readers", self.express, old, mine["device"]))
        self.assertIn(("forget", old, self.express), pool.EVENTS)

    def test_a_computer_left_without_a_key_on_a_connection_drops_its_reach_there(self):
        """第 2 批审查 9：密钥吊销了、电脑没退役：它在这条连接上的可达行删掉（目录与读请求不再算它）。"""
        pool = sys.modules["pearnly_cowork.connections.pool"]
        self.hello(self.express_key, hello_body(KEY_A))
        self.hello(self.mrerp_key, hello_body(KEY_A))  # 这台在 MR.ERP 上还有密钥
        device = self.row(self.express_key)["device"]
        pool.EVENTS.clear()
        credentials.leave(self.auth(self.express_key))
        self.assertEqual(pool.EVENTS, [("forget", device, self.express)])
        self.assertIsNone(STORE[DEVICE][device].get("retired_at"))
        self.assert_lock_order()

    def test_hello_takes_key_rows_before_computer_rows(self):
        """Review finding 4: claiming, merging and superseding never lock a key row after a computer row."""
        self.hello(self.express_key, hello_body(KEY_A))
        self.hello(self.mrerp_key, hello_body(KEY_A))
        self.hello(self.express_key, hello_body(KEY_A))
        self.assert_lock_order()

    def test_only_express_keys_are_asked_for_a_full_report(self):
        self.hello(self.express_key, hello_body(KEY_A))
        self.hello(self.mrerp_key, hello_body(KEY_A))
        self.assertTrue(self.row(self.express_key)["full_report_due"])
        self.assertFalse(self.row(self.mrerp_key).get("full_report_due"))

    def test_heartbeat_forces_one_full_report_after_attaching(self):
        name = self.row(self.express_key)["name"]
        self.assertFalse(credentials.full_report_due(name, full_report=False))
        self.hello(self.express_key, hello_body(KEY_A))
        self.assertTrue(credentials.full_report_due(name, full_report=False))
        self.assertTrue(credentials.full_report_due(name, full_report=False))
        self.assertFalse(credentials.full_report_due(name, full_report=True))
        self.assertFalse(credentials.full_report_due(name, full_report=False))


class DeviceSlotHello(Base):
    def test_per_computer_key_never_moves_and_never_changes_its_slot(self):
        a = self.register(device_key=KEY_A)
        b = self.register(device_key=KEY_B)
        slot = self.row(a["token"])["active_key"]
        self.assertEqual(self.hello(a["token"], hello_body(KEY_A))["device"], a["device"])
        self.assertEqual(self.hello(a["token"], hello_body(KEY_B)), {"status": "claimed_elsewhere"})
        self.assertEqual((self.row(a["token"])["device"], self.row(a["token"])["active_key"]), (a["device"], slot))
        self.assertEqual(self.auth(b["token"]).issuer, ADMIN)

    def test_old_slot_key_without_a_computer_gets_one(self):
        pool = sys.modules["pearnly_cowork.connections.pool"]
        old = self.register()
        self.before_deploy()  # 补丁漏掉的、没有电脑的旧槽位密钥
        key = self.row(old["token"])["name"]
        pool.EVENTS.clear()
        result = self.hello(old["token"], hello_body(KEY_A, name="ACC-09"))
        device = STORE[DEVICE][result["device"]]
        self.assertEqual((device["device_owner"], device["label"]), (ADMIN, "ACC-09"))
        self.assertEqual(self.row(old["token"])["active_key"], contracts.active_identity(SITE, self.express))
        # 第 4 批审查 L6：没挂电脑时池里按密钥编号算一台，按这个编号接手的任务、文件、可达行转给新挂上的电脑
        self.assertEqual(pool.EVENTS[:2], [("move", self.express, key, result["device"]),
                                           ("readers", self.express, key, result["device"])])

    def test_a_computer_holds_one_key_per_connection(self):
        old = self.register()
        self.before_deploy()  # 旧槽位密钥，还没挂电脑
        mine = self.register(device_key=KEY_A)  # 这台电脑已在这条连接上有自己的密钥
        self.assertEqual(self.hello(old["token"], hello_body(KEY_A)), {"status": "superseded", "device": mine["device"]})
        with self.assertRaises(AuthenticationError):
            self.auth(old["token"])
        self.assertIsNone(self.row(old["token"])["device"])
        self.assertEqual(self.active_keys(), [self.row(mine["token"])["name"]])

    def late_find(self):
        """第一次查时对方还没提交：看不到，写的时候撞唯一摘要；整段重来时看得到。"""
        real, calls = devices.find, []

        def late(digest, *, lock=False):
            calls.append(digest)
            return None if len(calls) == 1 else real(digest, lock=lock)

        devices.find = late
        self.addCleanup(setattr, devices, "find", real)
        return calls

    def test_two_members_saying_hello_at_once_end_on_one_computer(self):
        old = self.register()
        self.before_deploy()  # 没挂电脑的旧槽位 Express 密钥
        add_connection("mrerp", users=[ADMIN, ENTRY])
        mine = self.register(ENTRY, express=False, device_key=KEY_A)  # 同一台电脑的 MR.ERP 成员先建好了
        calls = self.late_find()
        result = self.hello(old["token"], hello_body(KEY_A))
        self.assertEqual(result, {"status": "attached", "device": mine["device"]})
        self.assertEqual(len(_rows(DEVICE)), 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.auth(old["token"]).issuer, ADMIN)
        self.assert_lock_order()

    def test_a_late_seen_own_key_still_supersedes(self):
        old = self.register()
        self.before_deploy()
        mine = self.register(device_key=KEY_A)  # 同一台电脑在这条连接上已有自己的密钥
        calls = self.late_find()
        self.assertEqual(self.hello(old["token"], hello_body(KEY_A)), {"status": "superseded", "device": mine["device"]})
        self.assertEqual(len(_rows(DEVICE)), 1)
        self.assertEqual(len(calls), 2)

    def test_bad_hello_body_is_refused_before_any_write(self):
        a = self.register(device_key=KEY_A)
        before = copy.deepcopy(STORE)
        with self.assertRaises(contracts.CredentialError):
            self.hello(a["token"], {"device_key": "short"})
        self.assertEqual(STORE, before)


# ── 补丁第 1 步 ───────────────────────────────────────────────────────────────



# ── 第 2 批：心跳里的这台电脑 ────────────────────────────────────────────────────────


class HeartbeatComputer(Base):
    """心跳不再读写连接配置（ADR-021 第 2 批）：所选账套、科目映射与版本记在电脑行上；连接域看到的是 `Member`。"""

    def claimed(self, key, name="ACC-01", hosts=None):
        made = self.register(device_key=key)
        doc = devices.read(made["device"])
        doc.host_names = json.dumps(hosts if hosts is not None else [name, "192.168.1.5"])
        doc.label = name
        device_documents.persist(doc)
        return made

    def test_member_carries_identity_host_and_aliases(self):
        old = self.register()  # 旧版（不带设备密钥）：补丁同款的「旧电脑」，没认领；池里一有认领的就登记不了
        a = self.claimed(KEY_A, "ACC-01", ["ACC-01", "192.168.1.5", "172.27.224.1"])
        b = self.claimed(KEY_B, "ACC-02", ["ACC-02", "172.27.224.1"])  # 两台都报的 WSL 地址
        self.claimed(KEY_C, "ACC-01", ["ACC-01"])  # 同名的另一台：本机账套键里加设备短号
        member = devices.member(a["device"], "WS", connection=self.express)
        self.assertFalse(member.legacy)
        self.assertEqual(member.host, "acc-01-" + a["device"][-6:].lower())
        self.assertEqual(member.aliases.get("192.168.1.5"), "acc-01")
        self.assertNotIn("172.27.224.1", member.aliases)  # 两台都报的虚拟网卡地址：不猜
        legacy = devices.member(self.row(old["token"])["device"], "WS", connection=self.express)
        self.assertTrue(legacy.legacy)
        self.assertTrue(legacy.host.startswith("pc-"))
        self.assertEqual(devices.member(b["device"], "WS", connection=self.express).version, "")

    def test_the_computer_keeps_its_own_selection_and_versions(self):
        old = self.row(self.register()["token"])["device"]
        made = self.claimed(KEY_A)
        plan = types.SimpleNamespace(offline=False, patch={"account_set": r"S:\X\BOOK", "revenue_acc": "4100",
                                                           "companion_version": "1.6.0", "max_payload_version": 2})
        self.assertTrue(devices.record_report(made["device"], self.express, plan))
        self.assertEqual(devices.state(made["device"], self.express), {"account_set": r"S:\X\BOOK", "revenue_acc": "4100",
                                                         "companion_version": "1.6.0", "max_payload_version": 2})
        LOCKS.clear()
        self.assertFalse(devices.record_report(made["device"], self.express, plan))  # 没变：不碰电脑行
        self.assertEqual(LOCKS, [])
        member = devices.member(made["device"], "WS", connection=self.express)
        self.assertEqual((member.selected, member.version, member.payload_version), (r"S:\X\BOOK", "1.6.0", 2))
        # 新版的电脑名由 device_hello 报，心跳报的不改它；旧电脑（没认领）照旧用心跳报的名字
        devices.record_report(made["device"], self.express, types.SimpleNamespace(offline=False, patch={"agent_device_name": "X"}))
        self.assertEqual(STORE[DEVICE][made["device"]]["label"], "ACC-01")
        devices.record_report(old, self.express, types.SimpleNamespace(offline=False, patch={"agent_device_name": "OLD-PC"}))
        self.assertEqual(STORE[DEVICE][old]["label"], "OLD-PC")

    def test_selection_is_kept_per_connection(self):
        """第 2 批审查 7：同一台电脑服务两条 Express 连接（erpcowork）：两条的所选、映射各存各的，不互改。"""
        second = add_connection("express", users=[ADMIN])
        made = self.claimed(KEY_A)
        one = types.SimpleNamespace(offline=False, patch={"account_set": r"S:\ONE", "revenue_acc": "4100"})
        two = types.SimpleNamespace(offline=False, patch={"account_set": r"S:\TWO", "revenue_acc": "4999"})
        self.assertTrue(devices.record_report(made["device"], self.express, one))
        self.assertTrue(devices.record_report(made["device"], second, two))
        LOCKS.clear()
        self.assertFalse(devices.record_report(made["device"], self.express, one))
        self.assertFalse(devices.record_report(made["device"], second, two))
        self.assertEqual(LOCKS, [])
        self.assertEqual(devices.state(made["device"], self.express), {"account_set": r"S:\ONE", "revenue_acc": "4100"})
        self.assertEqual(devices.member(made["device"], "WS", connection=second).selected, r"S:\TWO")
        # 补丁：这条连接上还没有的才补，另一条已有的不挡它
        self.assertTrue(devices.seed_state(made["device"], "CWEC-OTHER", {"account_set": r"S:\OLD"}, {}))
        self.assertFalse(devices.seed_state(made["device"], second, {"account_set": r"S:\OLD"}, {}))
        self.assertEqual(devices.state(made["device"], "CWEC-OTHER"), {"account_set": r"S:\OLD"})
        self.assertEqual(devices.state(made["device"], second)["account_set"], r"S:\TWO")

    def test_the_patch_hands_the_old_default_to_the_old_computer_once(self):
        old = self.row(self.register()["token"])["device"]
        config = {"account_set": r"S:\OLD\BOOK", "express_root": r"S:\OLD", "revenue_acc": "4100",
                  "directions": "purchase"}
        self.assertTrue(devices.seed_state(old, self.express, config, {"companion_version": "1.5.1", "max_payload_version": 2}))
        self.assertEqual(devices.state(old, self.express), {"account_set": r"S:\OLD\BOOK", "express_root": r"S:\OLD",
                                              "revenue_acc": "4100", "companion_version": "1.5.1",
                                              "max_payload_version": 2})
        self.assertFalse(devices.seed_state(old, self.express, {"account_set": r"S:\OTHER"}, {"companion_version": "9.9.9"}))
        self.assertEqual(devices.state(old, self.express)["account_set"], r"S:\OLD\BOOK")

    def test_newest_version_peers_and_reporter_label(self):
        a, b = self.claimed(KEY_A, "ACC-01"), self.claimed(KEY_B, "ACC-02")
        for made, version in ((a, "1.5.10"), (b, "1.6.0")):
            doc = devices.read(made["device"])
            doc.companion_version = version
            device_documents.persist(doc)
        self.assertEqual(devices.newest_version(self.express), "1.6.0")
        row_a = self.row(a["token"])
        credentials.record_seen(row_a["name"], row_a["token_generation"])
        devices.note_signed_in(devices.read(b["device"]), ENTRY)
        peers = credentials.pool_peers(self.express)
        self.assertEqual(peers[a["device"]]["online"], True)
        self.assertEqual((peers[b["device"]]["online"], peers[b["device"]]["user"]), (False, ENTRY))
        STORE[CONNECTION][self.express]["report_credential"] = row_a["name"]
        self.assertEqual(devices.reporter_label(self.express), "ACC-01")
        self.assertEqual(credentials.device_of(row_a["name"]), a["device"])

    def test_heartbeat_reads_and_writes_the_computer_not_the_connection_config(self):
        made = self.claimed(KEY_A)
        STORE[CONNECTION][self.express]["config_json"] = json.dumps({"account_set": r"S:\STALE", "revenue_acc": "1"})
        STORE[CONNECTION][self.express]["report_json"] = json.dumps({"companion_version": "OTHER-PC", "update_blocked": None})
        devices.record_report(made["device"], self.express, types.SimpleNamespace(offline=False, patch={"account_set": r"S:\MINE"}))
        seen = {}

        def plan_report(body, previous, now):
            seen["previous"] = previous
            return types.SimpleNamespace(offline=False, patch={"companion_version": "1.6.0", "account_set": r"S:\NEW"})

        def ingest(doc, plan, **kwargs):
            seen.update(kwargs)
            return {"ok": True}

        stubs = {"pearnly_cowork.companion.http_auth": types.SimpleNamespace(),
                 "pearnly_cowork.companion.release": types.SimpleNamespace(latest=lambda: None),
                 "pearnly_cowork.connections.collector": types.SimpleNamespace(ingest=ingest),
                 "pearnly_cowork.connections.reports": types.SimpleNamespace(
                     **vars(sys.modules["pearnly_cowork.connections.reports"]), plan_report=plan_report,
                     ReportError=ValueError),
                 "pearnly_cowork.push": types.ModuleType("pearnly_cowork.push"),
                 "pearnly_cowork.push.native": types.ModuleType("pearnly_cowork.push.native"),
                 "pearnly_cowork.push.native.service": types.SimpleNamespace(open_account_keys=lambda c: {"k"})}
        saved = {name: sys.modules.get(name) for name in stubs}
        sys.modules.update(stubs)
        sys.modules["pearnly_cowork.connections"].collector = stubs["pearnly_cowork.connections.collector"]
        try:
            sys.modules.pop("pearnly_cowork.companion.heartbeat", None)
            heartbeat = importlib.import_module("pearnly_cowork.companion.heartbeat")
            context = self.auth(made["token"])
            reply = heartbeat._receive(context, {})
        finally:
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module
        self.assertEqual(seen["previous"], {"account_set": r"S:\MINE", "update_blocked": None})
        member = seen["member"]
        self.assertEqual((member.device, member.legacy, member.host, member.version),
                         (made["device"], False, "acc-01", "1.6.0"))
        self.assertEqual(seen["protected"](), {"k"})
        self.assertEqual(devices.state(made["device"], self.express), {"account_set": r"S:\NEW", "companion_version": "1.6.0"})
        self.assertEqual(json.loads(STORE[CONNECTION][self.express]["config_json"])["account_set"], r"S:\STALE")
        self.assertIs(reply["account_sets_unchanged_ok"], True)

class PatchSeedsOldComputers(Base):
    def load_patch(self):
        spec = importlib.util.spec_from_file_location("v0_0_19_under_test",
                                                      APP / "patches/v0_0_19_companion_devices.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_each_active_key_gets_one_old_computer_and_reruns_do_nothing(self):
        STORE[CONNECTION][self.express]["report_json"] = json.dumps({"agent_device_name": "ACC-01"})
        mrerp = add_connection("mrerp", users=[ADMIN], label="MR.ERP office")
        first = self.register()["token"]
        current = self.register(replace=True, expected_generation=1)["token"]  # first 被顶掉、已吊销
        folder = self.register(express=False)["token"]
        self.before_deploy()
        before = {r["name"]: dict(r) for r in _rows(CREDENTIAL)}
        patch = self.load_patch()
        names = {mrerp: "FOLDER-PC"}
        patch._folder_name = lambda connection: names.get(connection, "")
        patch.execute()
        rows = {r["name"]: r for r in _rows(CREDENTIAL)}
        self.assertIsNone(rows[self.row(first)["name"]]["device"])  # 已吊销的不建电脑
        seeded = {r["connection"]: STORE[DEVICE][r["device"]] for r in rows.values() if r.get("device")}
        self.assertEqual({connection: (d["label"], d["device_owner"], d["secret_digest"])
                          for connection, d in seeded.items()},
                         {self.express: ("ACC-01", ADMIN, None), mrerp: ("FOLDER-PC", ADMIN, None)})
        for name, row in rows.items():
            self.assertEqual(row["active_key"], before[name]["active_key"])
        snapshot = copy.deepcopy(STORE)
        patch.execute()
        self.assertEqual(STORE, snapshot)
        self.assertEqual(self.auth(current).issuer, ADMIN)
        self.assertEqual(self.auth(folder).issuer, ADMIN)

    def test_name_falls_back_to_the_connection_label(self):
        self.register()
        self.before_deploy()
        self.assertEqual(devices.seed_legacy(None), 1)
        self.assertEqual([d["label"] for d in _rows(DEVICE)], ["express"])


if __name__ == "__main__":
    unittest.main()
