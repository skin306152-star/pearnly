"""账本变化即时同步（评估与主会话审定 docs/work/2026-11-22-ledger-instant-sync.md）：不连库的检查。

- 共用约定 `pearnly_core.ledger_sync.contracts`：Express 表名 → 类别、心跳 ``ledger_changes`` 的净化（只认这台电脑报过的账套、
  认识的表，``*`` 当全部）、一条变化的规范形状；
- 共用机器通道心跳（`cc_desktop.machine`）：变化交给产品、出错不挡心跳；回复带要盯的账套（只这台电脑够得到的、本机路径），
  按电脑缓存；
- ERP 一侧（`express.live`）：按账套找现用它的公司，主档类 → 当场排读，应收 → 在用收款的账套整本刷新；要盯的 = 已接公司的
  现用账套。
"""
import pathlib
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "apps/pearnly_core"), str(ROOT / "apps/pearnly_erp_portal")]

import cc_desktop_fake as fake  # noqa: E402

frappe = fake.install()

from pearnly_core.ledger_sync import contracts as L  # noqa: E402


class Contracts(unittest.TestCase):
    def test_express_table_names_map_to_kinds(self):
        self.assertEqual(L.table_kind("ARMAS.DBF"), L.CUSTOMER)
        self.assertEqual(L.table_kind(r"\\fileserver\d$\ACCOUNT\70EXP\test\stloc.cdx"), L.STOCK)
        self.assertEqual(L.table_kind("ARTRN.DBF"), L.RECEIVABLE)
        self.assertEqual(L.table_kind("PEARNLY.LCK"), "")
        self.assertEqual(L.table_kind("LIS1122.TMP"), "")

    def test_only_known_books_and_tables_are_taken(self):
        raw = [{"key": "k1", "tables": ["ARMAS.DBF", "junk.tmp"]},
               {"key": "k2", "tables": ["*"]},
               {"key": "stranger", "tables": ["ARMAS"]},
               {"key": "k1", "tables": ["ARTRN"]},
               {"key": "k3", "tables": ["PEARNLY.LCK"]},
               "bad", {"key": "k1", "tables": "ARMAS"}]
        found = L.express_changes(raw, ["k1", "k2", "k3"])
        self.assertEqual(found, {"k1": {L.CUSTOMER, L.RECEIVABLE}, "k2": set(L.KINDS)})
        self.assertEqual(L.express_changes(raw, []), {})
        self.assertEqual(L.express_changes("nope", ["k1"]), {})

    def test_a_change_has_one_shape_for_every_ledger(self):
        self.assertEqual(L.change(L.MRERP, "15:1", ["customer", "nope"], L.PROBE),
                         {"ledger": "mrerp", "book_key": "15:1", "kinds": ["customer"], "source": "probe"})
        self.assertEqual(L.change(L.EXPRESS_CLOUD, "x", ["*"], L.WATCH)["kinds"], sorted(L.KINDS))
        with self.assertRaises(ValueError):
            L.change("sap", "x", [], L.WATCH)
        with self.assertRaises(ValueError):
            L.change(L.EXPRESS, "x", [], "guess")

    def test_the_shared_package_stays_free_of_the_framework(self):
        import ast
        for path in (ROOT / "apps/pearnly_core/pearnly_core/ledger_sync").glob("*.py"):
            names = set()
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    names |= {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom):
                    names.add((node.module or "").split(".")[0])
            self.assertFalse(names & {"frappe", "requests", "pearnly_erp_portal", "pearnly_cowork"}, path.name)


class Heartbeat(unittest.TestCase):
    def setUp(self):
        from pearnly_core.cc_desktop import machine
        self.machine = machine
        store = {}
        cache = types.SimpleNamespace(get_value=store.get,
                                      set_value=lambda key, value, expires_in_sec=None: store.__setitem__(key, value),
                                      delete_value=lambda key: store.pop(key, None))
        self.store = store
        self.errors = []
        self.calls = []
        rows = {"k1": types.SimpleNamespace(path=r"S:\70EXP\T9", root=r"S:\70EXP"),
                "k2": types.SimpleNamespace(path=r"C:\PearnlyExpress\70EXP\X", root="")}
        patches = [mock.patch.object(frappe, "cache", cache, create=True),
                   mock.patch.object(frappe, "log_error", lambda **kw: self.errors.append(kw), create=True),
                   mock.patch.object(frappe, "get_traceback", lambda: "", create=True),
                   mock.patch.object(frappe.db, "savepoint", lambda name: None, create=True),
                   mock.patch.object(frappe.db, "rollback", lambda save_point=None: self.calls.append(("rollback",)),
                                     create=True),
                   mock.patch.object(machine.books, "readable", lambda device: rows)]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.context = types.SimpleNamespace(device="PCD-1")

    def test_changes_reach_the_product_for_known_books_only(self):
        with mock.patch.object(self.machine.products, "ledger_changed",
                               lambda device, changes: self.calls.append((device, changes))):
            self.machine._changes(self.context, {"ledger_changes": [{"key": "k1", "tables": ["STMAS.DBF"]},
                                                                    {"key": "zz", "tables": ["ARMAS"]}]})
            self.machine._changes(self.context, {})  # 旧版 CC 桌面版不带：什么都不做
        self.assertEqual(self.calls, [("PCD-1", {"k1": {L.PRODUCT}})])

    def test_a_product_error_never_breaks_the_heartbeat(self):
        def broken(device, changes):
            raise RuntimeError("boom")

        with mock.patch.object(self.machine.products, "ledger_changed", broken):
            self.machine._changes(self.context, {"ledger_changes": [{"key": "k1", "tables": ["*"]}]})
        self.assertEqual(self.calls, [("rollback",)])
        self.assertEqual(len(self.errors), 1)

    def test_the_watch_list_is_this_computers_paths_and_is_cached(self):
        asked = []
        with mock.patch.object(self.machine.products, "watch_books", lambda device: asked.append(device) or ["k1", "k9"]):
            first = self.machine._watch_reply("PCD-1")
            second = self.machine._watch_reply("PCD-1")
            self.machine.forget_watch("PCD-1")
            self.machine._watch_reply("PCD-1")
        self.assertEqual(first, [{"key": "k1", "path": r"S:\70EXP\T9", "root": r"S:\70EXP"}])  # k9 这台够不着
        self.assertEqual(first, second)
        self.assertEqual(asked, ["PCD-1", "PCD-1"])


class ErpSide(unittest.TestCase):
    def setUp(self):
        from pearnly_erp_portal.express import live
        self.live = live
        self.kicked, self.receivables = [], []
        masters = types.SimpleNamespace(kick=self.kicked.append, linked_companies=lambda: [
            types.SimpleNamespace(name="CO-1"), types.SimpleNamespace(name="CO-2"), types.SimpleNamespace(name="CO-3")])
        push = types.SimpleNamespace(linked=lambda company: company != "CO-OFF")
        express = sys.modules.get("pearnly_erp_portal.express")
        patches = [mock.patch.object(express, "masters", masters, create=True),
                   mock.patch.object(express, "push", push, create=True),
                   mock.patch.dict(sys.modules, {"pearnly_erp_portal.express.masters": masters,
                                                 "pearnly_erp_portal.express.push": push}),
                   mock.patch.object(live, "_receivables", lambda company, book: self.receivables.append(company)),
                   mock.patch.object(live, "_current", lambda row: {"CO-1": {"key": "k1"}, "CO-2": {"key": "k2"},
                                                                    "CO-OFF": {"key": "k1"}}.get(row.name)),
                   mock.patch.object(frappe.db, "get_value", lambda *a, **k: 0, create=True)]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_master_kinds_read_now_and_receivables_refresh(self):
        companies = {"k1": [(types.SimpleNamespace(name="CO-1"), {"key": "k1"}),
                            (types.SimpleNamespace(name="CO-OFF"), {"key": "k1"})],
                     "k2": [(types.SimpleNamespace(name="CO-2"), {"key": "k2"})]}
        with mock.patch.object(self.live, "_companies_of", lambda key: companies.get(key, [])):
            self.live.ledger_changed("PCD-1", {"k1": {L.CUSTOMER, L.RECEIVABLE}, "k2": {L.RECEIVABLE}, "k9": {L.STOCK}})
        self.assertEqual(self.kicked, ["CO-1"])          # 断开的公司不排
        self.assertEqual(self.receivables, ["CO-1", "CO-2"])

    def test_the_watch_list_is_the_current_book_of_each_linked_company(self):
        self.assertEqual(self.live.watch_books("PCD-1"), ["k1", "k2"])  # CO-3 没有现用账套（同期几份没选过）


class MrerpSyncButton(unittest.TestCase):
    """公司页「同步 MR.ERP」（同「同步 Express」，管理员）：人点的入口——排整读、清被踢标记照常登录、做完推给本人。"""

    def test_the_mrerp_sync_button_reads_as_a_manual_entry(self):
        source = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/mrerp/api.py").read_text(encoding="utf-8")
        body = source[source.index("def sync_company("):source.index("def detach(")]
        self.assertIn("connection.require_admin()", body)  # 服务端拦，不靠前端隐藏
        self.assertIn("masters.start(company)", body)
        masters = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/mrerp/masters.py").read_text(encoding="utf-8")
        start = masters[masters.index("def start("):masters.index("def probe_soon(")]
        self.assertIn("queue(company, notify=frappe.session.user, manual=True)", start)
        page = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/company.js").read_text(encoding="utf-8")
        self.assertIn('MRERP + "sync_company"', page)
        self.assertIn('t("company.syncMrerp")', page)

    def test_the_warm_read_after_a_probe_never_logs_in(self):
        masters = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/mrerp/masters.py").read_text(encoding="utf-8")
        probe = masters[masters.index("def probe_soon("):masters.index("def run(")]
        self.assertIn("master_export.probe(lease", probe)
        self.assertIn("manual=False, warm=True", probe)
        self.assertIn("login=not warm", masters)


if __name__ == "__main__":
    unittest.main()
