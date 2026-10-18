"""CC 桌面版多台电脑第 3 批（ADR-021，方案 2.5、2.6、2.10、第 5 节第 4–8 条）：写账分派、领取 v2、锁口径 v2、准备证据 v2、
池的状态。

- 纯函数：分派规则 `companion.route_contracts`（排头、谁的活谁的电脑做——10-16 取代三档顺序与 120 秒换手、按账套黏住、
  载荷版本、`attended`），
  位置替换 `push.express.wire_location`，锁口径 `push.native.contracts.writer_identities`（v1 / v2、公司锁），
  本单用到的主档行 `push.express.referenced`，领取报文的可选键 `companion.queue_contracts`；
- 编排：`companion.transport.lease` v2（每份账套只在这台能写的单里看排头、等锁 / 要更新就跳过看下一份、确定失效才隔离、
  每台同时一件、下发副本换成这台的写法、账本里冻结的载荷不变、电脑行最后记 ``index_pending``）与回执后的两件事；
  `companion.pool.state`（按人）；推送账本的确认 / 领取判据（`push.native.service`，按录入人 / 读文件夹的电脑）。

不连库、不依赖 Frappe：纯函数直接导入，编排模块在替身模块上加载（`unittest.mock`）。真实 Site 上的加锁、并发、HTTP 链与
真机在第 7 批联调。
"""
import ast
import importlib.util
import json
import pathlib
import sys
import types
import unittest
from datetime import datetime, timedelta
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_cowork/pearnly_cowork"
sys.path.insert(0, str(ROOT / "apps/pearnly_cowork"))
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_cowork.companion import queue_contracts, route_contracts as rc  # noqa: E402
from pearnly_cowork.companion.route_contracts import Account, Job, Peer  # noqa: E402
from pearnly_cowork.push.express import wire_location  # noqa: E402
from pearnly_cowork.push.express.referenced import used_masters  # noqa: E402
from pearnly_cowork.push.native.contracts import (  # noqa: E402
    LOCK_SCHEME, PushLedgerError, ReservationContext, digest, frozen_values, writer_identities)

NOW = datetime(2026, 10, 16, 3, 0, 0)
A, B, C = "CCD-A", "CCD-B", "CCD-C"
ME, OTHER = "me@example.com", "other@example.com"


def job(actor=ME, version=1, key="k1", name="CWP-1", route_device=""):
    return Job(name, key, actor, version, route_device)


MINE, THEIRS = frozenset({ME}), frozenset({OTHER})


# ── 分派规则（纯函数）──────────────────────────────────────────────────────────


class Routing(unittest.TestCase):
    """用户 10-16「谁的活谁的电脑做」（ADR-021 第 50–55 条）：只由录入人自己登录过 CC 的电脑写，定时任务只由读文件夹的
    那台写；同事的电脑等多久都不领，没有档位、没有 120 秒换手。"""

    def test_only_the_entry_users_own_computers_write_never_a_colleagues_however_long_it_waits(self):
        peers = {A: Peer(users=MINE, online=True), B: Peer(users=THEIRS, online=True), C: Peer(users=MINE)}
        reach = [A, B, C]
        self.assertEqual(rc.decide(A, job(), reach, peers, Account(), NOW), (True, rc.OWN, ""))
        self.assertEqual(rc.decide(B, job(), reach, peers, Account(), NOW).reason, rc.NOT_MINE)
        # 他自己的另一台电脑也写（他有几台就几台都算他的）
        self.assertEqual(rc.decide(C, job(), reach, peers, Account(), NOW), (True, rc.OWN, ""))
        # 他的电脑都关着：同事在线也不接；排多久都一样（没有排队时刻，判据里根本没有时间）
        peers = {A: Peer(users=MINE), B: Peer(users=THEIRS, online=True)}
        self.assertEqual(rc.decide(B, job(), [A, B], peers, Account(), NOW + timedelta(days=3)).reason, rc.NOT_MINE)
        self.assertFalse(hasattr(rc, "HANDOFF_SECONDS") or hasattr(rc, "tier") or hasattr(rc, "queued"))

    def test_routing_follows_who_signed_in_on_the_computer_not_who_registered_it(self):
        # D7：归属按在这台电脑上登录过 CC 的人；登记人只管权限，根本不进分派。共用的一台电脑：每个登录过的人都算
        peers = {A: Peer(users=frozenset({OTHER}), online=True), B: Peer(users=frozenset({ME, OTHER}), online=True)}
        self.assertTrue(rc.decide(B, job(), [A, B], peers, Account(), NOW).take)
        self.assertTrue(rc.decide(B, job(actor=OTHER), [A, B], peers, Account(), NOW).take)
        self.assertFalse(rc.decide(A, job(), [A, B], peers, Account(), NOW).take)

    def test_a_computer_nobody_signed_in_on_writes_everyones_work_during_the_transition(self):
        # 第 50 条过渡口径：补丁建的旧电脑、1.6 及更早、只认过电脑还没打开过对话页的，照旧谁的单都写（不弹框）
        peers = {A: Peer(online=True)}
        self.assertEqual(rc.decide(A, job(), [A], peers, Account(), NOW), (True, rc.OWN, ""))
        self.assertEqual(rc.decide(A, job(actor=OTHER), [A], peers, Account(), NOW), (True, rc.OWN, ""))
        self.assertTrue(rc.owns(A, Peer(), ME) and not rc.owns(A, Peer(users=THEIRS), ME))

    def test_a_scheduled_task_is_written_only_by_the_computer_that_reads_its_folder(self):
        peers = {A: Peer(users=MINE, online=True), B: Peer(users=THEIRS, online=True), C: Peer(online=True)}
        scheduled = job(route_device=B)
        self.assertEqual(rc.decide(B, scheduled, [A, B, C], peers, Account(), NOW), (True, rc.READER, ""))
        # 负责人自己的电脑、没有登录记录的电脑都不接
        self.assertEqual(rc.decide(A, scheduled, [A, B, C], peers, Account(), NOW).reason, rc.NOT_MINE)
        self.assertEqual(rc.decide(C, scheduled, [A, B, C], peers, Account(), NOW).reason, rc.NOT_MINE)
        # 读文件夹的那台够不到这份账套：谁都不写（确认那一步就按「没有能写的电脑」拒，`has_writer`）
        self.assertEqual(rc.decide(B, scheduled, [A, C], peers, Account(), NOW).reason, rc.UNREACHABLE)

    def test_only_a_computer_that_reaches_the_account_set_may_take_it(self):
        peers = {A: Peer(users=MINE, online=True), B: Peer(users=MINE, online=True)}
        self.assertEqual(rc.decide(B, job(), [A], peers, Account(), NOW).reason, rc.UNREACHABLE)
        # 没装 Express、只读文件夹的成员：没有可达行，永远领不到写入
        self.assertFalse(rc.decide(B, job(), [], peers, Account(), NOW).take)

    def test_payload_version_is_judged_among_the_entry_users_own_computers_only(self):
        peers = {A: Peer(users=MINE, online=True, payload_version=1), C: Peer(users=MINE, payload_version=3),
                 B: Peer(users=THEIRS, online=True, payload_version=3)}
        # 他自己的另一台（关着）支持：等它开机，不隔离
        self.assertEqual(rc.decide(A, job(version=2), [A, B, C], peers, Account(), NOW).reason, rc.VERSION)
        self.assertEqual(rc.decide(C, job(version=2), [A, B, C], peers, Account(), NOW), (True, rc.OWN, ""))
        # 他的电脑没有一台支持：等不来（同事的新版不算），界面「需更新」、领取时转人工
        peers[C] = peers[C]._replace(payload_version=1)
        self.assertEqual(rc.decide(A, job(version=2), [A, B, C], peers, Account(), NOW).reason, rc.NEEDS_UPDATE)
        self.assertTrue(rc.needs_update(2, [A, C], peers))
        # 没报过最高版本的旧版不拦（与今天预检同一口径）
        self.assertTrue(rc.supports(Peer(), 3))
        self.assertFalse(rc.needs_update(2, [], peers))

    def test_an_account_set_whose_master_index_is_pending_sticks_to_its_writer(self):
        account = Account(uid="c8d5876c:42")
        peers = {A: Peer(users=MINE, online=True, index_pending=frozenset()),
                 B: Peer(users=MINE, online=True, index_pending=frozenset({"c8d5876c:42"}))}
        self.assertEqual(rc.decide(B, job(), [A, B], peers, account, NOW), (True, rc.STICKY, ""))
        self.assertEqual(rc.decide(A, job(), [A, B], peers, account, NOW).reason, rc.STUCK)
        # 那台离线：解除，回到他自己的电脑写
        peers[B] = peers[B]._replace(online=False)
        self.assertEqual(rc.decide(A, job(), [A, B], peers, account, NOW), (True, rc.OWN, ""))
        # 黏住的那台自己要更高的载荷版本：等，不让给别人写坏顺序
        peers[B] = Peer(users=MINE, online=True, payload_version=1, index_pending=frozenset({"c8d5876c:42"}))
        self.assertEqual(rc.decide(B, job(version=2), [A, B], peers, account, NOW).reason, rc.VERSION)

    def test_a_colleagues_computer_holding_the_account_set_only_blocks_it_never_takes_the_job(self):
        account = Account(uid="u:1")
        peers = {A: Peer(users=MINE, online=True, index_pending=frozenset()),
                 B: Peer(users=THEIRS, online=True, index_pending=frozenset({"u:1"}))}
        self.assertEqual(rc.decide(A, job(), [A, B], peers, account, NOW).reason, rc.STUCK)
        self.assertEqual(rc.decide(B, job(), [A, B], peers, account, NOW).reason, rc.NOT_MINE)

    def test_old_desktops_stick_by_their_last_write_for_two_minutes(self):
        peers = {A: Peer(users=MINE, online=True), B: Peer(users=MINE, online=True)}  # 旧版：不报 index_pending
        recent = Account(uid="u", last_writer=B, last_written_at=NOW - timedelta(seconds=60))
        self.assertEqual(rc.sticky_holder(recent, [A, B], peers, NOW), B)
        self.assertFalse(rc.decide(A, job(), [A, B], peers, recent, NOW).take)
        old = recent._replace(last_written_at=NOW - timedelta(seconds=rc.STICKY_SECONDS))
        self.assertEqual(rc.sticky_holder(old, [A, B], peers, NOW), "")
        # 新版报了 index_pending（空的）：以它为准，不再按「最近写过」近似
        peers[B] = peers[B]._replace(index_pending=frozenset())
        self.assertEqual(rc.sticky_holder(recent, [A, B], peers, NOW), "")

    def test_only_each_account_sets_queue_head_is_considered(self):
        rows = [{"name": "1", "account_key": "s1"}, {"name": "2", "account_key": "s1"},
                {"name": "3", "account_key": "s2"}, {"name": "4", "account_key": "s1"}, {"name": "5", "account_key": ""}]
        self.assertEqual([row["name"] for row in rc.heads(rows)], ["1", "3"])
        self.assertEqual(len(rc.heads([{"account_key": str(i)} for i in range(80)])), rc.MAX_HEADS)

    def test_attended_only_for_the_entry_users_own_computer_and_never_for_a_scheduled_task(self):
        # 第 55 条：询问框只看服务端的 attended，不再看常驻
        self.assertTrue(rc.attended(job(), Peer(users=MINE)))
        self.assertFalse(rc.attended(job(route_device=A), Peer(users=MINE)))
        self.assertFalse(rc.attended(job(), Peer()))   # 没有登录记录的电脑：替谁写都不弹框
        self.assertFalse(hasattr(Peer(), "always_on") or hasattr(Job("n", "k", ME), "queued_at"))


# ── 下发副本的位置、锁口径、本单用到的主档 ─────────────────────────────────────────────


class Location(unittest.TestCase):
    FROZEN = {"account_set": r"\\srv\s\X\BOOK", "account_dir": r"\\srv\s\X\BOOK", "account_root": r"\\srv\s\X",
              "account_company": "ABC", "account_set_row": 3, "lines": [{"acc": "1100"}]}

    def test_the_copy_carries_this_computers_spelling_and_the_folder_id(self):
        sent = wire_location.localize(self.FROZEN, path=r"Z:\X\BOOK", root=r"Z:\X", row=7, uid="vol:9")
        self.assertEqual({key: sent[key] for key in wire_location.LOCATION_KEYS},
                         {"account_set": r"Z:\X\BOOK", "account_dir": r"Z:\X\BOOK", "account_root": r"Z:\X",
                          "account_set_row": 7})
        self.assertEqual((sent["account_uid"], sent["account_company"]), ("vol:9", "ABC"))
        self.assertEqual(self.FROZEN["account_set"], r"\\srv\s\X\BOOK")  # 账本里冻结的一字不动
        sent["lines"][0]["acc"] = "9"
        self.assertEqual(self.FROZEN["lines"][0]["acc"], "1100")

    def test_no_spelling_no_change_and_no_folder_id_no_key(self):
        self.assertEqual(wire_location.localize(self.FROZEN, path=""), self.FROZEN)
        self.assertNotIn("account_uid", wire_location.localize(self.FROZEN, path=r"S:\X\BOOK", root=r"S:\X"))
        self.assertEqual(wire_location.localize(self.FROZEN, path=r"S:\X\BOOK", row="x")["account_set_row"], 0)


class LockScheme(unittest.TestCase):
    def setUp(self):
        self.context = ReservationContext("actor", "WS", "", "history", 1, "CONN", r"\\srv\s\x\book",
                                          connection_revision=1)
        self.v1 = {"endpoint_id": "CONN", "account_set": r"\\srv\s\x\book", "root_key": r"\\srv\s\x",
                   "binding_generation": 0, "adapter": "express"}

    def test_v2_takes_no_connection_lock_and_the_same_account_lock_as_v1(self):
        old = dict(writer_identities(self.context, self.v1))
        new = dict(writer_identities(self.context, {**self.v1, "lock_scheme": LOCK_SCHEME}))
        self.assertEqual(sorted(old.values()), ["account", "endpoint"])
        self.assertEqual(list(new.values()), ["account"])  # D6：不再锁整条连接
        account = next(name for name, kind in old.items() if kind == "account")
        self.assertIn(account, new)  # 与部署前在途的旧记录取同一把（规范根 + 规范键，公式不变）
        self.assertEqual(account, digest(["account", "WS", r"\\srv\s\x", r"\\srv\s\x\book"]))

    def test_a_content_key_adds_the_company_lock_shared_by_copies(self):
        target = {**self.v1, "lock_scheme": LOCK_SCHEME, "company_key": "0105559000001|00000|2026-01-01"}
        mine = dict(writer_identities(self.context, target))
        self.assertEqual(sorted(mine.values()), ["account", "company"])
        # 同公司同期的另一份拷贝（另一份登记、另一条连接）：账套锁不同，公司锁相同
        copy = ReservationContext("actor", "WS", "", "h2", 1, "CONN-2", r"\\acc-02\c$\exp\book", connection_revision=1)
        theirs = dict(writer_identities(copy, {**target, "endpoint_id": "CONN-2", "root_key": r"\\acc-02\c$\exp"}))
        self.assertEqual({k for k, v in mine.items() if v == "company"}, {k for k, v in theirs.items() if v == "company"})
        self.assertFalse({k for k, v in mine.items() if v == "account"} & set(theirs))

    def test_the_scheme_is_frozen_with_the_record_and_only_for_express(self):
        values = frozen_values(self.context, {**self.v1, "lock_scheme": LOCK_SCHEME, "company_key": ""}, {},
                               "prepared", "", [])
        self.assertEqual(json.loads(values["target_json"])["lock_scheme"], LOCK_SCHEME)
        for bad in ({"lock_scheme": 3}, {"lock_scheme": LOCK_SCHEME, "adapter": "mrerp"}, {"company_key": 5}):
            with self.subTest(bad=bad), self.assertRaises(PushLedgerError):
                frozen_values(self.context, {**self.v1, **bad}, {}, "prepared", "", [])


class Referenced(unittest.TestCase):
    MASTERS = {
        "reported_accounts": [{"code": "1100", "name": "Cash"}, {"code": "5300", "name": "Exp"},
                              {"code": "9999", "name": "Unused"}],
        "reported_suppliers": [{"code": "S1", "name": "Kaew", "tax_id": "0105599210021"},
                               {"code": "S2", "name": "Other", "tax_id": "0105599999999"}],
        "reported_customers": [{"code": "C1", "name": "Buyer"}],
        "reported_products": [{"code": "P1", "name": "Soap"}, {"code": "P2", "name": "Rice"}],
        "reported_departments": [{"code": "BKK", "name": "Bangkok"}, {"code": "CNX", "name": "Chiang Mai"}],
        "reported_stock_acc_groups": [{"acccod": "LJ", "fit": True}, {"acccod": "ST", "fit": True}],
        "catalog_fingerprint": {"stock_master_count": 4},
    }

    def test_only_the_rows_this_payload_uses(self):
        payload = {"lines": [{"acc": "1100"}, {"acc": "5300"}], "items_account": "5300", "stock_acccod": "LJ",
                   "supplier": {"code": "S1", "name": "Kaew"}, "depcod": "BKK",
                   "items": [{"code": "P1", "name": "x"}, {"name": " rice "}]}
        used = used_masters(payload, self.MASTERS)
        self.assertEqual([row["code"] for row in used["accounts"]], ["1100", "5300"])
        self.assertEqual([row["code"] for row in used["supplier"]], ["S1"])
        self.assertEqual([row["code"] for row in used["products"]], ["P1", "P2"])
        self.assertEqual([row["code"] for row in used["departments"]], ["BKK"])
        self.assertEqual([row["acccod"] for row in used["stock_acc_groups"]], ["LJ"])
        self.assertEqual(used["customer"], [])
        # 没用到的行变了：这一份不变（领取照常）；用到的变了：变（照旧转人工）
        changed = {**self.MASTERS, "reported_accounts": [*self.MASTERS["reported_accounts"][:2], {"code": "9999"}]}
        self.assertEqual(used_masters(payload, changed), used)
        renamed = {**self.MASTERS, "reported_accounts": [{"code": "1100", "name": "Petty"}]}
        self.assertNotEqual(used_masters(payload, renamed), used)

    def test_a_new_party_watches_its_tax_id_so_a_later_master_is_not_duplicated(self):
        payload = {"supplier": {"code": "", "tax_id": "0105599210021", "name": "Kaew New", "supplier_new": True}}
        self.assertEqual([row["code"] for row in used_masters(payload, self.MASTERS)["supplier"]], ["S1"])
        self.assertEqual(used_masters(payload, {})["supplier"], [])
        nameless = {"customer": {"code": "", "name": "BUYER"}, "direction": "sales"}
        self.assertEqual([row["code"] for row in used_masters(nameless, self.MASTERS)["customer"]], ["C1"])

    def test_pos_receipt_accounts_and_bad_inputs(self):
        payload = {"pos": {"transfers": [{"acc": "9999", "amount": "10"}]}}
        self.assertEqual([row["code"] for row in used_masters(payload, self.MASTERS)["accounts"]], ["9999"])
        self.assertEqual(used_masters(None, None)["accounts"], [])


class WireKeys(unittest.TestCase):
    def test_lease_request_takes_index_pending_only_when_well_formed(self):
        self.assertEqual(queue_contracts.lease_request({}), {"max": 10, "agent_id": None})
        self.assertEqual(queue_contracts.lease_request({"index_pending": ["b", "a", "a"]})["index_pending"], ["a", "b"])
        self.assertEqual(queue_contracts.lease_request({"index_pending": []})["index_pending"], [])
        for bad in ("a", [1], [""], ["x" * 201], ["a"] * (queue_contracts.MAX_INDEX_PENDING + 1)):
            self.assertNotIn("index_pending", queue_contracts.lease_request({"index_pending": bad}), bad)

    def test_jobs_keep_four_keys_and_may_add_attended(self):
        log_id = "1b4e28ba-2fa1-11d2-883f-0016d3cca427"
        record = {"history": "H1", "invoice_no": "INV", "frozen_payload": {"a": 1}}
        plain = queue_contracts.job(log_id, record)
        self.assertEqual(set(plain), {"log_id", "history_id", "invoice_no", "payload"})
        marked = queue_contracts.job(log_id, record, False)
        self.assertIs(marked["attended"], False)
        reply = queue_contracts.lease_response([plain, marked])
        self.assertEqual([set(row) - set(plain) for row in reply["jobs"]], [set(), {"attended"}])
        for bad in ({**plain, "extra": 1}, {key: plain[key] for key in ("log_id", "payload")}):
            with self.assertRaises(queue_contracts.QueueContractError):
                queue_contracts.lease_response([bad])
        with self.assertRaises(queue_contracts.QueueContractError):
            queue_contracts.job(log_id, record, "yes")


class PureModules(unittest.TestCase):
    def test_the_new_contracts_import_no_framework(self):
        for path in (APP / "companion/route_contracts.py", APP / "push/express/wire_location.py",
                     APP / "push/express/referenced.py", APP / "push/native/contracts.py",
                     APP / "companion/queue_contracts.py"):
            tree = ast.parse(path.read_text())
            names = {alias.name.split(".")[0] for node in ast.walk(tree)
                     if isinstance(node, (ast.Import, ast.ImportFrom))
                     for alias in (node.names if isinstance(node, ast.Import) else [types.SimpleNamespace(
                         name=node.module or "")])}
            self.assertFalse(names & {"frappe", "pymysql"}, path.name)


# ── 编排：领取 v2（替身模块上加载真 `companion/transport.py`）──────────────────────────────


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


def _row(name, key, *, actor=ME, created=0, version=1, route_device=None):
    return Record(name=name, account_key=key, actor=actor, entered_by=actor, created_at=NOW + timedelta(seconds=created),
                  route_device=route_device, payload_version=version)


class _Rejected(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _Unavailable(Exception):
    pass


class _Permission(Exception):
    pass


def _load(name, path, modules):
    with mock.patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class Transport(unittest.TestCase):
    """一台电脑来领：每份账套只看排头、跳过不归它的、等锁的看下一份、确定失效才隔离、每台一件、副本换成它的写法。"""

    def setUp(self):
        self.calls = []
        self.pending = []
        self.busy = False
        self.reach = {"s1": Record(account_key="s1", path=r"Z:\X\S1", root=r"Z:\X", registry_row=2),
                      "s2": Record(account_key="s2", path=r"Z:\X\S2", root=r"Z:\X", registry_row=5)}
        self.reachers = {"s1": [A, B], "s2": [A, B]}
        self.peers = {A: {"users": {ME}, "online": True}, B: {"users": {OTHER}, "online": True}}
        self.outcome = {}
        self.registrations = {"s1": {"uid": "vol:1"}, "s2": {"uid": "vol:2"}}
        self.locks_taken = set()
        self.queries = []
        self.scopes = []
        frappe = types.ModuleType("frappe")
        frappe._ = lambda text: text
        frappe.PermissionError = _Permission
        frappe.QueryDeadlockError = type("QueryDeadlockError", (Exception,), {})
        frappe.throw = lambda message, exc=Exception: (_ for _ in ()).throw(exc(message))
        frappe.parse_json = json.loads
        frappe.db = types.SimpleNamespace(savepoint=lambda name: None, rollback=lambda **kw: None, sql=self.sql,
                                          get_value=lambda doctype, name, field, as_dict=False: types.SimpleNamespace(
                                              device_owner="owner@example.com", companion_version="1.8.0"))
        frappe.get_doc = lambda doctype, name, for_update=False: Record(
            name=name, connection="CONN", workspace="WS", client="", branch="legacy", binding_generation=0,
            actor=ME, history="H-" + name, history_version=1, account_key=self.key_of(name), connection_revision=1)
        ledger = types.SimpleNamespace(
            RECORD="Cowork Push Record", DispatchRejected=_Rejected, LeaseUnavailable=_Unavailable,
            now_utc=lambda value=None: NOW, lease=self.lease, quarantine_pending=self.quarantine,
            lock_busy=lambda name, context: name in self.locks_taken,
            trusted_adapter_context=lambda connection, owner, generation, **kw: types.SimpleNamespace(
                connection=connection, owner=owner, **kw))
        credentials = types.SimpleNamespace(
            revalidate=lambda context: context, device_of=lambda name: {"KEY-A": A}.get(name),
            pool_peers=lambda connection, now=None: {name: dict(peer) for name, peer in self.peers.items()})
        # 排头按这台电脑上登录过 CC 的人取（`devices.signed_in_of`，与 `pool_peers` 的 users 同一来源）
        devices = types.SimpleNamespace(DOCTYPE="Cowork CC Desktop Device",
                                        signed_in_of=lambda device: frozenset(self.peers.get(device, {}).get("users") or ()),
                                        member=lambda *a, **k: types.SimpleNamespace(selected=""),
                                        note_index_pending=lambda device, uids: self.calls.append(("index", device, uids)))
        # 领取时连接行锁着：每份账套的可达与登记一次批量取回，不逐份查（第 3 批审查）
        pool = types.SimpleNamespace(
            writable_reach=lambda connection, device: dict(self.reach) if device == A else {},
            reachers_of=lambda connection, keys, writable=False: (
                self.queries.append("reachers_of") or {key: list(self.reachers.get(key, [])) for key in keys}),
            registrations_of=lambda connection, keys: (
                self.queries.append("registrations_of") or {key: self.registrations[key] for key in keys
                                                             if key in self.registrations}))
        delivery_store = types.SimpleNamespace(owner=lambda credential: "companion:owner",
                                               create=lambda credential, claim: Record(
                                                   name="1b4e28ba-2fa1-11d2-883f-0016d3cca427"))
        stub = types.SimpleNamespace
        heads = _load("pearnly_cowork.companion.queue_heads", APP / "companion/queue_heads.py",
                      {"frappe": frappe, "pearnly_cowork.companion.route_contracts": rc})
        modules = {
            "frappe": frappe,
            "pearnly_cowork.companion.queue_heads": heads,
            "pearnly_cowork.connections.pool": pool,
            "pearnly_cowork.connections.refresh": stub(lease=lambda *a, **k: None),
            "pearnly_cowork.push.native.service": ledger,
            "pearnly_cowork.push.lifecycle": stub(error_code=lambda error: (str(error or "")[1:].split("]")[0]
                                                                            if str(error or "").startswith("[") else "")),
            "pearnly_cowork.companion.credentials": credentials,
            "pearnly_cowork.companion.delivery_store": delivery_store,
            "pearnly_cowork.companion.devices": devices,
            "pearnly_cowork.companion.receipt_auth": stub(),
        }
        self.transport = _load("pearnly_cowork.companion._transport_under_test", APP / "companion/transport.py", modules)
        self.credential = types.SimpleNamespace(connection="CONN", workspace="WS", client="", credential_id="KEY-A",
                                                token_generation=1, issuer=ME)

    def key_of(self, name):
        return next((row.account_key for row in self.pending if row.name == name), "")

    def sql(self, query, values=(), as_dict=False):
        q = " ".join(query.split())
        if "written_by_device=%s" in q:
            return [("CWP-BUSY",)] if self.busy else []
        if "MIN(r2.created_at)" in q:
            # 每份账套在这台能写的单里各取排头（真查询的语义在 `QueueHeadsQuery` 里用 SQLite 跑过）
            self.queries.append("heads")
            self.scopes.append({key: values[key] for key in ("everyone", "device", "anyone", "users")})
            wanted, first = set(values["keys"]), {}
            for row in sorted(self.pending, key=lambda r: (r.created_at, r.name)):
                owned = (row.route_device == values["device"] if row.route_device
                         else values["anyone"] or row.entered_by in values["users"])
                if row.account_key in wanted and owned:
                    first.setdefault(row.account_key, row)
            return sorted(first.values(), key=lambda r: (r.created_at, r.name))
        if "payload_json FROM" in q:
            return [Record(name=name, payload_json=json.dumps({"payload_version": 2})) for name in values[0]]
        if "status='leased' AND r.lease_expires_at<=%s" in q:
            return []
        raise AssertionError("unexpected query: " + q)

    def lease(self, name, *, context, adapter, now=None):
        self.calls.append(("lease", name, adapter.route, adapter.device))
        outcome = self.outcome.get(name, "leased")
        if outcome == "waiting":
            return {"leased": False, "reason": "waiting_lock", "record": {}}
        if isinstance(outcome, Exception):
            raise outcome
        payload = {"account_set": "\\\\srv\\s\\x\\" + name, "account_dir": "\\\\srv\\s\\x\\" + name,
                   "account_root": r"\\srv\s\x", "account_set_row": 1}
        self.frozen = payload
        return {"leased": True, "record": {"name": name, "history": "H-" + name, "invoice_no": "INV",
                                           "frozen_payload": payload}, "fence": 1, "attempt_token": "t"}

    def quarantine(self, name, *, context, adapter, reason):
        self.calls.append(("quarantine", name, reason))

    def run_lease(self, body=None):
        return self.transport.lease(self.credential, body or {})

    def test_a_colleagues_head_does_not_block_my_work_in_the_same_account_set(self):
        # 用户 10-16：s1 最早的是同事录的、同事的电脑关着；A 只在它能写的单里取排头，照样写 s1 里 A 自己的单
        self.peers[B]["online"] = False
        self.pending = [_row("CWP-1", "s1", actor=OTHER), _row("CWP-2", "s1", created=1),
                        _row("CWP-3", "s2", created=2)]
        reply = self.run_lease()
        self.assertEqual([call[:3] for call in self.calls if call[0] == "lease"], [("lease", "CWP-2", rc.OWN)])
        self.assertEqual(self.scopes[-1], {"everyone": 0, "device": A, "anyone": 0, "users": (ME,)})
        self.assertTrue(reply["jobs"][0]["attended"])

    def test_a_colleagues_job_is_never_taken_however_long_it_has_waited(self):
        self.pending = [_row("CWP-1", "s1", actor=OTHER, created=-86400)]
        self.assertEqual(self.run_lease()["jobs"], [])
        self.assertFalse([call for call in self.calls if call[0] == "lease"])

    def test_a_scheduled_task_goes_only_to_the_computer_that_reads_its_folder(self):
        self.pending = [_row("CWP-1", "s1", actor=OTHER, route_device=A), _row("CWP-2", "s2", route_device=B)]
        reply = self.run_lease()
        self.assertEqual([call[:4] for call in self.calls if call[0] == "lease"], [("lease", "CWP-1", rc.READER, A)])
        self.assertIs(reply["jobs"][0]["attended"], False)   # 定时任务：没人在电脑前，不弹询问框

    def test_a_computer_nobody_signed_in_on_writes_everyones_jobs_without_a_popup(self):
        self.peers[A]["users"] = set()
        self.pending = [_row("CWP-1", "s1", actor=OTHER)]
        reply = self.run_lease()
        self.assertEqual(self.scopes[-1]["anyone"], 1)
        self.assertEqual([call[:3] for call in self.calls if call[0] == "lease"], [("lease", "CWP-1", rc.OWN)])
        self.assertIs(reply["jobs"][0]["attended"], False)

    def test_the_heads_payload_is_localized_and_the_frozen_copy_is_untouched(self):
        self.pending = [_row("CWP-1", "s1", actor=OTHER), _row("CWP-3", "s2", created=2)]
        reply = self.run_lease()
        self.assertEqual([call[:3] for call in self.calls if call[0] == "lease"], [("lease", "CWP-3", rc.OWN)])
        job_ = reply["jobs"][0]
        self.assertTrue(job_["attended"])
        self.assertEqual((job_["payload"]["account_set"], job_["payload"]["account_root"],
                          job_["payload"]["account_set_row"], job_["payload"]["account_uid"]),
                         (r"Z:\X\S2", r"Z:\X", 5, "vol:2"))
        self.assertEqual(self.frozen["account_set"], r"\\srv\s\x\CWP-3")  # 冻结的那份没被改

    def test_waiting_for_a_lock_moves_on_to_the_next_account_set(self):
        self.pending = [_row("CWP-1", "s1"), _row("CWP-2", "s2", created=1)]
        self.outcome = {"CWP-1": "waiting"}
        reply = self.run_lease()
        self.assertEqual([call[1] for call in self.calls if call[0] == "lease"], ["CWP-1", "CWP-2"])
        self.assertEqual(len(reply["jobs"]), 1)

    def test_only_a_definite_rejection_is_quarantined_and_the_rest_still_flows(self):
        self.pending = [_row("CWP-1", "s1"), _row("CWP-2", "s2", created=1)]
        self.outcome = {"CWP-1": _Rejected("push.native_target_changed")}
        reply = self.run_lease()
        self.assertIn(("quarantine", "CWP-1", "push.native_target_changed"), self.calls)
        self.assertEqual(len(reply["jobs"]), 1)
        self.calls.clear()
        self.outcome = {"CWP-1": _Permission("lost")}
        self.run_lease()
        self.assertIn(("quarantine", "CWP-1", "companion.queue_access_revoked"), self.calls)
        # 要录入人另一台电脑才有的 CC 桌面版：跳过，从不隔离
        self.calls.clear()
        self.outcome = {}
        self.peers[A]["payload_version"] = 1
        self.peers[B] = {"users": {ME}, "online": False, "payload_version": 2}
        self.pending = [_row("CWP-9", "s1", version=2)]
        self.assertEqual(self.run_lease()["jobs"], [])
        self.assertFalse([call for call in self.calls if call[0] in ("quarantine", "lease")])

    def test_a_head_none_of_the_entry_users_computers_can_write_goes_to_manual_and_the_queue_behind_it_flows(self):
        # 第 3 批审查：录入人够得到它的电脑都只支持载荷版本 1，v2 的排头既领不走也不隔离时，同一账套后面的 v1 单全被堵住；
        # 以前在准备时就拦下（同一个原因码 / 词条）。同事的新版不算（它不替他写）；旧版不报最高版本的不算（不拦）
        self.peers[A]["payload_version"] = 1
        self.peers[B]["payload_version"] = 3
        self.pending = [_row("CWP-9", "s1", version=2), _row("CWP-10", "s1", created=1), _row("CWP-11", "s2", created=2)]
        reply = self.run_lease()
        self.assertIn(("quarantine", "CWP-9", "payload_version_outdated"), self.calls)
        self.assertEqual([call[1] for call in self.calls if call[0] == "lease"], ["CWP-11"])
        self.assertEqual(len(reply["jobs"]), 1)
        self.calls.clear()
        self.peers[B] = {"users": {ME}, "online": False, "payload_version": None}
        self.pending = [_row("CWP-9", "s1", version=2)]
        self.run_lease()
        self.assertFalse([call for call in self.calls if call[0] == "quarantine"])

    def test_heads_and_pool_rows_are_read_in_batches_and_a_busy_lock_skips_the_full_lease(self):
        # 第 3 批审查：连接行锁着时不逐份账套查；锁被占的排头不做整份重核（`ledger.lock_busy` 不加锁先看一眼）
        self.pending = [_row("CWP-1", "s1"), _row("CWP-2", "s2", created=1)]
        self.locks_taken = {"CWP-1"}
        reply = self.run_lease()
        self.assertEqual(self.queries, ["heads", "reachers_of", "registrations_of"])
        self.assertEqual([call[1] for call in self.calls if call[0] == "lease"], ["CWP-2"])
        self.assertEqual(len(reply["jobs"]), 1)

    def test_old_records_without_a_payload_version_are_read_back_in_one_query(self):
        self.peers[A]["payload_version"] = 1
        self.peers[B] = {"users": {ME}, "online": True, "payload_version": 2}
        self.pending = [_row("CWP-1", "s1", version=None)]
        self.assertEqual(self.run_lease()["jobs"], [])  # 冻结载荷要 2，A 只支持 1、他的另一台 B 支持：轮不到 A
        self.assertFalse([call for call in self.calls if call[0] in ("quarantine", "lease")])

    def test_one_lease_per_computer_at_a_time(self):
        self.pending = [_row("CWP-1", "s1")]
        self.busy = True
        self.assertEqual(self.run_lease()["jobs"], [])
        self.assertFalse([call for call in self.calls if call[0] == "lease"])

    def test_a_computer_that_reaches_nothing_never_gets_a_write(self):
        self.credential.credential_id = "KEY-LEGACY"  # 没挂电脑的旧密钥：按密钥编号算一台，它够不到任何账套
        self.pending = [_row("CWP-1", "s1", created=-3600)]
        self.assertEqual(self.run_lease()["jobs"], [])
        self.assertIn(("index", None, None), self.calls)

    def test_the_computers_index_pending_is_recorded_last(self):
        self.run_lease({"index_pending": ["vol:2"]})
        self.assertEqual(self.calls[-1], ("index", A, ["vol:2"]))

    def test_a_uid_mismatch_marks_the_reach_stale_and_asks_for_a_full_report(self):
        marked, full, written = [], [], []
        self.transport.account_pool = types.SimpleNamespace(
            mark_stale=lambda c, d, k: marked.append((c, d, k)) or True,
            note_written=lambda c, k, d, at: written.append((c, k, d)))
        self.transport.credentials = types.SimpleNamespace(device_of=lambda name: A,
                                                           request_full_report=lambda name: full.append(name))
        row = Record(connection="CONN", account_key="s1")
        delivery = Record(credential_id="KEY-A")
        self.transport._after_ack(row, delivery, {"error": "[ERR_ACCOUNT_UID_MISMATCH] folder id differs"},
                                  {"status": "pending"})
        self.assertEqual((marked, full), ([("CONN", A, "s1")], ["KEY-A"]))
        self.transport._after_ack(row, delivery, {"error": "[ERR_OTHER] x"}, {"status": "pending"})
        self.assertEqual(len(marked), 1)
        self.transport._after_ack(row, delivery, {"error": None}, {"status": "success"})
        self.assertEqual(written, [("CONN", "s1", A)])
        self.assertEqual(queue_contracts.ERR_ACCOUNT_UID_MISMATCH, "ERR_ACCOUNT_UID_MISMATCH")


class QueueHeadsQuery(unittest.TestCase):
    """排头查询本身（`companion.queue_heads.Q_HEADS`）在 SQLite 上跑：每份账套各取排头，一份账套积压再多也看得到别的
    账套；与领取同一套过滤（第 3 批审查 3、7）。MariaDB 上的执行计划与锁在第 7 批压测时量。"""

    def setUp(self):
        import sqlite3

        frappe = types.ModuleType("frappe")
        frappe.db = types.SimpleNamespace(sql=self.sql)
        frappe.parse_json = json.loads
        self.heads = _load("pearnly_cowork.companion._queue_heads_under_test", APP / "companion/queue_heads.py",
                           {"frappe": frappe, "pearnly_cowork.companion.route_contracts": rc})
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE `tabCowork Push Record` (name, account_key, actor, entered_by, route_device, created_at, "
                        "payload_version, payload_json, connection, workspace, client, branch, binding_generation, "
                        "status, result_unknown, history)")
        self.db.execute("CREATE TABLE `tabCowork OCR History` (name, source)")
        self.serial = 0

    def sql(self, query, values=(), as_dict=False):
        import re

        args = {}
        if isinstance(values, dict):
            for key, value in values.items():
                if isinstance(value, tuple):
                    marks = [f":{key}_{i}" for i in range(len(value))]
                    query = query.replace(f"%({key})s", "(" + ",".join(marks) + ")")
                    args.update({mark[1:]: item for mark, item in zip(marks, value)})
                else:
                    args[key] = value
            query = re.sub(r"%\((\w+)\)s", r":\1", query)
        else:
            marks = [f":v{i}" for i in range(len(values[0]))]
            query = query.replace("%s", "(" + ",".join(marks) + ")", 1)
            args = {mark[1:]: item for mark, item in zip(marks, values[0])}
        return [Record(dict(row)) for row in self.db.execute(query, args).fetchall()]

    def add(self, key, *, created, source="", status="pending", connection="CONN", client="", unknown=0, version=1,
            actor=ME, entered_by=None, route_device=None):
        self.serial += 1
        name = f"CWP-{self.serial:05d}"
        self.db.execute("INSERT INTO `tabCowork OCR History` VALUES (?, ?)", ("H" + name, source))
        self.db.execute("INSERT INTO `tabCowork Push Record` VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (name, key, actor, actor if entered_by is None else entered_by, route_device,
                         f"2026-10-16 03:{created // 60:02d}:{created % 60:02d}", version,
                         json.dumps({"payload_version": 3}), connection, "WS", client, "legacy", 0, status, unknown,
                         "H" + name))
        return name

    def test_a_backlog_in_one_account_set_does_not_hide_the_others(self):
        for second in range(0, 3000):
            self.add("s1", created=second % 3000)
        late = self.add("s2", created=3500)
        found = self.heads.heads("CONN", "WS", "", ["s1", "s2"])
        self.assertEqual([row.account_key for row in found], ["s1", "s2"])
        self.assertEqual(found[1].name, late)
        self.assertNotIn("LIMIT", self.heads.Q_HEADS)

    def test_the_same_filters_as_the_lease_and_ties_take_the_first_name(self):
        self.add("s1", created=1, source="erp_web")       # 内部 ERP 来源：不经电脑
        self.add("s1", created=2, unknown=1)               # 结果不明：不是排头
        self.add("s1", created=3, connection="OTHER")      # 别的连接
        self.add("s1", created=4, client="C1")             # 别的客户
        self.add("s1", created=5, status="manual")
        first = self.add("s1", created=6, version=None)
        self.add("s1", created=6)
        found = self.heads.heads("CONN", "WS", "", ["s1"])
        self.assertEqual([row.name for row in found], [first])
        # 没有载荷版本列的旧行：读回冻结的载荷
        self.assertEqual(self.heads.payload_versions(found), {first: 3})
        self.assertEqual(self.heads.heads("CONN", "WS", "", []), [])

    def test_each_computer_sees_the_heads_of_only_the_jobs_it_may_write(self):
        # 用户 10-16（第 51 条）：同事的单排在前面、同事的电脑关着时，我的电脑照样看到同一份账套里我的单
        theirs = self.add("s1", created=1, actor=OTHER)
        mine = self.add("s1", created=2)
        scheduled = self.add("s1", created=3, actor=OTHER, route_device="CCD-R")
        legacy = self.add("s2", created=4, actor=OTHER, entered_by="")   # 这一批之前的行没有录入人：按 actor
        names = lambda **kw: [row.name for row in self.heads.heads("CONN", "WS", "", ["s1", "s2"], **kw)]
        self.assertEqual(names(), [theirs, legacy])                                  # 不筛（内部核对）
        self.assertEqual(names(device=A, users={ME}), [mine])
        self.assertEqual(names(device=B, users={OTHER}), [theirs, legacy])
        self.assertEqual(names(device="CCD-R", users={ME}), [mine])                    # 冻结给它的那张不是 s1 最早的
        self.assertEqual(names(device="CCD-R", users=frozenset()), [theirs, legacy])  # 没有登录记录：谁的都写
        self.assertEqual(names(device="CCD-R"), [scheduled])                          # 池的状态按电脑：只看冻结给它的
        self.assertEqual(names(user=ME), [mine])                                       # 池的状态按人：只看他自己的
        self.assertEqual(names(user=OTHER), [theirs, legacy])


# ── 推送账本的判据（替身模块上加载真 `push/native/service.py`）──────────────────────────────


class RouteFieldGuard(unittest.TestCase):
    """推送记录的身份字段不可改；唯一例外是 `documents.reroute` 改 ``route_device``（同一台电脑改挂，10-16 审查）。"""

    def setUp(self):
        frappe = types.ModuleType("frappe")
        frappe._ = lambda text: text

        class Denied(Exception):
            pass

        def throw(message, exc=None):
            raise Denied(message)

        frappe.throw, frappe.PermissionError = throw, Denied
        model = types.ModuleType("frappe.model")
        document = types.ModuleType("frappe.model.document")

        class Document:
            def __init__(self, **values):
                self.__dict__.update(values)
                self.flags = types.SimpleNamespace(cowork_push_native=None, cowork_push_reroute=None)
                self.before = None

            def get(self, key):
                return self.__dict__.get(key)

            def get_doc_before_save(self):
                return self.before

            def is_new(self):
                return False

            def save(self, ignore_permissions=False):
                self.validate()
                return self

        document.Document = Document
        self.denied = Denied
        self.documents = _load("pearnly_cowork.push.native._documents_under_test", APP / "push/native/documents.py",
                               {"frappe": frappe, "frappe.model": model, "frappe.model.document": document})

    def _row(self):
        row = self.documents.PushDocument(doctype="Cowork Push Record", name="CWP-1", record_id="CWP-1",
                                          route_device="OLD", connection="CONN")
        row.before = {"doctype": "Cowork Push Record", "record_id": "CWP-1", "route_device": "OLD",
                      "connection": "CONN"}
        return row

    def test_only_reroute_may_change_the_frozen_computer(self):
        row = self._row()
        self.documents.reroute(row, "NEW")
        self.assertEqual(row.route_device, "NEW")
        row = self._row()
        row.route_device = "NEW"
        with self.assertRaises(self.denied):
            self.documents.persist(row)
        row = self._row()
        row.connection = "OTHER"
        with self.assertRaises(self.denied):
            self.documents.reroute(row, "NEW")  # 其余身份字段照旧不可改


class _Fail(Exception):
    pass


def account_pool_owned(peers, actor, *, device=""):
    """真 `connections.pool.owned`（不连库的那一段）。"""
    from pearnly_cowork.connections.member import serves

    if device:
        return {device: peers[device]} if device in peers else {}
    return {name: peer for name, peer in peers.items() if serves((peer or {}).get("users"), actor)}


class Ledger(unittest.TestCase):
    """确认不要密钥、有电脑够得到才能确认；证据 v1 一律不再成立；领取只核本单用到的；锁口径 v2 还让着同账套的旧单；
    问责记在写入尝试与记录上；进入排队的时刻。"""

    def setUp(self):
        self.reach = {"k1": [A]}
        self.presence = {A: {"users": {ME}}, B: {"users": {OTHER}}}
        self.registered = {"k1": {"content_key": "0105|00000|2026-01-01"}}
        self.revalidated = []
        self.sql = []
        self.persisted = []
        frappe = types.ModuleType("frappe")
        frappe._ = lambda text: text
        frappe.ValidationError = _Fail
        frappe.PermissionError = _Permission
        frappe.QueryDeadlockError = type("QueryDeadlockError", (Exception,), {})
        frappe.DuplicateEntryError = type("DuplicateEntryError", (Exception,), {})
        frappe.DoesNotExistError = type("DoesNotExistError", (Exception,), {})

        def throw(message, exc=None):
            raise (exc or _Fail)(message)

        frappe.throw = throw
        frappe.conf = {"pearnly_product": "cowork", "pearnly_cowork_erp_push_enabled": True}
        frappe.local = types.SimpleNamespace(message_log=[])
        frappe.session = types.SimpleNamespace(user=ME)
        self.values = {}
        self.writer_calls = []
        frappe.get_doc = lambda doctype, name: Record(name=name)
        frappe._dict = Record
        frappe.db = types.SimpleNamespace(
            savepoint=lambda name: None, rollback=lambda **kw: None,
            sql=lambda q, values=(), as_dict=False: self.sql.append((" ".join(q.split()), values)) or self.older,
            get_value=lambda doctype, name, field, for_update=False, as_dict=False: (
                Record({item: self.values.get((name, item)) for item in field}) if as_dict
                else self.values.get((name, field))))
        self.older = []

        def revalidate(loaded, target, evidence, *, mode="confirm", used=None):
            self.revalidated.append((mode, used(self.facts) if used else None))

        stub = types.SimpleNamespace
        modules = {
            "frappe": frappe,
            "pearnly_cowork.access": stub(scope=stub()), "pearnly_cowork.access.scope": stub(),
            "pearnly_cowork.history": stub(push_state=stub()), "pearnly_cowork.history.push_state": stub(),
            "pearnly_cowork.push.native.completion": stub(),
            "pearnly_cowork.connections.service": stub(),
            "pearnly_cowork.connections.access": stub(),
            "pearnly_cowork.connections.pool": stub(
                ACCOUNT_SET="Cowork Express Account Set",
                registration=lambda c, key: self.registered.get(key),
                # 真判据（登记、可达可写、密钥、签发人）在 `test_cowork_express_account_sets` 里守着；这里只看接线：
                # 只在替这张单干活的电脑里找（`owned`，真函数）
                presence=lambda connection: self.presence,
                owned=account_pool_owned,
                writers=lambda doc, key, peers=None: (
                    self.writer_calls.append((doc.name, key, tuple(sorted(peers or ()))))
                    or ([d for d in self.reach.get(key, []) if d in peers] if self.registered.get(key) else []))),
            "pearnly_cowork.connections.preparation": stub(revalidate=revalidate,
                                                           basis=lambda used: json.dumps(used, sort_keys=True)),
            "pearnly_cowork.push.native.documents": stub(TYPES=("Cowork Push Record", "Cowork Push Attempt",
                                                                "Cowork Push Writer Lock"),
                                                         persist=lambda doc: self.persisted.append(doc)),
        }
        self.ledger = _load("pearnly_cowork.push.native._service_under_test", APP / "push/native/service.py", modules)
        self.masters = {"reported_accounts": [{"code": "1100"}, {"code": "9999"}]}
        self.facts = {"mappings": {"accounts": [{"erp_type": "express", "pearnly_category": "fuel", "erp_code": "1100"},
                                                {"erp_type": "express", "pearnly_category": "rent", "erp_code": "5200"}],
                                   "clients": [{"erp_type": "express", "client_id": "", "erp_code": "C01"}]},
                      "supplier_profiles": {"0105": {"default_payment": "cash"}},
                      "bank_index": [{"amount": "10.00"}], "reported_config": self.masters}
        self.context = ReservationContext(ME, "WS", "", "H1", 1, "CONN", "k1", connection_revision=1)
        self.loaded = types.SimpleNamespace(revision=1, endpoint={"binding_generation": 0},
                                            evidence=types.SimpleNamespace(snapshot={}))

    def target(self, version=2, **extra):
        return {"adapter": "express", "account_set": "k1", "root_key": "r", "own_tax_id": "",
                "preparation_evidence": {"version": version, "query": {}}, **extra}

    def test_v1_evidence_never_dispatches_again(self):
        with mock.patch.object(self.ledger, "_validate_target", return_value=None):
            for mode in ("confirm", "lease"):
                with self.subTest(mode=mode), self.assertRaises(self.ledger.DispatchRejected):
                    self.ledger._dispatch_target(self.context, self.loaded, self.target(version=1), mode=mode,
                                                 payload={})
        self.assertEqual(self.revalidated, [])

    def test_lease_checks_only_the_rows_the_payload_uses(self):
        payload = {"lines": [{"acc": "1100"}]}
        with mock.patch.object(self.ledger, "_validate_target", return_value=None):
            self.ledger._dispatch_target(self.context, self.loaded, self.target(), mode="lease", payload=payload)
            self.ledger._dispatch_target(self.context, self.loaded, self.target(), mode="confirm")
        lease, confirm = self.revalidated
        self.assertEqual(lease[0], "lease")
        self.assertEqual([row["code"] for row in lease[1]["masters"]["accounts"]], ["1100"])
        # 第 3 批审查：映射只留映射到本单科目的行（改一条无关映射不再把整队单转人工）；银行流水窗口不核
        self.assertEqual([row["erp_code"] for row in lease[1]["mappings"]["accounts"]], ["1100"])
        self.assertEqual(lease[1]["mappings"]["clients"], self.facts["mappings"]["clients"])
        self.assertEqual(lease[1]["supplier_profiles"], self.facts["supplier_profiles"])
        self.assertEqual(set(lease[1]), {"mappings", "supplier_profiles", "masters"})
        self.assertEqual(confirm, ("confirm", None))
        self.assertEqual(self.ledger.basis_hash(self.facts, payload), json.dumps(lease[1], sort_keys=True))
        unrelated = json.loads(json.dumps(self.facts))
        unrelated["mappings"]["accounts"][1]["erp_code"] = "5300"
        unrelated["bank_index"] = []
        self.assertEqual(self.ledger.basis_hash(unrelated, payload), self.ledger.basis_hash(self.facts, payload))
        changed = json.loads(json.dumps(self.facts))
        changed["mappings"]["accounts"][0]["erp_code"] = "1200"  # 本单用到的那条映射改了
        self.assertNotEqual(self.ledger.basis_hash(changed, payload), self.ledger.basis_hash(self.facts, payload))

    def test_a_writer_is_one_of_the_entry_users_own_computers_that_reaches_the_account_set(self):
        self.assertTrue(self.ledger.has_writer("CONN", "k1", actor=ME))
        self.assertEqual(self.writer_calls[-1], ("CONN", "k1", (A,)))  # 与池的状态同一个判据，只在他的电脑里找
        # 只有同事的电脑够得到（账套只在别人电脑的 C 盘上）：不算，不转给同事（第 53 条）
        self.assertFalse(self.ledger.has_writer("CONN", "k1", actor=OTHER))
        self.reach = {"k1": [A, B]}
        self.assertTrue(self.ledger.has_writer("CONN", "k1", actor=OTHER))
        # 定时任务：只看读文件夹的那台
        self.assertTrue(self.ledger.has_writer("CONN", "k1", actor=ME, route_device=B))
        self.reach = {"k1": [A]}
        self.assertFalse(self.ledger.has_writer("CONN", "k1", actor=ME, route_device=B))
        self.assertFalse(self.ledger.has_writer("CONN", "k1", actor=ME, route_device="CCD-GONE"))
        # 一台电脑都没有（未连接）、没人够得到、没登记
        self.presence = {}
        self.assertFalse(self.ledger.has_writer("CONN", "k1", actor=ME))
        self.presence = {A: {"users": {ME}}}
        self.reach = {}
        self.assertFalse(self.ledger.has_writer("CONN", "k1", actor=ME))
        self.reach, self.registered = {"k1": [A]}, {}
        self.assertFalse(self.ledger.has_writer("CONN", "k1", actor=ME))

    def test_still_confirmable_needs_v2_evidence_and_a_writer_of_its_own(self):
        row = Record(target_json=json.dumps(self.target(version=1)), actor=ME, entered_by=ME)
        self.assertFalse(self.ledger._still_confirmable(row, self.context, self.loaded))
        row = Record(target_json=json.dumps(self.target()), actor=ME, entered_by=ME)
        with mock.patch.object(self.ledger, "_validate_target", return_value=None):
            self.assertTrue(self.ledger._still_confirmable(row, self.context, self.loaded))
            # 同事的那份准备：只有我的电脑够得到，他的确认不了
            self.assertFalse(self.ledger._still_confirmable(Record(row, actor=OTHER, entered_by=OTHER), self.context,
                                                            self.loaded))
            # 定时任务冻结的读文件夹电脑够不到
            self.assertFalse(self.ledger._still_confirmable(Record(row, route_device=B), self.context, self.loaded))
            self.reach = {}
            self.assertFalse(self.ledger._still_confirmable(row, self.context, self.loaded))

    def test_the_writer_scope_freezes_scheme_two_and_the_content_key(self):
        self.assertEqual(self.ledger.writer_scope("CONN", "k1"),
                         {"lock_scheme": LOCK_SCHEME, "company_key": "0105|00000|2026-01-01"})
        self.assertEqual(self.ledger.writer_scope("CONN", "none")["company_key"], "")

    def test_scheme_two_still_waits_for_an_older_record_of_the_same_account_set(self):
        row = Record(name="CWP-2", connection="CONN", account_key="k1", workspace="WS")
        self.assertFalse(self.ledger._older_writer(row, {"lock_scheme": None}))
        self.assertEqual(self.sql, [])  # v1 记录：endpoint 锁本来就串行，不查
        self.older = [("CWP-1",)]
        self.assertTrue(self.ledger._older_writer(row, {"lock_scheme": LOCK_SCHEME}))
        query, values = self.sql[-1]
        self.assertIn("result_unknown", query)
        self.assertTrue(query.endswith("FOR UPDATE"))
        self.assertEqual(values, ("CONN", "k1", "CWP-2"))

    def test_scheme_two_also_waits_for_an_open_record_of_the_same_company_by_the_current_registration(self):
        # 第 3 批审查 6：冻结的公司锁为空（准备时登记没有内容键）或部署前的 v1 记录（本来没有公司锁）时，按这份账套现在
        # 登记的内容键再核一次同工作空间里领走 / 结果不明的记录；别的连接的记录不加锁读
        row = Record(name="CWP-2", connection="CONN", account_key="k1", workspace="WS")
        answers = iter([[], [("CWP-OTHER-COPY",)]])
        self.ledger.frappe.db.sql = lambda q, values=(), as_dict=False: (
            self.sql.append((" ".join(q.split()), values)) or next(answers))
        self.assertTrue(self.ledger._older_writer(row, {"lock_scheme": LOCK_SCHEME, "company_key": ""}))
        query, values = self.sql[-1]
        self.assertIn("a.content_key=%s", query)
        self.assertFalse(query.endswith("FOR UPDATE"))
        self.assertEqual(values, ("WS", "0105|00000|2026-01-01", "CWP-2"))
        # 登记没有内容键：只按账套键核
        self.sql.clear()
        self.registered["k1"] = {"content_key": ""}
        self.ledger.frappe.db.sql = lambda q, values=(), as_dict=False: self.sql.append((q, values)) or []
        self.assertFalse(self.ledger._older_writer(row, {"lock_scheme": LOCK_SCHEME}))
        self.assertEqual(len(self.sql), 1)

    def test_a_look_before_the_lease_reads_the_locks_without_locking(self):
        target = self.target(lock_scheme=LOCK_SCHEME, company_key="0105|00000|2026-01-01")
        self.values.update({("CWP-1", "target_json"): json.dumps(target), ("CWP-1", "connection"): "CONN",
                            ("CWP-1", "account_key"): "k1", ("CWP-1", "workspace"): "WS"})
        self.older = [("LOCK",)]
        self.assertTrue(self.ledger.lock_busy("CWP-1", context=self.context))
        query, values = self.sql[-1]
        self.assertIn("COALESCE(holder,'')<>''", query)
        self.assertEqual(len(values[0]), 2)  # 账套锁 + 公司锁
        self.assertFalse(any(q.endswith("FOR UPDATE") for q, _ in self.sql))
        self.older = []
        self.assertFalse(self.ledger.lock_busy("CWP-1", context=self.context))
        self.assertFalse(self.ledger.lock_busy("CWP-NONE", context=self.context))

    def test_a_moved_computer_takes_over_its_frozen_scheduled_jobs_and_a_retired_ones_go_to_people(self):
        """10-16 审查：定时任务的单冻结了读文件夹的电脑（``route_device``）。同一台电脑的旧记录改挂到新记录时（1.6 升 1.7.0
        就会走到），这条连接上还没写完的跟过去，新记录领得到；电脑退役（移除、旧电脑密钥用完）时，还在排队的转人工
        （``push.no_writer``），不永远排着。先不加锁挑（``route_device`` 没有索引），再按主键逐张锁住复核。"""
        rows = {"CWP-1": Record(name="CWP-1", connection="CONN", route_device="OLD", status="pending", revision=2),
                "CWP-2": Record(name="CWP-2", connection="CONN", route_device="OLD", status="leased", revision=1),
                "CWP-3": Record(name="CWP-3", connection="CONN", route_device="OTHER", status="pending", revision=1),
                "CWP-4": Record(name="CWP-4", connection="CONN", route_device="OLD", status="success", revision=5)}
        self.older = [(name,) for name in rows]
        rerouted = []
        with mock.patch.object(self.ledger, "_current", side_effect=lambda doctype, name: rows[name]), \
                mock.patch.object(self.ledger.ledger_documents, "reroute", create=True,
                                  side_effect=lambda row, device: rerouted.append((row.name, device))
                                  or row.update(route_device=device)):
            self.assertEqual(self.ledger.reroute("CONN", "OLD", "NEW"), 2)
            self.assertEqual(self.ledger.reroute("CONN", "SAME", "SAME"), 0)
        self.assertEqual(rerouted, [("CWP-1", "NEW"), ("CWP-2", "NEW")])
        self.assertEqual((rows["CWP-1"].revision, rows["CWP-1"].status), (3, "pending"))  # 只换这一列，不改状态
        query, values = self.sql[-1]
        self.assertNotIn("FOR UPDATE", query)
        self.assertEqual(values, ("OLD", ("prepared", "pending", "leased"), "CONN"))
        rows = {"CWP-5": Record(name="CWP-5", route_device="GONE", status="pending", result_unknown=0),
                "CWP-6": Record(name="CWP-6", route_device="GONE", status="pending", result_unknown=1),
                "CWP-7": Record(name="CWP-7", route_device="ELSE", status="pending", result_unknown=0)}
        self.older = [(name,) for name in rows]
        saved = []
        with mock.patch.object(self.ledger, "_current", side_effect=lambda doctype, name: rows[name]), \
                mock.patch.object(self.ledger, "_save_record", side_effect=lambda row, t, **k: saved.append(row.name)):
            self.assertEqual(self.ledger.release_route("GONE"), 1)
            self.assertEqual(self.ledger.release_route(""), 0)
        self.assertEqual(saved, ["CWP-5"])
        self.assertEqual((rows["CWP-5"].status, rows["CWP-5"].reason), ("manual", "push.no_writer"))
        self.assertEqual(rows["CWP-6"].status, "pending")  # 结果不明的不碰（另由人核对）
        query, values = self.sql[-1]
        self.assertNotIn("FOR UPDATE", query)
        self.assertEqual(values, ("GONE", ("pending",)))

    def test_payload_version_outdated_is_a_quarantine_reason(self):
        with self.assertRaisesRegex(_Fail, "push.native_request_invalid"):
            self.ledger.quarantine_pending("CWP-1", context=self.context, adapter=None, reason="anything")
        with mock.patch.object(self.ledger, "_receipt_scope", side_effect=_Fail("scoped")), \
                self.assertRaisesRegex(_Fail, "scoped"):
            self.ledger.quarantine_pending("CWP-1", context=self.context, adapter=None,
                                           reason="payload_version_outdated")

    def _confirm(self, target, **extra):
        row = Record(name="CWP-1", status="prepared", revision=1, result_unknown=0, actor=ME, confirmation_request=None,
                     account_key="k1", connection="CONN", entered_by=ME, **extra)
        self.values[("CWP-1", "target_json")] = json.dumps(target)
        saved = []
        with mock.patch.object(self.ledger, "_authorize", return_value=self.loaded), \
                mock.patch.object(self.ledger, "_history", return_value=Record(record_version=1)), \
                mock.patch.object(self.ledger, "_locks", return_value=[]), \
                mock.patch.object(self.ledger, "_record", return_value=row), \
                mock.patch.object(self.ledger, "_dispatch_record"), \
                mock.patch.object(self.ledger, "_dispatch_target") as dispatch, \
                mock.patch.object(self.ledger, "project", side_effect=lambda r: dict(r)), \
                mock.patch.object(self.ledger, "_save_record", side_effect=lambda r, t, **k: saved.append(t)):
            result = self.ledger.confirm("CWP-1", context=self.context, expected_revision=1, request_key="rk")
        return result, dispatch, saved

    def test_confirm_needs_no_key_only_one_of_the_entry_users_computers_that_reaches_the_account_set(self):
        result, dispatch, saved = self._confirm(self.target())
        self.assertEqual(result["status"], "pending")
        self.assertNotIn("queued_at", result)   # 没有 120 秒换手，不再记排队时刻
        self.assertEqual(len(saved), 1)
        self.assertEqual(dispatch.call_args.kwargs, {"mode": "confirm"})
        # 只有同事的电脑够得到：拒（push.no_writer：「没有可写入该账套的电脑」）
        self.reach = {"k1": [B]}
        with self.assertRaisesRegex(_Fail, "push.no_writer"):
            self._confirm(self.target())
        # 定时任务：按读文件夹的那台判，不看负责人自己的电脑
        self.assertEqual(self._confirm(self.target(), route_device=B)[0]["status"], "pending")
        self.reach = {"k1": [A]}
        with self.assertRaisesRegex(_Fail, "push.no_writer"):
            self._confirm(self.target(), route_device=B)
        self.reach = {}
        with self.assertRaisesRegex(_Fail, "push.no_writer"):
            self._confirm(self.target())
        self.reach = {"k1": [A]}
        with self.assertRaisesRegex(_Fail, "push.native_target_changed"):
            self._confirm(self.target(version=1))

    def test_lease_records_which_computer_writes_and_why(self):
        row = Record(name="CWP-1", status="pending", result_unknown=0, fence=0, workspace="WS", client="",
                     connection="CONN", binding_generation=0, payload_json='{"a": 1}', target_json="{}")
        self.values[("CWP-1", "target_json")] = json.dumps(self.target(lock_scheme=LOCK_SCHEME))
        self.values[("CWP-1", "payload_json")] = '{"lines": [{"acc": "1100"}]}'
        adapter = self.ledger.trusted_adapter_context("CONN", "companion:x", 0, device=A, device_owner=OTHER,
                                                      route=rc.READER)
        made = []
        with mock.patch.object(self.ledger, "_dispatch_context", return_value=self.loaded), \
                mock.patch.object(self.ledger, "_dispatch_target") as dispatch, \
                mock.patch.object(self.ledger, "_locks", return_value=[]), \
                mock.patch.object(self.ledger, "_record", return_value=row), \
                mock.patch.object(self.ledger, "_dispatch_record"), \
                mock.patch.object(self.ledger, "_new", side_effect=lambda doctype, name, **v: made.append(v) or Record(
                    name=name, **v)), \
                mock.patch.object(self.ledger, "project", side_effect=lambda r: dict(r)), \
                mock.patch.object(self.ledger, "_save_record"):
            claim = self.ledger.lease("CWP-1", context=self.context, adapter=adapter)
            self.assertEqual(dispatch.call_args.kwargs, {"mode": "lease", "payload": {"lines": [{"acc": "1100"}]}})
            self.assertEqual((made[0]["device"], made[0]["device_owner"], made[0]["route"]), (A, OTHER, rc.READER))
            self.assertEqual((row.written_by_device, row.write_route), (A, rc.READER))
            self.assertTrue(claim["leased"])
            # 同一账套键上有领走 / 结果不明的别的记录：等（v2 不锁整条连接，旧单冻结的根可能不同）
            row.status = "pending"
            self.older = [("CWP-0",)]
            self.assertEqual(self.ledger.lease("CWP-1", context=self.context, adapter=adapter)["reason"], "waiting_lock")

    def test_a_job_returned_to_the_queue_waits_for_its_own_computers_again(self):
        row = Record(name="CWP-1", status="leased", wire_attempt=1, branch="legacy", failure_count=0, fence=1)
        attempt = Record(status="leased")
        self.values[("CWP-1", "target_json")] = json.dumps(self.target(lock_scheme=LOCK_SCHEME))
        adapter = self.ledger.trusted_adapter_context("CONN", "companion:x", 0)
        with mock.patch.object(self.ledger, "_receipt_scope"), \
                mock.patch.object(self.ledger, "_locks", return_value=[]), \
                mock.patch.object(self.ledger, "_record", return_value=row), \
                mock.patch.object(self.ledger, "_attempt", return_value=attempt), \
                mock.patch.object(self.ledger, "project", side_effect=lambda r: dict(r)), \
                mock.patch.object(self.ledger, "_save_record"):
            result = self.ledger.ack("CWP-1", context=self.context, adapter=adapter, fence=1, attempt_token="t",
                                     success=False, outcome="waiting_lock",
                                     error="[ERR_ACCOUNT_UID_MISMATCH] folder id differs")
        self.assertEqual(result["status"], "pending")
        self.assertNotIn("queued_at", row)   # 没有换手计时：退回排队后照旧只等它自己的电脑

    def test_an_mrerp_adapter_carries_no_computer(self):
        adapter = self.ledger.trusted_adapter_context("CONN", "server:mrerp", 0)
        self.assertEqual((adapter.device, adapter.device_owner, adapter.route), ("", "", ""))


# ── 池的状态（`companion.pool`）────────────────────────────────────────────────────


class PoolState(unittest.TestCase):
    """池的状态按人（第 51–53 条，用户 10-16）：只看替这件活干的电脑——他自己登录过 CC 的，或定时任务读文件夹的那台。"""

    def setUp(self):
        self.peers = {A: {"online": False, "payload_version": 1, "issuers": {ME}, "users": {ME}},
                      B: {"online": True, "payload_version": None, "issuers": {OTHER}, "users": {OTHER}}}
        self.valid = {ME, OTHER}
        self.reachers = {"s1": [A, B], "s2": [A], "s3": [B]}
        self.head = None
        frappe = types.ModuleType("frappe")
        frappe.get_doc = lambda doctype, name: Record(name=name, adapter=self.adapter, workspace="WS", client="")
        frappe.parse_json = json.loads
        frappe.session = types.SimpleNamespace(user=ME)

        def sql(q, values=(), as_dict=False):
            self.queries.append((q, values))
            if "MIN(r2.created_at)" in q and self.head:
                return [Record(name="CWP-1", account_key=values["keys"][0], payload_version=self.head)]
            return []

        frappe.db = types.SimpleNamespace(sql=sql, get_value=lambda *a, **k: "{}")
        self.adapter = "express"
        self.queries = []

        def writers(doc, key=None, *, peers=None):
            # 真判据在 `test_cowork_express_account_sets`；这里按同一口径给出，看 state 怎么用它
            devices = self.reachers.get(key, []) if key else {d for ds in self.reachers.values() for d in ds}
            return [d for d in sorted(devices) if d in peers and peers[d]["issuers"] & self.valid]

        pool = types.SimpleNamespace(writers=writers, owned=account_pool_owned)
        credentials = types.SimpleNamespace(pool_peers=lambda c, now=None: self.peers)
        heads = _load("pearnly_cowork.companion.queue_heads", APP / "companion/queue_heads.py",
                      {"frappe": frappe, "pearnly_cowork.companion.route_contracts": rc})
        modules = {"frappe": frappe, "pearnly_cowork.connections.pool": pool,
                   "pearnly_cowork.connections.access": types.SimpleNamespace(require_connection=lambda doc, lock: None),
                   "pearnly_cowork.companion.credentials": credentials,
                   "pearnly_cowork.companion.queue_heads": heads}
        self.pool = _load("pearnly_cowork.companion._pool_under_test", APP / "companion/pool.py", modules)

    def state(self, key=None, user=ME, **kw):
        return self.pool.state("CONN", key, user=user, **kw)

    def test_my_own_computer_offline_reads_as_offline_but_still_selectable(self):
        # 自己的电脑关着 → 「电脑离线（仍可选）」，排队等它开机；同事的电脑在线也不算自己在线
        self.assertEqual(self.state("s1"), {"online": False, "writers_known": True, "needs_update": False,
                                            "computers": True})
        self.assertEqual(self.state(), {"online": False, "writers_known": True, "needs_update": False,
                                        "computers": True})
        self.peers[A]["online"] = True
        self.assertTrue(self.state("s1")["online"])

    def test_only_a_colleagues_computer_reaches_it_means_no_writer_not_offline(self):
        # 账套只在别人电脑上（s3 只有 B 够得到）：我有电脑，但没有能写的 → 「没有能写的电脑」，确认按 push.no_writer 拒
        self.assertEqual(self.state("s3"), {"online": False, "writers_known": False, "needs_update": False,
                                            "computers": True})
        self.assertEqual(self.state("s3", user=OTHER)["writers_known"], True)

    def test_no_computer_of_my_own_reads_as_not_connected(self):
        # 我一台登录过 CC 的电脑都没有（同事的有）→ 「CC 桌面版未连接」
        self.assertEqual(self.state("s1", user="new@example.com"),
                         {"online": False, "writers_known": False, "needs_update": False, "computers": False})
        self.peers = {}
        self.assertEqual(self.state(), {"online": False, "writers_known": False, "needs_update": False,
                                        "computers": False})

    def test_a_computer_nobody_signed_in_on_counts_for_everyone_during_the_transition(self):
        self.peers[B]["users"] = set()
        self.assertEqual(self.state("s3", user="new@example.com")["writers_known"], True)
        self.assertTrue(self.state("s3", user="new@example.com")["online"])

    def test_a_scheduled_task_asks_about_the_computer_that_reads_its_folder(self):
        self.assertEqual(self.state("s3", device=B)["writers_known"], True)
        self.assertEqual(self.state("s2", device=B)["writers_known"], False)
        self.assertEqual(self.state("s2", device="CCD-GONE")["computers"], False)

    def test_a_computer_whose_issuer_lost_access_is_no_writer(self):
        self.valid = {OTHER}
        self.assertEqual(self.state("s2")["writers_known"], False)

    def test_needs_update_when_none_of_my_writers_supports_my_heads_payload_version(self):
        self.head = 2
        self.assertTrue(self.state("s1")["needs_update"])   # 只有我的 A（v1）：等不来；B 是同事的，不算
        self.peers[B]["users"] = {ME, OTHER}
        self.assertFalse(self.state("s1")["needs_update"])  # 我的另一台 B 没报过最高版本：不拦
        # 与电脑领取时看同一张排头（`queue_heads.Q_HEADS`，同一套过滤），只看我自己的单
        self.assertTrue(all(q == self.pool.queue_heads.Q_HEADS for q, _v in self.queries))
        self.assertEqual(self.queries[-1][1]["users"], (ME,))
        self.state("s3", device=B)
        self.assertEqual((self.queries[-1][1]["device"], self.queries[-1][1]["users"]), (B, ("",)))

    def test_for_user_asks_for_the_signed_in_user(self):
        self.assertEqual(self.pool.for_user("CONN", "s3")["writers_known"], False)
        self.pool.frappe.session.user = OTHER
        self.assertEqual(self.pool.for_user("CONN", "s3")["writers_known"], True)
        self.assertEqual(self.pool.for_user("CONN", "s2", device=B)["writers_known"], False)

    def test_mrerp_only_asks_whether_a_folder_reader_is_online(self):
        self.adapter = "mrerp"
        self.assertEqual(self.state(),
                         {"online": True, "writers_known": True, "needs_update": False, "computers": True})


if __name__ == "__main__":
    unittest.main()
