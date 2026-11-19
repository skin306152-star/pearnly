"""仓库下的仓位清单（第 5 项，主会话 10-26 审定；11-19 起只给盘点记「实际仓位」）与单据行定仓：内存替身，不连库。

- 没建清单的仓仓位照旧手输；建了清单，盘点记的仓位只能从清单里（在用的）选，空仓位照旧可以；
- 保存清单：名称去空白、不能重；盘点里记过的不能删（停用）；库存只到仓库，仓位不再有库存、不按库存拦停用；
- 单据行定仓（11-19 撤掉一单一仓）：行上的仓为准，没写用单头默认仓，再没有只认唯一仓；别家公司的仓被拒。
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
        self.notes = {("W1", "A-01"): "前排货架"}  # 库位说明（用户 10-29）
        self.codes = {}  # 仓位编码（11-07）：{(仓, 仓位): 编码}
        self.stock = {("W1", "A-01"): 3.0}
        self.moved = {("W1", "A-02")}

        def get_all(doctype, filters=None, pluck=None, fields=None, or_filters=None, **kw):
            if doctype == L.CHILD:
                rows = [{"location_name": name, "is_active": active, "description": self.notes.get((filters["parent"], name)),
                         "location_code": self.codes.get((filters["parent"], name))}
                        for name, active in self.lists.get(filters["parent"], [])
                        if "is_active" not in filters or active == filters["is_active"]]
                if or_filters:  # 按名称或编码模糊搜（like %字%）
                    rows = [row for row in rows if any(op == "like" and value.strip("%").casefold() in (row[field] or "").casefold()
                                                       for field, (op, value) in or_filters.items())]
                return [row[pluck] for row in rows] if pluck else [types.SimpleNamespace(**row) for row in rows]
            if doctype == "ERP Stock Balance":  # 仓还有没有货（`locations.has_stock`，库存只到仓库）
                return [qty for (wh, _loc), qty in self.stock.items() if wh == filters["warehouse"] and qty][:1]
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
        # 库位下拉按仓自己的公司核（LINE 改仓 10-30）：这里只测库位，仓的公司替成固定一家。
        patcher = mock.patch.object(L.warehouses, "company_of", lambda warehouse: "CO-1")
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
        with self.assertRaisesRegex(Refused, "no active location"):
            P.check("W3", "ZZZ")  # 清单全部停用 = 没有可用库位，先启用一个（主会话 10-26），不是不限制

    def test_saving_keeps_locations_a_count_recorded(self):
        with mock.patch.object(L, "api_get_locations", lambda warehouse: "reloaded"):
            # 库存只到仓库：仓位没有库存，停用不按库存拦（11-19）
            L.api_save_locations("W1", [{"location_name": "A-01", "is_active": 0}, {"location_name": "A-02"}])
        self.assertEqual(self.saved["W1"][0]["is_active"], 0)
        with self.assertRaisesRegex(Refused, "has been used cannot be removed"):
            L.api_save_locations("W1", [{"location_name": "A-01"}])  # A-02 盘点里记过，不能删
        with self.assertRaisesRegex(Refused, "Duplicate"):
            L.api_save_locations("W1", [{"location_name": "A-01"}, {"location_name": " A-01"}, {"location_name": "A-02"}])
        with mock.patch.object(L, "api_get_locations", lambda warehouse: "reloaded"):
            L.api_save_locations("W1", [{"location_name": "A-01"}, {"location_name": "A-02", "is_active": 0}])
        self.assertEqual(self.saved["W1"], [{"location_code": "", "location_name": "A-01", "is_active": 1, "description": ""},
                                            {"location_code": "", "location_name": "A-02", "is_active": 0, "description": ""}])
        with mock.patch.object(L, "api_get_locations", lambda warehouse: "reloaded"):
            L.api_save_locations("W1", [{"location_name": "A-01", "description": " 前排货架 "}, {"location_name": "A-02"}])
        self.assertEqual(self.saved["W1"][0]["description"], "前排货架")

    def test_options_list_only_this_warehouse_active_locations(self):
        self.stock[("W1", "OLD-B")] = 2.0
        options = L.api_location_options("", "W1")
        self.assertEqual([row["value"] for row in options], ["A-01", "A-02"])  # 库存只到仓库：不再列有库存的旧仓位
        self.assertEqual(options[0]["label"], "A-01 · 前排货架")  # 没编码：「名称 · 说明」，值仍是仓位名称
        self.assertEqual(L.api_location_options("A", None), [])
        self.assertEqual(L.api_location_options("", "W2"), [])  # 没建清单：没有选项，照旧手输

    def test_location_codes_are_unique_within_a_warehouse_and_may_be_empty(self):
        """仓位编码（11-07）：同一仓库里不能重（不分大小写）、可空、多行空编码可以；最长 30；别的仓同编码可以。"""
        with mock.patch.object(L, "api_get_locations", lambda warehouse: "reloaded"):
            with self.assertRaisesRegex(Refused, "Duplicate location code: a1"):
                L.api_save_locations("W1", [{"location_name": "A-01", "location_code": "A1"},
                                            {"location_name": "A-02", "location_code": " a1 "}])
            with self.assertRaisesRegex(Refused, "Invalid value"):
                L.api_save_locations("W1", [{"location_name": "A-01", "location_code": "X" * 31}, {"location_name": "A-02"}])
            L.api_save_locations("W1", [{"location_name": "A-01", "location_code": " A1 "}, {"location_name": "A-02"},
                                        {"location_name": "A-03", "location_code": ""}])
            self.assertEqual([row["location_code"] for row in self.saved["W1"]], ["A1", "", ""])
            L.api_save_locations("W2", [{"location_name": "B-01", "location_code": "A1"}])  # 别的仓同编码可以
        rows = [types.SimpleNamespace(location_code=code) for code in ("A1", "", None, "B2")]
        self.assertEqual(L.duplicate_code(rows), "")
        self.assertEqual(L.duplicate_code(rows + [types.SimpleNamespace(location_code="b2")]), "b2")  # 仓库控制器同一判据

    def test_options_show_code_name_and_note_and_search_by_either(self):
        self.codes[("W1", "A-01")] = "A1"
        options = L.api_location_options("", "W1")
        self.assertEqual(options[0], {"value": "A-01", "label": "A1 · A-01 · 前排货架"})  # 「编码 · 名称 · 说明」，值仍是名称
        self.assertEqual(options[1], {"value": "A-02", "label": "A-02"})  # 没编码、没说明只写名称
        self.assertEqual([row["value"] for row in L.api_location_options("a1", "W1")], ["A-01"])  # 按编码搜
        self.assertEqual([row["value"] for row in L.api_location_options("02", "W1")], ["A-02"])  # 按名称搜

    def test_imports_and_line_take_a_code_for_its_location(self):
        self.codes.update({("W1", "A-01"): "A1", ("W1", "A-02"): "A-01X"})
        self.assertEqual(P.resolve("W1", " a1 "), "A-01")      # 编码 → 名称（不分大小写）
        self.assertEqual(P.resolve("W1", "A-02"), "A-02")      # 本来就是名称
        self.assertEqual(P.resolve("W1", "Z-9"), "Z-9")        # 都不是：照原样，交给清单核
        self.assertEqual(P.resolve("W2", "A1"), "A1")          # 别的仓没有这个编码
        self.codes[("W1", "A-02")] = "A-01"
        self.assertEqual(P.resolve("W1", "A-01"), "A-01")      # 名称与别的仓位的编码撞了：按名称
        self.assertEqual(P.label("A-01", "A1", ""), "A1 · A-01")

    def test_exports_have_no_book_location_and_counts_keep_the_actual_one(self):
        """库存只到仓库（11-19）：库存导出、盘点汇总表不再有仓位 / 仓位编码列；盘点明细照旧有「实际仓位」（货在哪找到的）。"""
        import ast

        source = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/inventory/api.py").read_text(encoding="utf-8")
        tree = {node.targets[0].id: ast.literal_eval(node.value) for node in ast.parse(source).body
                if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") in
                ("EXPORT_COLUMNS", "COUNT_COLUMNS", "COUNT_DETAIL_COLUMNS")}
        for name in ("EXPORT_COLUMNS", "COUNT_COLUMNS"):
            fields = [field for field, _key in tree[name]]
            self.assertFalse({"location", "location_code"} & set(fields), name)
        self.assertIn(("location", "stock.workbench.actualLocation"), tree["COUNT_DETAIL_COLUMNS"])
        self.assertNotIn("book_location", [field for field, _key in tree["COUNT_DETAIL_COLUMNS"]])
        self.assertFalse(hasattr(P, "with_codes"))

    def test_a_warehouse_with_stock_cannot_be_turned_off(self):
        """G16（多仓闭环 P0）：同库位停用，仓里还有货就不能停；没货的照停；只改名不核。"""
        W = L.warehouses
        docs = {name: types.SimpleNamespace(name=name, is_active=1, warehouse_name=name, note=None, save=lambda: None)
                for name in ("W1", "W2")}
        for target, value in (("get_doc", lambda doctype, name: docs[name]),
                              ("db", types.SimpleNamespace(exists=lambda doctype, name: name in docs, commit=lambda: None))):
            patcher = mock.patch.object(W.frappe, target, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(W.guard, "require", lambda capability: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        with self.assertRaisesRegex(Refused, "warehouse with stock cannot be turned off"):
            W.update_warehouse("W1", is_active=0)
        self.assertEqual(docs["W1"].is_active, 1)
        W.update_warehouse("W1", warehouse_name="W1 new")  # 不停用就不核库存
        W.update_warehouse("W2", is_active=0)  # 没有货：照停
        self.assertEqual(docs["W2"].is_active, 0)

    def test_the_line_warehouse_wins_and_the_header_is_only_a_default(self):
        """11-19 撤掉一单一仓（同 Express 原生明细行选仓）：行上的仓为准；没写的用单头默认仓；都没写只认唯一仓，多仓报错让人选；
        别家公司的仓被拒（行上的仓同样按单据公司核，原来只核单头）；仓位一概不再落到行上。"""
        product = types.SimpleNamespace(is_active=1)
        owners = {"W1": "CO-1", "W2": "CO-1", "X1": "CO-2"}
        only = {"CO-1": None, "CO-S": "S1"}  # CO-1 两个仓（没有唯一仓），CO-S 只有一个

        def required(value=None, company=None):
            if value:
                if owners.get(value) != company:
                    raise Refused("Warehouse not found")
                return value
            if not only.get(company):
                raise Refused("Pick a warehouse")
            return only[company]

        for target, value in (("receiving", required), ("required", required)):
            patcher = mock.patch.object(P.warehouses, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(P.frappe.db, "get_value", lambda *a, **k: product, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.assertEqual(P.header("W1", company="CO-1"), {"warehouse": "W1"})
        self.assertEqual(P.header("", company="CO-1"), {"warehouse": None})
        line = {"product": "P-1", "item_type": "goods"}
        self.assertEqual(P.prepare({**line, "warehouse": "W2"}, "W1", company="CO-1"), {**line, "warehouse": "W2"})
        self.assertEqual(P.prepare(line, "W1", company="CO-1"), {**line, "warehouse": "W1"})  # 没写：单头默认仓
        with self.assertRaisesRegex(Refused, "Pick a warehouse"):
            P.prepare(line, None, company="CO-1")  # 多仓公司库存行没有仓：拒，让人选
        self.assertEqual(P.prepare(line, None, company="CO-S"), {**line, "warehouse": "S1"})  # 只有一个仓：服务端自动取
        with self.assertRaisesRegex(Refused, "Warehouse not found"):
            P.prepare({**line, "warehouse": "X1"}, "W1", company="CO-1")  # 别家公司的仓
        self.assertNotIn("location", P.prepare({**line, "location": "A-01", "to_location": "B"}, "W1", company="CO-1"))
        self.assertEqual(P.prepare({"product": "P-1", "item_type": "service"}, None, company="CO-1"),
                         {"product": "P-1", "item_type": "service"})  # 服务行不进出库，不取仓
        self.assertFalse(hasattr(P, "one_warehouse"))

    def test_a_cash_register_binds_only_an_active_warehouse(self):
        """G16：收银台只能绑启用着的仓；仓后来停用了，旧收银台不换仓照样能存（改名等）。"""
        from pearnly_erp_portal.pearnly_erp_portal.doctype.erp_pos_terminal import erp_pos_terminal as T

        rows = {"W1": ("CO-1", 1), "OFF": ("CO-1", 0)}
        patcher = mock.patch.object(T.frappe, "db", types.SimpleNamespace(
            get_value=lambda doctype, name, fields: rows.get(name)), create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        for target, value in (("_", lambda text: text), ("throw", lambda message, *a: (_ for _ in ()).throw(Refused(message))),
                              ("get_all", lambda *a, **k: [])):
            patcher = mock.patch.object(T.frappe, target, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

        def terminal(warehouse, new=True, changed=True):
            doc = T.ERPPOSTerminal.__new__(T.ERPPOSTerminal)
            doc.__dict__.update(warehouse=warehouse, company="CO-1", is_default=0, name="T1")
            doc.is_new = lambda: new
            doc.has_value_changed = lambda field: changed
            return doc

        terminal("W1").validate()
        with self.assertRaisesRegex(Refused, "turned off"):
            terminal("OFF").validate()
        with self.assertRaisesRegex(Refused, "turned off"):
            terminal("OFF", new=False, changed=True).validate()
        terminal("OFF", new=False, changed=False).validate()  # 仓后来停用：没换仓的旧收银台照样能存

    def test_prefill_suggests_a_warehouse_and_the_server_no_longer_guesses(self):
        """多仓闭环 P1（主会话 10-26 问 1 / 问 2）：建议顺序 来源 → 本人上次（人 + 单据类型 + 公司）→ 卖货仓（只给进货类）→
        唯一启用仓 → 空（必须选）；只建议本公司启用的仓；服务端 receiving() 不再落卖货仓。"""
        W = L.warehouses
        active = {"CO-1": ["W1", "W2", "SHOP"], "CO-2": ["X1"]}
        defaults = {}
        patches = [
            mock.patch.object(W, "_active_of", lambda company: active.get(company, [])),
            mock.patch.object(W.companies, "document_company", lambda company=None: company or "CO-1"),
            mock.patch.object(W.companies, "terminal_warehouse", lambda company: "SHOP" if company == "CO-1" else None),
            mock.patch.object(W.frappe, "defaults", types.SimpleNamespace(
                get_user_default=lambda key, user=None: defaults.get(key),
                set_user_default=lambda key, value, user=None: defaults.__setitem__(key, value)), create=True),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.assertEqual(W.prefill("purchase", "CO-1"), {"warehouse": "SHOP", "reason": "selling", "single": False})
        self.assertEqual(W.prefill("sales", "CO-1"), {"warehouse": "", "reason": "", "single": False})  # 出货类不给卖货仓，多仓必须选
        self.assertEqual(W.prefill("sales", "CO-1", context="W2"), {"warehouse": "W2", "reason": "context", "single": False})
        self.assertEqual(W.prefill("sales", "CO-1", context="X1")["reason"], "")  # 别家公司的仓不当来源
        W.remember("purchase", "W1", "CO-1")
        self.assertEqual(W.prefill("purchase", "CO-1"), {"warehouse": "W1", "reason": "last", "single": False})
        self.assertEqual(W.prefill("stock_in", "CO-1")["reason"], "selling")  # 按单据类型分开记
        active["CO-1"].remove("W1")  # 上次那个仓停用了：不再建议
        self.assertEqual(W.prefill("purchase", "CO-1")["warehouse"], "SHOP")
        # 只有一个仓：界面明细不出仓库栏（`single`，11-19）
        self.assertEqual(W.prefill("sales", "CO-2"), {"warehouse": "X1", "reason": "sole", "single": True})
        with self.assertRaisesRegex(Refused, "Invalid value"):
            W.prefill("anything", "CO-1")
        calls = []
        with mock.patch.object(W, "required", lambda value=None, company=None: calls.append(value) or "OK"):
            self.assertEqual(W.receiving(None, company="CO-1"), "OK")
        self.assertEqual(calls, [None])  # 没写仓：照 required()，不再落卖货仓

    def test_a_company_linked_to_express_takes_its_warehouses_from_express(self):
        """多仓闭环 P1（G15）：接了 Express 的公司仓库跟 Express 一一对应（主档同步按仓库码建 / 认领），ERP 里不新建——
        新建的 Express 没有，单据写不进去；没接的公司照常建。"""
        W = L.warehouses
        push = types.SimpleNamespace(linked=lambda company: company == "CO-X")
        express = types.ModuleType("pearnly_erp_portal.express")
        express.push = push
        inserted = []
        for target, value in (("get_doc", lambda values: types.SimpleNamespace(insert=lambda: inserted.append(values), name="WH-NEW")),
                              ("db", types.SimpleNamespace(exists=lambda doctype, filters: False, commit=lambda: None))):
            patcher = mock.patch.object(W.frappe, target, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        for patcher in (mock.patch.object(W.guard, "require", lambda capability: None),
                        mock.patch.object(W.companies, "document_company", lambda company=None: company),
                        mock.patch.dict(sys.modules, {"pearnly_erp_portal.express": express,
                                                      "pearnly_erp_portal.express.push": push})):
            patcher.start()
            self.addCleanup(patcher.stop)
        with self.assertRaisesRegex(Refused, "Create the warehouse in Express, then use Sync Express"):
            W.create_warehouse(warehouse_name="New", company="CO-X")
        self.assertEqual(W.create_warehouse(warehouse_name="New", company="CO-1"), {"name": "WH-NEW"})
        self.assertEqual([row["company"] for row in inserted], ["CO-1"])


if __name__ == "__main__":
    unittest.main()
