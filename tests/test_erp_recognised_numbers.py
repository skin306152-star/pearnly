#!/usr/bin/env python3
"""识别读出的数字在 ERP 各入口一致（主会话 11-19，ERP 界面窗口盘点：各入口处理不一，有几处静默变 0 或丢掉）。不连库。

- 采购识别出口（`purchasing.recognition.clean_result`）：「1,234.50」的数量 / 单价、「(0.25)」凑整、「฿10」折扣、「7%」税率
  都成纯数字（LINE 卡保存、CC 收单原来报「值无效」）；读不懂的单价原样留着让入账报错，不当 0；读不懂的税率去掉、按默认算。
- 销售识别出口（`sales.contracts.map_recognised`）：「฿100」单价、「฿50」整单折扣不再静默变 0；「1,000」数量、「10%」折扣读得懂，
  百分比折扣进 ``discount_pct``。
- 入账入参（`integrations.intake.build_payload`，LINE 卡 / CC 收单 / 网页编辑页）：旧记录再规整一次，销售行带上 ``discount_pct``。
"""
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "apps/pearnly_core"), str(ROOT / "apps/pearnly_erp_portal")]

import cc_desktop_fake as fake  # noqa: E402

frappe = fake.install()
# 采购识别模块里只用到纯函数部分：上传、计费、原件这几个模块不加载
for _name in ("pearnly_erp_portal.purchasing.files", "pearnly_erp_portal.recognition.billing",
              "pearnly_erp_portal.recognition.originals", "pearnly_erp_portal.recognition.policy",
              "pearnly_erp_portal.recognition.progress", "pearnly_core.originals.registered"):
    sys.modules.setdefault(_name, types.ModuleType(_name))

from pearnly_erp_portal.purchasing import recognition  # noqa: E402
from pearnly_erp_portal.recognition import background  # noqa: E402
from pearnly_erp_portal.sales import contracts as sales  # noqa: E402

INTAKE = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/integrations/intake.py").read_text(encoding="utf-8")


class Purchase(unittest.TestCase):
    def test_printed_spellings_become_numbers_at_the_recognition_exit(self):
        result = recognition.clean_result({
            "doc_no": "IV-1", "rounding": "(0.25)", "wht_rate": "3%", "discount_total": "฿10",
            "lines": [{"description": "A", "qty": "1,234.50", "unit_price": "1,234.50", "vat_rate": "7%", "discount": "฿10"},
                      {"description": "B", "qty": "2", "unit_price": "THB 99.00", "vat_rate": "??"}]})
        first, second = result["lines"]
        self.assertEqual((first["qty"], first["unit_price"], first["vat_rate"], first["discount"]), (1234.5, 1234.5, 7.0, 10.0))
        self.assertEqual((second["qty"], second["unit_price"]), (2.0, 99.0))
        self.assertNotIn("vat_rate", second)  # 读不懂的税率去掉：入账按默认 7
        self.assertEqual((result["rounding"], result["wht_rate"], result["discount_total"]), (-0.25, 3.0, 10.0))

    def test_an_unreadable_amount_is_kept_for_the_strict_check_not_turned_into_zero(self):
        result = recognition.clean_result({"lines": [{"description": "A", "qty": 1, "unit_price": "ราคาตามตกลง",
                                                      "discount": "ลดพิเศษ"}], "discount_total": "พิเศษ"})
        row = result["lines"][0]
        self.assertEqual((row["unit_price"], row["discount"], result["discount_total"]), ("ราคาตามตกลง", "ลดพิเศษ", "พิเศษ"))

    def test_a_percentage_discount_still_becomes_its_amount(self):
        result = recognition.clean_result({"lines": [{"description": "A", "qty": "2", "unit_price": "1,000", "discount": "10%"}]})
        self.assertEqual(result["lines"][0]["discount"], 200.0)

    def test_stored_records_are_tidied_again_before_posting(self):
        old = {"rounding": "0.5", "lines": [{"qty": "1,000", "unit_price": "฿12.50", "vat_rate": "7 %"}]}
        tidy = recognition.tidy_numbers(old)
        self.assertEqual((tidy["rounding"], tidy["lines"][0]["qty"], tidy["lines"][0]["unit_price"], tidy["lines"][0]["vat_rate"]),
                         (0.5, 1000.0, 12.5, 7.0))
        self.assertIn("result = _tidy_numbers(doc.direction, result)", INTAKE)


class Sales(unittest.TestCase):
    def test_baht_signs_and_commas_are_read_instead_of_zero(self):
        mapped = sales.map_recognised({"discount_total": "฿50", "vat_rate": "7%", "lines": [
            {"description": "A", "qty": "1,000", "unit_price": "฿100", "discount": "฿5"},
            {"description": "B", "qty": "2", "amount": "2,469.00"}]})
        first, second = mapped["lines"]
        self.assertEqual((first["qty"], first["unit_price"], first["discount"]), (1000.0, 100.0, 5.0))
        self.assertEqual(second["unit_price"], 1234.5)  # 只印了行金额：单价 = 行金额 ÷ 数量（带千分位也读得出）
        self.assertEqual((mapped["header_discount"], mapped["vat_rate"]), (50.0, 7.0))

    def test_a_percentage_discount_goes_to_discount_pct(self):
        mapped = sales.map_recognised({"lines": [{"description": "A", "qty": 2, "unit_price": 100, "discount": "10%"},
                                                 {"description": "B", "qty": 1, "unit_price": 100, "discount_percent": "5 %"}]})
        self.assertEqual([(row["discount"], row["discount_pct"]) for row in mapped["lines"]], [(0, 10.0), (0, 5.0)])
        totals = sales.compute_totals(mapped["lines"], vat_rate=0)
        self.assertEqual(float(totals["grand_total"]), 275.0)

    def test_an_unreadable_price_or_discount_is_kept_for_the_strict_check(self):
        mapped = sales.map_recognised({"discount_total": "ลดพิเศษ", "lines": [{"description": "A", "qty": "สอง",
                                                                                "unit_price": "ตามตกลง"}]})
        row = mapped["lines"][0]
        self.assertEqual((row["qty"], row["unit_price"], mapped["header_discount"]), ("สอง", "ตามตกลง", "ลดพิเศษ"))

    def test_the_posting_payload_keeps_the_percentage_discount(self):
        self.assertIn('"discount", "discount_pct") if row.get(key) not in (None, "")', INTAKE)


class WebForm(unittest.TestCase):
    """主会话 11-19：网页那一格读不懂时空着、原生必填拦着，不让数字控件显示成 0 被不知不觉提交；LINE / CC 拿原文让入账报错。"""

    def test_the_web_gets_null_for_an_unreadable_number_and_numbers_for_the_rest(self):
        result = {"discount_total": "พิเศษ", "rounding": 0.5, "lines": [
            {"qty": "สอง", "unit_price": "ตามตกลง", "discount": "n/a", "description": "1,000 ชิ้น"},
            {"qty": 2.0, "unit_price": 10.0, "discount": "10%"}]}
        web = background.web_numbers("purchase", result)
        self.assertEqual((web["discount_total"], web["rounding"]), (None, 0.5))
        self.assertEqual(web["lines"][0], {"qty": None, "unit_price": None, "discount": "n/a", "description": "1,000 ชิ้น"})
        self.assertEqual(web["lines"][1], {"qty": 2.0, "unit_price": 10.0, "discount": "10%"})
        self.assertEqual(result["lines"][0]["unit_price"], "ตามตกลง")  # 存下的识别原文不动（LINE / CC 入账照它报错）
        self.assertEqual(background.web_numbers("sales", {"header_discount": "ลด", "lines": []})["header_discount"], None)

    def test_both_web_paths_and_the_form_use_it(self):
        self.assertIn('"result": web_numbers(kind, result)', (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/recognition/background.py")
                      .read_text(encoding="utf-8"))
        self.assertIn("form = background.web_numbers(doc.direction, build_payload(doc))", INTAKE)
        desk = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/public/desk"
        self.assertIn('form.set_df_property(key, "reqd", 1)', (desk / "transactions.js").read_text(encoding="utf-8"))
        self.assertIn("p.trade.require_blank(line.form, values);", (desk / "transactions.js").read_text(encoding="utf-8"))
        for name in ("purchase-form.js", "sales-form.js"):
            text = (desk / name).read_text(encoding="utf-8")
            self.assertEqual(text.count("p.trade.require_blank(form, "), 2, name)  # 识别预填、LINE 卡编辑打开


if __name__ == "__main__":
    unittest.main()
