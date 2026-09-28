"""对账导出文件名用任务名称（`reconciliation.naming`）。"""
import unittest

from pearnly_cowork.reconciliation import naming


class ExportNames(unittest.TestCase):
    def test_the_task_title_names_the_file(self):
        # 2026-09-28 录制：导出是 BankRecon_v2_CBR-74c283…_KBANK.xlsx，认不出是哪一份
        self.assertEqual(naming.export_filename("KBank กระแสรายวัน ก.ย. 2569", "x"), "KBank กระแสรายวัน ก.ย. 2569.xlsx")
        self.assertEqual(naming.export_filename('ธ.ค./2569: "ทดลอง"?', "x"), "ธ.ค. 2569 ทดลอง.xlsx")
        self.assertEqual(naming.export_filename("   ", "BankRecon_v2_CBR-1_KBANK"), "BankRecon_v2_CBR-1_KBANK.xlsx")
        self.assertEqual(len(naming.export_filename("ก" * 200, "x")), 80 + len(".xlsx"))


if __name__ == "__main__":
    unittest.main()
