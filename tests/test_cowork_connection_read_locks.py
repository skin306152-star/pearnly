"""连接的只读视图不加行锁（2026-10-15 testcowork 两次死锁 1213）。

侧栏同时发 `pending_counts` 与 `task_overview`，两边都经 `companies.service.usable_connections` 调
`connections.service.records`：原先先 `LOCK IN SHARE MODE` 拿共享锁、再逐行 `FOR UPDATE` 升级排他锁，两个事务各持
同一批连接行的共享锁又都要升级 → 死锁；CC 桌面版心跳写连接行也被只读请求挡住。

- 纯只读、只回 DTO 的视图（`records` / `detail` / `targets` / `account_set_facts` / `refresh_status` /
  `stock_acc_group` / `mrerp_menu`）不请求任何行锁，SQL 里也不带 `LOCK IN SHARE MODE` / `FOR UPDATE`；
- 写路径照旧加锁（`read_for_push` 锁连接、快照、刷新行；`choose_stock_acc_group` 锁连接）；`state.read` 默认仍加锁。
- 授权检查（`connections/access.py` 读用户、角色、归属）也分开：只读视图传 `lock=False` 读本事务快照，写路径照旧当前读
  加共享锁。同日 13:48 侧栏又报 1020「Record has changed since last read in table 'tabUser'」——MariaDB 11.8 快照隔离下，
  事务开始后 `tabUser` 行被别的请求改过（`last_active`），再对它加锁读就被拒。

不连库、不依赖 Frappe：连接域的真 `service.py` 跑在内存里的小 Frappe 替身上，替身记下每次读是否要锁。
"""
import contextlib
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "pearnly_cowork"))

CONNECTION, SNAPSHOT, REFRESH = "Cowork ERP Connection", "Cowork ERP Snapshot", "Cowork ERP Refresh"
USER = "owner@example.com"
ACCOUNT = r"C:\ACCOUNT\70EXP\DEMO01"


class ValidationError(Exception):
    pass


class PermissionError_(Exception):
    pass


class Doc(dict):
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
        return False

    def save(self, **_ignored):
        DB.docs[self["doctype"]][self["name"]] = self
        return self


class Db:
    """记下每次读：`locks` 是要了锁的 (doctype, name)，`queries` 是原样的 SQL。"""

    def __init__(self):
        self.docs, self.locks, self.queries, self.access_locks = {}, [], [], []

    def get_value(self, doctype, name, fields="name", as_dict=False, for_update=False):
        if for_update:
            self.locks.append((doctype, name))
        row = self.docs.get(doctype, {}).get(name)
        if row is None:
            return None
        if fields == "name":
            return name
        if isinstance(fields, str):
            return row.get(fields)
        return Doc({field: row.get(field) for field in fields})

    def sql(self, query, values=(), as_dict=False):
        self.queries.append(" ".join(query.split()))
        if "FROM `tabCowork ERP Connection`" in query:
            workspace, client = values[0], values[1]
            return [Doc(name=name) for name, row in self.docs.get(CONNECTION, {}).items()
                    if row.workspace == workspace and (row.client or "") == client]
        if "FROM `tabHas Role`" in query:
            return [Doc(role="Cowork Intake")]
        if any(table in query for table in ("FROM `tabUser`", "FROM `tabCowork Workspace`", "FROM `tabCowork Client`")):
            return [Doc(enabled=1)]
        raise AssertionError(query)


DB = Db()


def _throw(message, exc=None):
    raise (exc or ValidationError)(message)


def _get_doc(doctype, name=None, for_update=False):
    if isinstance(doctype, dict):
        return Doc(doctype)
    if for_update:
        DB.locks.append((doctype, name))
    return DB.docs[doctype][name]


frappe = types.ModuleType("frappe")
frappe.__dict__.update(db=DB, throw=_throw, _=lambda text: text, get_doc=_get_doc, ValidationError=ValidationError,
                       PermissionError=PermissionError_, conf={}, local=types.SimpleNamespace(site="testcowork.pearnly.com"),
                       session=types.SimpleNamespace(user=USER))
frappe_utils = types.ModuleType("frappe.utils")
frappe_password = types.ModuleType("frappe.utils.password")
frappe_password.get_decrypted_password = lambda *args, **kwargs: ""
frappe_utils.password = frappe_password
frappe.utils = frappe_utils
pymysql = types.ModuleType("pymysql")
pymysql_err = types.ModuleType("pymysql.err")
pymysql_err.OperationalError = type("OperationalError", (Exception,), {})
pymysql_err.ProgrammingError = type("ProgrammingError", (Exception,), {})
pearnly_core = types.ModuleType("pearnly_core")
shell = types.ModuleType("pearnly_core.shell")
shell.entitlements = lambda: ["intake"]
pearnly_core.shell = shell
# `service.py` 看到的授权是替身：记下每次调用传的 `lock`（只读视图要传 False）。真 `access.py` 的 SQL 另在
# `AccessReadLocks` 里按原文件加载来测。


def _checked(check):
    def call(*args, lock=True, **kwargs):
        DB.access_locks.append(lock)
        return check(*args, **kwargs)
    return call


access = types.ModuleType("pearnly_cowork.connections.access")
access.TYPES = (CONNECTION, SNAPSHOT, REFRESH)
access.POOL_TYPES = ("Cowork Express Account Set", "Cowork Express Reach", "Cowork Express Account Facts")
access.write = lambda doc: doc.save()
access.require_actor = _checked(lambda actor=None, **kwargs: actor or USER)
access.require_scope = _checked(lambda workspace, client=None, **kwargs: None)
access.require_connection = _checked(lambda doc, actor=None, **kwargs: actor or USER)
access.roles = _checked(lambda actor: {"Cowork Intake"})
access.default_workspace = lambda: "W"
access.deny = lambda: _throw("Not Permitted", PermissionError_)
masters = types.ModuleType("pearnly_cowork.masters")
masters.workspaces = types.SimpleNamespace(site_workspace=lambda: "W")
masters.push_inputs = types.SimpleNamespace(mappings=lambda client, adapter=None: {"accounts": [], "clients": []},
                                            profiles=lambda workspace, tax: {})
reconciliation = types.ModuleType("pearnly_cowork.reconciliation")
reconciliation.bank_evidence = types.SimpleNamespace(window=lambda owner, date: [])
sys.modules.update({"frappe": frappe, "frappe.utils": frappe_utils, "frappe.utils.password": frappe_password,
                    "pymysql": pymysql, "pymysql.err": pymysql_err,
                    "pearnly_core": pearnly_core, "pearnly_core.shell": shell,
                    "pearnly_cowork.connections.access": access, "pearnly_cowork.masters": masters,
                    "pearnly_cowork.masters.push_inputs": masters.push_inputs,
                    "pearnly_cowork.masters.workspaces": masters.workspaces,
                    "pearnly_cowork.reconciliation": reconciliation,
                    "pearnly_cowork.reconciliation.bank_evidence": reconciliation.bank_evidence})

from pearnly_cowork.connections import model, service, state  # noqa: E402

LOCKING_SQL = ("LOCK IN SHARE MODE", "FOR UPDATE")


def connection(name, adapter="express", **values):
    return Doc({"doctype": CONNECTION, "name": name, "label": name, "adapter": adapter, "workspace": "W", "client": "",
                "enabled": 1, "record_revision": 1, "binding_generation": 0, "bound_account_set": "",
                "bound_profile_key": "", "config_json": model.dumps({}), "allowed_users": model.dumps([USER]),
                "projection_heads": "{}", "report_json": "{}", "stock_acc_groups": "", "mrerp_menus": "",
                "mrerp_menu_options": "", **values})


class ConnectionReadLocks(unittest.TestCase):
    def setUp(self):
        DB.docs.clear()
        DB.locks.clear()
        DB.queries.clear()
        DB.access_locks.clear()
        _, _, key = state.scope(None)
        heads = {key: {"status": "fresh", "revision": 1, "snapshot_id": "SNAP", "request_id": "REF"}}
        DB.docs[CONNECTION] = {"CONN": connection("CONN", projection_heads=model.dumps(heads)),
                               "MRERP": connection("MRERP", adapter="mrerp")}
        DB.docs[SNAPSHOT] = {"SNAP": Doc(doctype=SNAPSHOT, name="SNAP", connection="CONN", connection_revision=1,
                                         adapter="express", body=model.dumps({"account_sets": [
                                             {"source_id": ACCOUNT, "label": "Demo 01", "active": True,
                                              "attributes": {"path": ACCOUNT, "root": r"C:\ACCOUNT\70EXP",
                                                             "writable": True, "company": "Demo Co",
                                                             "tax_id": "0105599210021"}}]}))}
        DB.docs[REFRESH] = {"REF": Doc(doctype=REFRESH, name="REF", connection="CONN", connection_revision=1,
                                       status="succeeded", account_set_key="", adapter="express",
                                       result_revision=1, binding_generation=0)}

    def assertNoLocks(self):
        self.assertEqual(DB.locks, [])
        for query in DB.queries:
            self.assertFalse(any(word in query.upper() for word in LOCKING_SQL), query)
        self.assertTrue(DB.access_locks)  # 授权确实查过
        self.assertNotIn(True, DB.access_locks)  # 而且没要当前读加锁

    def test_the_sidebar_list_reads_take_no_row_locks(self):
        """`companies.usable_connections` / `facts` 走的两条：列表与账套目录。"""
        rows = service.records(None, None, 0, 100)
        self.assertEqual(sorted(row["name"] for row in rows), ["CONN", "MRERP"])
        facts = service.account_set_facts("CONN")
        self.assertEqual(facts["account_sets"][0]["attributes"]["tax_id"], "0105599210021")
        self.assertEqual(len(DB.queries), 1)  # 列表那一句，确实跑过
        self.assertNoLocks()

    def test_every_read_only_connection_view_takes_no_row_locks(self):
        self.assertEqual(service.detail("CONN")["name"], "CONN")
        found = service.targets("CONN")
        self.assertEqual((found["refresh_status"], found["account_sets"][0]["source_id"]), ("succeeded", ACCOUNT))
        self.assertEqual(service.refresh_status("CONN", "REF")["status"], "succeeded")
        self.assertIn("groups", service.stock_acc_group("CONN", ACCOUNT))
        self.assertIn("defaults", service.mrerp_menu("MRERP", "1:2"))
        self.assertNoLocks()

    def test_a_stale_snapshot_is_still_rejected_without_locks(self):
        """不加锁仍按连接版本核对快照：同一事务里的一致快照，比对照样成立。"""
        DB.docs[SNAPSHOT]["SNAP"].connection_revision = 0
        for view in (service.targets, service.account_set_facts):
            with self.subTest(view=view.__name__), self.assertRaises(ValidationError):
                view("CONN")
        self.assertNoLocks()

    def test_write_paths_still_lock_what_they_read(self):
        loaded = service.read_for_push("CONN", "W")
        self.assertEqual(loaded.revision, 1)
        self.assertEqual({doctype for doctype, _name in DB.locks}, {CONNECTION, SNAPSHOT, REFRESH})
        self.assertEqual(set(DB.access_locks), {True})  # 写路径的授权仍是当前读加锁
        DB.locks.clear()
        with contextlib.suppress(ValidationError):  # 没有上报的存货科目组：选不进，但先锁了连接行
            service.choose_stock_acc_group("CONN", ACCOUNT, "ST01")
        self.assertIn((CONNECTION, "CONN"), DB.locks)
        DB.locks.clear()
        state.read(CONNECTION, "CONN")  # 默认仍加锁
        self.assertIn((CONNECTION, "CONN"), DB.locks)


def _real_access():
    """按原文件加载真 `connections/access.py`（上面的替身只给 `service.py` 用）。"""
    import importlib.util

    path = ROOT / "apps" / "pearnly_cowork" / "pearnly_cowork" / "connections" / "access.py"
    spec = importlib.util.spec_from_file_location("pearnly_cowork.connections._access_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AccessReadLocks(unittest.TestCase):
    """真 `access.py`：默认（写路径）当前读加共享锁；`lock=False`（只读视图）不带锁。"""

    @classmethod
    def setUpClass(cls):
        cls.access = _real_access()

    def setUp(self):
        DB.queries.clear()
        self._conf = frappe.conf
        frappe.conf = {"pearnly_product": "cowork"}

    def tearDown(self):
        frappe.conf = self._conf

    def locking(self):
        return [query for query in DB.queries if "LOCK IN SHARE MODE" in query.upper()]

    def test_writers_keep_current_reads(self):
        doc = connection("CONN")
        self.assertEqual(self.access.require_connection(doc), USER)
        self.assertEqual(len(self.locking()), len(DB.queries))  # 用户、角色、工作空间：每句都带锁
        self.assertEqual(len(DB.queries), 3)

    def test_read_only_views_read_their_snapshot(self):
        doc = connection("CONN", client="C1")
        self.assertEqual(self.access.require_connection(doc, lock=False), USER)
        self.assertEqual(self.access.require_actor(lock=False), USER)
        self.access.require_scope("W", "C1", lock=False)
        self.assertEqual(self.access.roles(USER, lock=False), {"Cowork Intake"})
        self.assertEqual(len(DB.queries), 9)  # 用户、角色、工作空间、客户 + 用户、角色 + 工作空间、客户 + 角色
        self.assertEqual(self.locking(), [])

    def test_a_disabled_user_is_still_refused_without_locks(self):
        original = DB.sql

        def disabled(query, values=(), as_dict=False):
            if "FROM `tabUser`" in query:
                DB.queries.append(" ".join(query.split()))
                return [Doc(enabled=0)]
            return original(query, values, as_dict)

        DB.sql = disabled
        try:
            with self.assertRaises(PermissionError_):
                self.access.require_connection(connection("CONN"), lock=False)
        finally:
            DB.sql = original


if __name__ == "__main__":
    unittest.main()
