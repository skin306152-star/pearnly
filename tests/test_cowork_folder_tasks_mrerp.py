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
desktop_update = _load("connections_desktop_update_mrerp", APP / "connections/desktop_update.py", {})


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
    """MR.ERP 任务：付款方式口径；读文件夹的可以是它自己配的小助手，也可以借用同一工作空间里 Express 的小助手。"""

    ROOT_A, ROOT_B = "a" * 16, "b" * 16

    def _rules(self, adapter):
        connections = {"C1": Row(name="C1", adapter=adapter, workspace="W", client="", binding_generation=0, label=adapter),
                       "EX": Row(name="EX", adapter="express", workspace="W", client="", binding_generation=0, label="Express")}
        db = FakeDB({"Cowork Folder Intake Rule": {}})
        db.sql = lambda query, values=None, as_dict=False: [[0]] if "COUNT(*)" in query else []
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

        stub = types.SimpleNamespace
        frappe = _frappe(db, get_doc=lambda doctype, name: connections[name], new_doc=lambda doctype: Doc(),
                         session=stub(user="owner@example.com"))
        frappe.db.get_value = lambda doctype, name, field: "{}" if field == "report_json" else connections[name].get(field)
        group = {"label": "Company", "items": [{"value": "Y1", "label": "2569"}]}
        masters = mock.Mock(return_value={})
        agents = {"C1": {"roots": [{"id": self.ROOT_A, "label": "D:\\Bills"}], "version": "1.3.23"},
                  "EX": {"roots": [{"id": self.ROOT_B, "label": "S:\\Scans"}], "version": "1.3.23"}}
        modules = {"frappe": frappe, "frappe.utils": stub(now_datetime=lambda: "2026-10-09 10:00:00"),
                   "pearnly_cowork": types.ModuleType("pearnly_cowork"),
                   "pearnly_cowork.access": stub(scope=stub(require=lambda *a: None)),
                   "pearnly_cowork.companion": stub(devices=stub(reporter_label=lambda connection: "")),
                   "pearnly_cowork.connections": stub(
                       access=stub(require_connection=lambda doc: None), desktop_update=desktop_update,
                       preparation=stub(reported_masters=masters),
                       service=stub(targets=lambda name: {"groups": [group]}, stock_acc_group=lambda *a: {},
                                    records=lambda *a: [dict(row) for row in connections.values()],
                                    ensure_account_masters=lambda *a: {"state": "ready"})),
                   "pearnly_cowork.folder_intake": stub(
                       agent=stub(read=lambda name: agents.get(name, {}), fresh=lambda state: True),
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
                "root_id": self.ROOT_A, "folder": "", "run_day": 1, "run_time": "22:00", **extra}

    def test_mrerp_task_keeps_payment_and_no_posting_kind_or_defaults(self):
        rules, saved, masters = self._rules("mrerp")
        rules.save(json.dumps(self.values(payment="credit", posting_kind="stock", defaults={"payment": "cash"})))
        row = saved[-1]
        self.assertEqual((row["payment"], row["posting_kind"], json.loads(row["defaults_json"]), row["reader"]),
                         ("credit", "", {}, "C1"))
        masters.assert_not_called()  # 不读 Express 主档

    def test_mrerp_task_can_borrow_the_express_helper(self):
        rules, saved, _masters = self._rules("mrerp")
        rules.save(json.dumps(self.values(payment="credit", reader="EX", root_id=self.ROOT_B)))
        self.assertEqual((saved[-1]["connection"], saved[-1]["reader"]), ("C1", "EX"))
        view = rules.options("C1")
        self.assertEqual([reader["connection"] for reader in view["readers"]], ["C1", "EX"])
        with self.assertRaises(Exception):  # 文件夹不是那台小助手的
            rules.save(json.dumps(self.values(payment="credit", reader="EX", root_id=self.ROOT_A)))

    def test_mrerp_purchase_cash_and_missing_payment_are_refused(self):
        rules, saved, _masters = self._rules("mrerp")
        for payment in ("cash", "", None):
            with self.assertRaises(Exception):
                rules.save(json.dumps(self.values(payment=payment)))
        rules.save(json.dumps(self.values(direction="sales", payment="cash")))
        self.assertEqual(saved[-1]["payment"], "cash")

    def test_express_task_reads_only_on_its_own_helper(self):
        rules, saved, _masters = self._rules("express")
        with self.assertRaises(Exception):
            rules.save(json.dumps(self.values()))
        rules.save(json.dumps(self.values(posting_kind="service", payment="cash")))
        self.assertEqual((saved[-1]["posting_kind"], saved[-1]["payment"], saved[-1]["reader"]), ("service", "", "C1"))
        self.assertEqual([reader["connection"] for reader in rules.options("C1")["readers"]], ["C1"])
        with self.assertRaises(Exception):
            rules.save(json.dumps(self.values(posting_kind="service", reader="EX", root_id=self.ROOT_B)))


class Reader(unittest.TestCase):
    def test_machine_side_follows_the_reader(self):
        source = (APP / "folder_intake/machine.py").read_text()
        self.assertIn("if (rule.reader or rule.connection) != context.connection:", source)
        self.assertIn("IFNULL(NULLIF(reader, ''), connection) = %s", source)
        self.assertIn('"connection": context.connection, "run": run', source)
        patch = (APP / "patches/v0_0_14_folder_task_reader.py").read_text()
        self.assertIn("SET reader = connection WHERE IFNULL(reader, '') = ''", patch)
        self.assertIn("pearnly_cowork.patches.v0_0_14_folder_task_reader", (APP / "patches.txt").read_text())


class Probe(unittest.TestCase):
    def test_probe_answers_the_connection_kind_without_touching_anything(self):
        source = (APP / "folder_intake/machine.py").read_text()
        body = source[source.index("def _sync(context, body):"):source.index("def _record_run(")]
        probe = body.index('if body.get("probe") is True:')
        self.assertLess(probe, body.index("agent.remember("))
        self.assertIn('return {"server_time": time.time(), "rules": [], "entries": {}, **identity}', body)
        # 2026-10-12 起回复另带新版提示与提醒（`companion_latest` / `notices`），身份两项照旧在每一次回复里
        # 2026-10-15 起（ADR-020）另带根目录上限与还被用着的根目录（`max_roots` / `roots_in_use`）
        self.assertIn('reply = {"server_time": time.time(), "rules": rules, "entries": entries, "max_roots": '
                      'contracts.MAX_ROOTS,\n             **_in_use(context.connection), **identity}', body)
        self.assertLess(probe, body.index('if body.get("roots_only") is True:'))
        self.assertIn("return reply", body)


if __name__ == "__main__":
    unittest.main()
