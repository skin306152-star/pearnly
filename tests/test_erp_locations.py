"""仓库下的库位清单（第 5 项，主会话 10-26 审定）：内存替身，不连库。

- 没建清单的仓库位照旧手输；建了清单，库位只能从清单里（在用的）选，空库位照旧可以；
- 商品上的建议库位不在这个仓的清单里时留空，单据上明写的不在清单就拒；
- 保存清单：名称去空白、不能重；还有库存的不能停用；用过（有流水）或还有库存的不能删。
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
utils = sys.modules.setdefault("frappe.utils", types.ModuleType("frappe.utils"))
utils.flt = lambda value, *a: float(value or 0)
frappe.utils = utils

from pearnly_erp_portal.inventory import locations as L, positions as P  # noqa: E402


class Refused(Exception):
    pass


class Locations(unittest.TestCase):
    def setUp(self):
        self.lists = {"W1": [("A-01", 1), ("A-02", 1), ("OLD", 0)], "W2": []}
        self.stock = {("W1", "A-01"): 3.0}
        self.moved = {("W1", "A-02")}

        def get_all(doctype, filters=None, pluck=None, fields=None, **kw):
            if doctype == L.CHILD:
                rows = [{"location_name": name, "is_active": active} for name, active in self.lists.get(filters["parent"], [])
                        if "is_active" not in filters or active == filters["is_active"]]
                return [row[pluck] for row in rows] if pluck else [types.SimpleNamespace(**row) for row in rows]
            if doctype == "ERP Stock Balance":
                return [types.SimpleNamespace(location=loc, qty=qty) for (wh, loc), qty in self.stock.items()
                        if wh == filters["warehouse"]]
            return []

        self.saved = {}

        def get_doc(doctype, name):
            doc = types.SimpleNamespace(name=name, get=lambda field: [types.SimpleNamespace(location_name=n, is_active=a)
                                                                      for n, a in self.lists.get(name, [])])
            doc.set = lambda field, rows: self.saved.__setitem__(name, rows)
            doc.save = lambda ignore_permissions=False: None
            return doc

        db = types.SimpleNamespace(exists=lambda doctype, filters: bool(self.lists.get(filters["parent"])) if doctype == L.CHILD
                                   else (filters["warehouse"], filters["location"]) in self.moved, commit=lambda: None)
        for module in (L, P):
            for target, value in (("get_all", get_all), ("get_doc", get_doc), ("db", db), ("_", lambda text: text),
                                  ("throw", lambda message, *a: (_ for _ in ()).throw(Refused(message))),
                                  ("parse_json", lambda value: value)):
                patcher = mock.patch.object(module.frappe, target, value, create=True)
                patcher.start()
                self.addCleanup(patcher.stop)
        for target, value in (("require", lambda capability: None), ("has", lambda capability: True)):
            patcher = mock.patch.object(L.guard, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(L.warehouses, "required", lambda value, company=None: value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_warehouse_with_a_list_takes_only_listed_locations(self):
        self.assertEqual(P.check("W1", " A-01 "), "A-01")
        self.assertEqual(P.check("W1", ""), "")  # 不分库位照旧可以
        for value in ("B-09", "OLD"):  # 不在清单 / 已停用
            with self.assertRaisesRegex(Refused, "not in the location list"):
                P.check("W1", value)
        self.assertEqual(P.check("W2", "anything"), "anything")  # 没建清单照旧手输
        self.lists["W3"] = [("A-01", 0)]
        with self.assertRaisesRegex(Refused, "not in the location list"):
            P.check("W3", "ZZZ")  # 清单全部停用 = 一个都不能用（验收 10-26），不是不限制

    def test_saving_keeps_stocked_and_used_locations(self):
        with self.assertRaisesRegex(Refused, "with stock cannot be turned off"):
            L.api_save_locations("W1", [{"location_name": "A-01", "is_active": 0}, {"location_name": "A-02"}])
        with self.assertRaisesRegex(Refused, "has been used cannot be removed"):
            L.api_save_locations("W1", [{"location_name": "A-01"}])  # A-02 有流水，不能删
        with self.assertRaisesRegex(Refused, "Duplicate"):
            L.api_save_locations("W1", [{"location_name": "A-01"}, {"location_name": " A-01"}, {"location_name": "A-02"}])
        with mock.patch.object(L, "api_get_locations", lambda warehouse: "reloaded"):
            L.api_save_locations("W1", [{"location_name": "A-01"}, {"location_name": "A-02", "is_active": 0}])
        self.assertEqual(self.saved["W1"], [{"location_name": "A-01", "is_active": 1}, {"location_name": "A-02", "is_active": 0}])

    def test_options_search_this_warehouse_list(self):
        with mock.patch.object(L.frappe, "get_all", side_effect=lambda doctype, filters=None, **kw: (
                self.assertEqual((filters["parent"], filters["is_active"], filters["location_name"]), ("W1", 1, ["like", "%A%"]))
                or ["A-01", "A-02"])):
            self.assertEqual(L.api_location_options("A", "W1"), ["A-01", "A-02"])
        self.assertEqual(L.api_location_options("A", None), [])


if __name__ == "__main__":
    unittest.main()
