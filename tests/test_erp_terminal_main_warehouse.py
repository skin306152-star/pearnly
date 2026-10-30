"""收银台挂主仓（10-30：接 Express 的公司同步出 7 个仓，默认收银台不带仓，第一次收银就报「收银台没有仓」）。

Run with Frappe installed. No Site, Redis or network: the database calls are replaced.
- 默认收银台建时挂主仓（`warehouses.main_of`，最先建的启用仓）；给了仓用给的；
- `attach_main`：没挂仓、挂着停用 / 别家的仓的收银台改挂主仓，挂着本公司启用仓的不动，主仓都没有时不动；
- 每一处建收银台的代码都显式给仓（防再犯）。
"""
import pathlib
import re
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]


class TerminalMainWarehouse(unittest.TestCase):
    def setUp(self):
        from pearnly_erp_portal.cashier import terminals

        self.terminals = terminals
        self.saved = {}
        self.rows = [{"name": "T-1", "warehouse": ""}, {"name": "T-2", "warehouse": "WH-OFF"},
                     {"name": "T-3", "warehouse": "WH-2"}, {"name": "T-4", "warehouse": "WH-OTHER"}]

        def get_all(doctype, filters=None, **kwargs):
            if doctype == "ERP Warehouse":
                return ["WH-1", "WH-2"]
            return [terminals.frappe._dict(row) for row in self.rows]

        db = mock.MagicMock()
        db.set_value.side_effect = lambda doctype, name, field, value: self.saved.__setitem__(name, value)
        for patcher in (mock.patch.object(terminals.frappe, "get_all", side_effect=get_all),
                        mock.patch.object(terminals.frappe, "db", db),
                        mock.patch.object(terminals.warehouses, "main_of", return_value="WH-1")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_terminals_without_an_active_own_warehouse_move_to_the_main_one(self):
        changed = self.terminals.attach_main("CO-1")
        self.assertEqual(changed, ["T-1", "T-2", "T-4"])
        self.assertEqual(self.saved, {"T-1": "WH-1", "T-2": "WH-1", "T-4": "WH-1"})  # T-3 挂着本公司启用仓，不动

    def test_nothing_changes_without_a_main_warehouse(self):
        with mock.patch.object(self.terminals.warehouses, "main_of", return_value=None):
            self.assertEqual(self.terminals.attach_main("CO-1"), [])
        self.assertEqual(self.saved, {})

    def test_every_terminal_insert_names_its_warehouse(self):
        """建 ERP POS Terminal 的每一处都显式给仓（主仓 / 调用方给的仓），不再留空等卖货时报错。"""
        found = []
        for path in sorted((ROOT / "apps").rglob("*.py")):
            if "/test" in str(path) or "/patches/" in str(path):
                continue
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r'"doctype": DOCTYPE, "terminal_name"', text):
                body = text[match.start():match.start() + 600]
                found.append(str(path.relative_to(ROOT)))
                self.assertRegex(body, r'"warehouse": (warehouse or warehouses\.main_of|place|warehouses\.required)', f"{path} 建收银台没给仓")
        self.assertTrue(found)


if __name__ == "__main__":
    unittest.main()
