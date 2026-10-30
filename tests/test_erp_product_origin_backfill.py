"""商品来由补值（10-30：10-29 前 Express 同步进来的商品来由是空的，部署后 erp.products.create 在 erp-new 上不通过）。

Run with Frappe installed. No Site, Redis or network: the database calls are replaced.
- 补丁只改「带 Express 编码、来由为空」的商品，设成 express，值走参数；补前补后各数一次；
- 表不存在（还没装 ERP）时什么都不做；
- 补丁登记在 patches.txt。
"""
import pathlib
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal"


class ProductOriginBackfill(unittest.TestCase):
    def setUp(self):
        from pearnly_erp_portal.patches import v1_20_product_origin_express as patch

        self.patch = patch
        self.calls = []
        db = mock.MagicMock()
        db.table_exists.return_value = True
        counts = iter([[(7,)], [(0,)]])

        def sql(query, values=None):
            self.calls.append((" ".join(query.split()), values))
            return next(counts) if query.lstrip().lower().startswith("select") else None

        db.sql.side_effect = sql
        patcher = mock.patch.object(patch.frappe, "db", db)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.db = db

    def test_only_synced_products_without_an_origin_become_express(self):
        self.patch.execute()
        updates = [call for call in self.calls if call[0].lower().startswith("update")]
        self.assertEqual(len(updates), 1)
        query, values = updates[0]
        self.assertIn("ifnull(express_code, '') != ''", query)
        self.assertIn("ifnull(origin, '') = ''", query)
        self.assertEqual(values, ("express",))
        self.assertEqual(sum(call[0].lower().startswith("select count") for call in self.calls), 2)
        self.db.commit.assert_called_once()

    def test_nothing_happens_before_the_table_exists(self):
        self.db.table_exists.return_value = False
        self.patch.execute()
        self.assertEqual(self.calls, [])

    def test_the_patch_is_registered(self):
        self.assertIn("pearnly_erp_portal.patches.v1_20_product_origin_express",
                      (APP / "patches.txt").read_text(encoding="utf-8").splitlines())


if __name__ == "__main__":
    unittest.main()
