"""页头入口声明按页核对（`scripts/check-entries.py`）。

10-27 验收：打印第二批在销售详情、盘点页加的「打印」只在别的页声明过，按产品汇总的检查没拦住。现在文件属于哪页按共用件登记表定，
码必须在那一页（或 `*`）的声明里；不属于任何页的共用助手只认 `*`。
"""
import contextlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_entries", ROOT / "scripts/check-entries.py")
entries = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entries)

DECLARED = {"erp": {"actions": {"pearnly-purchase": ["Print"], "pearnly-sales": ["Cancel"], "*": ["selection"]},
                    "shortcuts": ["ctrl+s"], "routes": []}}
ROUTES = {"apps/pearnly_erp_portal/x/public/desk/sales-form.js": {("erp", "pearnly-sales")}}


def call(file, code, product="erp"):
    return {"file": file, "line": 1, "product": product, "codes": [code], "argument": f'__("{code}")', "call": "add_menu_item"}


def run(found):
    out = io.StringIO()
    with mock.patch.object(entries, "declarations", return_value=DECLARED), \
            mock.patch.object(entries, "page_routes", return_value=ROUTES), \
            mock.patch.object(entries, "calls", return_value=found), contextlib.redirect_stdout(out):
        return entries.check(), out.getvalue()


class PerPage(unittest.TestCase):
    def test_a_code_declared_only_on_another_page_fails(self):
        code, out = run([call("apps/pearnly_erp_portal/x/public/desk/sales-form.js", "Print")])
        self.assertEqual(code, 1)
        self.assertIn("「Print」不在 erp 的「pearnly-sales」声明里", out)

    def test_the_page_declaration_and_star_pass(self):
        sales = "apps/pearnly_erp_portal/x/public/desk/sales-form.js"
        self.assertEqual(run([call(sales, "Cancel"), call(sales, "selection")])[0], 0)

    def test_a_file_on_no_page_only_counts_star(self):
        helper = "apps/pearnly_erp_portal/x/public/desk/helper.js"
        self.assertEqual(run([call(helper, "selection")])[0], 0)
        code, out = run([call(helper, "Cancel")])
        self.assertEqual(code, 1)
        self.assertIn("不属于登记表里任何一页", out)

    def test_core_files_are_checked_against_every_product(self):
        self.assertEqual(run([call("apps/pearnly_core/x/public/desk/list.js", "selection", product=None)])[0], 0)


if __name__ == "__main__":
    unittest.main()
