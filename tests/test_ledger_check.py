"""录过的票先核账本（用户 11-10「万一 exp 那边删了呢」；接口定稿已发 D 线）：共用入口 `pearnly_core.ledger_tickets.check_present`
按产品声明的 ``pearnly_ledger_check`` 分派、补齐；Cowork / ERP 各经自己的 CC 桌面版请求通道发 ``express_doc_check``。

锁住：每张都有一项（缺的、认不出的、产品抛错的都是 unknown，不报错）；没有电脑能做（不在线、CC 桌面版太旧还不认这种请求）不建请求、
整批 unknown；电脑读好了按单号回 present / gone；等到上限还没读好整批 unknown，并删掉请求；参数照 CC 桌面版的规格。
"""
import gzip
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "apps/pearnly_core/pearnly_core"


class _Throw(Exception):
    pass


def _frappe(hooks):
    frappe = types.ModuleType("frappe")
    frappe._ = lambda text: text
    frappe.throw = lambda message, exc=None: (_ for _ in ()).throw((exc or _Throw)(message))
    frappe.get_hooks = lambda name: hooks.get(name, {})
    frappe.get_attr = lambda path: frappe.attrs[path]
    frappe.attrs = {}
    frappe.logged = []
    frappe.log_error = lambda title=None: frappe.logged.append(title)
    frappe.session = types.SimpleNamespace(user="u@example.com")
    frappe.db = types.SimpleNamespace(commits=0, rollbacks=0)
    frappe.db.commit = lambda: setattr(frappe.db, "commits", frappe.db.commits + 1)
    frappe.db.rollback = lambda: setattr(frappe.db, "rollbacks", frappe.db.rollbacks + 1)
    return frappe


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ITEMS = [{"doc_id": "D1", "docnum": "RR6911-001", "direction": "purchase", "date": "2026-11-01"},
         {"doc_id": "D2", "docnum": "IV6911-002", "direction": "sales"}]


def _content(rows):
    return gzip.compress(json.dumps({"items": rows}).encode("utf-8"))


class Base(unittest.TestCase):
    hooks: dict = {}

    def setUp(self):
        self.frappe = _frappe({"pearnly_ledger_check": dict(self.hooks)})
        package = types.ModuleType("pearnly_core.ledger_tickets")
        package.__path__ = [str(CORE / "ledger_tickets")]
        patcher = mock.patch.dict(sys.modules, {"frappe": self.frappe, "pearnly_core": types.ModuleType("pearnly_core"),
                                                "pearnly_core.ledger_tickets": package})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.check = _load("pearnly_core.ledger_tickets.check", CORE / "ledger_tickets/check.py")
        for key in ("check_present", "parse_result", "request_params", "EXPRESS"):
            setattr(package, key, getattr(self.check, key))
        sys.modules["pearnly_core"].ledger_tickets = package


class SharedEntryTests(Base):
    hooks = {"express": "p.express", "mrerp": "d.mrerp"}

    def test_every_document_gets_a_state_and_anything_unclear_is_unknown(self):
        calls = []
        self.frappe.attrs["p.express"] = lambda *a, **k: calls.append((a, k)) or {
            "D1": {"state": "present", "docnum": "RR6911-001"}, "X": {"state": "present"}}
        out = self.check.check_present("EXP1", "K1", ITEMS, wait=600)
        self.assertEqual({d: v["state"] for d, v in out.items()}, {"D1": "present", "D2": "unknown"})
        self.assertEqual(out["D2"]["docnum"], "IV6911-002")
        self.assertRegex(out["D1"]["checked_at"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
        (args, kwargs), = calls
        self.assertEqual((args[:2], kwargs), (("EXP1", "K1"), {"ledger": "express", "wait": 60}))  # 最多等 60 秒

    def test_a_state_the_contract_does_not_know_and_a_failing_product_are_unknown_not_errors(self):
        self.frappe.attrs["p.express"] = lambda *a, **k: {"D1": {"state": "deleted"}, "D2": "present"}
        self.assertEqual({v["state"] for v in self.check.check_present("EXP1", "K1", ITEMS).values()}, {"unknown"})
        self.frappe.attrs["p.express"] = lambda *a, **k: 1 / 0
        self.assertEqual({v["state"] for v in self.check.check_present("EXP1", "K1", ITEMS).values()}, {"unknown"})
        self.assertEqual(self.frappe.logged, ["Ledger check failed"])

    def test_mrerp_goes_to_the_d_line_hook(self):
        self.frappe.attrs["d.mrerp"] = lambda c, k, items, ledger, wait: {i["doc_id"]: {"state": "present"} for i in items
                                                                           if ledger == "mrerp"}
        out = self.check.check_present("MR1", "12:3", [{**ITEMS[0], "menu": "454"}], ledger="mrerp")
        self.assertEqual(out["D1"]["state"], "present")

    def test_no_declared_channel_is_all_unknown(self):
        self.frappe.get_hooks = lambda name: {}
        self.assertEqual({v["state"] for v in self.check.check_present("EXP1", "K1", ITEMS).values()}, {"unknown"})

    def test_bad_requests_are_programming_errors(self):
        for items in ([], [{"doc_id": "D1", "docnum": "", "direction": "purchase"}],
                      [{"doc_id": "D1", "docnum": "A", "direction": "pos"}], ITEMS[:1] * 2,
                      [{"doc_id": f"D{i}", "docnum": "A", "direction": "sales"} for i in range(51)]):
            with self.assertRaises(_Throw):
                self.check.check_present("EXP1", "K1", items)
        with self.assertRaises(_Throw):
            self.check.check_present("EXP1", "K1", ITEMS, ledger="sap")

    def test_the_result_and_the_params_follow_the_cc_desktop_contract(self):
        self.assertEqual(self.check.parse_result(_content([{"doc_id": "D1", "docnum": "RR6911-001", "state": "gone"}])),
                         {"D1": {"state": "gone", "docnum": "RR6911-001"}})
        self.assertEqual(self.check.parse_result(b"not gzip"), {})
        params = self.check.request_params("\\\\srv\\69test", "D:\\", ITEMS)
        source = (ROOT / "companion/src/companion/express_doc_check.py").read_text(encoding="utf-8")
        self.assertIn('PARAM_KEYS = ("ledger", "account_set", "account_root", "items")', source)
        self.assertEqual(sorted(params), ["account_root", "account_set", "items", "ledger"])
        self.assertEqual(params["items"][0], {"doc_id": "D1", "docnum": "RR6911-001", "direction": "purchase"})


class _Channel(Base):
    """两个产品的通道：电脑那边由替身扮演（状态序列），时钟替身走得快。"""

    def make(self, module_name, path, extra):
        self.statuses, self.created, self.dropped = [], [], []
        self.clock = [0.0]
        modules = dict(extra)
        with mock.patch.dict(sys.modules, modules):
            module = _load(module_name, path)
        module.time = types.SimpleNamespace(monotonic=lambda: self.clock[0],
                                            sleep=lambda s: self.clock.__setitem__(0, self.clock[0] + s))
        return module


class CoworkChannelTests(_Channel):
    def setUp(self):
        super().setUp()
        self.waiting = {}
        service = types.SimpleNamespace(
            waiting=lambda connection, kind, **k: dict(self.waiting),
            create=lambda kind, connection, params, key, account_key="": self.created.append((kind, params, key, account_key))
            or {"request": "R1"},
            status=lambda name: {"status": self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]},
            result=lambda name: (_content([{"doc_id": "D1", "docnum": "RR6911-001", "state": "present"},
                                           {"doc_id": "D2", "docnum": "IV6911-002", "state": "gone"}]), {}),
            drop=lambda name: self.dropped.append(name))
        catalog = {"account_sets": [{"source_id": "\\\\SRV\\69TEST\\", "attributes": {"path": "\\\\srv\\69test", "root": "D:\\"}}]}
        connections = types.SimpleNamespace(adapter_of=lambda c: "express", targets=lambda c: catalog)
        identity = types.SimpleNamespace(normalize_express_account_key=lambda v: str(v or "").rstrip("\\").lower())
        self.channel = self.make("pearnly_cowork.readback.ledger_check", ROOT / "apps/pearnly_cowork/pearnly_cowork/readback/ledger_check.py", {
            "pearnly_cowork": types.ModuleType("pearnly_cowork"),
            "pearnly_cowork.companion": types.SimpleNamespace(request_service=service),
            "pearnly_cowork.connections": types.SimpleNamespace(service=connections),
            "pearnly_core.express_rules": types.ModuleType("pearnly_core.express_rules"),
            "pearnly_core.express_rules.targets": types.ModuleType("pearnly_core.express_rules.targets"),
            "pearnly_core.express_rules.targets.identity": identity})

    def test_the_computer_answers_present_and_gone(self):
        self.statuses = ["requested", "leased", "done"]
        out = self.channel.check("EXP1", "\\\\srv\\69test", ITEMS, wait=60)
        self.assertEqual(out, {"D1": {"state": "present", "docnum": "RR6911-001"}, "D2": {"state": "gone", "docnum": "IV6911-002"}})
        (kind, params, key, account_key), = self.created
        self.assertEqual((kind, params["account_set"], account_key), ("express_doc_check", "\\\\srv\\69test", "\\\\srv\\69test"))
        self.assertTrue(key.startswith("doc_check:"))
        self.assertEqual(self.dropped, ["R1"])
        self.assertGreaterEqual(self.frappe.db.commits, 1)  # 建了请求先提交，电脑才看得到

    def test_no_computer_that_can_do_it_creates_nothing(self):
        for reason in ("offline", "update_needed", "unreachable", "missing"):  # 旧版 CC 桌面版不认 express_doc_check：update_needed
            self.waiting = {"status": "waiting_for_cc", "reason": reason}
            self.assertEqual(self.channel.check("EXP1", "\\\\srv\\69test", ITEMS), {})
        self.assertEqual(self.created, [])

    def test_waiting_out_the_limit_gives_up_and_drops_the_request(self):
        self.statuses = ["leased"]
        self.assertEqual(self.channel.check("EXP1", "\\\\srv\\69test", ITEMS, wait=60), {})
        self.assertEqual(self.dropped, ["R1"])
        self.assertLessEqual(self.clock[0], 62)

    def test_an_account_set_not_in_the_catalog_is_not_asked(self):
        self.assertEqual(self.channel.check("EXP1", "\\\\srv\\other", ITEMS), {})
        self.assertEqual(self.created, [])


class ErpChannelTests(_Channel):
    def setUp(self):
        super().setUp()
        self.reason = None
        requests = types.SimpleNamespace(
            waiting=lambda kind, key, actor=None: self.reason,
            create=lambda kind, key, params, book_key, actor: self.created.append((kind, key, params, book_key, actor))
            or {"request": "Q1"},
            status=lambda name: {"status": self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]},
            result=lambda name: (_content([{"doc_id": "D2", "docnum": "IV6911-002", "state": "present"}]), {}),
            drop=lambda name: self.dropped.append(name))
        books = types.SimpleNamespace(find=lambda key: {"key": key, "path": "C:\\PearnlyExpress\\T", "root": "C:\\"}
                                      if key == "BK1" else None)
        self.channel = self.make("pearnly_erp_portal.express.ledger_check",
                                 ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/express/ledger_check.py",
                                 {"pearnly_core.cc_desktop": types.SimpleNamespace(books=books, requests=requests)})

    def test_the_computer_answers_by_document(self):
        self.statuses = ["requested", "done"]
        self.assertEqual(self.channel.check("CO-1", "BK1", ITEMS), {"D2": {"state": "present", "docnum": "IV6911-002"}})
        (kind, key, params, book_key, actor), = self.created
        self.assertEqual((kind, book_key, actor, params["account_set"]), ("express_doc_check", "BK1", "u@example.com",
                                                                           "C:\\PearnlyExpress\\T"))
        self.assertEqual(self.dropped, ["Q1"])

    def test_old_or_offline_computers_and_unknown_books_create_nothing(self):
        self.reason = {"reason": "update_needed"}
        self.assertEqual(self.channel.check("CO-1", "BK1", ITEMS), {})
        self.reason = None
        self.assertEqual(self.channel.check("CO-1", "OTHER", ITEMS), {})
        self.assertEqual(self.created, [])

    def test_a_failed_read_is_given_up(self):
        self.statuses = ["requested", "failed"]
        self.assertEqual(self.channel.check("CO-1", "BK1", ITEMS), {})
        self.assertEqual(self.dropped, ["Q1"])


class DeclarationTests(unittest.TestCase):
    def test_both_products_declare_their_channel_and_cowork_stores_the_kind(self):
        for path, target in (("apps/pearnly_cowork/pearnly_cowork/hooks.py", "pearnly_cowork.readback.ledger_check.check"),
                             ("apps/pearnly_erp_portal/pearnly_erp_portal/hooks.py", "pearnly_erp_portal.express.ledger_check.check")):
            self.assertIn(f'pearnly_ledger_check = {{"express": "{target}"}}', (ROOT / path).read_text(encoding="utf-8"))
        doctype = json.loads((ROOT / "apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_companion_request/"
                                      "cowork_companion_request.json").read_text(encoding="utf-8"))
        kind = next(f for f in doctype["fields"] if f["fieldname"] == "kind")
        self.assertIn("express_doc_check", kind["options"].split("\n"))


if __name__ == "__main__":
    unittest.main()
