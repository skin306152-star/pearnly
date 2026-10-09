"""定时任务加 MR.ERP（2026-10-09）：与「新建录入」同一口径 —— MR.ERP 选付款方式（进项只有赊账）、没有过账类型与默认处理；
推送去向登记 MR.ERP（确认一步什么都不做）；小助手配对前可以只问连接类型（probe，不改任何东西）。不连库、不依赖 Frappe。"""
import importlib.util
import json
import pathlib
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_cowork/pearnly_cowork"
sys.path.insert(0, str(ROOT / "tests"))
from test_cowork_delete_everything import FakeDB, Row, _frappe, _load  # noqa: E402

contracts = _load("folder_contracts_mrerp", APP / "folder_intake/contracts.py", {})


class Payments(unittest.TestCase):
    def test_mrerp_purchase_is_credit_only(self):
        self.assertEqual(contracts.MRERP_PAYMENTS, {"purchase": ("credit",), "sales": ("cash", "credit")})


class Destinations(unittest.TestCase):
    def test_mrerp_is_a_destination_with_nothing_to_confirm(self):
        stub = types.SimpleNamespace
        frappe = stub(db=stub(commit=lambda: None), clear_messages=lambda: None)
        modules = {"frappe": frappe, "pearnly_cowork": types.ModuleType("pearnly_cowork"),
                   "pearnly_cowork.companion": stub(credentials=stub()),
                   "pearnly_cowork.push.application": stub(service=stub())}
        destinations = _load("folder_destinations_mrerp", APP / "folder_intake/destinations.py", modules)
        self.assertTrue(destinations.supported("mrerp") and destinations.supported("express"))
        self.assertTrue(destinations.online("mrerp", "C") and destinations.ready("mrerp", {}))
        self.assertIsNone(destinations.of("mrerp").confirm("C", {"name": "P"}, "k"))
        self.assertEqual(destinations.of("mrerp").intake_target, "mrerp")


class SaveMrerpTask(unittest.TestCase):
    def _rules(self, adapter):
        db = FakeDB({"Cowork ERP Connection": {"C1": {"adapter": adapter, "workspace": "W", "client": "",
                                                       "binding_generation": 0}},
                     "Cowork Folder Intake Rule": {}})
        saved = []

        class Doc(Row):
            def is_new(self):
                return True

            def update(self, values):
                dict.update(self, values)

            def __setattr__(self, key, value):
                self[key] = value

        def write(doc):
            doc["name"] = "R1"
            saved.append(dict(doc))
            db.tables["Cowork Folder Intake Rule"]["R1"] = Row(doc)

        stub = types.SimpleNamespace
        connection = Row(name="C1", adapter=adapter, workspace="W", client="", binding_generation=0)
        frappe = _frappe(db, get_doc=lambda doctype, name: connection, new_doc=lambda doctype: Doc(),
                         session=stub(user="owner@example.com"))
        frappe.db.count = lambda doctype, filters: 0
        group = {"label": "Company", "items": [{"value": "Y1", "label": "2569"}]}
        masters = mock.Mock(return_value={})
        modules = {"frappe": frappe, "frappe.utils": stub(now_datetime=lambda: "2026-10-09 10:00:00"),
                   "pearnly_cowork": types.ModuleType("pearnly_cowork"),
                   "pearnly_cowork.access": stub(scope=stub(require=lambda *a: None)),
                   "pearnly_cowork.connections": stub(access=stub(require_connection=lambda doc: None),
                                                      preparation=stub(reported_masters=masters),
                                                      service=stub(targets=lambda name: {"groups": [group]},
                                                                   stock_acc_group=lambda *a: {})),
                   "pearnly_cowork.folder_intake": stub(
                       agent=stub(read=lambda name: {"roots": [{"id": "r" * 16, "label": "D:\\Invoices"}]}),
                       contracts=contracts, destinations=stub(supported=lambda adapter: adapter in ("express", "mrerp")),
                       inbox=stub()),
                   "pearnly_cowork.folder_intake.access": stub(FILE="F", RULE="Cowork Folder Intake Rule", RUN="U",
                                                               read=None, write=write),
                   "pearnly_cowork.targets.identity": stub(normalize_express_account_key=lambda v: v)}
        rules = _load(f"folder_rules_mrerp_{adapter}", APP / "folder_intake/rules.py", modules)
        rules.project = lambda rule: dict(rule)
        return rules, saved, masters

    def values(self, **extra):
        return {"title": "Bills", "connection": "C1", "account_set_key": "Y1", "direction": "purchase",
                "root_id": "r" * 16, "folder": "", "run_day": 1, "run_time": "22:00", **extra}

    def test_mrerp_task_keeps_payment_and_no_posting_kind_or_defaults(self):
        rules, saved, masters = self._rules("mrerp")
        rules.save(json.dumps(self.values(payment="credit", posting_kind="stock", defaults={"payment": "cash"})))
        row = saved[-1]
        self.assertEqual((row["payment"], row["posting_kind"], json.loads(row["defaults_json"])), ("credit", "", {}))
        masters.assert_not_called()  # 不读 Express 主档

    def test_mrerp_purchase_cash_and_missing_payment_are_refused(self):
        rules, saved, _masters = self._rules("mrerp")
        for payment in ("cash", "", None):
            with self.assertRaises(Exception):
                rules.save(json.dumps(self.values(payment=payment)))
        rules.save(json.dumps(self.values(direction="sales", payment="cash")))
        self.assertEqual(saved[-1]["payment"], "cash")

    def test_express_task_still_needs_a_posting_kind_and_has_no_payment(self):
        rules, saved, _masters = self._rules("express")
        with self.assertRaises(Exception):
            rules.save(json.dumps(self.values()))
        rules.save(json.dumps(self.values(posting_kind="service", payment="cash")))
        self.assertEqual((saved[-1]["posting_kind"], saved[-1]["payment"]), ("service", ""))


class Probe(unittest.TestCase):
    def test_probe_answers_the_connection_kind_without_touching_anything(self):
        source = (APP / "folder_intake/machine.py").read_text()
        body = source[source.index("def _sync(context, body):"):source.index("def _record_run(")]
        probe = body.index('if body.get("probe") is True:')
        self.assertLess(probe, body.index("agent.remember("))
        self.assertIn('return {"server_time": time.time(), "rules": [], "entries": {}, **identity}', body)
        self.assertIn('return {"server_time": time.time(), "rules": rules, "entries": entries, **identity}', body)


if __name__ == "__main__":
    unittest.main()
