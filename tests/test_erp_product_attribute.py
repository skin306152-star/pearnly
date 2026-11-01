"""物料分类与特性/颜色（岳父 11-01）的纯规则：不连库。

- 商品 `category` 是物料分类主档的 Link（原来是裸整数 `category_id`），另加 `attribute`（特性/颜色，自由文本）；
- 新建 / 编辑弹窗左栏先后：条码、名称、分类、特性/颜色，其余在后（手机上左栏整列在前）；LINE 盘点页新建卡片同一顺序；
- 商品导入模板：分类写名称（老模板的「分类」数字列照认），特性/颜色一列紧跟分类，四语表头都认；Excel 把 3 存成 3.0 也按 3；
- 盘点表、期初表下载多「分类」「特性/颜色」两列（只给人看）；
- 新词条四语齐全；迁移补丁登记在 patches.txt。
"""
import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal"
sys.path[:0] = [str(ROOT / "apps/pearnly_erp_portal")]

from pearnly_erp_portal.inventory import opening_workbook as O, workbook as CW  # noqa: E402
from pearnly_erp_portal.products import contracts as C, workbook as W  # noqa: E402


class ProductFields(unittest.TestCase):
    def test_category_is_a_link_to_the_category_master_and_attribute_is_text(self):
        meta = json.loads((APP / "pearnly_erp_portal/doctype/erp_product/erp_product.json").read_text())
        fields = {field["fieldname"]: field for field in meta["fields"]}
        self.assertNotIn("category_id", fields)
        self.assertEqual((fields["category"]["fieldtype"], fields["category"]["options"]), ("Link", "ERP Product Category"))
        self.assertEqual((fields["attribute"]["fieldtype"], fields["attribute"]["length"]), ("Data", C.MAX_ATTRIBUTE))
        self.assertIn("attribute", meta["search_fields"].split(","))
        master = json.loads((APP / "pearnly_erp_portal/doctype/erp_product_category/erp_product_category.json").read_text())
        name = next(field for field in master["fields"] if field["fieldname"] == "category_name")
        self.assertTrue(name["reqd"] and name["unique"])
        self.assertEqual((master["title_field"], master["show_title_field_in_link"]), ("category_name", 1))

    def test_the_patch_is_registered_after_model_sync(self):
        text = (APP / "patches.txt").read_text()
        self.assertGreater(text.index("pearnly_erp_portal.patches.v1_23_product_category_master"), text.index("[post_model_sync]"))


class DialogOrder(unittest.TestCase):
    def test_the_product_dialog_starts_with_barcode_name_category_attribute(self):
        source = (APP / "public/desk/products.js").read_text()
        block = source[source.index("function product_fields("):source.index('{ fieldtype: "Column Break" },', source.index("function product_fields("))]
        names = re.findall(r'fieldname: "(\w+)"', block)
        self.assertEqual(names[:6], ["code_value", "product_name", "category", "attribute", "code", "internal_code"])

    def test_the_line_stocktake_card_uses_the_same_order(self):
        source = (APP / "www/stocktake.html").read_text()
        block = source[source.index("const NEW_FIELDS = ["):source.index("];", source.index("const NEW_FIELDS = ["))]
        names = re.findall(r"\['(\w+)'", block)
        self.assertEqual(names[:5], ["code", "product_name", "category", "attribute", "internal_code"])
        self.assertNotIn("numeric", block.split("'category'")[1].split("]")[0])


class ImportTemplate(unittest.TestCase):
    def test_category_and_attribute_columns_follow_each_other(self):
        fields = [field for field, _ in W.FIELD_LABELS]
        self.assertEqual(fields[fields.index("category") + 1], "attribute")
        self.assertIn(("attribute", "product.field.attribute"), W.FIELD_LABELS)
        self.assertNotIn("category_id", fields)

    def test_old_and_new_headers_land_on_the_category_and_attribute(self):
        aliases = W.header_aliases({"特性/颜色": "attribute"})
        for header in ("category_id", "分类", "หมวดหมู่", "category"):
            self.assertEqual(aliases[W._norm_header(header)], "category")
        for header in ("特性/颜色", "attribute", "สี", "คุณลักษณะ"):
            self.assertEqual(aliases[W._norm_header(header)], "attribute")

    def test_a_number_typed_into_a_text_column_is_read_as_typed(self):
        record = W._record(["Shirt", 3.0, "แดง", 42], {0: "product_name", 1: "category", 2: "attribute", 3: "internal_code"})
        self.assertEqual(record, {"product_name": "Shirt", "category": "3", "attribute": "แดง", "internal_code": "42"})


class StockSheets(unittest.TestCase):
    def test_count_and_opening_sheets_show_category_and_attribute_after_the_name(self):
        for labels in (CW.FIELD_LABELS, O.FIELD_LABELS):
            fields = [field for field, _ in labels]
            at = fields.index("product_name")
            self.assertEqual(fields[at + 1:at + 3], ["category", "attribute"])
        self.assertNotIn("category", O.TEXT_FIELDS)  # 只给人看，数字分类不触发「编码要写成文本」

    def test_count_sheet_reads_whole_numbers_back_without_the_decimal(self):
        self.assertEqual(CW._text(3.0), "3")
        self.assertEqual(CW._text(None), "")
        self.assertEqual(CW._text(" XL "), "XL")


class WebAddress(unittest.TestCase):
    def test_web_addresses_are_not_product_codes(self):
        for value in ("https://appdb.tisi.go.th/Q/i.php?d=abc", "HTTP://x.test", "  https://x.test  "):
            self.assertTrue(C.web_address(value), value)
        for value in ("8850001000019", "P-000001", "httpx", "VIN-https://", "", None):
            self.assertFalse(C.web_address(value), value)


class Copy(unittest.TestCase):
    def test_the_new_words_exist_in_four_languages(self):
        rows = {line.split("\t")[-1]: line.split("\t") for line in (APP / "i18n/messages.tsv").read_text().splitlines()}
        for key in ("product.field.attribute", "product.col.attribute"):
            self.assertEqual(rows[key][:4], ["Attribute / Color", "特性/颜色", "คุณลักษณะ / สี", "特性 / 色"])
        english = {line.split("\t")[0]: line.split("\t") for line in (APP / "i18n/messages.tsv").read_text().splitlines()}
        self.assertEqual(english["This is not a product barcode"][1], "这不是商品条码")


if __name__ == "__main__":
    unittest.main()
