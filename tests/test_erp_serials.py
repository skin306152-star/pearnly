"""按台管理（车辆按台补充设计，主会话 10-26 审定）：库存层统一登记 / 收回 / 出台，作废按按台流水回退。内存替身，不连库。

- 进（进货、入库「其他」）登记新台；台数必须等于基本单位数量；同公司同主号（不分大小写）只能有一台在用的；
- 出（销售、收银、出库）只认这个仓在库的台，销售 / 收银改「已售」，出库改「已出库」；
- 作废：出的台回在库、原仓原库位；进的台还在库就作废（不占号），有一台已卖出就整单拒；收回的台回原来的状态；
- 盘点生成的出入库单不登记、不改台；不开按台的商品不经这里；
- 收银退货只能退原小票卖出、现在还是已售的台，没选就按原小票取够数量；
- 有库存的商品开按台要按台期初登记，每个仓台数等于账面数才开；只有全部已售或作废才能关。
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
for name, value in (("nowdate", lambda: "2026-10-26"), ("flt", lambda v, *a: float(v or 0))):
    setattr(utils, name, value)
frappe.utils = utils

from pearnly_erp_portal.inventory import serials as S  # noqa: E402
from pearnly_erp_portal.products import codes  # noqa: E402


class Refused(Exception):
    pass


class Doc(dict):
    __getattr__ = dict.get

    def __init__(self, store, values):
        super().__init__(values)
        self._store = store

    def __setattr__(self, key, value):
        self[key] = value

    def insert(self, ignore_permissions=False):
        table = self._store.setdefault(self["doctype"], {})
        self["name"] = self.get("name") or f"{self['doctype'][:3]}-{len(table) + 1}"
        table[self["name"]] = self
        return self

    def save(self, ignore_permissions=False):
        return self


class Serials(unittest.TestCase):
    def setUp(self):
        self.db = {S.DOCTYPE: {}, S.MOVEMENT: {}}
        self.tracked = {"CAR"}

        def matches(row, filters):
            for key, value in (filters or {}).items():
                if isinstance(value, list) and value and value[0] == "in":
                    if row.get(key) not in value[1]:
                        return False
                elif isinstance(value, list) and value and value[0] == "is":
                    if not row.get(key):
                        return False
                elif row.get(key) != value:
                    return False
            return True

        def get_all(doctype, filters=None, fields=None, pluck=None, **kw):
            rows = [row for row in self.db.get(doctype, {}).values() if matches(row, filters)]
            if doctype == S.MOVEMENT:
                rows = list(reversed(rows))  # 后动的先回
            return [row[pluck] for row in rows] if pluck else rows

        def get_doc(doctype, name=None, for_update=False):
            if isinstance(doctype, dict):
                return Doc(self.db, doctype)
            return self.db[doctype][name]

        db = types.SimpleNamespace(
            exists=lambda doctype, filters: any(matches(row, filters) for row in self.db.get(doctype, {}).values())
            if isinstance(filters, dict) else filters in self.db.get(doctype, {}),
            set_value=lambda doctype, name, field, value, update_modified=False: self.db[doctype][name].__setitem__(field, value))
        for target, value in (("get_all", get_all), ("get_doc", get_doc), ("db", db), ("_", lambda text: text),
                              ("throw", lambda message, *a, title=None: (_ for _ in ()).throw(Refused(f"{title or ''} {message}")))):
            patcher = mock.patch.object(S.frappe, target, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.held = {}  # 商品那边占着的码（码的命名空间替身）：{码: (商品名, 种类)}

        def assert_free(value, owner=None, exclude=None):
            self.assertEqual(owner, "CO-1")  # 只在登记这台的公司里算
            if value in self.held:
                raise Refused("This code is already used by {0} ({1})".format(*self.held[value]))

        patcher = mock.patch.object(codes, "assert_free", assert_free)
        patcher.start()
        self.addCleanup(patcher.stop)
        for target, value in (("tracked", lambda products, company=None: {p for p in products if p in self.tracked}),
                              ("_base_qty", lambda line: int(line["qty"]))):
            patcher = mock.patch.object(S, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def line(self, qty, serials, product="CAR", warehouse="W1"):
        return {"product": product, "qty": qty, "warehouse": warehouse, "serials": serials}

    def receive(self, numbers, ref="PUR-1", warehouse="W1"):
        S.apply(lines=[self.line(len(numbers), [{"serial_no": n, "secondary_no": "E" + n[-3:], "color": "W01"} for n in numbers],
                                 warehouse=warehouse)], movement_type="purchase_in", ref_type="purchase", ref_id=ref, company="CO-1")
        return [unit for unit in self.db[S.DOCTYPE].values() if unit.in_ref == ref]

    def test_receiving_registers_each_unit_and_the_count_must_match(self):
        units = self.receive(["MP1TFR41JTG032993", "MP1TFR41JTG032994"])
        self.assertEqual([(u.status, u.warehouse, u.serial_tail) for u in units],
                         [(S.IN_STOCK, "W1", "TG032993"), (S.IN_STOCK, "W1", "TG032994")])
        with self.assertRaisesRegex(Refused, "does not match"):
            S.apply(lines=[self.line(2, [{"serial_no": "X1"}])], movement_type="purchase_in", ref_type="purchase",
                    ref_id="PUR-2", company="CO-1")
        with self.assertRaisesRegex(Refused, "already registered"):
            self.receive(["mp1tfr41jtg032993"], ref="PUR-3")  # 同公司同号不分大小写
        S.apply(lines=[self.line(5, [], product="WATER")], movement_type="purchase_in", ref_type="purchase",
                ref_id="PUR-4", company="CO-1")  # 不开按台的商品不经这里

    def test_unit_numbers_share_the_product_code_namespace(self):
        """主会话 10-26：车架号、发动机号与商品的码同一个命名空间，撞了商品的条码 / 编码就拒，报出那件商品与码的种类。"""
        self.held = {"MP1TFR41JTG032995": ("น้ำดื่ม", "product.codeKind.barcode"), "E995": ("สินค้า A", "product.codeKind.code")}
        with self.assertRaisesRegex(Refused, "already used by"):
            self.receive(["MP1TFR41JTG032995"])
        self.held = {"E996": ("สินค้า A", "product.codeKind.code")}
        with self.assertRaisesRegex(Refused, "already used by"):
            self.receive(["MP1TFR41JTG032996"])  # 发动机号 E996 撞了
        self.held = {}
        self.assertEqual(len(self.receive(["MP1TFR41JTG032997"])), 1)

    def test_selling_needs_a_unit_in_this_warehouse_and_voiding_puts_it_back(self):
        unit = self.receive(["MP1TFR41JTG032993"])[0]
        with self.assertRaisesRegex(Refused, "not in stock"):
            S.apply(lines=[self.line(1, [{"serial": unit.name}], warehouse="W2")], movement_type="sale_out",
                    ref_type="sale", ref_id="SAL-1", company="CO-1")
        S.apply(lines=[self.line(1, [{"serial": unit.name}])], movement_type="sale_out", ref_type="sale",
                ref_id="SAL-1", company="CO-1")
        self.assertEqual((unit.status, unit.out_ref), (S.SOLD, "SAL-1"))
        with self.assertRaisesRegex(Refused, "sold or issued"):
            S.reverse("purchase", "PUR-1")  # 卖出的台挡住进货作废
        S.reverse("sale", "SAL-1")
        self.assertEqual((unit.status, unit.warehouse, unit.out_ref), (S.IN_STOCK, "W1", None))
        S.apply(lines=[self.line(1, [{"serial": unit.name}])], movement_type="stock_out", ref_type="stock_entry",
                ref_id="STE-1", company="CO-1")
        self.assertEqual(unit.status, S.ISSUED)  # 出库是已出库，不是已售
        S.reverse("stock_entry", "STE-1")
        S.reverse("purchase", "PUR-1")
        self.assertEqual((unit.status, unit.serial_key), (S.VOID, None))  # 作废的不占号
        self.receive(["MP1TFR41JTG032993"], ref="PUR-5")

    def test_returns_take_back_sold_units_and_voiding_a_return_restores_them(self):
        unit = self.receive(["MP1TFR41JTG032993"])[0]
        S.apply(lines=[self.line(1, [{"serial": unit.name}])], movement_type="sale_out", ref_type="sale",
                ref_id="SAL-1", company="CO-1")
        with mock.patch.object(S.frappe, "get_all", side_effect=lambda doctype, filters=None, pluck=None, **kw:
                               [unit.name] if filters.get("out_ref") == "SAL-1" else []):
            self.assertEqual(S.refund_picks("SAL-1", "CAR", 1), [{"serial": unit.name}])  # 卖 1 退 1：自动取
            with self.assertRaises(Refused):
                S.refund_picks("SAL-1", "CAR", 1, [{"serial": "SRL-OTHER"}])
        S.apply(lines=[self.line(1, [{"serial": unit.name}], warehouse="W2")], movement_type="stock_in",
                ref_type="refund", ref_id="REF-1", company="CO-1")
        self.assertEqual((unit.status, unit.warehouse), (S.IN_STOCK, "W2"))
        S.reverse("refund", "REF-1")
        self.assertEqual(unit.status, S.SOLD)

    def test_voiding_an_issue_whose_unit_came_back_another_way_is_refused(self):
        """验收 10-26：入 2 台 → 出 X → 入库「退货」收回 X → 作废出库单原来会成功（账面 3 台、实际 2 台）。出的台必须还是这张单出的。"""
        unit = self.receive(["MP1TFR41JTG032993"])[0]
        S.apply(lines=[self.line(1, [{"serial": unit.name}])], movement_type="stock_out", ref_type="stock_entry",
                ref_id="STE-OUT", company="CO-1")
        S.apply(lines=[self.line(1, [{"serial": unit.name}])], movement_type="stock_in", ref_type="stock_entry",
                ref_id="STE-RET", company="CO-1")
        with self.assertRaisesRegex(Refused, "sold or issued"):
            S.reverse("stock_entry", "STE-OUT")
        self.assertEqual(unit.status, S.IN_STOCK)

    def test_a_partial_cashier_refund_must_say_which_units(self):
        sold = ["SRL-1", "SRL-2"]
        with mock.patch.object(S.frappe, "get_all", side_effect=lambda doctype, filters=None, pluck=None, **kw: sold):
            with self.assertRaisesRegex(Refused, "does not match"):
                S.refund_picks("SAL-1", "CAR", 1)  # 卖了 2 台退 1 台：要人选
            self.assertEqual(S.refund_picks("SAL-1", "CAR", 2), [{"serial": "SRL-1"}, {"serial": "SRL-2"}])
            self.assertEqual(S.refund_picks("SAL-1", "CAR", 1, [{"serial": "SRL-2"}]), [{"serial": "SRL-2"}])

    def test_issuing_checks_the_warehouse_of_the_unit_only(self):
        """G17：台记的仓要等于这一行出货的仓（库存只到仓库，11-19 取消仓位），别的仓不让出；行上带来的仓位不再看。"""
        unit = self.receive(["MP1TFR41JTG032993"])[0]
        with self.assertRaisesRegex(Refused, "not in stock"):
            S.apply(lines=[self.line(1, [{"serial": unit.name}], warehouse="W2")], movement_type="sale_out",
                    ref_type="sale", ref_id="SAL-9", company="CO-1")
        self.assertEqual(unit.status, S.IN_STOCK)
        S.apply(lines=[{**self.line(1, [{"serial": unit.name}]), "location": "B-2"}], movement_type="sale_out",
                ref_type="sale", ref_id="SAL-9", company="CO-1")
        self.assertEqual(unit.status, S.SOLD)
        self.assertFalse(any("location" in row for row in self.db[S.MOVEMENT].values() if isinstance(row, dict)))

    def transfer(self, unit, ref="STE-TF", source="W1", to="W2"):
        # 调拨的行不放仓（单头一出一进，11-19）：`documents._post` 交来的行只有物料、数量、单位、台，调出仓只看单头
        line = {key: value for key, value in self.line(1, [{"serial": unit.name}]).items() if key != "warehouse"}
        S.transfer(lines=[line], warehouse=source, to_warehouse=to, ref_type="stock_entry", ref_id=ref, company="CO-1")

    def test_a_transfer_moves_the_unit_and_voiding_it_brings_it_back(self):
        """多仓闭环 P2：调拨选调出仓在库的台，换到调入仓、仍是在库；作废回调出仓（单头一出一进，11-19 不再有仓位）。"""
        unit = self.receive(["MP1TFR41JTG032993"])[0]
        with self.assertRaisesRegex(Refused, "not in stock"):
            self.transfer(unit, source="W3")  # 不在这个仓
        self.transfer(unit)
        self.assertEqual((unit.status, unit.warehouse), (S.IN_STOCK, "W2"))
        actions = [row.action for row in self.db[S.MOVEMENT].values() if row.ref_id == "STE-TF"]
        self.assertEqual(actions, [S.TRANSFER_OUT, S.TRANSFER_IN])
        with self.assertRaisesRegex(Refused, "not in stock"):  # 在调入仓了，调出仓出不了
            S.apply(lines=[self.line(1, [{"serial": unit.name}])], movement_type="sale_out", ref_type="sale",
                    ref_id="SAL-1", company="CO-1")
        S.reverse("stock_entry", "STE-TF")
        self.assertEqual((unit.status, unit.warehouse), (S.IN_STOCK, "W1"))

    def test_voiding_a_transfer_whose_unit_was_sold_or_moved_on_is_refused(self):
        unit = self.receive(["MP1TFR41JTG032993"])[0]
        self.transfer(unit)
        S.apply(lines=[self.line(1, [{"serial": unit.name}], warehouse="W2")],
                movement_type="sale_out", ref_type="sale", ref_id="SAL-2", company="CO-1")
        with self.assertRaisesRegex(Refused, "sold or issued"):
            S.reverse("stock_entry", "STE-TF")
        S.reverse("sale", "SAL-2")
        self.transfer(unit, ref="STE-TF2", source="W2", to="W3")
        with self.assertRaisesRegex(Refused, "sold or issued"):
            S.reverse("stock_entry", "STE-TF")  # 已经再调走了：先作废后面那张
        S.reverse("stock_entry", "STE-TF2")
        S.reverse("stock_entry", "STE-TF")
        self.assertEqual(unit.warehouse, "W1")

    def test_the_last_eight_only_match_when_eight_are_typed(self):
        asked = []
        with mock.patch.object(S.frappe, "get_all", side_effect=lambda doctype, filters=None, **kw: asked.append(filters) or []):
            S.lookup("QQQQQQQQQ00000001", "CO-1")
            # 17 位完整车架号只认完全一样的：不按末 8 位、也不按发动机号
            self.assertFalse(any("serial_tail" in filters or "secondary_no" in filters for filters in asked))
            S.lookup("00000001", "CO-1")
            self.assertTrue(any(filters.get("serial_tail") == "00000001" for filters in asked))

    def test_stocktake_documents_do_not_move_units(self):
        S.apply(lines=[self.line(3, [])], movement_type="stock_in", ref_type="count", ref_id="STC-1", company="CO-1")
        self.assertEqual(self.db[S.DOCTYPE], {})

    def test_turning_on_with_stock_needs_the_units_of_each_warehouse(self):
        product = Doc(self.db, {"doctype": "ERP Product", "name": "CAR", "track_serial": 0, "serial_kind": "vehicle"})
        self.db["ERP Product"] = {"CAR": product}
        warehouses = types.SimpleNamespace(required=lambda value: value, company_of=lambda value: "CO-1")
        stock = types.SimpleNamespace(lock_products=lambda products: None)
        package = sys.modules["pearnly_erp_portal.inventory"]
        with mock.patch.object(S, "stock_by_warehouse", lambda product, company=None: {"W1": 2.0}), \
                mock.patch.object(S, "_visible", lambda places: {"W1": ("Main", "นิภา")}), \
                mock.patch.object(S.frappe.utils, "escape_html", lambda text: text, create=True), \
                mock.patch.dict(sys.modules, {"pearnly_erp_portal.inventory.warehouses": warehouses,
                                              "pearnly_erp_portal.inventory.stock": stock}), \
                mock.patch.object(package, "warehouses", warehouses, create=True), \
                mock.patch.object(package, "stock", stock, create=True):
            with self.assertRaisesRegex(Refused, "match the stock.*Main · นิภา: 1 / 2"):
                S.turn_on("CAR", [{"serial_no": "A1", "warehouse": "W1"}])
            result = S.turn_on("CAR", [{"serial_no": "A1", "warehouse": "W1"}, {"serial_no": "A2", "warehouse": "W1"}])
        self.assertEqual((result["registered"], product.track_serial), (2, 1))
        with mock.patch.object(S, "stock_by_warehouse", lambda product, company=None: {"W1": 2.0}):
            with self.assertRaisesRegex(Refused, "turned off"):
                S.turn_off("CAR")

    def test_other_companies_stock_is_named_only_to_people_who_can_see_it(self):
        """G17（主会话 10-26）：按台是商品级开关，开之前各公司的账面都要登记（口径不改）；看得到的仓逐个列「仓 · 公司：已登记 / 账面」，
        看不到的公司不露名称与数量，合成一句「其他公司还有未登记的库存」。打开按台前的各仓账面表与期初模板同样不列别家的仓。"""
        visible = {"W1": ("Main", "นิภา")}
        with mock.patch.object(S, "_visible", lambda places: {k: v for k, v in visible.items() if k in places}), \
                mock.patch.object(S.frappe.utils, "escape_html", lambda text: text, create=True):
            message = S._shortfall({"W1": 2.0, "OTHER": 3.0}, {"W1": 2})
            self.assertEqual(message, "Other companies have unregistered stock")  # W1 已对上不列；别家只一句、不露 OTHER 与 3
            message = S._shortfall({"W1": 2.0, "OTHER": 3.0}, {"W1": 1})
            self.assertEqual(message.split("<br>"), ["Main · นิภา: 1 / 2", "Other companies have unregistered stock"])
            with mock.patch.object(S, "stock_by_warehouse", lambda product, company=None: {"W1": 2.0, "OTHER": 3.0}), \
                    mock.patch.object(S, "_require_manage", lambda: None):
                rows = S.api_opening_stock("CAR")
        self.assertEqual(rows, [{"warehouse": "W1", "warehouse_name": "Main · นิภา", "qty": 2.0},
                                {"warehouse": "", "warehouse_name": "Other companies have unregistered stock", "qty": None}])

    def test_the_opening_sheet_leaves_the_warehouse_to_this_company(self):
        """G17（多仓闭环 P0）：期初 Excel 的仓库名不再按名称跨公司先找一遍（两家都叫 Main 时取到别家的仓、随后被拒），
        原样交给开按台时的 `warehouses.required`，在本公司里按名称或编号认。"""
        sheet = [("serial_no", "secondary_no", "color", "warehouse", "location"), ("A1", "", "", "Main", "A-01")]
        workbook = types.SimpleNamespace(active=types.SimpleNamespace(iter_rows=lambda values_only: iter(sheet)))
        openpyxl = types.SimpleNamespace(load_workbook=lambda data, read_only, data_only: workbook)

        def get_value(doctype, filters, *a, **k):
            raise AssertionError(f"looked up {doctype} {filters} across companies")

        with mock.patch.dict(sys.modules, {"openpyxl": openpyxl}), \
                mock.patch.object(S.frappe.db, "get_value", get_value, create=True), \
                mock.patch.object(S, "_aliases", lambda: {field: field for field in S.WORKBOOK_FIELDS}):
            rows = S.parse_workbook(b"xlsx")
        # 旧表带着的仓位列不认（11-19 取消仓位），只认号码与仓
        self.assertEqual([(row["serial_no"], row["warehouse"]) for row in rows], [("A1", "Main")])
        self.assertNotIn("location", rows[0])

    def test_document_units_are_kept_by_line_number(self):
        doc = types.SimpleNamespace(rows=[])
        doc.set = lambda field, value: doc.rows.clear()
        doc.append = lambda field, row: doc.rows.append(types.SimpleNamespace(**row))
        doc.get = lambda field: doc.rows
        S.set_doc_serials(doc, [{"product": "CAR", "serials": [{"serial_no": " A1 ", "color": "W01"}]},
                                {"product": "WATER"}, {"product": "CAR", "serials": [{"serial": "SRL-9"}]}])
        self.assertEqual(S.line_serials(doc), {1: [{"serial": None, "serial_no": "A1", "secondary_no": "", "color": "W01"}],
                                               3: [{"serial": "SRL-9", "serial_no": "", "secondary_no": "", "color": ""}]})



class SerialRoles(unittest.TestCase):
    def test_every_role_that_reads_products_or_stock_reads_and_exports_units(self):
        """验收 10-26：只带老岗位角色（开户时的 ERP Stock / ERP Products …）或管理员的账号打不开按台记录。能读商品或库存的
        角色新旧两套都给读与导出（行仍按公司与仓过滤），迁移时补，已有站点生效。"""
        sys.modules.setdefault("pearnly_core.provisioning", types.ModuleType("pearnly_core.provisioning"))
        provisioning = sys.modules["pearnly_core.provisioning"]
        for name in ("_ensure_role", "_grant"):
            setattr(provisioning, name, getattr(provisioning, name, lambda *a, **k: None))
        sharing = types.ModuleType("pearnly_erp_portal.access.sharing")
        sharing.remove_legacy_shares = lambda: None
        with mock.patch.dict(sys.modules, {"pearnly_erp_portal.access.sharing": sharing}):
            from pearnly_erp_portal.access import menu, setup

            grants = {}
            db = types.SimpleNamespace(delete=lambda *a, **k: None, exists=lambda *a, **k: True)
            with mock.patch.object(setup, "_grant", lambda doctype, role, perms, permlevel=0: grants.__setitem__((doctype, role), perms)), \
                    mock.patch.object(setup, "_ensure_role", lambda role: None), \
                    mock.patch.object(setup.frappe, "db", db, create=True), \
                    mock.patch.object(setup.frappe, "clear_cache", lambda: None, create=True), \
                    mock.patch.object(setup.frappe, "conf", {"pearnly_features": []}, create=True):  # 管理员补表权限另测
                setup.sync_roles()
        for role in ("ERP Products", "ERP Stock", "ERP Purchase", "ERP Sales", "ERP Cashier", "ERP Admin",
                     menu.role("inventory"), menu.role("stock-card"), menu.role("pos-receipts")):
            for doctype in menu.SERIALS:
                self.assertTrue(grants[(doctype, role)].get("read") and grants[(doctype, role)].get("export"), (doctype, role))
        self.assertNotIn(("ERP Serial No", "ERP Tasks"), grants)
        self.assertNotIn(("ERP Serial No", menu.role("tasks")), grants)


if __name__ == "__main__":
    unittest.main()
