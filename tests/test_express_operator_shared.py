"""代录 Express 共用层（多产品共用）：纯规则与 CC 桌面版那份一字不差；对比报告三种方式并排、缺数据不估。

    python3 tests/test_express_operator_shared.py
"""
import importlib.util
import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SERVER = _load("op_server_contracts", ROOT / "apps/pearnly_core/pearnly_core/express_operator/contracts.py")
CLIENT = _load("op_client_contract", ROOT / "companion/src/companion/express_operator/contract.py")
REPORT = _load("op_report", ROOT / "apps/pearnly_core/pearnly_core/express_operator/report.py")
PAYLOAD = _load("op_client_payload_contract", ROOT / "companion/src/companion/payload_contract.py")
ROUTE = _load("op_route_contracts", ROOT / "apps/pearnly_core/pearnly_core/cc_desktop/wire/route_contracts.py")


class SameContract(unittest.TestCase):
    def test_reasons_levels_and_states_match_the_desktop(self):
        self.assertEqual(SERVER.REASONS, CLIENT.REASONS)
        self.assertEqual(SERVER.BATCH_STOPPERS, CLIENT.BATCH_STOPPERS)
        self.assertEqual(SERVER.DONE_STATES, CLIENT.DONE_STATES)
        self.assertEqual(SERVER.ENTRY_MODE, CLIENT.ENTRY_MODE)
        for name in ("PENDING", "ENTERING", "SAVED", "VOIDED", "WAITING", "SKIPPED", "BATCH_READY", "BATCH_RUNNING",
                     "BATCH_PAUSED", "BATCH_STOPPED", "BATCH_FINISHED", "AUTO", "AUTO_REPORTED", "HUMAN"):
            self.assertEqual(getattr(SERVER, name), getattr(CLIENT, name), name)

    def test_targets_match_the_desktop_and_the_routing_rule(self):
        """执行目标（ADR-026）：两端同值；派单纯规则认的 Express 与契约同一个；载荷 18 两端同号、是 CC 认的最高版本。"""
        self.assertEqual((SERVER.TARGET_EXPRESS, SERVER.TARGET_MRERP), ("express", "mrerp"))
        self.assertEqual(SERVER.TARGETS, CLIENT.TARGETS)
        self.assertEqual((SERVER.TARGET_EXPRESS, SERVER.TARGET_MRERP), (CLIENT.TARGET_EXPRESS, CLIENT.TARGET_MRERP))
        self.assertEqual(ROUTE.EXPRESS, SERVER.TARGET_EXPRESS)
        self.assertEqual(SERVER.MRERP_OPERATOR_PAYLOAD_VERSION, PAYLOAD.MRERP_OPERATOR_PAYLOAD_VERSION)
        self.assertEqual(SERVER.MRERP_OPERATOR_PAYLOAD_VERSION, max(PAYLOAD.SUPPORTED_PAYLOAD_VERSIONS))
        self.assertGreater(SERVER.MRERP_OPERATOR_PAYLOAD_VERSION, PAYLOAD.REMARKS_PAYLOAD_VERSION)
        # 这一版 CC 只报 Express（MR.ERP 执行层随第 2 步）：心跳的写法过派单规则的净化不变
        self.assertEqual(ROUTE.stored_targets(list(PAYLOAD.OPERATOR_TARGETS)), "express")
        for payload in ({}, {"operator": {}}, {"operator": {"target": "mrerp"}}, {"operator": {"target": "x"}}, "x", None):
            self.assertEqual(SERVER.target_of(payload), CLIENT.target_of(payload), payload)

    def test_shared_layer_header(self):
        for path in (ROOT / "apps/pearnly_core/pearnly_core/express_operator").glob("*.py"):
            self.assertIn("多产品共用", path.read_text(encoding="utf-8").split('"""')[1], path.name)


class OperatorPayload(unittest.TestCase):
    def test_an_operator_payload_is_recognized_from_the_dict_or_the_stored_text(self):
        marked = SERVER.for_operator({"payload_version": 9}, {"id": "b1", "index": 1, "total": 1, "start_at": 0})
        self.assertTrue(SERVER.is_operator(marked))
        self.assertTrue(SERVER.is_operator(json.dumps(marked)))
        for other in ({"payload_version": 9}, "{}", "", None, "not json", '["operator"]'):
            self.assertFalse(SERVER.is_operator(other))

    def test_direct_payload_plus_operator_marker_and_version_16(self):
        payload = {"doctype": "RR", "payload_version": 4, "items": [{"code": "A"}]}
        out = SERVER.for_operator(payload, {"id": "b1", "index": 2, "total": 3, "start_at": 0}, vat_included=True)
        self.assertEqual(out["entry_mode"], "operator")
        self.assertEqual(out["payload_version"], 16)
        self.assertEqual(out["operator"], {"batch": {"id": "b1", "index": 2, "total": 3, "start_at": 0.0},
                                           "vat_included": True})
        self.assertEqual(out["items"], payload["items"])
        self.assertNotIn("entry_mode", payload)  # 不改原载荷
        # 站点上这张单的编号随批次信息带过去（CC 用它当单据号，页面对得上是哪一张）
        doc = SERVER.for_operator(payload, {"id": "b1", "index": 1, "total": 1, "doc": "CWH-1"})
        self.assertEqual(doc["operator"]["batch"]["doc"], "CWH-1")
        client = _load("op_client_payload", ROOT / "companion/src/companion/payload_contract.py")
        self.assertEqual(client.OPERATOR_PAYLOAD_VERSION, SERVER.OPERATOR_PAYLOAD_VERSION)
        self.assertEqual(client.ENTRY_OPERATOR, SERVER.ENTRY_MODE)
        self.assertIsNone(client.check_payload_version(out))

    # 改动前（a5ea574f 的 for_operator）对同一份输入出的载荷，排序键与不排序键各一份：Express 目标必须逐字节相同
    GOLDEN_INPUT = {"direction": "purchase", "doctype": "RR", "payload_version": 17, "docdate_be": "691020", "ref_no": "INV-7",
                    "supplier": {"code": "P001", "name": "บริษัท ทดสอบ"},
                    "items": [{"code": "501780", "qty": "2", "unit_price": "10.00", "remarks": ["VIN-1"]}],
                    "account_uid": "vsn:1234/5"}
    GOLDEN = [
        ({"id": "b1", "index": 1, "total": 2, "start_at": 0, "doc": "CWH-1", "company": "C-1"}, True, True,
         '{"account_uid": "vsn:1234/5", "direction": "purchase", "docdate_be": "691020", "doctype": "RR", "entry_mode": "operator", '
         '"items": [{"code": "501780", "qty": "2", "remarks": ["VIN-1"], "unit_price": "10.00"}], "operator": {"batch": '
         '{"company": "C-1", "doc": "CWH-1", "id": "b1", "index": 1, "start_at": 0.0, "total": 2}, "vat_included": true}, '
         '"payload_version": 17, "ref_no": "INV-7", "supplier": {"code": "P001", "name": "บริษัท ทดสอบ"}}'),
        ({"id": "task9", "index": 2, "total": 2, "start_at": 1762000000.5}, False, True,
         '{"account_uid": "vsn:1234/5", "direction": "purchase", "docdate_be": "691020", "doctype": "RR", "entry_mode": "operator", '
         '"items": [{"code": "501780", "qty": "2", "remarks": ["VIN-1"], "unit_price": "10.00"}], "operator": {"batch": '
         '{"id": "task9", "index": 2, "start_at": 1762000000.5, "total": 2}, "vat_included": false}, "payload_version": 17, '
         '"ref_no": "INV-7", "supplier": {"code": "P001", "name": "บริษัท ทดสอบ"}}'),
        ({"id": "b1", "index": 1, "total": 2, "start_at": 0, "doc": "CWH-1", "company": "C-1"}, True, False,
         '{"direction": "purchase", "doctype": "RR", "payload_version": 17, "docdate_be": "691020", "ref_no": "INV-7", '
         '"supplier": {"code": "P001", "name": "บริษัท ทดสอบ"}, "items": [{"code": "501780", "qty": "2", "unit_price": "10.00", '
         '"remarks": ["VIN-1"]}], "account_uid": "vsn:1234/5", "entry_mode": "operator", "operator": {"batch": {"id": "b1", '
         '"index": 1, "total": 2, "start_at": 0.0, "doc": "CWH-1", "company": "C-1"}, "vat_included": true}}'),
    ]

    def test_an_express_payload_is_byte_for_byte_what_it_was_before_targets(self):
        """代录目标分路第 1 步对 Express 零变化：默认、显式 ``target=express``、批次里多带 ``target``（``products.submit`` 加的）
        三种写法出的载荷与改动前逐字节相同，没有 ``target`` 键、版本照旧。"""
        for batch, vat, sort, golden in self.GOLDEN:
            for kwargs, extra in (({}, {}), ({"target": "express"}, {}), ({}, {"target": "express"}),
                                  ({}, {"target": "mrerp"})):  # 批次里的 target 不影响载荷：目标只认参数
                out = SERVER.for_operator(dict(self.GOLDEN_INPUT), {**batch, **extra}, vat_included=vat, **kwargs)
                self.assertEqual(json.dumps(out, ensure_ascii=False, sort_keys=sort), golden, (kwargs, extra))
        small = SERVER.for_operator({"payload_version": 4, "items": []}, {"id": "x", "index": 1, "total": 1})
        self.assertEqual(json.dumps(small, sort_keys=True), '{"entry_mode": "operator", "items": [], "operator": {"batch": '
                         '{"id": "x", "index": 1, "start_at": 0.0, "total": 1}, "vat_included": false}, "payload_version": 16}')

    def test_an_mrerp_payload_carries_its_target_and_version_18(self):
        """MR.ERP 目标：``operator.target = mrerp``、版本至少 18；这一版 CC 认得（宿主按目标分路），只认到 17 的旧版诚实拒收。"""
        out = SERVER.for_operator({"doctype": "RR", "payload_version": 4, "items": []},
                                  {"id": "b1", "index": 1, "total": 1, "doc": "CWH-1"}, target="mrerp")
        self.assertEqual(out["operator"], {"batch": {"id": "b1", "index": 1, "total": 1, "start_at": 0.0, "doc": "CWH-1"},
                                           "vat_included": False, "target": "mrerp"})
        self.assertEqual((out["entry_mode"], out["payload_version"]), ("operator", 18))
        self.assertTrue(SERVER.is_operator(out))
        self.assertEqual((SERVER.target_of(out), CLIENT.target_of(out)), ("mrerp", "mrerp"))
        self.assertIsNone(PAYLOAD.check_payload_version(out))
        older = frozenset(range(1, 18))  # 1.12.11 及更早
        self.assertNotIn(out["payload_version"], older)
        with self.assertRaises(ValueError):
            SERVER.for_operator({}, {"id": "b1", "index": 1, "total": 1}, target="flowaccount")

    def test_bad_batch_refused(self):
        for batch in ({"id": "", "index": 1, "total": 1}, {"id": "b", "index": 0, "total": 1},
                      {"id": "b", "index": 3, "total": 2}):
            with self.assertRaises(ValueError):
                SERVER.for_operator({}, batch)


class ProductTarget(unittest.TestCase):
    """产品钩子（``products``）：这家公司录到哪种账本由产品声明，没声明 = Express；交单时目标带进批次信息。"""

    def setUp(self):
        import types

        self.hooks, self.calls = {}, []

        class Invalid(Exception):
            pass

        frappe = types.ModuleType("frappe")
        frappe.get_hooks = lambda name: self.hooks if name == "pearnly_express_operator" else {}
        frappe.get_attr = lambda path: {"t.target": lambda company: {"CO-M": "mrerp", "CO-X": "flowaccount"}.get(company),
                                         "t.submit": lambda *a: self.calls.append(a) or []}[path]
        frappe._ = lambda text: text

        def throw(message, exc=None):
            raise Invalid(message)

        frappe.throw, self.Invalid = throw, Invalid
        self.saved = {name: sys.modules.get(name) for name in ("frappe", "pearnly_core", "pearnly_core.express_operator",
                                                               "pearnly_core.express_operator.contracts")}
        package = types.ModuleType("pearnly_core")
        package.__path__ = [str(ROOT / "apps/pearnly_core/pearnly_core")]
        operator = types.ModuleType("pearnly_core.express_operator")
        operator.__path__ = [str(ROOT / "apps/pearnly_core/pearnly_core/express_operator")]
        operator.contracts = SERVER
        sys.modules.update({"frappe": frappe, "pearnly_core": package, "pearnly_core.express_operator": operator,
                            "pearnly_core.express_operator.contracts": SERVER})
        self.products = _load("op_products", ROOT / "apps/pearnly_core/pearnly_core/express_operator/products.py")

    def tearDown(self):
        for name, module in self.saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_no_declaration_is_express_and_a_declared_target_rides_in_the_batch(self):
        self.assertEqual(self.products.target_of("CO-1"), "express")
        self.hooks.update(submit="t.submit")
        self.products.submit("CO-1", ["h1"], {"id": "b1", "total": 1, "start_at": 0}, "CCD-1", None)
        self.assertEqual(self.calls[-1][2], {"id": "b1", "total": 1, "start_at": 0, "target": "express"})
        self.hooks.update(target="t.target")
        self.assertEqual(self.products.target_of("CO-1"), "express")  # 产品回空 = Express
        self.assertEqual(self.products.target_of("CO-M"), "mrerp")
        self.products.submit("CO-M", ["h1"], {"id": "b2", "total": 1, "start_at": 0}, "CCD-1", None)
        self.assertEqual(self.calls[-1][2]["target"], "mrerp")
        with self.assertRaises(self.Invalid):
            self.products.target_of("CO-X")  # 认不出的目标拦下，不悄悄当 Express 录
        self.hooks.pop("submit")
        self.assertEqual(self.products.submit("CO-1", ["h1"], {"id": "b3"}, "", None), [])


class Comparison(unittest.TestCase):
    def runs(self):
        D = REPORT.DocResult
        operator = REPORT.Run("operator", [
            D("a", "RR", 30, True, cost_thb=Decimal("0.35")),
            D("b", "HS", 26, True, cost_thb=Decimal("0.35")),
            D("c", "RR", 40, None, True, "party_missing", placeholders=1, cost_thb=Decimal("0.05")),
        ], machine_hours=0.1)
        direct = REPORT.Run("direct", [D("a", "RR", 0.5, True, cost_thb=Decimal("0.30")),
                                       D("b", "HS", 0.5, False, cost_thb=Decimal("0.30")),
                                       D("c", "RR", 0.5, None, True, "party_missing", cost_thb=Decimal("0.30"))])
        manual = REPORT.Run("manual", [D("a", "RR", 120), D("b", "HS", 90), D("c", "RR", None)], baseline=True)
        return [manual, direct, operator]

    def test_three_methods_side_by_side(self):
        table = REPORT.compare(self.runs())
        self.assertEqual(table["methods"], ["operator", "direct", "manual"])
        self.assertTrue(table["same_docs"])
        rows = {r["metric"]: r["values"] for r in table["rows"]}
        self.assertEqual(rows["docs"], {"operator": 3, "direct": 3, "manual": 3})
        self.assertEqual(rows["docs_by_type"]["operator"], {"HS": 1, "RR": 2})
        self.assertEqual(rows["accurate"], {"operator": 2, "direct": 1, "manual": None})  # 手录是基准，不自评
        self.assertEqual(rows["accuracy"]["operator"], Decimal("100.0"))
        self.assertEqual(rows["human"], {"operator": 1, "direct": 1, "manual": None})
        self.assertEqual(rows["human_by_reason"]["operator"], {"party_missing": 1})
        self.assertEqual(rows["placeholders"], {"operator": 1, "direct": None, "manual": None})
        self.assertEqual(rows["cost_thb"]["operator"], Decimal("0.75"))
        self.assertEqual(rows["seconds_total"], {"operator": 96, "direct": 1.5, "manual": None})  # 手录少一张的用时：不估
        self.assertEqual(rows["machine_hours"], {"operator": 0.1, "direct": None, "manual": None})

    def test_different_document_sets_are_flagged(self):
        runs = self.runs()
        runs[0].docs.pop()
        self.assertFalse(REPORT.compare(runs)["same_docs"])


if __name__ == "__main__":
    unittest.main()
