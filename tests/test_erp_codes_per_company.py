"""编码、条码按公司认（10-24 集成核对）：盘点扫码 / LINE 手机盘点 / 盘点导入 / 期初导入。

Run with Frappe installed. No Site, Redis or network: database reads are replaced.
"""
import unittest


class CodesAreReadPerCompany(unittest.TestCase):
    """编码、条码不再全租户唯一（多家公司从各自 Express 同步，同码各一件）：盘点扫码 / LINE 手机盘点 / 盘点导入 / 期初导入
    按盘点（期初）仓库所属的公司认，只认这家 Express 的档和本地档，这家的优先（10-24 集成核对）。"""

    ROWS = [{"name": "PRD-B", "code": "A001", "express_company": "CO-B", "is_active": 1},
            {"name": "PRD-L", "code": "A001", "express_company": "", "is_active": 1},
            {"name": "PRD-A", "code": "A001", "express_company": "CO-A", "is_active": 1}]

    def test_a_scanned_code_picks_this_companys_product_then_a_local_one(self):
        from unittest import mock

        from pearnly_erp_portal.inventory import count

        def get_all(doctype, filters=None, **kwargs):
            field, value = next((key, val) for key, val in filters.items() if key != "is_active")
            return [row for row in self.ROWS if row.get(field) == value]

        def get_value(doctype, name, fields=None, as_dict=False):
            return {"name": name}

        def throw(message, *a):
            raise ValueError(message)

        with mock.patch.object(count.frappe, "get_all", side_effect=get_all), \
                mock.patch.object(count.frappe, "db", __import__("types").SimpleNamespace(get_value=get_value)), \
                mock.patch.object(count, "_", lambda text: text), mock.patch.object(count.frappe, "throw", side_effect=throw):
            # 本公司的档与本地档同码（码的命名空间撞了，主会话 10-26）：不默认挑一件，报错让人挑；候选里两件都在
            with self.assertRaisesRegex(ValueError, "Several matches"):
                count.product_of_code("A001", "CO-A")
            self.assertEqual({row["name"] for row in count.products_of_code("A001", "CO-A")}, {"PRD-A", "PRD-L"})
            self.assertEqual(count.product_of_code("A001", "CO-C")["name"], "PRD-L")   # 这家只看得到本地档
            self.assertIsNone(count.product_of_code("ZZZ", "CO-A"))
        only_other = [self.ROWS[0]]
        with mock.patch.object(count.frappe, "get_all", side_effect=lambda doctype, filters=None, **kw:
                               only_other if doctype == "ERP Product" else []):
            self.assertIsNone(count.product_of_code("A001", "CO-A"))   # 只有别家公司的：不认

    def test_opening_rows_ignore_other_companies_with_the_same_code(self):
        from types import SimpleNamespace

        from pearnly_erp_portal.inventory import opening_batch

        catalog = [SimpleNamespace(**row, get=lambda key, row=row: row.get(key), product_name="", base_unit="pcs", unit="pcs")
                   for row in self.ROWS if row["name"] != "PRD-L"]
        self.assertEqual(opening_batch._product({"product_code": "A001"}, catalog, "CO-A").name, "PRD-A")
        with self.assertRaises(ValueError):
            opening_batch._product({"product_code": "A001"}, catalog)   # 不给公司：照旧判有歧义




class InternalCodes(unittest.TestCase):
    """内部编码（主会话 10-26）：扫码认得到；本地商品手输、全租户不能重，也不能与哪家公司的 Express 编码撞；Express 来的
    商品就是 Express 编码，页面原样带回来不算改。"""

    def test_a_scan_finds_the_internal_code_and_the_express_code(self):
        from unittest import mock

        from pearnly_erp_portal.inventory import count

        rows = [{"name": "PRD-L", "code": "P-000001", "internal_code": "A-01", "express_company": "", "is_active": 1},
                {"name": "PRD-A", "code": "P-000002", "express_code": "STK-9", "express_company": "CO-A", "is_active": 1}]

        def get_all(doctype, filters=None, **kwargs):
            field, value = next((key, val) for key, val in filters.items() if key != "is_active")
            return [row for row in rows if row.get(field) == value]

        db = __import__("types").SimpleNamespace(get_value=lambda doctype, name, fields=None, as_dict=False: {"name": name})
        with mock.patch.object(count.frappe, "get_all", side_effect=get_all), mock.patch.object(count.frappe, "db", db):
            self.assertEqual(count.product_of_code("A-01", "CO-A")["name"], "PRD-L")
            self.assertEqual(count.product_of_code("STK-9", "CO-A")["name"], "PRD-A")
            self.assertIsNone(count.product_of_code("STK-9", "CO-B"))   # 别家公司的 Express 编码不认

    def held(self, held):
        """码的命名空间（`products.codes`）替身：``held`` = {码: (商品名, 种类)}。"""
        import types
        from unittest import mock

        from pearnly_erp_portal.products import codes

        def sql(query, params, as_dict=False):
            value = params[0] if "u.barcode" not in query else params[1]
            field = query.split("s.`")[1].split("`")[0] if "s.`" in query else \
                query.split("p.`")[1].split("`")[0] if "p.`" in query else "unit_barcode"
            hit = held.get(value)
            owner = hit[2] if hit and len(hit) > 2 else ""
            return [types.SimpleNamespace(name="PRD-X", product_name=hit[0], express_company=owner, company=owner,
                                          serial_kind="vehicle")] if hit and hit[1] == field else []

        class Refused(Exception):
            pass

        return [mock.patch.object(codes.frappe, "db", types.SimpleNamespace(sql=sql)),
                mock.patch.object(codes, "_", side_effect=lambda text: text),
                mock.patch.object(codes.frappe, "throw", side_effect=lambda message, *a: (_ for _ in ()).throw(Refused(message)))], Refused

    def test_every_scannable_code_shares_one_namespace(self):
        """主会话 10-26（验收：内部编码填了饮料的条码，收银 / 入库 / 盘点扫码都把饮料顶掉了）：内部编码撞条码、条码撞内部编码、
        撞系统编码 / 单位条码都拒，报出是哪件商品的哪种码。"""
        from pearnly_erp_portal.products import service

        patches, Refused = self.held({"8858998583485": ("น้ำดื่ม", "barcode"), "IC-BAR-77": ("สินค้า A", "internal_code"),
                                      "P-000003": ("น้ำดื่ม", "code"), "BOX-12": ("น้ำดื่ม", "unit_barcode")})
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        for kwargs, kind in (({"internal": "8858998583485"}, "product.codeKind.barcode"),
                             ({"internal": "P-000003"}, "product.codeKind.code"),
                             ({"internal": "BOX-12"}, "product.codeKind.unitBarcode")):
            with self.assertRaisesRegex(Refused, "already used"):
                service._assert_unique(None, None, **kwargs)
        with self.assertRaisesRegex(Refused, "already used"):
            service._assert_unique(None, "IC-BAR-77")   # 条码撞别件的内部编码
        service._assert_unique(None, "4800000000001", internal="NEW-1")

    def test_chassis_and_engine_numbers_are_in_the_namespace(self):
        """主会话 10-26：按台记录在用的车架号、发动机号也算，商品的条码 / 内部编码撞了就拒，报出车型与「车架号 / 发动机号」。"""
        from unittest import mock

        from pearnly_erp_portal.products import codes, service

        patches, Refused = self.held({"MP1TFR41JTG032993": ("Toyota Hilux", "serial_no", "CO-00001"),
                                      "1GD4801234": ("Toyota Hilux", "secondary_no", "CO-00001")})
        for patch in patches + [mock.patch.object(codes, "_active_company", lambda: "CO-00001")]:
            patch.start()
            self.addCleanup(patch.stop)
        for kwargs, label in (({"barcode": "MP1TFR41JTG032993"}, "serial.vehicle.primary"),
                              ({"internal": "1GD4801234"}, "serial.vehicle.secondary")):
            with self.assertRaises(Refused) as raised:
                service._assert_unique(None, kwargs.get("barcode"), internal=kwargs.get("internal"))
            self.assertIn(label, str(raised.exception))
        self.assertEqual([hit["kind"] for hit in codes.holders("MP1TFR41JTG032993", owner="CO-00001")], ["serial_no"])

    def test_a_clash_with_another_companys_express_item_does_not_show_its_name(self):
        """验收 10-26：本地商品查重是全租户范围，撞到别家公司从 Express 同步来的档时只说「已被占用」，不带那家的商品名。"""
        from unittest import mock

        from pearnly_erp_portal.products import codes, service

        patches, Refused = self.held({"8851111111111": ("ของบริษัทอื่น", "barcode", "CO-00078"),
                                      "8852222222222": ("ของเรา", "barcode", "CO-00001")})
        for patch in patches + [mock.patch.object(codes, "_active_company", lambda: "CO-00001")]:
            patch.start()
            self.addCleanup(patch.stop)
        with self.assertRaises(Refused) as raised:
            service._assert_unique(None, "8851111111111")
        self.assertEqual(str(raised.exception), "This code is already in use")
        with self.assertRaisesRegex(Refused, "already used by"):
            service._assert_unique(None, "8852222222222")

    def test_a_scan_that_hits_several_products_lists_them(self):
        """存量里撞了的码：扫码不默认挑一件，把候选交给页面让人选。"""
        from unittest import mock

        from pearnly_erp_portal.products import service

        rows = {"barcode": [{"name": "PRD-1", "product_name": "A", "express_company": ""}],
                "internal_code": [{"name": "PRD-2", "product_name": "B", "express_company": ""}]}
        get_list = lambda doctype, filters=None, fields=None, **kw: rows.get(next(k for k in filters if k != "is_active"), [])  # noqa: E731
        with mock.patch.object(service.guard, "require", lambda capability: None), \
                mock.patch.object(service.frappe, "get_list", side_effect=get_list), \
                mock.patch.object(service.codes.frappe, "get_all", side_effect=lambda *a, **k: []):
            found = service.find_product("8858998583485")
        self.assertEqual((found["ambiguous"], [row["name"] for row in found["candidates"]]), (1, ["PRD-2", "PRD-1"]))  # 按认码顺序：内部编码先于条码


class MasterRecordsArePerCompany(unittest.TestCase):
    """10-24 验收问题 23：门户自己的单条读写接口不经 has_permission，只开了 CO-00001 的员工读到、能改别家公司 Express 的档。"""

    def test_another_companys_master_is_refused_and_local_or_own_pass(self):
        from types import SimpleNamespace
        from unittest import mock

        import frappe
        from pearnly_erp_portal.masters import access

        doc = lambda owner: SimpleNamespace(get=lambda key, owner=owner: owner if key == "express_company" else None)  # noqa: E731
        with mock.patch.object(access, "company_allowed", side_effect=lambda name, user=None: name == "CO-00001"), \
                mock.patch.object(access, "_", side_effect=lambda text: text), \
                mock.patch.object(access.frappe, "throw", side_effect=lambda message, exc=Exception: (_ for _ in ()).throw(exc(message))):
            self.assertTrue(access.require_master(doc("CO-00001"), user="u@example.com"))
            self.assertTrue(access.require_master(doc(""), user="u@example.com"))   # 本地档各家都能用
            with self.assertRaises(frappe.PermissionError):
                access.require_master(doc("CO-42088"), user="u@example.com")
            self.assertTrue(access.require_master(doc("CO-42088"), user="Administrator"))


if __name__ == "__main__":
    unittest.main()
