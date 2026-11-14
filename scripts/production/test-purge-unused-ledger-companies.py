"""清掉没用过的自动建公司（`purge-unused-ledger-companies.py`）：不连库的检查——判定与计划指纹。真库上的预演 / 执行在实验栈跑过
（记录见 docs/work/2026-11-14-company-onboarding.md「清理没用过的自动建公司」）。"""
from pathlib import Path
import runpy
import unittest

script = runpy.run_path(str(Path(__file__).with_name("purge-unused-ledger-companies.py")))


class Purge(unittest.TestCase):
    def test_any_reference_keeps_the_company(self):
        candidates = [{"company": "CO-1", "company_name": "A"}, {"company": "CO-2", "company_name": "B"},
                      {"company": "CO-3", "company_name": "C"}]
        references = {"CO-1": {}, "CO-2": {"Purchase Document.company": 1}, "CO-3": {"ERP Line Intake.company": 0}}
        purge, keep = script["decide"](candidates, references)
        self.assertEqual([row["company"] for row in purge], ["CO-1", "CO-3"])
        self.assertEqual(keep, [{"company": "CO-2", "company_name": "B", "blocked_by": {"Purchase Document.company": 1}}])

    def test_the_fingerprint_changes_when_the_plan_does(self):
        one = [{"company": "CO-1", "delete": {"ERP Customer": 10, "ERP Company": 1}}]
        same = [{"company": "CO-1", "delete": {"ERP Company": 1, "ERP Customer": 10}}]
        more = [{"company": "CO-1", "delete": {"ERP Company": 1, "ERP Customer": 11}}]
        self.assertEqual(script["fingerprint"](one), script["fingerprint"](same))
        self.assertNotEqual(script["fingerprint"](one), script["fingerprint"](more))
        self.assertNotEqual(script["fingerprint"](one), script["fingerprint"](one + [{"company": "CO-2", "delete": {}}]))

    def test_documents_and_cc_records_are_never_in_the_own_set(self):
        for doctype in ("Purchase Document", "ERP Sales Document", "ERP Sale", "ERP Shift", "ERP Stock Entry", "ERP Stock Count",
                        "ERP Stock Opening", "ERP Collection", "ERP Line Intake", "ERP Company Note", "ERP Serial No"):
            self.assertNotIn(doctype, script["OWN"])
        self.assertIn("company", script["NAME_FIELDS"])  # CC 写入计划、定时任务等按公司挂的记录也算引用


if __name__ == "__main__":
    unittest.main()
