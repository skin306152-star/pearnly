"""CC 桌面版多台电脑第 3 批（ADR-021，方案 2.5、2.6、2.10、第 5 节第 4–8 条）：写账分派、领取 v2、锁口径 v2、准备证据 v2、
池的状态。

- 纯函数：分派规则 `companion.route_contracts`（排头、三档顺序、120 秒换手、按账套黏住、载荷版本、`attended`），
  位置替换 `push.express.wire_location`，锁口径 `push.native.contracts.writer_identities`（v1 / v2、公司锁），
  本单用到的主档行 `push.express.referenced`，领取报文的可选键 `companion.queue_contracts`；
- 编排：`companion.transport.lease` v2（每份账套只看排头、不归这台 / 等锁 / 要更新就跳过看下一份、确定失效才隔离、
  每台同时一件、下发副本换成这台的写法、账本里冻结的载荷不变、电脑行最后记 ``index_pending``）与回执后的两件事；
  `companion.pool.state`；推送账本的确认 / 领取判据（`push.native.service`）。

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


def job(actor=ME, waited=0, version=1, key="k1", name="CWP-1"):
    return Job(name, key, actor, NOW - timedelta(seconds=waited), version)


# ── 分派规则（纯函数）──────────────────────────────────────────────────────────


class Routing(unittest.TestCase):
    def test_the_entry_users_own_computer_first_then_always_on_then_any_other(self):
        peers = {A: Peer(user=ME, online=True), B: Peer(always_on=True, online=True), C: Peer(online=True)}
        reach = [A, B, C]
        self.assertEqual(rc.decide(A, job(), reach, peers, Account(), NOW), (True, rc.OWN, ""))
        self.assertEqual(rc.decide(B, job(), reach, peers, Account(), NOW).reason, rc.BETTER)
        self.assertEqual(rc.decide(C, job(), reach, peers, Account(), NOW).reason, rc.BETTER)
        # 录入人的电脑关着：常驻电脑接手；常驻也关着：别的在线电脑
        peers[A] = peers[A]._replace(online=False)
        self.assertEqual(rc.decide(B, job(), reach, peers, Account(), NOW), (True, rc.ALWAYS_ON, ""))
        self.assertFalse(rc.decide(C, job(), reach, peers, Account(), NOW).take)
        peers[B] = peers[B]._replace(online=False)
        self.assertEqual(rc.decide(C, job(), reach, peers, Account(), NOW), (True, rc.OTHER, ""))

    def test_routing_follows_who_is_signed_in_not_who_registered_the_computer(self):
        # D7：路由按这台电脑上登录 CC 的人；登记人只管权限，根本不进分派
        peers = {A: Peer(user=OTHER, online=True), B: Peer(user=ME, online=True)}
        self.assertTrue(rc.decide(B, job(), [A, B], peers, Account(), NOW).take)
        self.assertFalse(rc.decide(A, job(), [A, B], peers, Account(), NOW).take)

    def test_after_two_minutes_queued_any_computer_that_reaches_it_writes(self):
        peers = {A: Peer(user=ME, online=True), C: Peer(online=True)}
        self.assertFalse(rc.decide(C, job(waited=119), [A, C], peers, Account(), NOW).take)
        self.assertEqual(rc.decide(C, job(waited=120), [A, C], peers, Account(), NOW), (True, rc.OTHER, ""))

    def test_only_a_computer_that_reaches_the_account_set_may_take_it(self):
        peers = {A: Peer(user=ME, online=True), B: Peer(online=True)}
        self.assertEqual(rc.decide(B, job(waited=999), [A], peers, Account(), NOW).reason, rc.UNREACHABLE)
        # 没装 Express、只读文件夹的成员：没有可达行，永远领不到写入
        self.assertFalse(rc.decide(B, job(waited=999), [], peers, Account(), NOW).take)

    def test_a_better_computer_that_cannot_write_this_payload_version_does_not_hold_the_head(self):
        peers = {A: Peer(user=ME, online=True, payload_version=1), C: Peer(online=True, payload_version=3)}
        self.assertEqual(rc.decide(C, job(version=2), [A, C], peers, Account(), NOW), (True, rc.OTHER, ""))
        self.assertEqual(rc.decide(A, job(version=2), [A, C], peers, Account(), NOW).reason, rc.VERSION)
        # 池里一台都不支持：只能等人更新（界面「需更新」）
        peers[C] = peers[C]._replace(payload_version=1)
        self.assertEqual(rc.decide(C, job(version=2), [A, C], peers, Account(), NOW).reason, rc.NEEDS_UPDATE)
        self.assertTrue(rc.needs_update(2, [A, C], peers))
        # 没报过最高版本的旧版不拦（与今天预检同一口径）
        self.assertTrue(rc.supports(Peer(), 3))
        self.assertFalse(rc.needs_update(2, [], peers))

    def test_an_account_set_whose_master_index_is_pending_sticks_to_its_writer(self):
        account = Account(uid="c8d5876c:42")
        peers = {A: Peer(user=ME, online=True, index_pending=frozenset()),
                 B: Peer(online=True, index_pending=frozenset({"c8d5876c:42"}))}
        self.assertEqual(rc.decide(B, job(), [A, B], peers, account, NOW), (True, rc.STICKY, ""))
        self.assertEqual(rc.decide(A, job(waited=999), [A, B], peers, account, NOW).reason, rc.STUCK)
        # 那台离线：解除，回到正常顺序
        peers[B] = peers[B]._replace(online=False)
        self.assertEqual(rc.decide(A, job(), [A, B], peers, account, NOW), (True, rc.OWN, ""))
        # 黏住的那台自己要更高的载荷版本：等，不让给别人写坏顺序
        peers[B] = Peer(online=True, payload_version=1, index_pending=frozenset({"c8d5876c:42"}))
        self.assertEqual(rc.decide(B, job(version=2), [A, B], peers, account, NOW).reason, rc.VERSION)

    def test_old_desktops_stick_by_their_last_write_for_two_minutes(self):
        peers = {A: Peer(user=ME, online=True), B: Peer(online=True)}  # 旧版：不报 index_pending
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

    def test_queued_at_falls_back_to_creation_and_attended_only_for_the_own_desktop(self):
        self.assertEqual(rc.queued(None, NOW), NOW)
        self.assertTrue(rc.attended(rc.OWN, Peer()))
        self.assertFalse(rc.attended(rc.OWN, Peer(always_on=True)))
        for route in (rc.ALWAYS_ON, rc.OTHER, rc.STICKY):
            self.assertFalse(rc.attended(route, Peer()))


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


def _row(name, key, *, actor=ME, created=0, queued=None, version=1):
    return Record(name=name, account_key=key, actor=actor, entered_by=actor, created_at=NOW + timedelta(seconds=created),
                  queued_at=queued, payload_version=version)


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
        self.peers = {A: {"user": ME, "online": True}, B: {"user": OTHER, "online": True}}
        self.outcome = {}
        self.registrations = {"s1": {"uid": "vol:1"}, "s2": {"uid": "vol:2"}}
        frappe = types.ModuleType("frappe")
        frappe._ = lambda text: text
        frappe.PermissionError = _Permission
        frappe.QueryDeadlockError = type("QueryDeadlockError", (Exception,), {})
        frappe.throw = lambda message, exc=Exception: (_ for _ in ()).throw(exc(message))
        frappe.parse_json = json.loads
        frappe.db = types.SimpleNamespace(savepoint=lambda name: None, rollback=lambda **kw: None, sql=self.sql,
                                          get_value=lambda doctype, name, field: "owner@example.com")
        frappe.get_doc = lambda doctype, name, for_update=False: Record(
            name=name, connection="CONN", workspace="WS", client="", branch="legacy", binding_generation=0,
            actor=ME, history="H-" + name, history_version=1, account_key=self.key_of(name), connection_revision=1)
        ledger = types.SimpleNamespace(
            RECORD="Cowork Push Record", DispatchRejected=_Rejected, LeaseUnavailable=_Unavailable,
            now_utc=lambda value=None: NOW, lease=self.lease, quarantine_pending=self.quarantine,
            trusted_adapter_context=lambda connection, owner, generation, **kw: types.SimpleNamespace(
                connection=connection, owner=owner, **kw))
        credentials = types.SimpleNamespace(
            revalidate=lambda context: context, device_of=lambda name: {"KEY-A": A}.get(name),
            pool_peers=lambda connection, now=None: {name: dict(peer) for name, peer in self.peers.items()})
        devices = types.SimpleNamespace(DOCTYPE="Cowork CC Desktop Device",
                                        member=lambda *a, **k: types.SimpleNamespace(selected=""),
                                        note_index_pending=lambda device, uids: self.calls.append(("index", device, uids)))
        pool = types.SimpleNamespace(writable_reach=lambda connection, device: dict(self.reach) if device == A else {},
                                     reachers=lambda connection, key, writable=False: list(self.reachers.get(key, [])),
                                     registration=lambda connection, key: self.registrations.get(key))
        delivery_store = types.SimpleNamespace(owner=lambda credential: "companion:owner",
                                               create=lambda credential, claim: Record(
                                                   name="1b4e28ba-2fa1-11d2-883f-0016d3cca427"))
        stub = types.SimpleNamespace
        modules = {
            "frappe": frappe,
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
        if "r.account_key IN %s" in q:
            wanted = set(values[3])
            return [row for row in sorted(self.pending, key=lambda r: (r.created_at, r.name)) if row.account_key in wanted]
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

    def test_a_head_that_is_not_this_computers_turn_is_skipped_not_blocking_the_next(self):
        # s1 的排头是 B 的人录的（B 在线，第 ① 档）；A 去看 s2（A 的人录的）
        self.pending = [_row("CWP-1", "s1", actor=OTHER), _row("CWP-2", "s1", created=1),
                        _row("CWP-3", "s2", created=2)]
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
        # 不归这台、要更新的：跳过，从不隔离
        self.calls.clear()
        self.outcome = {}
        self.peers[A]["payload_version"] = 1
        self.pending = [_row("CWP-9", "s1", version=2)]
        self.assertEqual(self.run_lease()["jobs"], [])
        self.assertFalse([call for call in self.calls if call[0] in ("quarantine", "lease")])

    def test_one_lease_per_computer_at_a_time(self):
        self.pending = [_row("CWP-1", "s1")]
        self.busy = True
        self.assertEqual(self.run_lease()["jobs"], [])
        self.assertFalse([call for call in self.calls if call[0] == "lease"])

    def test_after_two_minutes_another_computer_writes_without_a_popup(self):
        self.pending = [_row("CWP-1", "s1", actor=OTHER, queued=NOW - timedelta(seconds=121))]
        reply = self.run_lease()
        self.assertEqual([call[2] for call in self.calls if call[0] == "lease"], [rc.OTHER])
        self.assertIs(reply["jobs"][0]["attended"], False)

    def test_a_computer_that_reaches_nothing_never_gets_a_write(self):
        self.credential.credential_id = "KEY-LEGACY"  # 没挂电脑的旧密钥：按密钥编号算一台，它够不到任何账套
        self.pending = [_row("CWP-1", "s1", queued=NOW - timedelta(hours=1))]
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


# ── 推送账本的判据（替身模块上加载真 `push/native/service.py`）──────────────────────────────


class _Fail(Exception):
    pass


class Ledger(unittest.TestCase):
    """确认不要密钥、有电脑够得到才能确认；证据 v1 一律不再成立；领取只核本单用到的；锁口径 v2 还让着同账套的旧单；
    问责记在写入尝试与记录上；进入排队的时刻。"""

    def setUp(self):
        self.reach = {"k1": [A]}
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
        frappe.db = types.SimpleNamespace(
            savepoint=lambda name: None, rollback=lambda **kw: None,
            sql=lambda q, values=(), as_dict=False: self.sql.append((" ".join(q.split()), values)) or self.older,
            get_value=lambda doctype, name, field, for_update=False: self.values.get((name, field)))
        self.older = []

        def revalidate(loaded, target, evidence, *, mode="confirm", used=None):
            self.revalidated.append((mode, used(self.masters) if used else None))

        stub = types.SimpleNamespace
        modules = {
            "frappe": frappe,
            "pearnly_cowork.access": stub(scope=stub()), "pearnly_cowork.access.scope": stub(),
            "pearnly_cowork.history": stub(push_state=stub()), "pearnly_cowork.history.push_state": stub(),
            "pearnly_cowork.push.native.completion": stub(),
            "pearnly_cowork.connections.service": stub(),
            "pearnly_cowork.connections.access": stub(),
            "pearnly_cowork.connections.pool": stub(
                registration=lambda c, key: self.registered.get(key),
                reachers=lambda c, key, writable=False: list(self.reach.get(key, []))),
            "pearnly_cowork.connections.preparation": stub(revalidate=revalidate,
                                                           basis=lambda facts, used: json.dumps(used, sort_keys=True)),
            "pearnly_cowork.push.native.documents": stub(TYPES=("Cowork Push Record", "Cowork Push Attempt",
                                                                "Cowork Push Writer Lock"),
                                                         persist=lambda doc: self.persisted.append(doc)),
        }
        self.ledger = _load("pearnly_cowork.push.native._service_under_test", APP / "push/native/service.py", modules)
        self.masters = {"reported_accounts": [{"code": "1100"}, {"code": "9999"}]}
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
        self.assertEqual([row["code"] for row in lease[1]["accounts"]], ["1100"])
        self.assertEqual(confirm, ("confirm", None))
        self.assertEqual(self.ledger.basis_hash({"reported_config": self.masters}, payload),
                         json.dumps(lease[1], sort_keys=True))

    def test_a_writer_is_any_computer_that_reaches_the_registered_account_set(self):
        self.assertTrue(self.ledger.has_writer("CONN", "k1"))
        self.reach = {}
        self.assertFalse(self.ledger.has_writer("CONN", "k1"))
        self.reach, self.registered = {"k1": [A]}, {}
        self.assertFalse(self.ledger.has_writer("CONN", "k1"))

    def test_still_confirmable_needs_v2_evidence_and_a_writer(self):
        row = Record(target_json=json.dumps(self.target(version=1)))
        self.assertFalse(self.ledger._still_confirmable(row, self.context, self.loaded))
        row = Record(target_json=json.dumps(self.target()))
        with mock.patch.object(self.ledger, "_validate_target", return_value=None):
            self.assertTrue(self.ledger._still_confirmable(row, self.context, self.loaded))
            self.reach = {}
            self.assertFalse(self.ledger._still_confirmable(row, self.context, self.loaded))

    def test_the_writer_scope_freezes_scheme_two_and_the_content_key(self):
        self.assertEqual(self.ledger.writer_scope("CONN", "k1"),
                         {"lock_scheme": LOCK_SCHEME, "company_key": "0105|00000|2026-01-01"})
        self.assertEqual(self.ledger.writer_scope("CONN", "none")["company_key"], "")

    def test_scheme_two_still_waits_for_an_older_record_of_the_same_account_set(self):
        row = Record(name="CWP-2", connection="CONN", account_key="k1")
        self.assertFalse(self.ledger._older_writer(row, {"lock_scheme": None}))
        self.assertEqual(self.sql, [])  # v1 记录：endpoint 锁本来就串行，不查
        self.older = [("CWP-1",)]
        self.assertTrue(self.ledger._older_writer(row, {"lock_scheme": LOCK_SCHEME}))
        query, values = self.sql[-1]
        self.assertIn("result_unknown", query)
        self.assertTrue(query.endswith("FOR UPDATE"))
        self.assertEqual(values, ("CONN", "k1", "CWP-2"))

    def _confirm(self, target):
        row = Record(name="CWP-1", status="prepared", revision=1, result_unknown=0, actor=ME, confirmation_request=None,
                     account_key="k1", connection="CONN")
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

    def test_confirm_needs_no_key_only_a_computer_that_reaches_the_account_set(self):
        result, dispatch, saved = self._confirm(self.target())
        self.assertEqual((result["status"], result["queued_at"]), ("pending", saved[0]))
        self.assertEqual(dispatch.call_args.kwargs, {"mode": "confirm"})
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
                                                      route=rc.OTHER)
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
            self.assertEqual((made[0]["device"], made[0]["device_owner"], made[0]["route"]), (A, OTHER, rc.OTHER))
            self.assertEqual((row.written_by_device, row.write_route), (A, rc.OTHER))
            self.assertTrue(claim["leased"])
            # 同一账套键上有领走 / 结果不明的别的记录：等（v2 不锁整条连接，旧单冻结的根可能不同）
            row.status = "pending"
            self.older = [("CWP-0",)]
            self.assertEqual(self.ledger.lease("CWP-1", context=self.context, adapter=adapter)["reason"], "waiting_lock")

    def test_a_job_returned_to_the_queue_restarts_its_handoff_clock(self):
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
        self.assertIsNotNone(row.queued_at)

    def test_an_mrerp_adapter_carries_no_computer(self):
        adapter = self.ledger.trusted_adapter_context("CONN", "server:mrerp", 0)
        self.assertEqual((adapter.device, adapter.device_owner, adapter.route), ("", "", ""))


# ── 池的状态（`companion.pool`）────────────────────────────────────────────────────


class PoolState(unittest.TestCase):
    def setUp(self):
        self.peers = {A: {"online": False, "payload_version": 1, "issuers": {ME}},
                      B: {"online": True, "payload_version": None, "issuers": {OTHER}}}
        self.valid = {ME, OTHER}
        self.reachers = {"s1": [A, B], "s2": [A]}
        self.head = None
        frappe = types.ModuleType("frappe")
        frappe.get_doc = lambda doctype, name: Record(name=name, adapter=self.adapter)
        frappe.parse_json = json.loads
        frappe.db = types.SimpleNamespace(
            sql=lambda q, values=(), as_dict=False: [Record(name="CWP-1", payload_version=self.head)] if self.head else [],
            get_value=lambda *a, **k: "{}")
        self.adapter = "express"
        pool = types.SimpleNamespace(reachers=lambda c, key, writable=False: self.reachers.get(key, []),
                                     writer_devices=lambda c: {d for ds in self.reachers.values() for d in ds})
        credentials = types.SimpleNamespace(pool_peers=lambda c, now=None: self.peers,
                                            issuer_valid=lambda doc, issuer: issuer in self.valid)
        modules = {"frappe": frappe, "pearnly_cowork.connections.pool": pool,
                   "pearnly_cowork.connections.access": types.SimpleNamespace(),
                   "pearnly_cowork.companion.credentials": credentials}
        self.pool = _load("pearnly_cowork.companion._pool_under_test", APP / "companion/pool.py", modules)

    def test_writers_known_while_offline_and_online_when_one_is(self):
        self.assertEqual(self.pool.state("CONN"), {"online": True, "writers_known": True, "needs_update": False})
        self.assertEqual(self.pool.state("CONN", "s2"), {"online": False, "writers_known": True, "needs_update": False})
        self.assertEqual(self.pool.state("CONN", "nope")["writers_known"], False)

    def test_a_computer_whose_issuer_lost_access_is_no_writer(self):
        self.valid = {OTHER}
        self.assertEqual(self.pool.state("CONN", "s2")["writers_known"], False)

    def test_needs_update_when_no_writer_supports_the_heads_payload_version(self):
        self.head = 2
        self.assertFalse(self.pool.state("CONN", "s1")["needs_update"])  # B 没报过最高版本：不拦
        self.assertTrue(self.pool.state("CONN", "s2")["needs_update"])

    def test_mrerp_only_asks_whether_a_folder_reader_is_online(self):
        self.adapter = "mrerp"
        self.assertEqual(self.pool.state("CONN"), {"online": True, "writers_known": True, "needs_update": False})


if __name__ == "__main__":
    unittest.main()
