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
        self.notes = {("W1", "A-01"): "前排货架"}  # 库位说明（用户 10-29）
        self.stock = {("W1", "A-01"): 3.0}
        self.moved = {("W1", "A-02")}

        def get_all(doctype, filters=None, pluck=None, fields=None, **kw):
            if doctype == L.CHILD:
                rows = [{"location_name": name, "is_active": active, "description": self.notes.get((filters["parent"], name))}
                        for name, active in self.lists.get(filters["parent"], [])
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

    def test_saving_keeps_stocked_and_used_locations(self):
        with self.assertRaisesRegex(Refused, "with stock cannot be turned off"):
            L.api_save_locations("W1", [{"location_name": "A-01", "is_active": 0}, {"location_name": "A-02"}])
        with self.assertRaisesRegex(Refused, "has been used cannot be removed"):
            L.api_save_locations("W1", [{"location_name": "A-01"}])  # A-02 有流水，不能删
        with self.assertRaisesRegex(Refused, "Duplicate"):
            L.api_save_locations("W1", [{"location_name": "A-01"}, {"location_name": " A-01"}, {"location_name": "A-02"}])
        with mock.patch.object(L, "api_get_locations", lambda warehouse: "reloaded"):
            L.api_save_locations("W1", [{"location_name": "A-01"}, {"location_name": "A-02", "is_active": 0}])
        self.assertEqual(self.saved["W1"], [{"location_name": "A-01", "is_active": 1, "description": ""},
                                            {"location_name": "A-02", "is_active": 0, "description": ""}])
        with mock.patch.object(L, "api_get_locations", lambda warehouse: "reloaded"):
            L.api_save_locations("W1", [{"location_name": "A-01", "description": " 前排货架 "}, {"location_name": "A-02"}])
        self.assertEqual(self.saved["W1"][0]["description"], "前排货架")

    def test_options_list_this_warehouse_and_mark_old_stocked_places(self):
        self.stock[("W1", "OLD-B")] = 2.0
        options = L.api_location_options("", "W1")
        self.assertEqual([row["value"] for row in options], ["A-01", "A-02", "OLD-B"])  # 有库存的旧库位也列出、标「不在清单」
        self.assertEqual(options[-1]["label"], "OLD-B · stock.location.offList")
        self.assertEqual(options[0]["label"], "A-01 · 前排货架")  # 有说明的显示「库位 · 说明」，值仍是库位名
        self.assertEqual(L.api_location_options("A", None), [])
        self.assertEqual(L.api_location_options("", "W2"), [])  # 没建清单：没有选项，照旧手输

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

    def test_the_header_warehouse_and_location_lead_the_lines(self):
        """多仓闭环 P1 单头「仓库 + 库位」：行上没写仓的用单头仓；没写库位、又在单头仓的用单头库位（单头写的不再拿商品建议库位
        顶掉）；行上明写的照旧；换到别的仓的行取商品建议库位（不在那个仓清单里就留空）。没写单头仓时库位也不留。"""
        product = types.SimpleNamespace(is_active=1, default_location="A-02")
        for target, value in (("receiving", lambda value, company=None: value or "W1"),
                              ("required", lambda value, company=None: value)):
            patcher = mock.patch.object(P.warehouses, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(P.frappe.db, "get_value", lambda *a, **k: product, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.assertEqual(P.header("W1", " A-01 "), {"warehouse": "W1", "location": "A-01"})
        self.assertEqual(P.header("", "A-01"), {"warehouse": None, "location": ""})
        # 单头库位是人明写的：进出都按清单核（验收复核 P1：销售单头写 ZZ-99 从不存在的库位出了货）；没建清单的仓照旧手输
        with self.assertRaisesRegex(Refused, "not in the location list"):
            P.header("W1", "ZZ-99")
        self.assertEqual(P.header("W2", "anything"), {"warehouse": "W2", "location": "anything"})
        line = {"product": "P-1", "item_type": "goods"}
        self.assertEqual(P.prepare(line, "W1", location="A-01"), {**line, "warehouse": "W1", "location": "A-01"})
        self.assertEqual(P.prepare({**line, "location": "B"}, "W1", location="A-01")["location"], "B")
        self.assertEqual(P.prepare(line, "W1")["location"], "A-02")  # 单头没写库位：商品建议库位（在清单里）
        self.assertEqual(P.prepare({**line, "warehouse": "W2"}, "W1", location="A-01"), {**line, "warehouse": "W2", "location": "A-02"})
        product.default_location = "Z-99"
        self.assertEqual(P.prepare(line, "W1")["location"], "")  # 建议库位不在 W1 清单：留空让人选

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
            mock.patch.object(W, "names_of", lambda company=None, include_inactive=True: active.get(company, [])),
            mock.patch.object(W.companies, "document_company", lambda company=None: company or "CO-1"),
            mock.patch.object(W.companies, "selling_warehouse", lambda company=None: "SHOP" if company == "CO-1" else None),
            mock.patch.object(W.frappe, "defaults", types.SimpleNamespace(
                get_user_default=lambda key, user=None: defaults.get(key),
                set_user_default=lambda key, value, user=None: defaults.__setitem__(key, value)), create=True),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.assertEqual(W.prefill("purchase", "CO-1"), {"warehouse": "SHOP", "reason": "selling"})
        self.assertEqual(W.prefill("sales", "CO-1"), {"warehouse": "", "reason": ""})  # 出货类不给卖货仓，多仓必须选
        self.assertEqual(W.prefill("sales", "CO-1", context="W2"), {"warehouse": "W2", "reason": "context"})
        self.assertEqual(W.prefill("sales", "CO-1", context="X1")["reason"], "")  # 别家公司的仓不当来源
        W.remember("purchase", "W1", "CO-1")
        self.assertEqual(W.prefill("purchase", "CO-1"), {"warehouse": "W1", "reason": "last"})
        self.assertEqual(W.prefill("stock_in", "CO-1")["reason"], "selling")  # 按单据类型分开记
        active["CO-1"].remove("W1")  # 上次那个仓停用了：不再建议
        self.assertEqual(W.prefill("purchase", "CO-1")["warehouse"], "SHOP")
        self.assertEqual(W.prefill("sales", "CO-2"), {"warehouse": "X1", "reason": "sole"})
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
