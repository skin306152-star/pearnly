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

        with mock.patch.object(count.frappe, "get_all", side_effect=get_all):
            self.assertEqual(count.product_of_code("A001", "CO-A")["name"], "PRD-A")
            self.assertEqual(count.product_of_code("A001", "CO-C")["name"], "PRD-L")   # 这家没有：认本地档
            self.assertIsNone(count.product_of_code("ZZZ", "CO-A"))
        only_other = [self.ROWS[0]]
        with mock.patch.object(count.frappe, "get_all", return_value=only_other):
            self.assertIsNone(count.product_of_code("A001", "CO-A"))   # 只有别家公司的：不认

    def test_opening_rows_ignore_other_companies_with_the_same_code(self):
        from types import SimpleNamespace

        from pearnly_erp_portal.inventory import opening_batch

        catalog = [SimpleNamespace(**row, get=lambda key, row=row: row.get(key), product_name="", base_unit="pcs", unit="pcs")
                   for row in self.ROWS if row["name"] != "PRD-L"]
        self.assertEqual(opening_batch._product({"product_code": "A001"}, catalog, "CO-A").name, "PRD-A")
        with self.assertRaises(ValueError):
            opening_batch._product({"product_code": "A001"}, catalog)   # 不给公司：照旧判有歧义




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
