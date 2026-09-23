"""单据编号的形状与前缀：这是收发存报表"关联单据"那一列的取值来源。

两处口径，各管一段：

· **前缀**来自旧线上生产版本 `services/erp/document_numbers.py`
  （固定源 ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188）：采购 PE、销售 SI、入库 IR、
  出库 IS，四个字母固定。
· **生成逻辑**来自用户给的 MR.ERP 源码 `/Users/skin/mrerp-source`：每种单据一个计数器
  （`tbl_autono.runautono`，按 `comidyear` + `idmenu`），号码 = `userdefinedno` + `-` +
  六位零填充流水（`allform.php` 的 `str_pad(..., 6, "0", STR_PAD_LEFT)`），
  表单把号填进只读输入框、提交时计数器推到已用号 + 1（`allsave.php`）。

合起来就是 `IR2569-000001`：类型码 + 佛历年份 + 六位流水，按"类型 + 年"从 000001 起。
这些断言不需要 Frappe，也不连库。
"""
from datetime import date
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_erp_portal"))

from pearnly_erp_portal.numbering import contracts  # noqa: E402


class Prefixes(unittest.TestCase):
    def test_the_four_business_prefixes_are_fixed(self):
        """采购 PE / 销售 SI / 入库 IR / 出库 IS —— 与旧线上逐字相同。"""
        self.assertEqual(contracts.BY_DIRECTION,
                         {"purchase": "PE", "sales": "SI", "in": "IR", "out": "IS"})
        self.assertEqual(contracts.PREFIXES, ("PE", "SI", "IR", "IS"))

    def test_the_old_production_source_pins_the_same_letters(self):
        """把口径钉在旧线上那份实现上：四个字母必须仍然出现在它的映射里。"""
        old = Path("/Users/skin/Developer/Pearnly/pearnly-app")
        if not (old / ".git").exists():
            self.skipTest("旧项目不在本机")
        source = (old / "services/erp/document_numbers.py")
        if not source.is_file():
            self.skipTest("旧项目工作树里没有这个文件")
        text = source.read_text(encoding="utf-8")
        self.assertIn('"purchase": "PE", "sales": "SI", "in": "IR", "out": "IS"', text)
        self.assertIn("year + 543", text, "旧线上日期是佛历年")

    def test_an_unknown_direction_is_refused(self):
        with self.assertRaises(ValueError):
            contracts.prefix_for("returns")
        with self.assertRaises(ValueError):
            contracts.prefix_for(None)


class Shape(unittest.TestCase):
    def test_the_number_is_prefix_buddhist_year_and_serial(self):
        """`IR2569-000001`：佛历 2569 = 公历 2026。"""
        self.assertEqual(contracts.format_number("IR", date(2026, 9, 22), 1), "IR2569-000001")
        self.assertEqual(contracts.format_number("IS", date(2026, 1, 5), 42), "IS2569-000042")
        self.assertEqual(contracts.format_number("PE", date(2027, 12, 31), 9999),
                         "PE2570-009999")

    def test_the_serial_is_six_digits_and_pads(self):
        """六位补零是照 MR.ERP 的 `str_pad($runautono, 6, "0")` 搬的，不是四位。"""
        self.assertEqual(contracts.SERIAL_WIDTH, 6)
        self.assertEqual(contracts.format_number("SI", date(2026, 9, 22), 7), "SI2569-000007")
        self.assertTrue(contracts.format_number("SI", date(2026, 9, 22), 7).endswith("000007"))

    def test_the_serial_carries_over_six_digits(self):
        self.assertEqual(contracts.format_number("IR", date(2026, 9, 22), 1234567),
                         "IR2569-1234567")

    def test_the_bucket_is_the_buddhist_year(self):
        """桶是佛历年：MR.ERP 的计数器也是按年记账（`comidyear` + `idmenu`）。

        "同类型同一年共用一个计数器"由序号表名字里的前缀保证，所以桶只放年份。
        """
        self.assertEqual(contracts.year_bucket(date(2026, 9, 22)), "2569")
        self.assertEqual(contracts.year_bucket(date(2026, 1, 5)), "2569")
        self.assertEqual(contracts.year_bucket(date(2026, 12, 31)), "2569")
        self.assertEqual(contracts.year_bucket(date(2027, 1, 1)), "2570")
        self.assertEqual(contracts.year_bucket("2026-09-22"), contracts.year_bucket(date(2026, 9, 22)))

    def test_a_number_the_system_did_not_issue_is_not_accepted(self):
        """手输的、别家前缀的、位数不对的、老的四位日流水格式，都不算本系统的号。"""
        for value in ("STE-2026-00052", "IR-25690922-0001", "IR2569-1", "IR2569-00001",
                      "XX2569-000001", "IR256-000001", "IR2569-00000A", "IR2026-000001", "",
                      None, "IR2569-000001-1", "IR25690-000001"):
            with self.subTest(value=value):
                self.assertFalse(contracts.matches(value), value)

    def test_issued_numbers_round_trip(self):
        number = contracts.format_number("IS", date(2026, 9, 22), 12)
        self.assertTrue(contracts.matches(number))
        self.assertTrue(contracts.matches(number, "IS"))
        self.assertFalse(contracts.matches(number, "IR"))
        self.assertEqual(contracts.split(number), ("IS", "2569", 12))

    def test_the_mrerp_source_pins_the_six_digit_mechanism(self):
        """把形状钉在用户给的那份 MR.ERP 源码上：六位补零 + 表单只读显示。

        源码不在本机就跳过（和旧项目那条一样的处理）。
        """
        source = Path("/Users/skin/mrerp-source/src/arse/allform.php")
        save = Path("/Users/skin/mrerp-source/src/arse/allsave.php")
        if not source.is_file() or not save.is_file():
            self.skipTest("MR.ERP 源码不在本机")
        form = source.read_text(encoding="utf-8", errors="replace")
        self.assertIn('str_pad($fdrunautono_db, 6, "0", STR_PAD_LEFT)', form)
        self.assertIn("readonly", form, "表单上那个号是只读显示，不给手改")
        self.assertIn("UPDATE tbl_autono SET runautono=runautono+1",
                      save.read_text(encoding="utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
