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


class SameContract(unittest.TestCase):
    def test_reasons_levels_and_states_match_the_desktop(self):
        self.assertEqual(SERVER.REASONS, CLIENT.REASONS)
        self.assertEqual(SERVER.BATCH_STOPPERS, CLIENT.BATCH_STOPPERS)
        self.assertEqual(SERVER.DONE_STATES, CLIENT.DONE_STATES)
        self.assertEqual(SERVER.ENTRY_MODE, CLIENT.ENTRY_MODE)
        for name in ("PENDING", "ENTERING", "SAVED", "VOIDED", "WAITING", "SKIPPED", "BATCH_READY", "BATCH_RUNNING",
                     "BATCH_PAUSED", "BATCH_STOPPED", "BATCH_FINISHED", "AUTO", "AUTO_REPORTED", "HUMAN"):
            self.assertEqual(getattr(SERVER, name), getattr(CLIENT, name), name)

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

    def test_bad_batch_refused(self):
        for batch in ({"id": "", "index": 1, "total": 1}, {"id": "b", "index": 0, "total": 1},
                      {"id": "b", "index": 3, "total": 2}):
            with self.assertRaises(ValueError):
                SERVER.for_operator({}, batch)


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
