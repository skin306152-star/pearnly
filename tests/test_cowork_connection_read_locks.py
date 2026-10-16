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
import importlib.util
import json
import pathlib
import sys
import types
import unittest
from unittest import mock

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
        if "FROM `tabCowork ERP Connection`" in query and "adapter IN" in query:
            # 每种 ERP 的主连接（`service.main_connections`）：不挂客户、代数 0；启用的在前，再按建立先后
            workspace, adapters = values
            rows = [row for row in self.docs.get(CONNECTION, {}).values()
                    if row.workspace == workspace and not row.client and not row.binding_generation
                    and row.adapter in adapters and (row.enabled or "enabled=1" not in query)]
            rows.sort(key=lambda row: (-int(row.enabled or 0), row.get("creation") or 0, row.name))
            return [Doc(name=row.name, adapter=row.adapter) for row in rows]
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
# 使用名单（D2，ADR-021 第 4 批）：同真 `access.listed`，Express 池「全所可用」时不看名单
access.listed = lambda doc, actor: bool(doc.get("adapter") == "express" and int(doc.get("all_members") or 0)) or (
    actor in json.loads(doc.get("allowed_users") or "[]"))
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
        self.assertEqual(len(DB.queries), 2)  # 列表与每种 ERP 的主连接（ADR-021）两句，确实跑过、都不加锁
        self.assertNoLocks()

    def test_each_erp_has_one_main_connection_for_every_entry(self):
        """ADR-021（方案 2.10）：每种 ERP 的主连接 = 最早建、启用、不挂客户；列表按修改时间排不再决定用哪条。
        一条启用的都没有时列表标最早建的那条停用的（入口照旧显示「已停用」），电脑登记只认启用的。"""
        DB.docs[CONNECTION] = {
            "EXP-NEW": connection("EXP-NEW", creation=3),
            "EXP-OLD": connection("EXP-OLD", creation=1),
            "EXP-OFF": connection("EXP-OFF", creation=0, enabled=0),
            "EXP-CLIENT": connection("EXP-CLIENT", creation=-1, client="CL"),
            "MR-OFF": connection("MR-OFF", adapter="mrerp", creation=2, enabled=0)}
        self.assertEqual(service.main_connections("W"), {"express": "EXP-OLD"})
        self.assertEqual(service.main_connections("W", enabled=False), {"express": "EXP-OLD", "mrerp": "MR-OFF"})
        rows = service.records(None, None, 0, 100)  # 录入角色：只列启用、在名单里的
        self.assertEqual(service.main_of(rows, "express")["name"], "EXP-OLD")
        self.assertIsNone(service.main_of(rows, "mrerp"))
        self.assertEqual(sorted(row["name"] for row in rows if row["main"]), ["EXP-OLD"])
        self.assertNotIn("EXP-CLIENT", {row["name"] for row in rows})  # 挂客户的连接不在这个范围，更不会是主连接
        self.assertIsNone(service.main_of([row for row in rows if row["name"] != "EXP-OLD"], "express"))
        self.assertNoLocks()

    def test_an_all_members_pool_is_listed_for_everyone(self):
        """D2（ADR-021 第 4 批）：Express 池勾了「全所可用」，名单里没有的录入员也看得到、用得上；MR.ERP 只看名单。
        新建的 Express 池默认全所可用（连接详情带 ``all_members``）。"""
        DB.docs[CONNECTION] = {
            "EXP": connection("EXP", allowed_users=model.dumps(["boss@x.com"]), all_members=1),
            "MR": connection("MR", adapter="mrerp", allowed_users=model.dumps(["boss@x.com"]), all_members=1),
            "EXP-LIST": connection("EXP-LIST", allowed_users=model.dumps(["boss@x.com"]))}
        rows = service.records(None, None, 0, 100)
        self.assertEqual([row["name"] for row in rows], ["EXP"])
        self.assertTrue(rows[0]["all_members"])

    def test_configure_keeps_or_changes_all_members(self):
        """第 4 批审查 M3：`configure` 收 ``all_members``：不给就不变（现有界面只改名单时不会悄悄关掉全所可用），给了照它存，
        关掉以后名单才起作用；MR.ERP 不能全所可用。新建的 Express 池默认 1。"""
        DB.docs[CONNECTION] = {"CONN": connection("CONN", all_members=1, allowed_users=model.dumps([USER])),
                               "MR": connection("MR", adapter="mrerp")}
        with mock.patch.object(access, "require_assignee", lambda *args, **kwargs: None, create=True):
            kept = service.configure("W", "express", "k1", "CONN", {}, users=[USER, "boss@x.com"], name="CONN",
                                     expected_revision=1)
            self.assertTrue(kept["all_members"])
            self.assertEqual(DB.docs[CONNECTION]["CONN"]["all_members"], 1)
            closed = service.configure("W", "express", "k2", "CONN", {}, users=["boss@x.com"], name="CONN",
                                       expected_revision=2, all_members=False)
            self.assertEqual((closed["all_members"], closed["users"]), (False, ["boss@x.com"]))
            self.assertEqual(DB.docs[CONNECTION]["CONN"]["all_members"], 0)
            self.assertEqual([row["name"] for row in service.records(None, None, 0, 100)], ["MR"])  # 名单里没有自己
            with self.assertRaises(ValidationError):
                service.configure("W", "mrerp", "k3", "MR", {}, name="MR", expected_revision=1, all_members=True)
            with self.assertRaises(model.ConnectionError):  # 接口层（`api._call`）照 `enabled` 转成同一句错
                service.configure("W", "express", "k4", "CONN", {}, name="CONN", expected_revision=3,
                                  all_members="maybe")
            # 同一请求号重放：给了 all_members 的请求指纹带它，与原请求一致就原样回
            again = service.configure("W", "express", "k2", "CONN", {}, users=["boss@x.com"], name="CONN",
                                      expected_revision=2, all_members=False)
            self.assertEqual(again["revision"], 3)
        api = (ROOT / "apps/pearnly_cowork/pearnly_cowork/connections/api.py").read_text(encoding="utf-8")
        self.assertIn("credentials, all_members)", api)  # 网页接口照传

    def test_set_members_changes_only_who_may_use_the_pool(self):
        """第 6 批：设置里「全所可用」开关与名单（`service.set_members`）只改这两个字段，不升版本、不动配置（改谁能用不该让
        目录、刷新、主档的证据失效）；勾掉全所可用时名单不能空；只核新加进名单的人；MR.ERP 没有这个开关；只限管理员。"""
        config = model.dumps({"system_url": "x"})
        DB.docs[CONNECTION] = {"CONN": connection("CONN", all_members=1, allowed_users=model.dumps([USER]),
                                                  record_revision=4, config_json=config),
                               "MR": connection("MR", adapter="mrerp")}
        checked = []
        with mock.patch.object(access, "require_assignee", lambda user, *a, **k: checked.append(user), create=True):
            closed = service.set_members("CONN", all_members=False, users=[USER, "boss@x.com"])
            self.assertEqual((closed["all_members"], closed["users"]), (False, sorted([USER, "boss@x.com"])))
            row = DB.docs[CONNECTION]["CONN"]
            self.assertEqual((row["all_members"], row["record_revision"], row["config_json"]), (0, 4, config))
            self.assertEqual(checked, ["boss@x.com"])
            # 只给开关：名单不变
            opened = service.set_members("CONN", all_members=True)
            self.assertEqual((opened["all_members"], opened["users"]), (True, sorted([USER, "boss@x.com"])))
            self.assertEqual(DB.docs[CONNECTION]["CONN"]["record_revision"], 4)
            # 与现值相同不写
            with mock.patch.object(access, "write", side_effect=AssertionError("written")):
                self.assertTrue(service.set_members("CONN", all_members=True, users=["boss@x.com", USER])["all_members"])
            for bad in ({"all_members": False, "users": []}, {"all_members": False, "users": [USER, USER]},
                        {"users": "nope"}):
                with self.assertRaises(ValidationError):
                    service.set_members("CONN", **bad)
            with self.assertRaises(model.ConnectionError):
                service.set_members("CONN", all_members="maybe")
            with self.assertRaises(ValidationError):  # MR.ERP 没有全所可用
                service.set_members("MR", all_members=False, users=[USER])

            def no_admin(actor=None, *, management=False, **kwargs):
                if management:
                    raise PermissionError_("Not Permitted")
                return actor or USER

            with mock.patch.object(access, "require_actor", no_admin):
                with self.assertRaises(PermissionError_):
                    service.set_members("CONN", all_members=False, users=[USER])
            self.assertEqual(DB.docs[CONNECTION]["CONN"]["all_members"], 1)
        # 列表给每行 ``manageable``（设置里只给管理员显示开关）
        self.assertTrue(all("manageable" in row for row in service.records(None, None, 0, 100)))
        api = (ROOT / "apps/pearnly_cowork/pearnly_cowork/connections/api.py").read_text(encoding="utf-8")
        self.assertIn("_call(service.set_members, name, all_members, users)", api)
        self.assertIn('@frappe.whitelist(methods=["POST"])\ndef set_members', api)

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


class AllMembersPatch(unittest.TestCase):
    def test_existing_express_main_connections_open_to_all_members_without_a_revision_bump(self):
        """D2（ADR-021 第 4 批，方案 4.1 第 5 步）：每个归属下的 Express 主连接 ``all_members = 1``；直接写字段、不改修改时间、
        不升 ``record_revision``（放宽名单不作废排队的单）；后建的 Express、MR.ERP 不动；已经是 1 的不再写。"""
        written = []
        values = {"EXP-MAIN": 0, "EXP-DONE": 1}
        stub = types.SimpleNamespace
        fake = stub(db=stub(has_column=lambda doctype, column: True,
                            sql=lambda query, values=None: [("W1",), ("W2",)],
                            get_value=lambda doctype, name, field: values[name],
                            set_value=lambda doctype, name, field, value, update_modified=True:
                            written.append((name, field, value, update_modified))))
        mains = {"W1": {"express": "EXP-MAIN", "mrerp": "MR"}, "W2": {"express": "EXP-DONE"}}
        connections = types.ModuleType("pearnly_cowork.connections")
        connections.service = stub(main_connections=lambda workspace: mains[workspace])
        modules = {"frappe": fake, "pearnly_cowork": types.ModuleType("pearnly_cowork"),
                   "pearnly_cowork.connections": connections, "pearnly_cowork.connections.service": connections.service}
        path = ROOT / "apps/pearnly_cowork/pearnly_cowork/patches/v0_0_22_express_all_members.py"
        with mock.patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location("patch_all_members", path)
            patch = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(patch)
            patch.execute()
        self.assertEqual(written, [("EXP-MAIN", "all_members", 1, False)])
        self.assertNotIn("record_revision", path.read_text(encoding="utf-8").split('"""', 2)[2])
        lines = (ROOT / "apps/pearnly_cowork/pearnly_cowork/patches.txt").read_text().splitlines()
        self.assertLess(lines.index("pearnly_cowork.patches.v0_0_21_reader_devices"),
                        lines.index("pearnly_cowork.patches.v0_0_22_express_all_members"))
        doctype = json.loads((ROOT / "apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_erp_connection/"
                                     "cowork_erp_connection.json").read_text(encoding="utf-8"))
        field = next(row for row in doctype["fields"] if row["fieldname"] == "all_members")
        self.assertEqual((field["fieldtype"], field["read_only"], field["default"]), ("Check", 1, "0"))


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

    def test_one_use_list_predicate_with_all_members_for_express(self):
        """连接的使用名单只有一个判据（`listed`）：用连接、列连接、看推送记录都按它（方案 2.9）。"""
        pool = connection("CONN", allowed_users=model.dumps(["boss@x.com"]), all_members=1)
        self.assertTrue(self.access.listed(pool, USER))
        self.assertEqual(self.access.require_connection(pool), USER)
        listed_only = connection("CONN", allowed_users=model.dumps(["boss@x.com"]))
        self.assertFalse(self.access.listed(listed_only, USER))
        with self.assertRaises(PermissionError_):
            self.access.require_connection(listed_only)
        mrerp = connection("MR", adapter="mrerp", allowed_users=model.dumps(["boss@x.com"]), all_members=1)
        self.assertFalse(self.access.listed(mrerp, USER))  # MR.ERP 名单本次不动
        self.assertTrue(self.access.listed(connection("CONN"), USER))
        source = (ROOT / "apps/pearnly_cowork/pearnly_cowork/connections/service.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("access.listed(doc, actor)"), 2)  # records 与 read_for_log
        self.assertNotIn("in model.parse(doc.allowed_users", source)

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
