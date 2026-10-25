"""商品内部编码（主会话 10-26）的纯规则：不连库。

- 系统编码 ``code`` 照旧由系统发、不改；另加「内部编码」：本地商品手输，Express 来的商品就是 Express 编码；
- 扫码 / 手输认商品按 系统编码 → 内部编码 → Express 编码 → 条码 → 二维码 的顺序；
- 盘点表「编码」一格显示内部编码，没有就用系统编码；
- 商品导入模板多一列「内部编码」，表头四种写法都认。
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps/pearnly_erp_portal")]

from pearnly_erp_portal.products import contracts as C, workbook as W  # noqa: E402


class InternalCode(unittest.TestCase):
    def test_local_products_use_the_typed_code_and_express_products_their_express_code(self):
        self.assertEqual(C.internal_code({"code": "P-000001", "internal_code": "A-01"}), "A-01")
        self.assertEqual(C.internal_code({"code": "P-000002", "express_code": "STK-9"}), "STK-9")
        self.assertEqual(C.internal_code({"code": "P-000003"}), "")
        self.assertEqual(C.internal_code(None), "")

    def test_the_stocktake_code_cell_falls_back_to_the_system_code(self):
        self.assertEqual(C.count_code({"code": "P-000001", "internal_code": "A-01"}), "A-01")
        self.assertEqual(C.count_code({"code": "P-000003"}), "P-000003")

    def test_lookups_try_the_system_code_first_then_the_internal_code(self):
        self.assertEqual(C.LOOKUP_FIELDS[:3], ("code", "internal_code", "express_code"))
        self.assertIn("internal_code", C.UNIQUE_FIELDS)
        self.assertEqual(C.MAX_INTERNAL_CODE, 20)

    def test_the_product_template_has_an_internal_code_column(self):
        self.assertIn(("internal_code", "product.field.internalCode"), W.FIELD_LABELS)
        self.assertIn("internal_code", W.TEXT_FIELDS)
        aliases = W.header_aliases({"Internal code": "internal_code"})
        for header in ("Internal code", "internal_code", "รหัสภายใน", "内部编码"):
            self.assertEqual(aliases[W._norm_header(header)], "internal_code")
        self.assertEqual(W._record(["Ice", "A-01"], {0: "product_name", 1: "internal_code"}),
                         {"product_name": "Ice", "internal_code": "A-01"})


if __name__ == "__main__":
    unittest.main()
