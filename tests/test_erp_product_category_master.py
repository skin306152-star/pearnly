"""物料分类主档（岳父 11-01：分类要能写中 / 泰 / 英文字和数字）：迁移补丁与服务端规则。

Run with Frappe installed (candidate image). No Site, Redis or network: the database calls are replaced.
- 补丁：存量 `category_id` 每个不同的数字建一条同名分类（0 / 空不建），商品挂过去，旧列删掉；同名分类已有就复用；
  旧列没了（已迁过）什么都不做；
- 权限：新建分类要物料修改权或盘点录入权，改名 / 停用只给物料修改权；同名不许重复；空名不许；
- 盘点里就地新建收到的分类：认分类单号、认名称，名称没有就建（盘点录入权也能建）；
- 「分类 · 特性/颜色」一行有哪格印哪格。
"""
import unittest
from unittest import mock

import frappe


class Patch(unittest.TestCase):
    def setUp(self):
        from pearnly_erp_portal.patches import v1_23_product_category_master as patch

        self.patch = patch
        db = mock.MagicMock()
        db.table_exists.return_value = True
        db.has_column.return_value = True
        db.sql.return_value = [frappe._dict(name="P1", category_id=3), frappe._dict(name="P2", category_id=3),
                               frappe._dict(name="P3", category_id=12)]
        db.get_value.side_effect = lambda doctype, filters, field: "PC-00009" if filters == {"category_name": "12"} else None
        made = iter(["PC-00001"])
        docs = []

        def get_doc(values):
            doc = mock.MagicMock(**values)
            doc.name = next(made)
            docs.append(values)
            return doc

        self.docs = docs
        patches = [mock.patch.object(patch.frappe, "db", db), mock.patch.object(patch.frappe, "get_doc", side_effect=get_doc)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.db = db

    def test_each_number_becomes_one_category_and_products_follow(self):
        self.patch.execute()
        query = " ".join(self.db.sql.call_args_list[0].args[0].split())
        self.assertIn("category_id is not null and category_id != 0", query)
        self.assertIn("ifnull(category, '') = ''", query)
        self.assertEqual(self.docs, [{"doctype": "ERP Product Category", "category_name": "3", "is_active": 1}])
        linked = {call.args[1]: call.args[3] for call in self.db.set_value.call_args_list}
        self.assertEqual(linked, {"P1": "PC-00001", "P2": "PC-00001", "P3": "PC-00009"})
        self.db.sql_ddl.assert_called_once_with("ALTER TABLE `tabERP Product` DROP COLUMN `category_id`")

    def test_nothing_happens_once_the_old_column_is_gone(self):
        self.db.has_column.return_value = False
        self.patch.execute()
        self.db.sql.assert_not_called()
        self.db.sql_ddl.assert_not_called()


class Rules(unittest.TestCase):
    def setUp(self):
        from pearnly_erp_portal.products import categories

        self.c = categories
        self.held = set()
        patcher = mock.patch.object(categories.guard, "has", side_effect=lambda cap, user=None: cap in self.held)
        patcher.start()
        self.addCleanup(patcher.stop)
        require = mock.patch.object(categories.guard, "require",
                                    side_effect=self._require)
        require.start()
        self.addCleanup(require.stop)
        self.db = mock.MagicMock()

        def throw(message, exc=frappe.ValidationError, *args, **kwargs):
            raise exc(message)

        for target, value in (("db", self.db), ("throw", throw)):
            item = mock.patch.object(categories.frappe, target, value)
            item.start()
            self.addCleanup(item.stop)
        item = mock.patch.object(categories, "_", lambda text: text)
        item.start()
        self.addCleanup(item.stop)

    def _require(self, cap, user=None):
        if cap not in self.held:
            raise frappe.PermissionError(cap)

    def test_stocktake_clerks_can_create_but_not_rename(self):
        self.held = {"count.write"}
        self.assertTrue(self.c.can_create())
        self.db.exists.return_value = True
        with self.assertRaises(frappe.PermissionError):
            self.c.update("PC-00001", category_name="XL")
        self.held = set()
        self.assertFalse(self.c.can_create())
        with self.assertRaises(frappe.PermissionError):
            self.c.create("XL")

    def test_names_must_be_present_and_unique(self):
        self.held = {"products.manage"}
        with self.assertRaises(frappe.ValidationError):
            self.c.create("   ")
        with mock.patch.object(self.c, "find", return_value="PC-00001"), self.assertRaises(frappe.ValidationError):
            self.c.create("xl")

    def test_stocktake_accepts_a_category_id_a_name_or_a_new_name(self):
        self.held = {"count.write"}
        self.db.exists.side_effect = lambda doctype, name: name == "PC-00001"
        with mock.patch.object(self.c, "find", side_effect=lambda text: "PC-00002" if text == "M" else None), \
                mock.patch.object(self.c, "create", return_value={"name": "PC-00003"}) as create:
            self.assertEqual(self.c.accept("PC-00001"), "PC-00001")
            self.assertEqual(self.c.accept("M"), "PC-00002")
            self.assertEqual(self.c.accept("ไซส์ XL"), "PC-00003")
            create.assert_called_once_with("ไซส์ XL")
            self.assertIsNone(self.c.accept(""))

    def test_web_addresses_are_refused_where_codes_are_read_and_written(self):
        from pearnly_erp_portal.products import codes

        with mock.patch.object(codes.frappe, "throw", side_effect=lambda message, exc=frappe.ValidationError: (_ for _ in ()).throw(exc(message))), \
                mock.patch.object(codes, "_", lambda text: text):
            with self.assertRaises(codes.NotProductCode):
                codes.candidates("https://appdb.tisi.go.th/Q/i.php?d=x")
            with self.assertRaises(codes.NotProductCode):
                codes.assert_free("http://example.test/p")
            codes.refuse_web_address("8850001000019")

    def test_spec_line_skips_empty_parts(self):
        self.assertEqual(self.c.spec("XL", "แดง"), "XL · แดง")
        self.assertEqual(self.c.spec("", "แดง"), "แดง")
        self.assertEqual(self.c.spec(None, None), "")


if __name__ == "__main__":
    unittest.main()
