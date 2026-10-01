"""银行页逐页自核的两个修法（用户 2026-10-01 同意）。离线：模型、原文都用替身。

① 读出的行明显少于原文里带日期的行（压测 B08 第 6 页：两张表只读了一张，20 行带日期只读出 11 行）：带上「这页约有
   N 行」再读一次，只有读出更多行、且断点没有增加才用。
② 页内余额链只断一处、按前后余额倒推的金额原样印在本页原文里（压测 B07-BAY-CA 第 8 页：邻行的金额读到了这一行）：
   按倒推值纠正、标成已更正；其余情况一律不改。
"""
import json
import unittest
from types import SimpleNamespace

from pearnly_cowork.ocr.enterprise import pipeline, self_check


def entry(day, deposit="", withdrawal="", balance=""):
    return {"transaction_date": f"2026-01-{day:02d}", "deposit": deposit, "withdrawal": withdrawal, "balance": balance}


def transcript_with(lines, extra=""):
    return "\n".join(f"{day:02d}/01/26 TRF {amount} {balance}" for day, amount, balance in lines) + extra


class DatedLines(unittest.TestCase):
    def test_counts_one_per_printed_row_in_the_usual_formats(self):
        text = "Statement 01/01/2026 - 31/01/2026\n05/01/26 TRF 100.00 1,100.00\n06-01-2026 X 5.00\n" \
               "15 ม.ค. 69 โอน 20.00\n17 JAN 26 POS 3.00\nTotal 128.00"
        self.assertEqual(self_check.dated_lines(text), 5)  # 期间那一行也算：所以「少读」要差 3 行以上

    def chain(self, count, drop=()):
        """``count`` 笔连续的流水（每笔 +10），去掉 ``drop`` 里的几笔：去掉中间一段就在接缝处断一处。"""
        rows, balance = [], 1000
        for day in range(1, count + 1):
            balance += 10
            if day not in drop:
                rows.append(entry(day, deposit="10.00", balance=f"{balance:,.2f}"))
        return {"opening_balance": "1,000.00", "entries": rows}

    def test_short_only_when_clearly_fewer_and_the_chain_breaks_at_the_seam(self):
        text = "\n".join(f"{d:02d}/01/26 x" for d in range(1, 21))
        self.assertEqual(self_check.rows_short(self.chain(20, drop=range(7, 16)), text), 20)  # 11 / 20、断一处
        self.assertIsNone(self_check.rows_short(self.chain(20, drop=range(18, 21)), text))  # 末尾少 3 笔：链没断
        self.assertIsNone(self_check.rows_short(self.chain(20, drop=(5, 6)), text))          # 只差 2 行
        self.assertIsNone(self_check.rows_short(self.chain(20), text))

    def test_pages_that_only_look_short_are_not_read_again(self):
        header_dates = "Statement 01/01/2026 - 31/01/2026\nPrinted 02/02/2026\nAs of 31/01/2026"
        self.assertIsNone(self_check.rows_short({"entries": []}, header_dates))  # 无流水月：一行都没读出
        no_balances = {"opening_balance": "1,000.00", "entries": [
            entry(day, deposit="10.00", balance="5.00") for day in range(1, 11)]}  # 每笔都断：没有逐笔余额的版式
        self.assertIsNone(self_check.rows_short(no_balances, "\n".join(f"{d:02d}/01/26 {d:02d}/01/26 x" for d in range(1, 21))))


class RepairSingleBreak(unittest.TestCase):
    def page(self, wrong="9,999.00"):
        # 1000 → +200 → 1200 → +450（读成了别的数）→ 1650 → −50 → 1600
        return {"opening_balance": "1,000.00", "closing_balance": "1,600.00", "entries": [
            entry(1, deposit="200.00", balance="1,200.00"), entry(2, deposit=wrong, balance="1,650.00"),
            entry(3, withdrawal="50.00", balance="1,600.00")]}

    def test_the_amount_printed_on_the_page_replaces_the_misread_one(self):
        page = self.page()
        index = self_check.repair_single_break(page, transcript_with([(1, "200.00", "1,200.00"), (2, "450.00", "1,650.00"),
                                                                       (3, "50.00", "1,600.00")]))
        self.assertEqual(index, 1)
        self.assertEqual((page["entries"][1]["deposit"], page["entries"][1]["chain_amount_imputed"]), ("450.00", True))
        self.assertEqual(self_check.bank_breaks(page), 0)

    def test_nothing_changes_when_the_page_does_not_print_that_amount(self):
        page = self.page()
        before = json.dumps(page, sort_keys=True)
        self.assertIsNone(self_check.repair_single_break(page, "05/01/26 something else 12.00"))
        self.assertEqual(json.dumps(page, sort_keys=True), before)

    def test_two_breaks_or_a_break_at_the_carried_balance_are_left_for_review(self):
        page = self.page()
        page["entries"][2]["withdrawal"] = "70.00"  # 再断一处
        self.assertIsNone(self_check.repair_single_break(page, "450.00 20.00"))
        carried = {"opening_balance": "1,000.00", "closing_balance": "9,999.00",
                   "entries": [entry(1, deposit="200.00", balance="1,200.00")]}
        self.assertIsNone(self_check.repair_single_break(carried, "1,200.00 9,999.00 200.00 8,799.00"))

    def test_a_misread_balance_is_not_turned_into_an_amount(self):
        # 余额读错（1,650 读成 1,690）：这一笔与下一笔两处都断，不是「只断一处」
        page = self.page(wrong="450.00")
        page["entries"][1]["balance"] = "1,690.00"
        self.assertIsNone(self_check.repair_single_break(page, "490.00 450.00"))

    def test_withdrawals_and_amount_direction_rows(self):
        page = {"opening_balance": "500.00", "entries": [
            {"amount": "7.00", "direction": "withdrawal", "balance": "380.00"}]}
        self.assertEqual(self_check.repair_single_break(page, "01/01/26 ATM 120.00 380.00"), 0)
        self.assertEqual((page["entries"][0]["amount"], page["entries"][0]["direction"]), ("120.00", "withdrawal"))


class Checked(unittest.TestCase):
    def run_checked(self, first, second, transcript, may_reread=True):
        calls = []

        def model_call(request):
            calls.append(request)
            return SimpleNamespace(text=json.dumps(second), finish_reason="STOP", model="gemini-3.8-flash",
                                   input_tokens=10, output_tokens=10)
        data, response, audit, extra = pipeline._checked(model_call, b"img", "image/png", "bank", transcript, 6,
                                                         first, SimpleNamespace(model="m"), {}, may_reread)
        return data, audit, calls

    def table(self, count, start=1, balance=1000):
        rows, lines = [], []
        for day in range(start, start + count):
            balance += 10
            rows.append(entry(day, deposit="10.00", balance=f"{balance:,.2f}"))
            lines.append((day, "10.00", f"{balance:,.2f}"))
        return rows, lines

    def test_a_page_read_short_is_read_again_and_the_fuller_reading_is_used(self):
        rows, lines = self.table(20)
        first = {"opening_balance": "1,000.00", "entries": rows[:6] + rows[15:]}  # 漏了中间一张表：接缝处断一处
        second = {"opening_balance": "1,000.00", "entries": rows}
        data, audit, calls = self.run_checked(first, second, transcript_with(lines))
        self.assertEqual(len(calls), 1)
        self.assertIn("about 20 dated transaction lines", calls[0].prompt)
        self.assertEqual(len(data["entries"]), 20)
        self.assertEqual((audit["adopted"], audit["rows"], audit["dated_lines"]), ("reread", [11, 20], 20))

    def test_a_fuller_reading_with_more_breaks_is_used_but_every_row_is_marked_for_review(self):
        # 用户 2026-10-01 定：一页两张表，两张表的余额本来不是一条链（B08 第 6 页补回 24 行、断点 3 → 8）
        rows, lines = self.table(20)
        broken = [dict(row) for row in rows]
        broken[15]["deposit"] = "99.00"
        broken[17]["deposit"] = "77.00"
        first = {"opening_balance": "1,000.00", "entries": rows[:6] + rows[15:]}
        data, audit, _calls = self.run_checked(first, {"opening_balance": "1,000.00", "entries": broken},
                                               transcript_with(lines))
        self.assertEqual(len(data["entries"]), 20)
        self.assertEqual((audit["adopted"], audit["recovered"]), ("reread", True))
        self.assertTrue(all(row.get("review_required") for row in data["entries"]))

    def test_a_reading_far_beyond_the_printed_dates_is_not_used(self):
        rows, lines = self.table(20)
        many = [entry(1, deposit="10.00", balance="1.00") for _ in range(40)]  # 原文 20 行带日期，却读出 40 行
        first = {"opening_balance": "1,000.00", "entries": rows[:6] + rows[15:]}
        data, audit, _calls = self.run_checked(first, {"opening_balance": "1,000.00", "entries": many},
                                               transcript_with(lines))
        self.assertEqual(len(data["entries"]), 11)
        self.assertNotIn("adopted", audit)

    def test_a_clean_fuller_reading_is_not_marked_for_review(self):
        rows, lines = self.table(20)
        first = {"opening_balance": "1,000.00", "entries": rows[:6] + rows[15:]}
        data, audit, _calls = self.run_checked(first, {"opening_balance": "1,000.00", "entries": rows},
                                               transcript_with(lines))
        self.assertNotIn("recovered", audit)
        self.assertFalse(any(row.get("review_required") for row in data["entries"]))

    def test_a_page_read_in_full_is_not_read_again(self):
        rows, lines = self.table(20)
        data, audit, calls = self.run_checked({"opening_balance": "1,000.00", "entries": rows}, {}, transcript_with(lines))
        self.assertEqual(calls, [])
        self.assertEqual((audit["state"], audit["reread"]), ("passed", False))

    def test_no_budget_left_means_no_extra_paid_call(self):
        rows, lines = self.table(20)
        _data, audit, calls = self.run_checked({"opening_balance": "1,000.00", "entries": rows[:6] + rows[15:]}, {},
                                               transcript_with(lines), may_reread=False)
        self.assertEqual(calls, [])
        self.assertFalse(audit["reread"])

    def test_a_single_break_left_after_the_reread_is_repaired_from_the_page_text(self):
        rows, lines = self.table(10)
        wrong = [dict(row) for row in rows]
        wrong[4]["deposit"] = "90.00"  # 读成了邻行的数
        page = {"opening_balance": "1,000.00", "entries": wrong}
        data, audit, calls = self.run_checked(page, {"opening_balance": "1,000.00", "entries": [dict(r) for r in wrong]},
                                              transcript_with(lines))
        self.assertEqual(len(calls), 1)  # 断了一处：照原来先再读一次（再读还是同样断）
        self.assertEqual((audit["state"], audit["repaired_row"]), ("passed", 4))
        self.assertEqual(data["entries"][4]["deposit"], "10.00")
        self.assertTrue(data["entries"][4]["chain_amount_imputed"])


class RecoveredPagesReachTheResult(unittest.TestCase):
    """补读的页从识别结果一路传到对账结果里的提示（与「第几页没读出」同一条路）。"""

    def test_the_contract_keeps_them_and_refuses_bad_values(self):
        from pearnly_cowork.reconciliation import bank_recon_contracts as contracts
        from pearnly_cowork.reconciliation.recon_contracts import ReconciliationContractError

        record = {"file": "a.pdf", "rows": 10, "ok": True, "recovered_pages": [6]}
        info = contracts.parse_info({"stmt_files": [record], "gl_files": []})
        self.assertEqual(info["stmt_files"][0]["recovered_pages"], [6])
        with self.assertRaises(ReconciliationContractError):
            contracts.parse_info({"stmt_files": [{**record, "recovered_pages": ["6"]}], "gl_files": []})

    def test_the_result_names_the_page(self):
        import inspect

        from pearnly_cowork.reconciliation import revision

        self.assertIn("reconciliation.statement_pages_recovered", inspect.getsource(revision))


if __name__ == "__main__":
    unittest.main()
